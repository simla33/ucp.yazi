"""Doctor reports from real Yazi/private X11; Cocoa-only cases are simulated."""
import json
import re
import shutil
import subprocess

from support.integration import IntegrationTest, REPO, tree


HELPERS = ("wl-copy", "wl-paste", "xclip", "osascript", "pbpaste",
           "realpath", "cp", "chmod", "mktemp")
FILE_HELPERS = ("realpath", "cp", "chmod", "mktemp")


class DoctorSupport(IntegrationTest):
    def restricted_path(self, names):
        for name in names:
            executable = shutil.which(name)
            self.assertIsNotNone(executable, "required real helper: " + name)
            (self.bin / name).symlink_to(executable)
        self.env["PATH"] = str(self.bin)

    def doctor(self):
        self.done.unlink(missing_ok=True)
        (self.root / "result").unlink(missing_ok=True)
        self.offset = self.yazi.mark()
        self.emit("plugin", "ucp-test", "doctor")
        self.finish("doctor")
        report = (self.root / "result").read_text()
        fields = dict(line.split(": ", 1) for line in report.splitlines() if ": " in line)
        for name in (*HELPERS, "Session", "Clipboard backend", "Formats", "Unrecognized types"):
            self.assertIn(name, fields)
        return report, fields

    def assert_private(self, report, *secrets):
        for secret in (str(self.root), self.env["HOME"], self.env["XAUTHORITY"], *secrets):
            self.assertNotIn(secret, report)
        self.assertNotRegex(report, r"[\x00-\x08\x0b-\x1f\x7f]")


class DoctorX11Tests(DoctorSupport):
    def test_real_metadata_versions_and_clipboard_remain_unchanged(self):
        self.restricted_path(("xclip", *FILE_HELPERS))
        payload = b"private clipboard credential: ucp-doctor-payload-secret\n"
        self.clip(payload, "text/plain")
        self.write(self.dst / "private-filename-secret", b"unchanged")
        self.start()
        before = tree(self.dst), tree(self.src), tree(self.tmpdir)
        report, fields = self.doctor()
        self.assertEqual(fields["Clipboard backend"].split()[0], "x11")
        self.assertIn("text/plain", fields["Formats"].split(", "))
        for name in ("xclip", *FILE_HELPERS):
            flag = "-version" if name == "xclip" else "--version"
            output = subprocess.run([shutil.which(name), flag], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, check=True, timeout=3)
            first_line = (output.stdout or output.stderr).decode().splitlines()[0]
            expected = re.search(r"\d+\.\d[\d.]*", first_line).group()
            self.assertEqual(fields[name], "available; version " + expected)
        self.assertEqual(self.clip_read("text/plain"), payload)
        self.assertEqual((tree(self.dst), tree(self.src), tree(self.tmpdir)), before)
        self.assert_private(report, payload.decode().strip(), "private-filename-secret")

    def test_unknown_mime_is_counted_without_exposing_its_identifier(self):
        self.restricted_path(("xclip",))
        target = "application/x-private-account-ucp-secret"
        payload = b"private unknown payload"
        self.clip(payload, target)
        metadata = self.clip_read("TARGETS").decode().splitlines()
        self.start()
        report, fields = self.doctor()
        self.assertEqual(fields["Clipboard backend"].split()[0], "x11")
        # xclip advertises the supplied atom and protocol atoms, not payloads.
        recognized = {"UTF8_STRING", "STRING", "TEXT"}
        self.assertEqual(int(fields["Unrecognized types"]),
                         sum(mime not in recognized for mime in metadata))
        self.assertGreaterEqual(int(fields["Unrecognized types"]), 1)
        self.assert_private(report, target, payload.decode())
        self.assertEqual(self.clip_read(target), payload)

    def test_mime_parameters_and_session_values_are_redacted(self):
        self.restricted_path(("xclip",))
        target = "text/plain;charset=private-charset-secret"
        payload = b"private parameter payload"
        self.clip(payload, target)
        secrets = {
            "WAYLAND_DISPLAY": "private-wayland-secret",
            "SSH_CONNECTION": "private-hostname-secret 23456 private-peer-secret 22",
            "TMUX": "private-multiplexer-secret",
            "AWS_SECRET_ACCESS_KEY": "private-credential-secret",
        }
        self.env.update(secrets)
        self.start()
        report, fields = self.doctor()
        self.assertEqual(fields["Session"], "wayland+x11; ssh=yes; multiplexer=yes")
        self.assertEqual(fields["Clipboard backend"].split()[0], "x11")
        self.assertIn("text/plain", fields["Formats"].split(", "))
        self.assert_private(report, *secrets.values(), "private-charset-secret", payload.decode())
        self.assertEqual(self.clip_read(target), payload)

    def test_missing_helpers_are_reported_without_claiming_empty_clipboard(self):
        payload = b"clipboard remains populated when helpers cannot be found"
        self.clip(payload, "text/plain")
        self.restricted_path(())
        self.start()
        before = tree(self.dst), tree(self.tmpdir)
        report, fields = self.doctor()
        for name in HELPERS:
            self.assertEqual(fields[name], "missing")
        self.assertEqual(fields["Clipboard backend"].split()[0], "unavailable")
        self.assertEqual(fields["Formats"], "unknown")
        self.assertEqual(fields["Unrecognized types"], "unknown")
        self.assertEqual(self.clip_read("text/plain"), payload)
        self.assertEqual((tree(self.dst), tree(self.tmpdir)), before)
        self.assert_private(report, payload.decode())

    def test_real_failed_probe_redacts_stderr_and_does_not_report_empty(self):
        self.restricted_path(("xclip",))
        self.env["DISPLAY"] = "private-display-hostname-secret:invalid"
        self.start()
        report, fields = self.doctor()
        self.assertTrue(fields["xclip"].startswith("available"))
        self.assertEqual(fields["Clipboard backend"].split()[0], "unavailable")
        self.assertEqual(fields["Formats"], "unknown")
        self.assertEqual(fields["Unrecognized types"], "unknown")
        self.assert_private(report, self.env["DISPLAY"], "private-display-hostname-secret")


