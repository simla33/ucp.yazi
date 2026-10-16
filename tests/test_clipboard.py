"""Private-X11 end-to-end copy/paste, native yank precedence and saves."""
import datetime
import os
import shutil
import time

from support.integration import IntegrationTest, PNG, eventually, file_uri, tree


class ClipboardTransfers(IntegrationTest):
    def test_x11_copy_publishes_encoded_multi_file_uris(self):
        names = ["space name", "O'Brien-ü-文", "literal%25", "CR\rLF\nname", "trailing space "]
        paths = [self.write(self.src / name, name.encode()) for name in names]
        before = tree(self.src)
        self.start(self.src)
        self.select_all(len(paths))
        self.begin("copy")
        self.finish("copy")
        data = eventually(lambda: self.clip_read(), "copy did not publish URI clipboard")
        self.assertEqual(set(data.decode().splitlines()), set(file_uri(p) for p in paths))
        self.assertTrue(data.endswith(b"\r\n"))
        self.assertNotIn(b"\nname", data, "native path newline leaked into URI framing")
        self.assertEqual(tree(self.src), before)

    def test_missing_wayland_helpers_fall_back_to_xclip(self):
        source = self.write(self.src / "fallback.txt", b"X11 clipboard bytes")
        (self.bin / "xclip").symlink_to(shutil.which("xclip"))
        self.env["PATH"] = str(self.bin)
        self.env["WAYLAND_DISPLAY"] = "ucp-missing-wayland"
        self.start(self.src)
        self.begin("copy")
        self.finish("copy")
        self.assertEqual(self.clip_read(), (file_uri(source) + "\r\n").encode())
        self.assertEqual(source.read_bytes(), b"X11 clipboard bytes")

    def test_external_multi_file_paste_handles_localhost_and_encoded_names(self):
        paths = [self.write(self.src / name, name.encode()) for name in ["sp ace", "ü日本語", "literal%25", "literal%2F", "quo'te", "CR\rLF\nname", "trailing "]]
        directory = self.src / "dir"
        self.write(directory / "file", b"nested")
        (directory / "empty").mkdir()
        paths.append(directory)
        payload = ("\r\n".join(file_uri(p, localhost=(i % 2 == 0)) for i, p in enumerate(paths)) + "\r\n").encode()
        before = tree(self.src)
        self.clip(payload)
        self.start()
        self.begin()
        self.finish()
        self.assertEqual(tree(self.dst), before)
        self.assertEqual(tree(self.src), before)
        self.no_staging()

    def test_external_replacement_wins_over_internal_copied_selection(self):
        internal = self.write(self.src / "internal", b"internal selection")
        external = self.write(self.root / "external-source" / "external", b"external clipboard")
        self.start(self.src)
        self.begin("copy")
        self.finish("copy")
        self.state(lambda s: int(s[3]) == 1, "plugin copy did not retain internal yank")
        self.files_clip([external])
        self.cd(self.dst)
        self.begin()
        self.finish()
        self.assertEqual((self.dst / "external").read_bytes(), b"external clipboard")
        self.assertFalse((self.dst / "internal").exists())
        self.assertEqual(internal.read_bytes(), b"internal selection")
        self.no_staging()

    def test_native_cut_wins_over_usable_external_clipboard(self):
        internal = self.write(self.src / "cut-me", b"move payload")
        external = self.write(self.root / "external-source" / "external", b"do not copy")
        self.files_clip([external])
        self.start(self.src)
        self.yank(cut=True)
        self.cd(self.dst)
        self.begin()
        self.finish()
        eventually(lambda: (self.dst / "cut-me").exists() and not internal.exists(), "native cut did not move the source")
        self.assertEqual((self.dst / "cut-me").read_bytes(), b"move payload")
        self.assertFalse((self.dst / "external").exists())
        self.assertEqual(external.read_bytes(), b"do not copy")

    def test_missing_clipboard_helpers_still_copy_via_internal_yank(self):
        source = self.write(self.src / "fallback", b"native fallback bytes")
        # A genuinely restricted PATH: no fake success commands and no xclip,
        # wl-copy, wl-paste, osascript or pbcopy executable can be found.
        for name in ("realpath", "chmod", "cp", "mktemp"):
            binary = shutil.which(name)
            if binary:
                (self.bin / name).symlink_to(binary)
        self.env["PATH"] = str(self.bin)
        self.start(self.src)
        self.begin("copy")
        self.finish("copy")
        self.state(lambda s: int(s[3]) == 1, "missing helper erased native yank")
        self.cd(self.dst)
        self.begin()
        self.finish()
        eventually(lambda: (self.dst / source.name).exists(), "native fallback did not create destination")
        self.assertEqual((self.dst / source.name).read_bytes(), b"native fallback bytes")
        self.assertEqual(source.read_bytes(), b"native fallback bytes")

    def test_raw_posix_newline_file_lists_are_rejected(self):
        one = self.write(self.src / "one", b"one")
        two = self.write(self.src / "two", b"two")
        self.clip((str(one) + "\n" + str(two)).encode(), "code/file-list")
        self.start()
        self.begin()
        self.finish()
        self.assertEqual(tree(self.dst), {})
        self.assertEqual(one.read_bytes(), b"one")
        self.assertEqual(two.read_bytes(), b"two")

    def test_unusable_external_file_representation_falls_back_to_internal_yank(self):
        source = self.write(self.src / "native", b"native copied bytes")
        self.clip(b"not an encoded file URL", "text/uri-list")
        self.start(self.src)
        self.yank()
        self.cd(self.dst)
        self.begin()
        self.finish()
        eventually(lambda: (self.dst / "native").exists(), "unusable clipboard blocked native fallback")
        self.assertEqual((self.dst / "native").read_bytes(), b"native copied bytes")
        self.assertEqual(source.read_bytes(), b"native copied bytes")

    def test_remote_file_url_authority_is_not_read_as_a_local_path(self):
        local = self.write(self.src / "local", b"local bytes")
        foreign = self.write(self.src / "foreign", b"must not read as local")
        remote = file_uri(foreign).replace("file:///", "file://untrusted.invalid/", 1)
        self.clip((remote + "\r\n" + file_uri(local, localhost=True) + "\r\n").encode())
        self.start()
        self.begin()
        self.finish()
        self.assertEqual(set(p.name for p in self.dst.iterdir()), {"local"})
        self.assertEqual((self.dst / "local").read_bytes(), b"local bytes")
        self.assertEqual(foreign.read_bytes(), b"must not read as local")
        self.no_staging()


