"""Real Sway headless clipboard integration; never connects to the user's compositor."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

from support.integration import IntegrationTest, PNG, REPO, eventually, uri_bytes


class WaylandClipboardTests(IntegrationTest):
    use_x11 = False

    def setUp(self):
        missing = [name for name in ("sway", "wl-copy", "wl-paste") if not shutil.which(name)]
        if missing:
            self.skipTest("native Wayland tools missing: " + ", ".join(missing))
        super().setUp()
        config = self.root / "sway.conf"
        config.write_text("xwayland disable\noutput * mode 1280x720\nseat seat0 fallback true\n")
        compositor_env = dict(self.env)
        compositor_env.pop("WAYLAND_DISPLAY", None)
        compositor_env.pop("DISPLAY", None)
        compositor_env.pop("SWAYSOCK", None)
        compositor_env.update(
            WLR_BACKENDS="headless", WLR_RENDERER="pixman", WLR_HEADLESS_OUTPUTS="1",
            WLR_LIBINPUT_NO_DEVICES="1", XDG_SESSION_TYPE="wayland",
            DBUS_SESSION_BUS_ADDRESS="unix:path=" + str(self.root / "no-session-bus"),
        )
        log = self.root / "sway.log"
        with log.open("wb") as output:
            self.compositor = self.processes.spawn(
                [shutil.which("sway"), "--config", str(config)], env=compositor_env,
                stdout=output, stderr=output,
            )

        def socket_ready():
            if self.compositor.poll() is not None:
                raise RuntimeError("isolated Sway exited: " + log.read_text(errors="replace"))
            return next((p for p in (self.root / "runtime").glob("wayland-*")
                         if stat.S_ISSOCK(p.stat().st_mode)), None)

        socket = eventually(socket_ready, "private headless Wayland socket")
        self.env.update(WAYLAND_DISPLAY=socket.name, XDG_SESSION_TYPE="wayland")

    def clip(self, data, target="text/uri-list"):
        owner = self.processes.spawn(
            [shutil.which("wl-copy"), "--foreground", "--type", target],
            env=self.env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        owner.stdin.write(data)
        owner.stdin.close()
        self.owners.append(owner)
        eventually(lambda: self.clip_read(target) == data, "private Wayland clipboard ownership")

    def clip_read(self, target="text/uri-list"):
        try:
            output = subprocess.run(
                [shutil.which("wl-paste"), "--no-newline", "--type", target],
                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2,
            )
        except subprocess.TimeoutExpired:
            return None
        return output.stdout if output.returncode == 0 else None

    def test_copy_multiple_local_files_and_paste_exact_bytes(self):
        sources = [self.write(self.src / "a space 'é%.txt", b"one"),
                   self.write(self.src / "b.txt", b"two")]
        self.start(self.src)
        self.select_all(2)
        self.begin("copy")
        self.finish("copy")
        self.assertEqual(self.clip_read(), uri_bytes(sources))
        self.cd(self.dst)
        self.begin()
        self.finish()
        self.assertEqual([(self.dst / p.name).read_bytes() for p in sources], [b"one", b"two"])
        self.assertEqual((self.root / "result").read_bytes(), b"2\x000\x000\x00false")
        self.no_staging()

    def test_image_text_and_cancel_preserve_native_yank(self):
        saved = self.write(self.src / "native.txt", b"native")
        self.start(self.src)
        self.yank()
        self.cd(self.dst)
        self.clip(b"private text\r\ntrailing  ", "text/plain;charset=utf-8")
        self.begin()
        self.yazi.wait(b"Enter file name", self.offset)
        self.yazi.send(b"\x03")
        self.finish()
        self.assertEqual((self.root / "result").read_bytes(), b"0\x001\x000\x00true")
        self.state(lambda s: s[3] == "1", "cancelled Wayland paste retains yank")
        self.assertFalse(any(self.dst.iterdir()))
        self.begin()
        self.yazi.wait(b"Enter file name", self.offset)
        self.yazi.send(b"note.txt\r")
        self.finish()
        self.assertEqual((self.dst / "note.txt").read_bytes(), b"private text\r\ntrailing  ")
        self.clip(PNG, "image/png")
        self.begin()
        self.finish()
        image = next(self.dst.glob("pasted_*.png"))
        self.assertEqual(image.read_bytes(), PNG)
        self.assertEqual(saved.read_bytes(), b"native")
        self.no_staging()

    def test_cli_doctor_reads_wayland_metadata_without_changing_clipboard(self):
        payload = b"private synthetic Wayland text"
        self.clip(payload, "text/plain;charset=utf-8")
        result = subprocess.run(
            self.sandbox([sys.executable, str(REPO / "ucp"), "doctor"]),
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"wayland (readable metadata)", result.stdout)
        self.assertIn(b"text/plain", result.stdout)
        self.assertNotIn(payload, result.stdout)
        self.assertEqual(self.clip_read("text/plain;charset=utf-8"), payload)
