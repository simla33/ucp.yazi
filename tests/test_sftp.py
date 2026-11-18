"""Real localhost SFTP selections, refusal safety, and native downloads."""
import re
import shutil
import subprocess
import unittest

from support.integration import IntegrationTest, eventually, tree
from support.sftp import LocalSftp


class SftpTransfers(IntegrationTest):
    def setUp(self):
        super().setUp()
        version = subprocess.run([shutil.which("yazi"), "--version"], env=self.env,
                                 capture_output=True, text=True, timeout=5)
        match = re.search(r"Version:\s+(\d+)\.(\d+)\.(\d+)", version.stdout)
        if not match or tuple(map(int, match.groups())) < (26, 9, 1):
            self.skipTest("real SFTP coverage requires Yazi 26.9.1+ with vfs.toml")
        self.sftp = LocalSftp(self)

    def sandbox(self, command, mounts=()):
        # Keep filesystem/PID isolation. Only this test needs host loopback to
        # reach the private, key-only sshd; the base harness unshares networking.
        argv = super().sandbox(command, mounts)
        argv.remove("--unshare-net")
        return argv

    def remote(self):
        self.start(self.dst)
        try:
            self.cd(self.sftp.url)
            self.state(lambda s: s[0] == self.sftp.url and s[1].startswith(self.sftp.url + "/"),
                       "real remote directory did not acquire a hover")
        except AssertionError as exc:
            raise AssertionError("%s\nsshd:\n%s\nYazi:\n%s" % (
                exc, self.sftp.diagnostics(), self.yazi.text()[-6000:].decode(errors="replace"))) from exc
        self.sftp.assert_authenticated()

    def refuse_and_native_paste(self, count, expected):
        previous = self.write(self.root / "previous-clipboard-file", b"keep the old clipboard")
        self.files_clip([previous])
        original = self.clip_read()
        before = tree(self.sftp.files)
        self.begin("copy")
        self.finish("copy")
        self.assertEqual(self.clip_read(), original, "remote refusal replaced the external clipboard")
        self.state(lambda s: int(s[3]) == count and s[4] == "false", "real remote native yank retained")
        # Verify the native state, not notification text: Yazi may truncate or
        # repaint the toast before its full contents reach the PTY transcript.
        self.cd(self.dst)
        # Deliberately use Yazi's native paste, not ucp's external-clipboard
        # precedence: the preserved clipboard still contains a different file.
        self.emit("paste")
        try:
            eventually(lambda: all((self.dst / name).read_bytes() == data for name, data in expected.items()),
                       "native SFTP download did not reproduce source bytes", timeout=20)
        except AssertionError as exc:
            raise AssertionError("%s\nsshd:\n%s\nYazi:\n%s" % (
                exc, self.sftp.diagnostics(), self.yazi.text()[-6000:].decode(errors="replace"))) from exc
        self.assertFalse((self.dst / previous.name).exists())
        self.assertEqual(self.clip_read(), original)
        self.assertEqual(tree(self.sftp.files), before)
        self.no_staging()

    def test_remote_hover_refuses_external_export_and_native_paste_downloads(self):
        name = "O'Brien-ü-文-%25.txt"
        data = b"real SFTP bytes\0\xff\n"
        self.write(self.sftp.files / name, data)
        self.remote()
        self.refuse_and_native_paste(1, {name: data})

    def test_remote_selection_refuses_external_export_and_native_paste_downloads_tree(self):
        files = {"space name": b"first remote payload", "literal%25": b"second\0payload",
                 "directory/nested-ü": bytes(range(256))}
        for name, data in files.items():
            self.write(self.sftp.files / name, data)
        (self.sftp.files / "directory" / "empty").mkdir()
        self.remote()
        self.select_all(3)
        self.refuse_and_native_paste(3, files)
        self.assertTrue((self.dst / "directory" / "empty").is_dir())

    def test_lua_fs_copy_materializes_actual_remote_hover_without_clipboard_export(self):
        name = "api-download-%25-ü"
        data = bytes(range(256)) * 4096
        self.write(self.sftp.files / name, data)
        plugin = self.config / "plugins" / "sftp-proof.yazi"
        plugin.mkdir()
        (plugin / "main.lua").write_text(r'''
local hovered = ya.sync(function() return cx.active.current.hovered.url end)
return { entry = function()
    local source = hovered()
    local size, err = fs.copy(source, Url(os.getenv("UCP_SFTP_DOWNLOAD")))
    assert(fs.write(Url(os.getenv("UCP_SFTP_RESULT")), tostring(size) .. "\0" .. tostring(err)))
end }
''')
        destination = self.dst / name
        result = self.root / "sftp-copy-result"
        self.env.update(UCP_SFTP_DOWNLOAD=str(destination), UCP_SFTP_RESULT=str(result))
        self.remote()
        self.emit("plugin", "sftp-proof")
        observed = eventually(lambda: result.read_bytes(), "real remote fs.copy API did not return", timeout=20)
        self.assertEqual(observed, (str(len(data)) + "\0nil").encode(), self.sftp.diagnostics())
        self.assertEqual(destination.read_bytes(), data)
        self.assertEqual((self.sftp.files / name).read_bytes(), data)
        self.no_staging()
