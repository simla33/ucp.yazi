"""SIMULATED macOS executable boundary on Linux, NOT native Cocoa validation.

Real Yazi and the unchanged plugin execute their macOS paths. Only osascript /
pbpaste stand in for unavailable native services, using explicit fixture bytes.
These cases prove safe argv/framing/filesystem behavior, not AppKit execution.
"""
import base64
import json
from pathlib import Path

from support.integration import IntegrationTest, PNG, REPO, file_uri, tree


class SimulatedMacOSBoundary(IntegrationTest):
    use_x11 = False

    def setUp(self):
        super().setUp()
        self.mac_state = self.root / "mac-state.json"
        self.mac_log = self.root / "mac-calls.jsonl"
        self.mac = {"types": ["image/png"], "image": base64.b64encode(PNG).decode()}
        self.env.update(UCP_MAC_STATE=str(self.mac_state), UCP_MAC_LOG=str(self.mac_log))
        for name in ("osascript", "pbpaste"):
            (self.bin / name).symlink_to(REPO / "tests" / "fixtures" / "osascript.py")
        self.configure()

    def configure(self, **updates):
        self.mac.update(updates)
        self.mac_state.write_text(json.dumps(self.mac))

    def calls(self, kind):
        return [entry for entry in map(json.loads, self.mac_log.read_text().splitlines()) if entry["kind"] == kind]

    def test_encoded_multi_file_urls_preserve_all_names(self):
        names = ["space name", "O'Brien", "Unicode-ü-文", "percent%25", "percent%2F", "CR\rLF\nname", "trailing space "]
        paths = [self.write(self.src / name, name.encode()) for name in names]
        before = tree(self.src)
        self.configure(types=["text/uri-list"], files=[file_uri(p, localhost=(i % 2 == 0)) for i, p in enumerate(paths)])
        self.start()
        self.begin()
        self.finish()
        self.assertEqual(tree(self.dst), before)
        self.assertEqual(tree(self.src), before)
        self.no_staging()

    def test_one_newline_path_does_not_select_unrelated_absolute_path(self):
        private = self.write(self.root / "private.txt", b"unselected private bytes")
        self.write(self.src / "visible", b"unselected prefix bytes")
        selected = self.write(self.src / ("visible\n" + str(private)), b"selected file bytes")
        self.configure(types=["text/uri-list"], files=[file_uri(selected)])
        self.start()
        self.begin()
        self.finish()
        self.assertEqual(tree(self.dst), {"private.txt": ("file", b"selected file bytes", 0o644)})
        self.assertEqual(private.read_bytes(), b"unselected private bytes")
        self.assertEqual(selected.read_bytes(), b"selected file bytes")
        self.no_staging()


    def _weird_tmpdir(self):
        # A truncated mktemp stdout path must not read/delete this independent file.
        prefix = self.write(self.root / "tmp O'Brien", b"prefix file must survive")
        actual = self.root / "tmp O'Brien\nCR\rand LF\n"
        actual.mkdir(mode=0o700)
        self.write(actual / "keep-sentinel", b"exact TMPDIR sentinel")
        self.env["TMPDIR"] = str(actual)
        return prefix, actual

    def _image_cleanup(self, failure=False, partial=False):
        prefix, actual = self._weird_tmpdir()
        self.configure(image_failure=failure, partial_write=partial)
        self.start()
        entries_before = set(actual.iterdir())
        self.begin()
        self.finish()
        calls = self.calls("image")
        self.assertEqual(len(calls), 1)
        call = calls[0]
        target = call["target"]
        self.assertEqual(Path(target).parent.parent, actual)
        self.assertEqual(call["parent_mode"], 0o700, "clipboard image staging directory was not private")
        self.assertEqual(call["unexpected_entries"], [])
        self.assertEqual(set(actual.iterdir()), entries_before)
        self.assertEqual((actual / "keep-sentinel").read_bytes(), b"exact TMPDIR sentinel")
        self.assertEqual(prefix.read_bytes(), b"prefix file must survive")
        images = list(self.dst.glob("pasted_*.png"))
        if failure:
            self.assertEqual(tree(self.dst), {}, "failed image writer bytes were consumed")
        else:
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].read_bytes(), PNG)
        self.no_staging()

    def test_png_exact_apostrophe_crlf_tmpdir_success_cleanup(self):
        self._image_cleanup()

    def test_png_exact_apostrophe_crlf_tmpdir_writer_failure_cleanup(self):
        self._image_cleanup(failure=True)

    def test_png_exact_apostrophe_crlf_tmpdir_partial_writer_failure_cleanup(self):
        self._image_cleanup(failure=True, partial=True)

    def test_failed_file_extraction_continues_to_image(self):
        self.configure(types=["text/uri-list", "image/png"], files=[])
        self.start()
        entries_before = set(self.tmpdir.iterdir())
        self.begin()
        self.finish()
        images = list(self.dst.glob("pasted_*.png"))
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].read_bytes(), PNG)
        self.assertEqual(set(self.tmpdir.iterdir()), entries_before)
        self.no_staging()

    def test_failed_file_extraction_continues_to_text(self):
        self.configure(types=["text/uri-list", "text/plain"], files=[], text="fallback text\nwith final LF\n")
        self.start()
        self.begin()
        self.yazi.wait(b"Enter file name", self.offset)
        self.yazi.send(b"fallback.txt\r")
        self.finish()
        self.assertEqual((self.dst / "fallback.txt").read_bytes(), b"fallback text\nwith final LF\n")
        self.no_staging()

