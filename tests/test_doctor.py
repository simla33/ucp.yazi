"""Standalone doctor CLI: private real X11 and simulated Cocoa boundaries."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

if os.name != "nt":
    from support.integration import IntegrationTest, Processes, tree

REPO = Path(__file__).resolve().parents[1]
HELPERS = ("wl-copy", "wl-paste", "xclip", "osascript", "pbpaste",
           "realpath", "cp", "chmod", "mktemp")
FILE_HELPERS = ("realpath", "cp", "chmod", "mktemp")


@unittest.skipIf(os.name == "nt", "POSIX helper fixtures; Windows has native doctor coverage")
class DoctorSupport(unittest.TestCase):
    use_x11 = False

    def setUp(self):
        if self.use_x11 and (not sys.platform.startswith("linux") or
                             not all(shutil.which(name) for name in ("Xvfb", "xclip"))):
            self.skipTest("private X11 requires Linux, Xvfb and xclip; Yazi is not required")
        temp = tempfile.TemporaryDirectory(prefix="ucp-doctor-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.src, self.dst = self.root / "src", self.root / "dst"
        self.bin, self.tmpdir = self.root / "bin", self.root / "tmp"
        for path in (self.src, self.dst, self.bin, self.tmpdir, self.root / "home"):
            path.mkdir(mode=0o700)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("UCP_", "YAZI_", "LD_"))}
        for name in ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "SSH_CONNECTION",
                     "SSH_CLIENT", "SSH_TTY", "TMUX", "ZELLIJ"):
            self.env.pop(name, None)
        self.env.update(PATH=str(self.bin), HOME=str(self.root / "home"),
                        TMPDIR=str(self.tmpdir), DISPLAY="ucp-test-no-x11",
                        XAUTHORITY=str(self.root / "no-xauthority"))
        self.processes = Processes()
        self.addCleanup(self.processes.close)
        self.owners = []
        if self.use_x11:
            # Reuse only X11 lifecycle/clipboard methods, never IntegrationTest's
            # Yazi-dependent setup, configuration, or client.
            IntegrationTest._start_xvfb(self)

    def clip(self, data, target="text/uri-list"):
        return IntegrationTest.clip(self, data, target)

    def clip_read(self, target="text/uri-list"):
        return IntegrationTest.clip_read(self, target)

    def restricted_path(self, names):
        for name in names:
            executable = shutil.which(name)
            self.assertIsNotNone(executable, "required real helper: " + name)
            (self.bin / name).symlink_to(executable)

    def helper(self, name, source):
        path = self.bin / name
        path.write_text("#!" + sys.executable + "\n" + source)
        path.chmod(0o700)

    def cli(self, *arguments, timeout=15):
        return subprocess.run([sys.executable, str(REPO / "ucp"), *arguments],
                              env=self.env, cwd=self.dst, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)

    def doctor(self):
        result = self.cli("doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = result.stdout
        fields = dict(line.split(": ", 1) for line in report.splitlines() if ": " in line)
        for name in (*HELPERS, "Session", "Clipboard backend", "Clipboard metadata",
                     "Formats", "Unrecognized types"):
            self.assertIn(name, fields)
        return report, fields

    def assert_private(self, report, *secrets):
        for secret in (str(self.root), self.env["HOME"], self.env["XAUTHORITY"], *secrets):
            self.assertNotIn(secret, report)
        self.assertNotRegex(report, r"[\x00-\x08\x0b-\x1f\x7f]")


class DoctorX11Tests(DoctorSupport):
    use_x11 = True

    def test_real_metadata_versions_and_clipboard_remain_unchanged(self):
        self.restricted_path(("xclip", *FILE_HELPERS))
        payload = b"private clipboard credential: ucp-doctor-payload-secret\n"
        self.clip(payload, "text/plain")
        (self.dst / "private-filename-secret").write_bytes(b"unchanged")
        before = tree(self.dst), tree(self.src), tree(self.tmpdir)
        report, fields = self.doctor()
        self.assertEqual(fields["Clipboard backend"].split()[0], "x11")
        self.assertEqual(fields["Clipboard metadata"], "available")
        self.assertIn("text/plain", fields["Formats"].split(", "))
        for name in ("xclip", *FILE_HELPERS):
            flag = "-version" if name == "xclip" else "--version"
            output = subprocess.run([shutil.which(name), flag], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, check=True, timeout=3)
            first_line = (output.stdout or output.stderr).decode().splitlines()[0]
            expected = re.search(r"\d+\.\d[\d.]*", first_line).group()
            self.assertEqual(fields[name], "available; version " + expected)
        self.assertEqual(fields["Yazi"], "unavailable")
        self.assertEqual(self.clip_read("text/plain"), payload)
        self.assertEqual((tree(self.dst), tree(self.src), tree(self.tmpdir)), before)
        self.assert_private(report, payload.decode().strip(), "private-filename-secret")

    def test_unknown_mime_is_counted_without_exposing_its_identifier(self):
        self.restricted_path(("xclip",))
        target = "application/x-private-account-ucp-secret"
        payload = b"private unknown payload"
        self.clip(payload, target)
        metadata = self.clip_read("TARGETS").decode().splitlines()
        report, fields = self.doctor()
        self.assertEqual(fields["Clipboard backend"].split()[0], "x11")
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
        report, fields = self.doctor()
        self.assertEqual(fields["Session"], "wayland+x11; ssh=yes; multiplexer=yes")
        self.assertEqual(fields["Clipboard backend"].split()[0], "x11")
        self.assertIn("text/plain", fields["Formats"].split(", "))
        self.assert_private(report, *secrets.values(), "private-charset-secret", payload.decode())
        self.assertEqual(self.clip_read(target), payload)

    def test_missing_helpers_are_reported_without_claiming_empty_clipboard(self):
        payload = b"clipboard remains populated when helpers cannot be found"
        self.clip(payload, "text/plain")
        before = tree(self.dst), tree(self.tmpdir)
        report, fields = self.doctor()
        for name in HELPERS:
            self.assertEqual(fields[name], "missing")
        self.assertEqual(fields["Clipboard backend"].split()[0], "unavailable")
        self.assertEqual(fields["Clipboard metadata"], "unavailable")
        self.assertEqual(fields["Formats"], "unknown")
        self.assertEqual(fields["Unrecognized types"], "unknown")
        self.assertEqual(self.clip_read("text/plain"), payload)
        self.assertEqual((tree(self.dst), tree(self.tmpdir)), before)
        self.assert_private(report, payload.decode())

    def test_real_failed_probe_redacts_stderr_and_does_not_report_empty(self):
        self.restricted_path(("xclip",))
        self.env["DISPLAY"] = "private-display-hostname-secret:invalid"
        report, fields = self.doctor()
        self.assertTrue(fields["xclip"].startswith("available"))
        self.assertEqual(fields["Clipboard backend"].split()[0], "unavailable")
        self.assertEqual(fields["Clipboard metadata"], "failed")
        self.assertEqual(fields["Formats"], "unknown")
        self.assertEqual(fields["Unrecognized types"], "unknown")
        self.assert_private(report, self.env["DISPLAY"], "private-display-hostname-secret")


class DoctorBoundaryTests(DoctorSupport):
    def test_no_yazi_or_helpers_required_and_no_disk_writes(self):
        before = tree(self.root)
        report, fields = self.doctor()
        self.assertEqual(tree(self.root), before)
        self.assertEqual(fields["Clipboard metadata"], "unavailable")
        self.assertEqual(fields["Formats"], "unknown")
        self.assertEqual(fields["Yazi"], "unavailable")
        self.assert_private(report)

    def test_safe_numeric_versions_from_stdout_and_stderr(self):
        self.helper("wl-copy", "print('wl-clipboard 2.2.1-private-build-secret')\n")
        self.helper("xclip", "import sys\n"
                    "if sys.argv[1:] == ['-version']:\n"
                    "    print('xclip version 0.13\\nprivate-stderr-secret', file=sys.stderr)\n"
                    "elif sys.argv[1:] == ['-selection', 'clipboard', '-o', '-t', 'TARGETS']:\n"
                    "    print('text/plain;charset=private-charset-secret')\n"
                    "else:\n"
                    "    raise AssertionError('payload operation forbidden')\n")
        report, fields = self.doctor()
        self.assertEqual(fields["wl-copy"], "available; version 2.2.1")
        self.assertEqual(fields["xclip"], "available; version 0.13")
        self.assertEqual(fields["Formats"], "text/plain")
        self.assert_private(report, "private-build-secret", "private-stderr-secret", "private-charset-secret")

    def test_yazi_multiline_and_single_line_versions_are_recognized_without_build_details(self):
        for banner in ("Yazi\n    Version: 26.9.1 (private-build-secret)\n    Debug: false",
                       "Yazi 26.9.1 (private-build-secret)"):
            with self.subTest(banner=banner):
                self.helper("yazi", "import sys\nassert sys.argv[1:] == ['--version']\n"
                            "print(" + repr(banner) + ")\n")
                report, fields = self.doctor()
                self.assertEqual(fields["Yazi"], "26.9.1")
                self.assert_private(report, "private-build-secret", "Debug")

    def test_unrecognized_version_banners_are_never_echoed(self):
        banners = {
            "wl-copy": "private-banner-secret 12.34",
            "wl-paste": "wl-clipboard 1.2.3.4.5-private-suffix-secret",
            "realpath": "realpath (GNU coreutils) 9.6private-secret",
            "cp": "cp (GNU coreutils) 99999999999.2",
            "chmod": "chmod (GNU coreutils) ９.６",
            "mktemp": "mktemp (GNU coreutils) 1..2",
        }
        for name, banner in banners.items():
            self.helper(name, "import sys\n"
                        "assert sys.argv[1:] in (['--version'], ['--list-types'])\n"
                        "print(" + repr(banner) + ")\n")
        report, fields = self.doctor()
        for name in banners:
            self.assertEqual(fields[name], "available (version unavailable)")
        self.assert_private(report, *banners.values())

    def test_successful_empty_wayland_metadata_is_not_failure(self):
        self.helper("wl-paste", "import sys\n"
                    "if sys.argv[1:] == ['--version']:\n"
                    "    print('wl-clipboard 2.2.1')\n"
                    "else:\n"
                    "    assert sys.argv[1:] == ['--list-types']\n")
        _, fields = self.doctor()
        self.assertEqual(fields["Clipboard metadata"], "available")
        self.assertEqual(fields["Clipboard backend"].split()[0], "wayland")
        self.assertEqual(fields["Formats"], "none recognized")
        self.assertEqual(fields["Unrecognized types"], "0")

    def test_failed_metadata_discards_stdout_and_stderr(self):
        self.helper("wl-paste", "import sys\n"
                    "print('private-stdout-secret')\n"
                    "print('private-stderr-secret', file=sys.stderr)\n"
                    "sys.exit(23)\n")
        report, fields = self.doctor()
        self.assertEqual(fields["wl-paste"], "available (version unavailable)")
        self.assertEqual(fields["Clipboard metadata"], "failed")
        self.assertEqual(fields["Formats"], "unknown")
        self.assert_private(report, "private-stdout-secret", "private-stderr-secret")

    def test_hung_version_and_metadata_probes_are_bounded(self):
        self.helper("wl-paste", "import time\ntime.sleep(60)\n")
        started = time.monotonic()
        report, fields = self.doctor()
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(fields["wl-paste"], "available (version unavailable)")
        self.assertEqual(fields["Clipboard metadata"], "failed")
        self.assertEqual(fields["Formats"], "unknown")
        self.assert_private(report)

    def test_subcommand_exit_behavior(self):
        for arguments in ((), ("copy",), ("doctor", "extra"), ("doctor", "--unknown")):
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("usage: ucp", result.stderr)
        for arguments in (("--help",), ("doctor", "--help")):
            result = self.cli(*arguments)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertIn("usage: ucp", result.stdout)

    def test_posix_executable_entrypoint(self):
        self.assertEqual((REPO / "ucp").read_text().splitlines()[0], "#!/usr/bin/env python3")
        self.assertTrue(os.access(REPO / "ucp", os.X_OK))
        (self.bin / "python3").symlink_to(sys.executable)
        result = subprocess.run([str(REPO / "ucp"), "doctor"], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ucp doctor (metadata only)", result.stdout)


class SimulatedMacDoctorTests(DoctorSupport):
    """Unavailable Cocoa helper boundary only; does not validate native AppKit."""

    def setUp(self):
        super().setUp()
        self.mac_state = self.root / "doctor-mac-state.json"
        self.mac_log = self.root / "doctor-mac-calls.jsonl"
        self.env.update(UCP_MAC_STATE=str(self.mac_state), UCP_MAC_LOG=str(self.mac_log))
        for name in ("osascript", "pbpaste"):
            (self.bin / name).symlink_to(REPO / "tests" / "fixtures" / "osascript.py")

    def test_only_metadata_is_requested_and_unknown_mac_types_are_counted(self):
        private_type = "com.private-account-secret.clipboard"
        private_text = "private-mac-payload-secret"
        self.mac_state.write_text(json.dumps({
            "types": ["public.utf8-plain-text", "public.file-url", private_type],
            "text": private_text,
        }))
        before = tree(self.tmpdir), tree(self.dst), self.mac_state.read_bytes()
        report, fields = self.doctor()
        calls = [json.loads(line) for line in self.mac_log.read_text().splitlines()]
        self.assertEqual([call["kind"] for call in calls], ["types"])
        script = calls[0]["argv"][3]
        self.assertNotRegex(script, r"readObjects|dataForType|stringForType|setString|clearContents|writeObjects")
        self.assertEqual(fields["Clipboard backend"].split()[0], "mac")
        self.assertEqual(set(fields["Formats"].split(", ")), {"text/plain", "text/uri-list"})
        self.assertEqual(fields["Unrecognized types"], "1")
        self.assertEqual(fields["pbpaste"], "available (version not probed)")
        self.assert_private(report, private_type, private_text)
        self.assertEqual((tree(self.tmpdir), tree(self.dst), self.mac_state.read_bytes()), before)

    def test_successful_empty_metadata_is_not_a_probe_failure(self):
        self.mac_state.write_text(json.dumps({"types": []}))
        _, fields = self.doctor()
        self.assertEqual(fields["Clipboard backend"].split()[0], "mac")
        self.assertEqual(fields["Clipboard metadata"], "available")
        self.assertEqual(fields["Formats"], "none recognized")
        self.assertEqual(fields["Unrecognized types"], "0")
        calls = [json.loads(line) for line in self.mac_log.read_text().splitlines()]
        self.assertEqual([call["kind"] for call in calls], ["types"])

    def test_failed_cocoa_metadata_is_not_an_empty_clipboard(self):
        self.mac_state.write_text("private-invalid-state-secret")
        report, fields = self.doctor()
        self.assertEqual(fields["osascript"], "available (version not probed)")
        self.assertEqual(fields["Clipboard metadata"], "failed")
        self.assertEqual(fields["Formats"], "unknown")
        self.assertFalse(self.mac_log.exists())
        self.assert_private(report, "private-invalid-state-secret")
