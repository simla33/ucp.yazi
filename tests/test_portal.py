"""Opt-in real FileTransfer portal + Flatpak receiver on a private X11 display."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

from support.integration import IntegrationTest, REPO, eventually, uri_bytes

PORTAL = "application/vnd.portal.filetransfer"
RECEIVER = r'''
import json
import os
from pathlib import Path
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Gdk, Gio, GLib

expected = json.loads(sys.argv[1])
assert all(not os.path.exists(path) for path in expected['originals']), 'Host files already accessible'
assert Gtk.init_check(), 'Private X11 display unavailable'
clipboard = Gdk.Display.get_default().get_clipboard()
loop = GLib.MainLoop()
result = {}

chunks = []

def verified(key):
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        for _ in range(2):
            paths = connection.call_sync(
                'org.freedesktop.portal.Documents', '/org/freedesktop/portal/documents',
                'org.freedesktop.portal.FileTransfer', 'RetrieveFiles',
                GLib.Variant('(sa{sv})', (key, {})), None,
                Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            by_name = {Path(path).name: Path(path) for path in paths}
            assert set(by_name) == set(expected['files']) | {'bundle'}
            for name, content in expected['files'].items():
                path = by_name[name]
                assert path.read_bytes() == bytes.fromhex(content), name
                try:
                    handle = path.open('r+b')
                except PermissionError:
                    pass
                else:
                    handle.close()
                    raise AssertionError('Portal granted write access')
            bundle = by_name['bundle']
            assert (bundle / 'nested' / 'payload').read_bytes() == b'nested portal bytes\0\xff'
            assert (bundle / 'empty').is_dir()
            assert list((bundle / 'empty').iterdir()) == []
        result['ok'] = True
    except Exception as error:
        result['error'] = str(error)
    finally:
        loop.quit()

def read_chunk(stream, pending):
    try:
        data = stream.read_bytes_finish(pending).get_data()
        if data:
            chunks.append(data)
            stream.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, None, read_chunk)
        else:
            stream.close(None)
            verified(b''.join(chunks).decode())
    except Exception as error:
        result['error'] = str(error)
        loop.quit()

def received(clipboard, pending):
    try:
        stream, mime = clipboard.read_finish(pending)
        assert mime == 'application/vnd.portal.filetransfer'
        stream.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, None, read_chunk)
    except Exception as error:
        result['error'] = str(error)
        loop.quit()

clipboard.read_async(['application/vnd.portal.filetransfer'], GLib.PRIORITY_DEFAULT, None, received)
GLib.timeout_add_seconds(15, lambda: (loop.quit(), False)[1])
loop.run()
assert result.get('ok'), result.get('error', 'Clipboard receive timed out')
print('Repeated portal reads preserved all bytes and directories without write access')
'''


class FlatpakPortalTests(IntegrationTest):
    def setUp(self):
        if os.environ.get("UCP_FLATPAK_TESTS") != "1":
            self.skipTest("set UCP_FLATPAK_TESTS=1 for a real installed Flatpak receiver")
        if not shutil.which("flatpak") or not importlib.util.find_spec("gi"):
            self.skipTest("Flatpak and PyGObject are required")
        self.bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        if not self.bus:
            self.skipTest("a session bus with the FileTransfer portal is required")
        self.app = os.environ.get("UCP_FLATPAK_APP", "org.telegram.desktop")
        info = subprocess.run(["flatpak", "info", self.app], capture_output=True)
        if info.returncode:
            self.skipTest("configured Flatpak receiver is not installed")
        super().setUp()
        self.env["DBUS_SESSION_BUS_ADDRESS"] = self.bus

    def retrieve(self, key):
        from gi.repository import Gio, GLib
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return connection.call_sync(
            "org.freedesktop.portal.Documents", "/org/freedesktop/portal/documents",
            "org.freedesktop.portal.FileTransfer", "RetrieveFiles",
            GLib.Variant("(sa{sv})", (key, {})), None,
            Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]

    def assert_transfer_closed(self, key):
        from gi.repository import Gio, GLib

        def closed():
            try:
                self.retrieve(key)
                return False
            except GLib.Error as error:
                if Gio.DBusError.get_remote_error(error) != "org.freedesktop.DBus.Error.AccessDenied":
                    raise
                return True

        eventually(closed, "clipboard replacement did not revoke the transfer key")

    def test_readonly_multi_file_directory_transfer_repeats_and_releases_key(self):
        files = {}
        paths = []
        for index in range(17):
            name = "%02d '文%%.bin" % index
            content = bytes((index, 0, 255)) + b"portal file\r\n"
            paths.append(self.write(self.src / name, content))
            files[name] = content.hex()
        bundle = self.src / "bundle"
        self.write(bundle / "nested" / "payload", b"nested portal bytes\0\xff")
        (bundle / "empty").mkdir()
        paths.append(bundle)
        self.start(self.src)
        self.select_all(len(paths))
        self.begin("copy")
        self.finish("copy")
        self.assertEqual(set(self.clip_read().splitlines()), set(uri_bytes(paths).splitlines()))
        self.assertIn(PORTAL.encode(), self.clip_read("TARGETS").splitlines())
        key = self.clip_read(PORTAL).decode()
        self.assertEqual(set(self.retrieve(key)), set(map(str, paths)))
        doctor = subprocess.run([sys.executable, str(REPO / "ucp"), "doctor"],
                                env=self.env, capture_output=True, timeout=15)
        self.assertEqual(doctor.returncode, 0)
        report = doctor.stdout.decode()
        self.assertIn(PORTAL, report)
        self.assertNotIn(key, report)
        self.assertNotIn(str(self.src), report)
        expected = json.dumps({"originals": list(map(str, paths)), "files": files})
        receiver_env = dict(os.environ, DISPLAY=self.env["DISPLAY"], XAUTHORITY=self.env["XAUTHORITY"])
        receiver_env.pop("WAYLAND_DISPLAY", None)
        receiver = self.processes.spawn([
            "flatpak", "run", "--command=python3", "--unshare=network",
            "--nosocket=wayland", "--socket=x11", "--env=GDK_BACKEND=x11",
            "--env=XDG_CONFIG_HOME=/tmp/ucp-config", "--env=XDG_DATA_HOME=/tmp/ucp-data",
            "--env=XDG_CACHE_HOME=/tmp/ucp-cache", self.app, "-c", RECEIVER, expected,
        ], env=receiver_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, errors = receiver.communicate(timeout=30)
        self.assertEqual(receiver.returncode, 0, errors.decode(errors="replace"))
        self.clip(b"replacement", "text/plain")
        self.assert_transfer_closed(key)
        for path in paths[:-1]:
            self.assertEqual(path.read_bytes(), bytes.fromhex(files[path.name]))
        self.no_staging()

    def test_symlink_batch_preserves_uri_fallback_and_native_link_copy(self):
        source = self.write(self.src / "source", b"unchanged source")
        link = self.src / "link"
        link.symlink_to("source")
        self.start(self.src)
        self.select_all(2)
        self.begin("copy")
        self.finish("copy")
        self.assertEqual(set(self.clip_read().splitlines()), set(uri_bytes([source, link]).splitlines()))
        self.assertNotIn(PORTAL.encode(), self.clip_read("TARGETS").splitlines())
        self.cd(self.dst)
        self.begin()
        self.finish()
        self.assertEqual((self.dst / "source").read_bytes(), source.read_bytes())
        self.assertTrue((self.dst / "link").is_symlink())
        self.assertEqual(os.readlink(self.dst / "link"), "source")
        self.no_staging()
