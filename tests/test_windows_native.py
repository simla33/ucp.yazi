"""Native Windows proof; opt in only on a dedicated Windows desktop/runner.

Run: $env:UCP_WINDOWS_TESTS='1'; python -m unittest discover -s tests -p test_windows_native.py -v
Ordinary discovery never imports POSIX support or accesses the desktop clipboard.
"""
import datetime
import os
import stat
import subprocess
import sys
import time
import unittest

from support.windows_integration import (
    ENABLED, REPO, SKIP_REASON, WindowsIntegrationTest, eventually, same_path, tree,
)


@unittest.skipUnless(ENABLED, SKIP_REASON)
class WindowsClipboard(WindowsIntegrationTest):
    def test_copy_multiselection_persists_real_filedrop_and_pastes_directories(self):
        names = ["space name.txt", "O'Brien-ü-日本語.txt", "literal%25.txt", "literal%2F.txt",
                 "literal;&$()[]#.txt", ".hidden"]
        paths = [self.write(self.src / name, (name + "\r\n").encode()) for name in names]
        directory = self.src / "O'Brien-文 directory"
        self.write(directory / "nested" / "payload", b"nested\0bytes")
        (directory / "empty" / "nested").mkdir(parents=True)
        paths.append(directory)
        before = tree(self.src)
        self.start(self.src)
        self.select_all(len(paths))
        self.begin("copy")
        self.finish("copy")
        exported = self.fixture("get-files")
        self.assertEqual({os.path.normcase(p) for p in exported},
                         {os.path.normcase(str(p)) for p in paths})
        self.state(lambda s: int(s[3]) == len(paths), "copy retained native yank")
        self.cd(self.dst)
        self.begin()
        self.finish()
        self.counts(len(paths))
        self.assertEqual(tree(self.dst), before)
        self.assertEqual(tree(self.src), before)
        self.state(lambda s: int(s[3]) == 0, "successful paste cleared native yank")
        self.no_staging()

    def test_external_single_filedrop_replaces_stale_plugin_yank(self):
        native = self.write(self.src / "native", b"native source")
        external = self.write(self.root / "outside-source" / "external", b"external bytes")
        self.start(self.src)
        self.begin("copy")
        self.finish("copy")
        self.files_clip([external])
        self.cd(self.dst)
        self.begin()
        self.finish()
        self.counts(1)
        self.assertEqual(tree(self.dst), {"external": ("file", b"external bytes")})
        self.assertEqual(native.read_bytes(), b"native source")
        self.state(lambda s: int(s[3]) == 0, "external paste cleared stale yank")
        self.no_staging()

    def test_native_cut_precedes_external_clipboard(self):
        native = self.write(self.src / "native", b"move me")
        external = self.write(self.root / "external", b"do not paste")
        self.files_clip([external])
        self.start(self.src)
        self.yank(cut=True)
        self.cd(self.dst)
        self.begin()
        self.finish()
        eventually(lambda: (self.dst / native.name).exists() and not native.exists(),
                   "native cut did not move source")
        self.assertEqual(tree(self.dst), {"native": ("file", b"move me")})
        self.assertEqual(external.read_bytes(), b"do not paste")
        self.no_staging()

    def test_mixed_batch_counts_and_failed_paste_retain_usable_native_yank(self):
        native = self.write(self.src / "native", b"retained native yank")
        source = self.root / "external"
        self.write(source / "bundle" / "nested" / "one", b"nested")
        (source / "bundle" / "empty").mkdir()
        collision = self.write(source / "collision", b"new collision")
        last = self.write(source / "last", b"last")
        self.write(self.dst / "collision", b"old collision")
        before = tree(source)
        self.files_clip([source / "bundle", collision, source / "missing", last])
        self.start(self.src)
        self.yank()
        self.cd(self.dst)
        self.begin()
        self.menu("q")
        self.finish()
        self.counts(2, 1, 1)
        self.assertEqual(tree(self.dst / "bundle"), tree(source / "bundle"))
        self.assertEqual((self.dst / "collision").read_bytes(), b"old collision")
        self.assertEqual((self.dst / "last").read_bytes(), b"last")
        self.assertEqual(tree(source), before)
        self.state(lambda s: int(s[3]) == 1, "partial failure retained native yank")
        self.emit("paste")
        eventually(lambda: (self.dst / native.name).exists(), "retained native yank was not usable")
        self.assertEqual((self.dst / native.name).read_bytes(), native.read_bytes())
        self.no_staging()

    def test_file_collision_escape_retains_yank_and_originals(self):
        source = self.write(self.src / "native", b"incoming")
        self.write(self.dst / source.name, b"original")
        self.files_clip([source])
        self.start(self.src)
        self.yank()
        self.cd(self.dst)
        self.begin()
        self.menu("\x1b")
        self.finish()
        self.counts(0, 1)
        self.assertEqual((self.dst / source.name).read_bytes(), b"original")
        self.assertEqual(source.read_bytes(), b"incoming")
        self.state(lambda s: int(s[3]) == 1, "cancel retained native yank")
        self.no_staging()

    def test_text_utf8_crlf_trailing_whitespace_and_collision_choices(self):
        text = "Unicode 文 ü O'Brien %25\r\nsecond line\r\n \t"
        self.save_text(text, "O'Brien-文.txt")
        self.counts(1)
        self.assertEqual((self.dst / "O'Brien-文.txt").read_bytes(), text.encode())
        self.assertEqual(self.fixture("get-text"), text)
        self.write(self.dst / "note.txt", b"original")
        self.write(self.dst / "note_copy.txt", b"earlier")
        self.save_text(text, "note.txt", "c")
        self.counts(1)
        self.assertEqual((self.dst / "note.txt").read_bytes(), b"original")
        self.assertEqual((self.dst / "note_copy.txt").read_bytes(), b"earlier")
        self.assertEqual((self.dst / "note_copy_1.txt").read_bytes(), text.encode())
        self.save_text(text, "note.txt", "o")
        self.counts(1)
        self.assertEqual((self.dst / "note.txt").read_bytes(), text.encode())
        self.no_staging()

    def test_text_filename_cancel_keeps_native_yank_and_writes_nothing(self):
        native = self.write(self.src / "native", b"retained")
        self.fixture("set-text", text="discard this fixture")
        self.start(self.src)
        self.yank()
        self.cd(self.dst)
        self.begin()
        self.yazi.wait("Enter file name", self.offset)
        self.yazi.send("\x03")
        self.finish()
        self.counts(0, 1, 0, True)
        self.assertEqual(tree(self.dst), {})
        self.state(lambda s: int(s[3]) == 1, "filename cancellation retained yank")
        self.emit("paste")
        eventually(lambda: (self.dst / native.name).exists(), "cancelled text lost native paste")
        self.assertEqual((self.dst / native.name).read_bytes(), b"retained")
        self.no_staging()

    def test_text_rejects_windows_unsafe_names_without_escape(self):
        sentinel = self.write(self.root / "escape", b"outside sentinel")
        self.start()
        for name in ("../escape", "..\\escape", "C:relative", "NUL.txt", "CON", "COM1.log",
                     "stream:secret", "trailing.", "trailing ", "bad\"quote"):
            with self.subTest(name=name):
                self.save_text("must not escape", name)
                self.counts(0, 0, 1)
                self.assertEqual(tree(self.dst), {})
                self.assertEqual(sentinel.read_bytes(), b"outside sentinel")
                self.no_staging()

    def test_real_bitmap_converts_to_png_with_exact_pixels_and_private_acl(self):
        self.fixture("set-image")
        self.start()
        self.begin()
        self.finish()
        self.counts(1)
        images = list(self.dst.glob("pasted_*.png"))
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.fixture("image-info", path=str(images[0])),
                         {"width": 2, "height": 2,
                          "pixels": ["FFFF0000", "FF00FF00", "FF0000FF", "FFFFFFFF"]})
        self.assertEqual(self.acl(images[0])["rules"], self.acl(self.write(self.src / "reference"))["rules"])
        self.assertTrue(self.acl(images[0])["protected"])
        self.no_staging()

    def test_image_create_copy_avoids_reserved_timestamps_and_clears_yank(self):
        self.write(self.src / "native", b"native")
        now = int(time.time())
        for second in range(now - 2, now + 181):
            stamp = datetime.datetime.fromtimestamp(second).strftime("%Y-%m-%d_%H-%M-%S")
            self.write(self.dst / ("pasted_%s.png" % stamp), b"original")
            self.write(self.dst / ("pasted_%s_copy.png" % stamp), b"earlier")
        before = tree(self.dst)
        self.fixture("set-image")
        self.start(self.src)
        self.yank()
        self.cd(self.dst)
        self.begin()
        self.menu("c")
        self.finish()
        self.counts(1)
        after = tree(self.dst)
        for name, original in before.items():
            self.assertEqual(after[name], original)
        created = set(after) - set(before)
        self.assertEqual(len(created), 1)
        name = created.pop()
        self.assertTrue(name.endswith("_copy_1.png"))
        self.assertEqual(self.fixture("image-info", path=str(self.dst / name))["pixels"],
                         ["FFFF0000", "FF00FF00", "FF0000FF", "FFFFFFFF"])
        self.state(lambda s: int(s[3]) == 0, "image success cleared native yank")
        self.no_staging()


