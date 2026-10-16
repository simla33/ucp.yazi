"""Consumer-visible safety, transactional copying and collision-menu regressions."""
import os
import shutil
import subprocess

from support.integration import IntegrationTest, REPO, tree


class FilesystemSafety(IntegrationTest):
    def test_links_empty_directories_and_encoded_names(self):
        directory = self.src / "tree"
        (directory / "empty" / "nested").mkdir(parents=True)
        names = ["sp ace", "日本語-ü", "O'Brien", 'double"quote', "literal%25", "literal%2F", "CR\rname", "LF\nname", "trailing space "]
        for i, name in enumerate(names):
            self.write(directory / name, ("payload-%d" % i).encode())
        os.symlink("sp ace", directory / "relative")
        os.symlink(str(directory / "sp ace"), directory / "absolute")
        os.symlink("does-not-exist", directory / "dangling")
        os.symlink("empty", directory / "directory-link")
        before = tree(directory)
        self.paste([directory])
        self.assertEqual(tree(self.dst / "tree"), before)
        self.assertEqual(tree(directory), before, "copy changed source data or links")
        self.no_staging()

    def test_newline_names_are_not_line_based_traversal(self):
        outside = self.write(self.root / "escape-sentinel", b"outside must survive")
        directory = self.src / "newlines"
        for name in ["first\nsecond", "entry\n..", "entry\n.", "carriage\rreturn"]:
            self.write(directory / name, name.encode())
        source = tree(directory)
        self.paste([directory])
        self.assertEqual(tree(self.dst / directory.name), source)
        self.assertEqual(set(p.name for p in self.dst.iterdir()), {directory.name})
        self.assertEqual(outside.read_bytes(), b"outside must survive")
        self.assertEqual(tree(directory), source)
        self.no_staging()

    def test_newline_directory_cannot_read_private_file_or_overwrite_sibling(self):
        source = self.src / "bundle"
        self.write(source / "part", b"selected prefix file")
        self.write(source / "part\n.." / "victim", b"selected nested bytes")
        private = self.write(self.root / "victim", b"unselected private bytes")
        sibling = self.write(self.dst / "victim", b"destination sibling sentinel")
        selected = tree(source)
        self.paste([source])
        self.assertEqual(tree(self.dst / "bundle"), selected)
        self.assertEqual(sibling.read_bytes(), b"destination sibling sentinel")
        self.assertEqual(private.read_bytes(), b"unselected private bytes")
        self.assertEqual(tree(source), selected)
        self.no_staging()

    def test_directory_symlink_without_trailing_slash(self):
        self._directory_symlink(False)

    def test_directory_symlink_with_trailing_slash(self):
        self._directory_symlink(True)

    def _directory_symlink(self, slash):
        real = self.src / "real"
        self.write(real / "must-not-be-dereferenced", b"linked directory")
        link = self.src / "link"
        os.symlink("real", link)
        self.paste([str(link) + ("/" if slash else "")])
        self.assertTrue((self.dst / "link").is_symlink())
        self.assertEqual(os.readlink(self.dst / "link"), "real")
        self.assertEqual((real / "must-not-be-dereferenced").read_bytes(), b"linked directory")
        self.no_staging()

    def test_ordinary_directory_trailing_slash(self):
        directory = self.src / "ordinary"
        self.write(directory / "nested" / "file", b"ordinary directory bytes")
        self.paste([str(directory) + "/"])
        self.assertEqual(tree(directory), tree(self.dst / directory.name))
        self.assertFalse((self.dst / directory.name).is_symlink())
        self.no_staging()

    def test_unique_file_copy_does_not_overwrite_existing_suffix_or_dangling_link(self):
        source = self.write(self.src / "a.txt", b"new")
        self.write(self.dst / "a.txt", b"old")
        self.write(self.dst / "a_copy.txt", b"keep first copy")
        os.symlink("missing", self.dst / "a_copy_1.txt")
        self.paste([source], b"c")
        self.assertEqual((self.dst / "a.txt").read_bytes(), b"old")
        self.assertEqual((self.dst / "a_copy.txt").read_bytes(), b"keep first copy")
        self.assertEqual(os.readlink(self.dst / "a_copy_1.txt"), "missing")
        self.assertEqual((self.dst / "a_copy_2.txt").read_bytes(), b"new")
        self.no_staging()

    def test_extensionless_name_already_containing_copy_gets_distinct_copy(self):
        source = self.write(self.src / "Makefile_copy", b"new")
        old = self.write(self.dst / source.name, b"must not replace")
        self.paste([source], b"c")
        self.assertEqual(old.read_bytes(), b"must not replace")
        names = set(p.name for p in self.dst.iterdir()) - {source.name}
        self.assertEqual(len(names), 1)
        self.assertEqual((self.dst / names.pop()).read_bytes(), b"new")
        self.no_staging()

    def test_unique_directory_copy_preserves_preexisting_copy(self):
        source = self.src / "d"
        self.write(source / "new", b"incoming")
        self.write(self.dst / "d" / "old", b"original")
        self.write(self.dst / "d_copy" / "keep", b"first copy")
        before = tree(self.dst)
        self.paste([source], b"c")
        self.assertEqual(tree(self.dst / "d"), before["d"][2])
        self.assertEqual(tree(self.dst / "d_copy"), before["d_copy"][2])
        self.assertEqual(tree(self.dst / "d_copy_1"), tree(source))
        self.no_staging()

    def test_merge_preserves_unrelated_entries_and_private_directory_modes(self):
        source = self.src / "d"
        self.write(source / "new", b"incoming")
        self.write(source / "shared", b"new shared")
        self.write(self.dst / "d" / "private" / "secret", b"confidential")
        self.write(self.dst / "d" / "shared", b"old shared")
        (self.dst / "d").chmod(0o700)
        (self.dst / "d" / "private").chmod(0o700)
        self.paste([source], b"m")
        self.assertEqual((self.dst / "d" / "private" / "secret").read_bytes(), b"confidential")
        self.assertEqual((self.dst / "d" / "shared").read_bytes(), b"new shared")
        self.assertEqual((self.dst / "d" / "new").read_bytes(), b"incoming")
        for p in [self.dst / "d", self.dst / "d" / "private"]:
            self.assertEqual(p.stat().st_mode & 0o777, 0o700, "merge widened private directory permissions")
        self.no_staging()

    def test_fresh_private_directory_keeps_mode(self):
        source = self.src / "private"
        self.write(source / "secret", b"private payload")
        source.chmod(0o700)
        self.paste([source])
        self.assertEqual((self.dst / "private").stat().st_mode & 0o777, 0o700)
        self.assertEqual(tree(source), tree(self.dst / "private"))
        self.no_staging()

    def test_overwrite_private_directory_keeps_mode(self):
        source = self.src / "private"
        self.write(source / "secret", b"private payload")
        source.chmod(0o700)
        self.write(self.dst / "private" / "old", b"old")
        self.paste([source], b"o")
        self.assertEqual((self.dst / "private").stat().st_mode & 0o777, 0o700)
        self.assertEqual(tree(source), tree(self.dst / "private"))
        self.no_staging()

    def test_file_onto_existing_directory_preserves_unrelated_entries(self):
        source = self.write(self.src / "a", b"incoming")
        self.write(self.dst / "a" / "keep", b"untouched")
        self.write(self.dst / "a" / "a", b"nested old")
        self.paste([source], b"o")
        self.assertTrue((self.dst / "a").is_dir())
        self.assertEqual((self.dst / "a" / "keep").read_bytes(), b"untouched")
        self.assertEqual((self.dst / "a" / "a").read_bytes(), b"incoming")
        self.no_staging()

    def test_shell_metacharacters_in_file_directory_collision_are_literal(self):
        name = 'literal;touch "${TMPDIR}UCP_INJECTED";#'
        source = self.write(self.src / name, b"literal filename bytes")
        keep = self.write(self.dst / name / "keep", b"unrelated destination bytes")
        nested_keep = self.write(self.dst / name / name / "keep", b"nested directory bytes")
        self.paste([source], b"o")
        self.assertEqual((self.dst / name / name / name).read_bytes(), b"literal filename bytes")
        self.assertEqual(keep.read_bytes(), b"unrelated destination bytes")
        self.assertEqual(nested_keep.read_bytes(), b"nested directory bytes")
        self.assertEqual(source.read_bytes(), b"literal filename bytes")
        self.assertFalse((self.root / "tmpUCP_INJECTED").exists())
        self.no_staging()

    def test_unreadable_source_does_not_delete_existing_directory(self):
        if os.geteuid() == 0:
            self.skipTest("chmod read-failure injection requires a non-root uid")
        source = self.src / "d"
        unreadable = self.write(source / "unreadable", b"cannot be copied", mode=0)
        self.write(source / "readable", b"can be copied")
        self.write(self.dst / "d" / "keep", b"irreplaceable")
        before = tree(self.dst)
        try:
            self.paste([source], b"o")
            
            self.assertEqual(tree(self.dst), before)
            self.no_staging()
        finally:
            unreadable.chmod(0o600)

    def test_staging_failure_does_not_change_existing_target(self):
        if os.geteuid() == 0:
            self.skipTest("chmod staging-failure injection requires a non-root uid")
        source = self.write(self.src / "a", b"new")
        self.write(self.dst / "a", b"irreplaceable")
        before = tree(self.dst)
        self.dst.chmod(0o500)
        try:
            self.paste([source], b"o")
            
            self.assertEqual(tree(self.dst), before)
            self.no_staging()
        finally:
            self.dst.chmod(0o700)

    def test_final_rename_failure_rolls_back_original_target(self):
        cc = shutil.which("cc")
        if not cc:
            self.skipTest("optional final-rename fault test requires a C compiler")
        library = self.root / "rename-fault.so"
        built = subprocess.run(self.sandbox([
            cc, "-shared", "-fPIC", "-O2", "-o", library,
            REPO / "tests" / "fixtures" / "rename_fail.c", "-ldl",
        ]), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(built.returncode, 0, built.stderr.decode(errors="replace"))
        source = self.src / "d"
        self.write(source / "incoming", b"new tree")
        self.write(self.dst / "d" / "keep", b"original tree must be restored")
        original = tree(self.dst)
        marker = self.root / "rename-fault-fired"
        # The preload injects exactly one EIO at libc's final staged->target
        # rename, not in Lua. Subsequent rollback renames execute normally.
        self.env.update(LD_PRELOAD=str(library), UCP_FAIL_RENAME_TO=str(self.dst / "d"), UCP_FAIL_RENAME_MARKER=str(marker))
        self.paste([source], b"o")
        self.assertTrue(marker.exists(), "fault boundary was not exercised")
        
        self.assertEqual(tree(self.dst), original)
        self.assertEqual((source / "incoming").read_bytes(), b"new tree")
        self.no_staging()

    def test_same_file_is_rejected_without_rewriting(self):
        source = self.write(self.dst / "same", b"same-file sentinel", mode=0o600)
        before = source.stat()
        self.paste([source], b"o")
        
        self.assertEqual(source.read_bytes(), b"same-file sentinel")
        self.assertEqual(source.stat().st_ino, before.st_ino)
        self.no_staging()

    def test_descendant_overlap_is_rejected(self):
        source = self.src / "tree"
        self.write(source / "inside" / "sentinel", b"must survive")
        original = tree(source)
        self.start(source / "inside")
        self.paste([source])
        
        self.assertEqual(tree(source), original)
        self.no_staging()

    def test_ancestor_overlap_is_rejected(self):
        target = self.dst / "d"
        source = target / "deep" / "d"
        self.write(source / "sentinel", b"source nested in target")
        self.write(target / "keep", b"must survive")
        original = tree(self.dst)
        self.paste([source], b"o")
        
        self.assertEqual(tree(self.dst), original)
        self.no_staging()

    def test_canonical_alias_same_source_is_rejected(self):
        source = self.write(self.dst / "a", b"alias sentinel")
        alias = self.root / "alias"
        os.symlink(self.dst, alias)
        before = tree(self.dst)
        self.paste([alias / "a"], b"o")
        
        self.assertEqual(tree(self.dst), before)
        self.assertEqual(source.read_bytes(), b"alias sentinel")
        self.no_staging()

    def test_destination_leaf_symlink_does_not_change_referent(self):
        referent = self.write(self.root / "referent", b"escape sentinel")
        os.symlink(referent, self.dst / "a")
        before = tree(self.dst)
        source = self.write(self.src / "a", b"malicious overwrite")
        self.paste([source], b"o")
        
        self.assertEqual(tree(self.dst), before)
        self.assertEqual(referent.read_bytes(), b"escape sentinel")
        self.no_staging()

    def test_merge_destination_intermediate_symlink_is_rejected(self):
        outside = self.root / "outside"
        sentinel = self.write(outside / "sentinel", b"escape sentinel")
        (self.dst / "d").mkdir()
        os.symlink(outside, self.dst / "d" / "nested")
        source = self.src / "d"
        self.write(source / "nested" / "sentinel", b"do not escape")
        before = tree(self.dst)
        self.paste([source], b"m")
        
        self.assertEqual(sentinel.read_bytes(), b"escape sentinel")
        self.assertEqual(tree(self.dst), before)
        self.no_staging()

    def test_destination_root_symlink_is_rejected(self):
        alias = self.root / "destination-alias"
        os.symlink(self.dst, alias)
        self.write(self.dst / "keep", b"root sentinel")
        source = self.write(self.src / "a", b"must not write through root")
        before = tree(self.dst)
        self.start(alias)
        self.paste([source])
        
        self.assertEqual(tree(self.dst), before)
        self.no_staging()

    def test_direct_file_links_including_dangling_are_preserved(self):
        target = self.write(self.src / "target", b"link referent must survive")
        links = [
            ("relative", "target"),
            ("absolute", str(target)),
            ("dangling", "missing-target"),
        ]
        for name, referent in links:
            os.symlink(referent, self.src / name)
        before = tree(self.src)
        self.paste([self.src / name for name, _ in links])
        for name, referent in links:
            self.assertTrue((self.dst / name).is_symlink())
            self.assertEqual(os.readlink(self.dst / name), referent)
        self.assertEqual(tree(self.src), before)
        self.assertEqual(target.read_bytes(), b"link referent must survive")
        self.no_staging()

    def test_same_directory_is_rejected_before_overwrite(self):
        source = self.dst / "d"
        self.write(source / "sentinel", b"same directory sentinel")
        before = tree(self.dst)
        inode = source.stat().st_ino
        self.paste([source], b"o")
        
        self.assertEqual(tree(self.dst), before)
        self.assertEqual(source.stat().st_ino, inode)
        self.no_staging()

    def test_alias_descendant_overlap_is_rejected(self):
        source = self.src / "d"
        self.write(source / "inside" / "sentinel", b"alias overlap sentinel")
        alias = self.root / "source-alias"
        os.symlink(self.src, alias)
        before = tree(source)
        self.start(source / "inside")
        self.paste([alias / "d"])
        
        self.assertEqual(tree(source), before)
        self.no_staging()

    def test_dangling_destination_symlink_is_not_treated_as_free_name(self):
        source = self.write(self.src / "a", b"must not replace dangling link")
        os.symlink("missing", self.dst / "a")
        before = tree(self.dst)
        self.paste([source], b"o")
        
        self.assertEqual(tree(self.dst), before)
        self.assertFalse((self.dst / "missing").exists())
        self.no_staging()

    def test_failed_merge_leaves_entire_existing_tree_unchanged(self):
        if os.geteuid() == 0:
            self.skipTest("chmod read-failure injection requires a non-root uid")
        source = self.src / "d"
        self.write(source / "00-new-file", b"must not be partially merged")
        unreadable = self.write(source / "99-unreadable", b"unreadable", mode=0)
        self.write(self.dst / "d" / "keep", b"existing private data")
        before = tree(self.dst)
        try:
            self.paste([source], b"m")
            
            self.assertEqual(tree(self.dst), before)
            self.no_staging()
        finally:
            unreadable.chmod(0o600)

    def test_unreadable_directory_traversal_is_not_reported_as_empty_success(self):
        if os.geteuid() == 0:
            self.skipTest("chmod traversal-failure injection requires a non-root uid")
        source = self.src / "d"
        self.write(source / "hidden-by-mode", b"not an empty directory")
        self.write(self.dst / "d" / "keep", b"original target")
        before = tree(self.dst)
        source.chmod(0)
        try:
            self.paste([source], b"o")
            
            self.assertEqual(tree(self.dst), before)
            self.no_staging()
        finally:
            source.chmod(0o700)

    def test_missing_realpath_fails_closed_without_target_mutation(self):
        source = self.src / "d"
        self.write(source / "new", b"source")
        self.write(self.dst / "d" / "keep", b"must survive canonicalization failure")
        before = tree(self.dst)
        for name in ("xclip", "chmod", "cp", "mktemp"):
            binary = shutil.which(name)
            if binary:
                (self.bin / name).symlink_to(binary)
        self.env["PATH"] = str(self.bin)
        self.paste([source], b"o")
        
        self.assertEqual(tree(self.dst), before)
        self.assertEqual((source / "new").read_bytes(), b"source")
        self.no_staging()

    def test_directory_name_retains_quotes_unicode_and_trailing_whitespace(self):
        source = self.src / "O'Brien-ü\nCR\rtrailing \n "
        self.write(source / "file", b"exact directory name bytes")
        (source / "empty").mkdir()
        self.paste([source])
        self.assertEqual(set(p.name for p in self.dst.iterdir()), {source.name})
        self.assertEqual(tree(self.dst / source.name), tree(source))
        self.no_staging()

    def test_chosen_cwd_with_symlinked_ancestor_uses_resolved_destination(self):
        source = self.write(self.src / "incoming", b"chosen destination bytes")
        self.write(self.dst / "nested" / "keep", b"destination sentinel")
        alias = self.root / "alias"
        os.symlink(self.dst, alias)
        self.start(alias / "nested")
        self.paste([source])
        self.assertEqual((self.dst / "nested" / "incoming").read_bytes(), b"chosen destination bytes")
        self.assertEqual((self.dst / "nested" / "keep").read_bytes(), b"destination sentinel")
        self.assertEqual(source.read_bytes(), b"chosen destination bytes")
        self.no_staging()

    def test_create_copy_skips_occupied_candidate_containing_source(self):
        source = self.dst / "d_copy" / "deep" / "d"
        self.write(source / "incoming", b"selected source bytes")
        self.write(self.dst / "d" / "keep", b"original destination")
        occupied = tree(self.dst / "d_copy")
        selected = tree(source)
        self.paste([source], b"c")
        self.assertEqual(tree(self.dst / "d_copy_1"), selected)
        self.assertEqual(tree(self.dst / "d_copy"), occupied)
        self.assertEqual((self.dst / "d" / "keep").read_bytes(), b"original destination")
        self.no_staging()


def collision_case(directory, key):
    def case(self):
        source = self.src / "a"
        if directory:
            self.write(source / "new", b"new")
            self.write(self.dst / "a" / "old", b"old")
        else:
            self.write(source, b"new")
            self.write(self.dst / "a", b"old")
        old = tree(self.dst)
        self.paste([source], key)
        if key in (b"q", b"\x1b"):
            self.assertEqual(tree(self.dst), old)
        elif key == b"c":
            self.assertEqual(tree(self.dst / "a") if directory else (self.dst / "a").read_bytes(),
                             old["a"][2] if directory else b"old")
            self.assertEqual(tree(self.dst / "a_copy") if directory else (self.dst / "a_copy").read_bytes(),
                             tree(source) if directory else b"new")
        else:
            self.assertEqual(tree(self.dst / "a") if directory else (self.dst / "a").read_bytes(),
                             tree(source) if directory else b"new")
        self.no_staging()
    return case


# Separate unittest IDs make a failed key unambiguous; both menu shapes run.
for _directory in (False, True):
    for _name, _key in [("overwrite", b"o"), ("copy", b"c"), ("skip", b"q"), ("escape", b"\x1b")]:
        setattr(FilesystemSafety, "test_%s_menu_%s" % ("directory" if _directory else "file", _name), collision_case(_directory, _key))


class BulkCollisionMenus(IntegrationTest):
    def _bulk(self, key, directories_first):
        f = self.write(self.src / "file.txt", b"new file")
        d = self.src / "dir"
        self.write(d / "new", b"new directory")
        self.write(self.dst / "file.txt", b"old file")
        self.write(self.dst / "dir" / "old", b"old directory")
        # The final non-colliding item proves Cancel All really stops the batch.
        later = self.write(self.src / "later", b"must not arrive after Q")
        before = tree(self.dst)
        paths = [d, f, later] if directories_first else [f, d, later]
        self.paste(paths, key)
        if key == b"Q":
            self.assertEqual(tree(self.dst), before)
        elif key == b"O":
            self.assertEqual((self.dst / "file.txt").read_bytes(), b"new file")
            self.assertEqual(tree(self.dst / "dir"), tree(d))
            self.assertEqual((self.dst / "later").read_bytes(), b"must not arrive after Q")
        else:
            self.assertEqual((self.dst / "file.txt").read_bytes(), b"old file")
            self.assertEqual((self.dst / "file_copy.txt").read_bytes(), b"new file")
            self.assertEqual(tree(self.dst / "dir"), before["dir"][2])
            self.assertEqual(tree(self.dst / "dir_copy"), tree(d))
        self.no_staging()


for _key in (b"O", b"C", b"Q"):
    for _dir_first in (False, True):
        def _case(self, key=_key, first=_dir_first):
            self._bulk(key, first)
        setattr(BulkCollisionMenus, "test_bulk_%s_%s_first" % (_key.decode(), "directory" if _dir_first else "file"), _case)