class SimulatedMacDoctorTests(DoctorSupport):
    """Unavailable Cocoa helper boundary only; does not validate native AppKit."""
    use_x11 = False

    def setUp(self):
        super().setUp()
        self.mac_state = self.root / "doctor-mac-state.json"
        self.mac_log = self.root / "doctor-mac-calls.jsonl"
        self.env.update(UCP_MAC_STATE=str(self.mac_state), UCP_MAC_LOG=str(self.mac_log),
                        PATH=str(self.bin))
        for name in ("osascript", "pbpaste"):
            (self.bin / name).symlink_to(REPO / "tests" / "fixtures" / "osascript.py")

    def test_only_metadata_is_requested_and_unknown_mac_types_are_counted(self):
        private_type = "com.private-account-secret.clipboard"
        private_text = "private-mac-payload-secret"
        self.mac_state.write_text(json.dumps({
            "types": ["public.utf8-plain-text", "public.file-url", private_type],
            "text": private_text,
        }))
        self.start()
        before = tree(self.tmpdir), tree(self.dst)
        report, fields = self.doctor()
        calls = [json.loads(line) for line in self.mac_log.read_text().splitlines()]
        self.assertEqual([call["kind"] for call in calls], ["types"])
        self.assertEqual(fields["Clipboard backend"].split()[0], "mac")
        self.assertEqual(set(fields["Formats"].split(", ")), {"text/plain", "text/uri-list"})
        self.assertEqual(fields["Unrecognized types"], "1")
        self.assertEqual(fields["pbpaste"], "available (version not probed)")
        self.assert_private(report, private_type, private_text)
        self.assertEqual((tree(self.tmpdir), tree(self.dst)), before)

    def test_successful_empty_metadata_is_not_a_probe_failure(self):
        self.mac_state.write_text(json.dumps({"types": []}))
        self.start()
        _, fields = self.doctor()
        self.assertEqual(fields["Clipboard backend"].split()[0], "mac")
        self.assertEqual(fields["Formats"], "none recognized")
        self.assertEqual(fields["Unrecognized types"], "0")
        calls = [json.loads(line) for line in self.mac_log.read_text().splitlines()]
        self.assertEqual([call["kind"] for call in calls], ["types"])