@unittest.skipUnless(ENABLED, SKIP_REASON)
class WindowsFilesystem(WindowsIntegrationTest):
    def test_directory_rename_overwrite_and_create_copy_preserve_empty_children(self):
        source = self.src / "bundle"
        self.write(source / "nested" / "file", b"directory payload")
        (source / "empty").mkdir()
        before = tree(source)
        self.paste([source])
        self.counts(1)
        self.assertEqual(tree(self.dst / "bundle"), before)
        self.write(self.dst / "bundle" / "old", b"must be replaced")
        self.paste([source], "o")
        self.counts(1)
        self.assertEqual(tree(self.dst / "bundle"), before)
        self.write(self.dst / "bundle_copy" / "keep", b"existing copy")
        self.paste([source], "c")
        self.counts(1)
        self.assertEqual(tree(self.dst / "bundle_copy_1"), before)
        self.assertEqual((self.dst / "bundle_copy" / "keep").read_bytes(), b"existing copy")
        self.assertEqual(tree(source), before)
        self.no_staging()

    def test_case_insensitive_file_collision_and_bulk_overwrite(self):
        sources = [self.write(self.src / name, b"incoming-" + name.encode()) for name in ("Alpha.txt", "Beta.txt")]
        self.write(self.dst / "ALPHA.TXT", b"old alpha")
        self.write(self.dst / "BETA.TXT", b"old beta")
        self.paste(sources, "O")
        self.counts(2)
        self.assertEqual(len(list(self.dst.iterdir())), 2)
        for source in sources:
            self.assertEqual((self.dst / source.name).read_bytes(), source.read_bytes())
        self.no_staging()

    def test_bulk_create_copy_applies_across_file_and_directory_collisions(self):
        source_file = self.write(self.src / "file.txt", b"incoming file")
        source_dir = self.src / "directory"
        self.write(source_dir / "nested", b"incoming directory")
        later = self.write(self.src / "later", b"non-colliding")
        self.write(self.dst / "file.txt", b"original file")
        self.write(self.dst / "directory" / "keep", b"original directory")
        self.paste([source_dir, source_file, later], "C")
        self.counts(3)
        self.assertEqual((self.dst / "file.txt").read_bytes(), b"original file")
        self.assertEqual((self.dst / "file_copy.txt").read_bytes(), source_file.read_bytes())
        self.assertEqual((self.dst / "directory" / "keep").read_bytes(), b"original directory")
        self.assertEqual(tree(self.dst / "directory_copy"), tree(source_dir))
        self.assertEqual((self.dst / "later").read_bytes(), later.read_bytes())
        self.no_staging()

    def test_cancel_all_after_success_counts_remaining_items_and_retains_native_yank(self):
        native = self.write(self.root / "native-selection" / "native", b"retained yank")
        first = self.write(self.src / "first", b"already copied")
        collision = self.write(self.src / "collision", b"must not overwrite")
        directory = self.src / "directory"
        self.write(directory / "new", b"must not merge")
        later = self.write(self.src / "later", b"must not arrive")
        self.write(self.dst / "collision", b"original")
        self.write(self.dst / "directory" / "keep", b"original directory")
        before = tree(self.dst)
        self.start(native.parent)
        self.yank()
        self.cd(self.dst)
        self.paste([first, collision, directory, later], "Q")
        self.counts(1, 3, 0, True)
        expected = dict(before, first=("file", b"already copied"))
        self.assertEqual(tree(self.dst), expected)
        self.state(lambda s: int(s[3]) == 1, "cancel-all retained native yank")
        self.emit("paste")
        eventually(lambda: (self.dst / native.name).exists(), "cancel-all lost usable native yank")
        self.assertEqual((self.dst / native.name).read_bytes(), native.read_bytes())
        self.no_staging()

    def test_merge_casefolds_children_preserves_destination_acl_and_unrelated_data(self):
        source = self.src / "bundle"
        self.write(source / "shared.txt", b"incoming shared")
        self.write(source / "new", b"new file")
        (source / "empty").mkdir()
        target = self.dst / "bundle"
        self.write(target / "SHARED.TXT", b"old shared")
        self.write(target / "private" / "keep", b"confidential existing")
        self.private(target, "ReadAndExecute")
        self.private(target / "private", "ReadAndExecute")
        target_acl, child_acl = self.acl(target), self.acl(target / "private")
        self.paste([source], "m")
        self.counts(1)
        self.assertEqual((target / "shared.txt").read_bytes(), b"incoming shared")
        self.assertEqual((target / "new").read_bytes(), b"new file")
        self.assertTrue((target / "empty").is_dir())
        self.assertEqual((target / "private" / "keep").read_bytes(), b"confidential existing")
        self.assertEqual(len([p for p in target.iterdir() if p.name.lower() == "shared.txt"]), 1)
        self.assertEqual(self.acl(target), target_acl)
        self.assertEqual(self.acl(target / "private"), child_acl)
        self.no_staging()

    def test_inherited_source_acl_is_preserved_not_replaced_by_destination_inheritance(self):
        self.private(self.src, "ReadAndExecute")
        source = self.write(self.src / "protected.txt", b"private source")
        source_acl = self.acl(source)
        self.assertFalse(source_acl["protected"])
        self.fixture("readonly", path=str(source))
        self.paste([source])
        self.counts(1)
        copied = self.dst / source.name
        copied_acl = self.acl(copied)
        self.assertEqual(copied_acl["rules"], source_acl["rules"])
        self.assertTrue(copied_acl["protected"])
        self.assertTrue(copied_acl["readonly"])
        self.assertEqual(copied.read_bytes(), b"private source")
        self.assertTrue(self.acl(source)["readonly"])
        self.no_staging()

    def test_readonly_existing_target_overwrite_cleans_backup_and_preserves_source_acl(self):
        source = self.write(self.src / "readonly.txt", b"incoming")
        self.private(source, "ReadAndExecute")
        target = self.write(self.dst / source.name, b"readonly original")
        self.fixture("readonly", path=str(target))
        self.paste([source], "o")
        self.counts(1)
        self.assertEqual(target.read_bytes(), b"incoming")
        self.assertEqual(self.acl(target), self.acl(source))
        self.assertEqual(source.read_bytes(), b"incoming")
        self.no_staging()

    def test_acl_read_failure_rolls_back_directory_overwrite_without_partial_entries(self):
        source = self.src / "bundle"
        self.write(source / "00-readable", b"must not install partially")
        denied = self.write(source / "99-denied", b"unreadable source")
        self.write(self.dst / "bundle" / "keep", b"original target")
        before = tree(self.dst)
        self.fixture("deny-read", path=str(denied))
        try:
            self.paste([source], "o")
            self.counts(0, 0, 1)
            self.assertEqual(tree(self.dst), before)
            self.no_staging()
        finally:
            self.private(denied)
        self.assertEqual(denied.read_bytes(), b"unreadable source")

    def test_relative_absolute_dangling_and_directory_symlinks_are_copied_as_links(self):
        target = self.write(self.src / "referent", b"do not dereference")
        directory = self.src / "directory"
        self.write(directory / "keep", b"directory referent")
        links = [("relative", "referent", False), ("absolute", str(target), False),
                 ("dangling", "does-not-exist", False), ("directory-link", "directory", True),
                 ("dangling-directory", "absent-directory", True)]
        for name, referent, is_directory in links:
            self.symlink(self.src / name, referent, is_directory)
        before = tree(self.src)
        self.paste([self.src / name for name, _, _ in links])
        self.counts(len(links))
        for name, _, is_directory in links:
            source, copied = self.src / name, self.dst / name
            self.assertTrue(copied.is_symlink())
            self.assertEqual(os.readlink(copied), os.readlink(source))
            self.assertEqual(bool(copied.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_DIRECTORY), is_directory)
        self.assertEqual(tree(self.src), before)
        self.no_staging()

    def test_source_junction_is_cloned_not_traversed(self):
        outside = self.root / "outside"
        sentinel = self.write(outside / "keep", b"junction referent")
        link = self.src / "junction"
        self.junction(link, outside)
        self.paste([link])
        self.counts(1)
        copied = self.dst / link.name
        self.assertTrue(copied.is_junction())
        self.assertEqual(os.readlink(copied), os.readlink(link))
        self.assertEqual(sentinel.read_bytes(), b"junction referent")
        self.no_staging()

    def test_destination_junction_refused_for_files_and_nested_text(self):
        outside = self.root / "outside"
        sentinel = self.write(outside / "keep", b"outside must survive")
        link = self.dst / "junction"
        self.junction(link, outside)
        source = self.src / link.name
        self.write(source / "new", b"must not follow destination")
        before = tree(self.dst), tree(outside)
        self.paste([source], "o")
        self.counts(0, 0, 1)
        self.assertEqual((tree(self.dst), tree(outside)), before)
        self.save_text("must not escape", "junction/keep", "o")
        self.counts(0, 0, 1)
        self.assertEqual((tree(self.dst), tree(outside)), before)
        self.assertEqual(sentinel.read_bytes(), b"outside must survive")
        self.no_staging()

    def test_destination_dangling_symlink_is_not_overwritten(self):
        source = self.write(self.src / "entry", b"new")
        self.symlink(self.dst / "entry", "missing")
        before = tree(self.dst)
        self.paste([source], "o")
        self.counts(0, 0, 1)
        self.assertEqual(tree(self.dst), before)
        self.no_staging()

    def test_case_alias_same_directory_and_junction_descendant_overlap_refused(self):
        source = self.src / "Bundle"
        self.write(source / "inside" / "keep", b"overlap sentinel")
        before = tree(source)
        alias = self.root / "alias"
        self.junction(alias, self.src)
        self.start(source / "inside")
        self.paste([str(alias / "BUNDLE")], "o")
        self.counts(0, 0, 1)
        self.assertEqual(tree(source), before)
        self.cd(self.src)
        self.paste([str(source).upper()], "o")
        self.counts(0, 0, 1)
        self.assertEqual(tree(source), before)
        self.no_staging()