class ClipboardSaves(IntegrationTest):
    def text_input(self, name, action=None):
        self.clip(b"new clipboard text\nUnicode: \xe6\x96\x87\n", "text/plain")
        self.start()
        self.begin()
        self.yazi.wait(b"Enter file name", self.offset)
        self.yazi.send(name)
        self.yazi.send(b"\r")
        if action:
            self.menu(action)
        self.finish()

    def test_text_new_file_bytes(self):
        self.text_input("note.txt")
        self.assertEqual((self.dst / "note.txt").read_bytes(), b"new clipboard text\nUnicode: \xe6\x96\x87\n")
        self.no_staging()

    def test_text_copy_conflict_reserves_unused_suffix(self):
        self.write(self.dst / "note.txt", b"original")
        self.write(self.dst / "note_copy.txt", b"earlier copy")
        self.text_input("note.txt", b"c")
        self.assertEqual((self.dst / "note.txt").read_bytes(), b"original")
        self.assertEqual((self.dst / "note_copy.txt").read_bytes(), b"earlier copy")
        self.assertEqual((self.dst / "note_copy_1.txt").read_bytes(), b"new clipboard text\nUnicode: \xe6\x96\x87\n")
        self.no_staging()

    def test_text_overwrite_conflict(self):
        self.write(self.dst / "note.txt", b"original")
        self.text_input("note.txt", b"o")
        self.assertEqual((self.dst / "note.txt").read_bytes(), b"new clipboard text\nUnicode: \xe6\x96\x87\n")
        self.no_staging()

    def test_text_cancel_at_filename_writes_nothing(self):
        self.clip(b"discard this text", "text/plain")
        self.start()
        self.begin()
        self.yazi.wait(b"Enter file name", self.offset)
        # Yazi's input is modal: Ctrl-C always closes, unlike a first Escape
        # which may only switch input from insert to normal mode.
        self.yazi.send(b"\x03")
        self.finish()
        self.assertEqual(tree(self.dst), {})
        self.no_staging()

    def test_text_escape_at_conflict_writes_nothing(self):
        self.write(self.dst / "note.txt", b"original")
        before = tree(self.dst)
        self.text_input("note.txt", b"\x1b")
        self.assertEqual(tree(self.dst), before)
        self.no_staging()

    def test_text_q_at_conflict_writes_nothing(self):
        self.write(self.dst / "note.txt", b"original")
        before = tree(self.dst)
        self.text_input("note.txt", b"q")
        self.assertEqual(tree(self.dst), before)
        self.no_staging()

    def test_text_intermediate_symlink_does_not_write_outside_destination(self):
        outside = self.root / "outside"
        sentinel = self.write(outside / "note.txt", b"outside sentinel")
        os.symlink(outside, self.dst / "link")
        before = tree(self.dst)
        self.text_input("link/note.txt", b"o")
        
        self.assertEqual(sentinel.read_bytes(), b"outside sentinel")
        self.assertEqual(tree(self.dst), before)
        self.no_staging()

    def test_text_dot_path_cannot_overwrite_parent_sentinel(self):
        sentinel = self.write(self.root / "escape", b"parent sentinel")
        self.text_input("../escape", b"o")
        
        self.assertEqual(sentinel.read_bytes(), b"parent sentinel")
        self.assertEqual(tree(self.dst), {})
        self.no_staging()

    def test_png_clipboard_bytes(self):
        self.clip(PNG, "image/png")
        self.start()
        self.begin()
        self.finish()
        images = list(self.dst.glob("pasted_*.png"))
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].read_bytes(), PNG)
        self.assertEqual(images[0].read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.no_staging()

    def _image_conflict(self, action):
        self.clip(PNG, "image/png")
        # Reserve every timestamp in the next two minutes *before* invoking
        # the real image path. No race against os.date's one-second boundary.
        now = int(time.time())
        for second in range(now - 2, now + 121):
            stamp = datetime.datetime.fromtimestamp(second).strftime("%Y-%m-%d_%H-%M-%S")
            self.write(self.dst / ("pasted_%s.png" % stamp), b"original image sentinel")
            self.write(self.dst / ("pasted_%s_copy.png" % stamp), b"earlier image copy sentinel")
        before = tree(self.dst)
        self.start()
        self.begin()
        self.menu(action)
        self.finish()
        for name, value in before.items():
            self.assertEqual((self.dst / name).read_bytes(), value[1], "image conflict overwrote an existing file")
        copies = [self.dst / name for name in set(tree(self.dst)) - set(before)]
        if action == b"c":
            self.assertEqual(len(copies), 1)
            self.assertTrue(copies[0].name.endswith("_copy_1.png"))
            self.assertEqual(copies[0].read_bytes(), PNG)
        else:
            self.assertEqual(tree(self.dst), before)
        self.no_staging()

    def test_png_unique_copy_conflict(self):
        self._image_conflict(b"c")

    def test_png_escape_at_conflict_writes_nothing(self):
        self._image_conflict(b"\x1b")