@unittest.skipUnless(ENABLED, SKIP_REASON)
class WindowsDoctor(WindowsIntegrationTest):
    def doctor(self):
        completed = subprocess.run([sys.executable, str(REPO / "ucp"), "doctor"],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=40)
        self.assertEqual(completed.returncode, 0, "standalone doctor failed")
        self.assertEqual(completed.stderr, b"", "doctor emitted unexpected diagnostics")
        report = completed.stdout.decode("utf-8")
        fields = dict(line.split(": ", 1) for line in report.splitlines() if ": " in line)
        for key in ("Clipboard backend", "Formats", "Unrecognized types", "Clipboard metadata",
                    "PowerShell", "Windows desktop session", "ACL/native filesystem APIs"):
            self.assertIn(key, fields)
        self.assertEqual(fields["Clipboard metadata"], "available")
        self.assertIn("ucp doctor", report)
        self.assertNotRegex(report, r"[\x00-\x08\x0b-\x1f\x7f]")
        return report, fields

    def assert_private_report(self, report, *secrets):
        for secret in (str(self.root), str(self.root).replace("\\", "/"),
                       self.env["USERPROFILE"], *secrets):
            self.assertNotIn(secret, report)

    def test_doctor_text_metadata_is_accurate_private_and_nonmutating(self):
        secret = "ucp-private-clipboard-credential 文\r\ntrailing \t"
        self.fixture("set-text", text=secret)
        self.write(self.dst / "private-filename-secret", b"unchanged bytes")
        self.env["AWS_SECRET_ACCESS_KEY"] = "private-environment-value"
        before = tree(self.src), tree(self.dst), tree(self.tmpdir)
        formats = self.fixture("formats")
        report, fields = self.doctor()
        self.assertIn("windows", fields["Clipboard backend"].lower())
        self.assertIn("text/plain", fields["Formats"])
        self.assertRegex(report, r"(?i)powershell[^\n]*5\.1")
        self.assertRegex(report, r"(?i)(ACL|DACL)")
        self.assertEqual(self.fixture("get-text"), secret)
        self.assertEqual(self.fixture("formats"), formats)
        self.assertEqual((tree(self.src), tree(self.dst), tree(self.tmpdir)), before)
        self.assert_private_report(report, secret, "private-filename-secret", "private-environment-value")
        self.no_staging()

    def test_doctor_unknown_identifier_and_payload_are_never_exposed(self):
        private_type = "ucp-private-account-format-" + self.client_id
        secret = "private unknown clipboard content"
        self.fixture("set-unknown", format=private_type, text=secret)
        formats = self.fixture("formats")
        report, fields = self.doctor()
        self.assertGreaterEqual(int(fields["Unrecognized types"]), 1)
        self.assertNotIn("text/plain", fields["Formats"])
        self.assert_private_report(report, private_type, secret)
        self.assertEqual(self.fixture("formats"), formats)
        self.assertEqual(self.fixture("get-unknown", format=private_type), secret)
        self.assertEqual(tree(self.dst), {})
        self.no_staging()

    def test_doctor_filedrop_does_not_disclose_paths_or_change_clipboard(self):
        source = self.write(self.src / "private-selected-filename", b"private-file-content")
        self.files_clip([source])
        report, fields = self.doctor()
        self.assertIn("text/uri-list", fields["Formats"])
        self.assert_private_report(report, source.name, "private-file-content")
        exported = self.fixture("get-files")
        self.assertEqual(len(exported), 1)
        self.assertTrue(same_path(exported[0], source))
        self.assertEqual(source.read_bytes(), b"private-file-content")
        self.assertEqual(tree(self.dst), {})
        self.no_staging()


if __name__ == "__main__":
    unittest.main()
