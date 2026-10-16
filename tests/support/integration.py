"""Real Yazi/PTY + private X11 integration support (stdlib only).

The small generated driver calls the *unmodified* ucp entry point. Its only
additional behavior is writing probe/completion files: no fs/clipboard/UI API
is replaced. Negative assertions wait for that completion fence, not a sleep.
"""
import base64
import fcntl
import os
from pathlib import Path
import pty
import re
import signal
import socket
import struct
import subprocess
import tempfile
import termios
import threading
import time
import unittest
import uuid
from urllib.parse import quote

from . import envcheck

REPO = Path(__file__).resolve().parents[2]
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)
ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def eventually(check, message, timeout=12):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (OSError, ValueError, IndexError) as exc:
            last = exc
        time.sleep(0.025)
    raise AssertionError("%s%s" % (message, ": %s" % last if last else ""))


def file_uri(path, localhost=False):
    return "file://" + ("localhost" if localhost else "") + quote(str(path), safe="/-._~")


def uri_bytes(paths):
    return ("\r\n".join(file_uri(p) for p in paths) + "\r\n").encode()


def tree(root):
    """No symlink following; includes empty directories, link text and modes."""
    result = {}
    root = Path(root)
    if not root.exists():
        return result
    for p in sorted(root.iterdir()):
        mode = p.lstat().st_mode & 0o777
        if p.is_symlink():
            result[p.name] = ("link", os.readlink(p))
        elif p.is_dir():
            result[p.name] = ("dir", mode, tree(p))
        else:
            result[p.name] = ("file", p.read_bytes(), mode)
    return result


DRIVER = r'''
local ucp = require("ucp")
local M = {}
local probe = ya.sync(function()
    local tab = cx.active
    local selected, yanked = 0, 0
    for _ in pairs(tab.selected) do selected = selected + 1 end
    for _ in pairs(cx.yanked) do yanked = yanked + 1 end
    return table.concat({tostring(tab.current.cwd),
        tab.current.hovered and tostring(tab.current.hovered.url) or "",
        tostring(selected), tostring(yanked), tostring(cx.yanked.is_cut)}, "\0")
end)
function M:entry(job)
    local action = job.args[1]
    if action == "probe" then
        assert(fs.write(Url(os.getenv("UCP_PROBE")), probe()))
        return
    end
    ucp:entry({args = {action, "notify"}})
    assert(fs.write(Url(os.getenv("UCP_DONE")), action))
end
return M
'''


class Processes:
    def __init__(self):
        self.items = []

    def spawn(self, argv, **kw):
        kw.setdefault("start_new_session", True)
        p = subprocess.Popen(argv, **kw)
        self.items.append(p)
        return p

    def close(self):
        items, self.items = self.items, []
        for p in reversed(items):
            # Kill the group even if its leader already exited (xclip forks).
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait(timeout=2)


class Pty:
    def __init__(self, processes, argv, env):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 160, 0, 0))
        self.output = bytearray()
        self.lock = threading.Lock()

        def controlling_tty():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        try:
            self.process = processes.spawn(
                argv, stdin=slave, stdout=slave, stderr=slave, env=env,
                start_new_session=False, preexec_fn=controlling_tty,
            )
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self):
        while True:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                return
            if not chunk:
                return
            with self.lock:
                self.output.extend(chunk)

    def send(self, keys):
        os.write(self.master, keys.encode() if isinstance(keys, str) else keys)

    def mark(self):
        with self.lock:
            return len(self.output)

    def text(self, offset=0):
        with self.lock:
            return ANSI.sub(b"", bytes(self.output[offset:]))

    def wait(self, pattern, offset=0, timeout=12):
        def matches():
            text = self.text(offset)
            return pattern.search(text) if hasattr(pattern, "search") else pattern in text
        try:
            return eventually(matches, "PTY did not show %r" % pattern, timeout)
        except AssertionError as exc:
            raise AssertionError("%s\nPTY tail:\n%s" % (exc, self.text()[-6000:].decode(errors="replace"))) from exc

    def close(self):
        os.close(self.master)
        self.thread.join(timeout=2)


class IntegrationTest(unittest.TestCase):
    """Each test has its own X server, writable tree, HOME and Yazi client ID."""
    use_x11 = True

    def setUp(self):
        if not envcheck.linux_ok():
            self.skipTest(envcheck.skip_reason())
        self.temp = tempfile.TemporaryDirectory(prefix="ucp-regression-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.processes = Processes()
        self.addCleanup(self.processes.close)
        self.src = self.root / "src"
        self.dst = self.root / "dst"
        self.bin = self.root / "bin"
        self.config = self.root / "config"
        self.tmpdir = self.root / "tmp"
        for p in (self.src, self.dst, self.bin, self.config, self.tmpdir,
                  self.root / "home", self.root / "cache", self.root / "data", self.root / "state", self.root / "runtime"):
            p.mkdir(mode=0o700)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("YAZI_", "UCP_", "LD_"))}
        for k in ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "SSH_TTY", "TMUX", "ZELLIJ"):
            self.env.pop(k, None)
        self.env.update(
            HOME=str(self.root / "home"), YAZI_CONFIG_HOME=str(self.config),
            XDG_CONFIG_HOME=str(self.root / "xdg-config"), XDG_CACHE_HOME=str(self.root / "cache"),
            XDG_DATA_HOME=str(self.root / "data"), XDG_STATE_HOME=str(self.root / "state"),
            XDG_RUNTIME_DIR=str(self.root / "runtime"), TMPDIR=str(self.tmpdir),
            TERM="xterm-256color", COLORTERM="truecolor", SHELL="/bin/sh",
            PATH=str(self.bin) + os.pathsep + "/usr/bin:/bin",
            UCP_PROBE=str(self.root / "probe"), UCP_DONE=str(self.root / "done"),
        )
        self.done = self.root / "done"
        self.probe_file = self.root / "probe"
        self.client_id = str(uuid.uuid4().int % (10 ** 15) + 10 ** 15)
        self.owners = []
        self.yazi = None
        if self.use_x11:
            self._start_xvfb()
        else:
            # A failed simulated Cocoa command must never fall back to any
            # user's default X display. This is deliberately invalid syntax.
            self.env.update(DISPLAY="ucp-test-no-x11", XAUTHORITY=str(self.root / "no-xauthority"))
        plugins = self.config / "plugins"
        (plugins / "ucp.yazi").mkdir(parents=True)
        (plugins / "ucp.yazi" / "main.lua").symlink_to(REPO / "main.lua")
        (plugins / "ucp-test.yazi").mkdir()
        (plugins / "ucp-test.yazi" / "main.lua").write_text(DRIVER)
        (self.config / "yazi.toml").write_text('[mgr]\nshow_hidden = true\nsort_by = "alphabetical"\n')
        (self.config / "keymap.toml").write_text('''[mgr]
prepend_keymap = [
  { on = "p", run = "plugin ucp-test paste" },
  { on = "y", run = "plugin ucp-test copy" },
  { on = "r", run = "plugin ucp-test probe" },
]
''')
        # Make mode-failure fixtures removable even after failed assertions.
        self.addCleanup(self._restore_modes)

    def _restore_modes(self):
        for current, dirs, _ in os.walk(self.root, followlinks=False):
            os.chmod(current, 0o700)
            for name in dirs:
                p = Path(current) / name
                if not p.is_symlink():
                    os.chmod(p, 0o700)

    def sandbox(self, command, mounts=()):
        argv = [envcheck.bin_path("bwrap"), "--die-with-parent", "--unshare-pid", "--unshare-ipc",
                "--unshare-uts", "--unshare-net", "--ro-bind", "/", "/", "--bind", str(self.root), str(self.root),
                "--dev", "/dev", "--proc", "/proc"]
        for path in mounts:
            argv += ["--tmpfs", str(path)]
        return argv + ["--"] + list(map(str, command))

    def _start_xvfb(self):
        # Never let Xvfb choose :0: an Xwayland desktop may own that display
        # without the socket/lock arrangement Xvfb's auto allocator expects.
        for _ in range(12):
            number = 10000 + uuid.uuid4().int % 20000
            display = ":%d" % number
            socket_path = "/tmp/.X11-unix/X%d" % number
            lock_path = Path("/tmp/.X%d-lock" % number)
            if display == os.environ.get("DISPLAY") or os.path.lexists(socket_path) or os.path.lexists(lock_path):
                continue
            authority = self.root / "Xauthority"
            def field(data):
                return struct.pack("!H", len(data)) + data
            # Xauthority FamilyWild record, confined to this server's random
            # cookie. Xlib and Xvfb both consume the standard binary format.
            authority.write_bytes(
                struct.pack("!H", 65535) + field(b"") + field(str(number).encode())
                + field(b"MIT-MAGIC-COOKIE-1") + field(os.urandom(16))
            )
            authority.chmod(0o600)
            log_path = self.root / "xvfb.log"
            with log_path.open("ab") as log:
                server = self.processes.spawn(
                    [envcheck.bin_path("Xvfb"), display, "-screen", "0", "1280x1024x24",
                     "-nolisten", "tcp", "-auth", str(authority)],
                    stdout=log, stderr=log,
                )

            def owned_socket():
                if server.poll() is not None:
                    return "exited"
                if int(lock_path.read_text().strip()) != server.pid:
                    return False
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.connect(socket_path)
                return "owned" if server.poll() is None else "exited"

            status = eventually(owned_socket, "spawned private Xvfb did not own its lock/socket")
            if status == "owned":
                self.env.update(DISPLAY=display, XAUTHORITY=str(authority))
                self.display_server = server
                return
            # A competing server can win a high-display race. Never connect a
            # clipboard client to it; retry a fresh number instead.
            if b"already active" not in log_path.read_bytes().lower():
                self.fail("private Xvfb failed: " + log_path.read_text(errors="replace"))
        self.fail("could not reserve a unique high private X display")

    def write(self, path, data=b"incoming", mode=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if mode is not None:
            path.chmod(mode)
        return path

    def clip(self, data, target="text/uri-list"):
        p = self.processes.spawn(
            [envcheck.bin_path("xclip"), "-display", self.env["DISPLAY"], "-selection", "clipboard", "-quiet", "-t", target],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env,
        )
        p.stdin.write(data)
        p.stdin.close()
        self.owners.append(p)
        # Ownership is established only once the offered target is readable.
        eventually(lambda: self.clip_read(target) == data, "private clipboard did not acquire supplied bytes")

    def clip_read(self, target="text/uri-list"):
        try:
            p = subprocess.run(
                [envcheck.bin_path("xclip"), "-display", self.env["DISPLAY"], "-selection", "clipboard", "-o", "-t", target],
                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2,
            )
        except subprocess.TimeoutExpired:
            return None
        return p.stdout if p.returncode == 0 else None

    def files_clip(self, paths):
        self.clip(uri_bytes(paths))

    def start(self, cwd=None, mounts=()):
        cwd = Path(cwd or self.dst)
        # No writes to user DDS state or socket paths: HOME/XDG/TMPDIR are private.
        self.yazi = Pty(self.processes, self.sandbox([
            envcheck.bin_path("yazi"), "--client-id", self.client_id, str(cwd),
        ], mounts), self.env)
        self.addCleanup(self._close_yazi)
        nonempty = any(cwd.iterdir())
        try:
            self.state(lambda s: s[0] == str(cwd) and (not nonempty or bool(s[1])),
                       "Yazi readiness/current directory/initial hover")
        except AssertionError as exc:
            text = self.yazi.text().decode(errors="replace")
            if "Operation not permitted" in text or "No permissions to create" in text:
                self.skipTest("bubblewrap user namespaces unavailable: " + text[-500:])
            raise AssertionError("%s\n%s" % (exc, text[-6000:])) from exc

    def _close_yazi(self):
        # Killing children first releases blocked PTY reads and detached X11
        # selection owners before the workspace can be removed.
        self.processes.close()
        self.yazi.close()

    def state(self, predicate=lambda s: True, message="Yazi state"):
        def check():
            self.probe_file.unlink(missing_ok=True)
            self.yazi.send(b"r")
            try:
                s = eventually(lambda: self.probe_file.read_bytes().decode().split("\0"), "probe response", timeout=0.3)
            except AssertionError:
                return False
            return s if len(s) == 5 and predicate(s) else False
        return eventually(check, message)

    def emit(self, command, *args):
        p = subprocess.run(self.sandbox([
            envcheck.bin_path("ya"), "emit-to", self.client_id, command, *args,
        ]), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
        self.assertEqual(p.returncode, 0, p.stderr.decode(errors="replace"))

    def cd(self, path):
        self.emit("cd", str(path))
        self.state(lambda s: s[0] == str(path), "DDS cd was not applied")

    def select_all(self, count):
        self.emit("toggle_all", "--state=on")
        self.state(lambda s: int(s[2]) == count, "selection count")

    def yank(self, cut=False):
        self.emit("yank", *(["--cut"] if cut else []))
        self.state(lambda s: int(s[3]) > 0 and s[4] == str(cut).lower(), "yank state")

    def begin(self, action="paste"):
        self.done.unlink(missing_ok=True)
        self.offset = self.yazi.mark()
        self.yazi.send(b"p" if action == "paste" else b"y")

    def menu(self, key, label=b"Overwrite"):
        # Safety checks may correctly reject an unsafe target before a menu.
        # Do not send a menu key to the manager after that early return.
        eventually(lambda: label in self.yazi.text(self.offset) or self.done.exists(),
                   "collision menu or completed safety rejection")
        if self.done.exists():
            return
        self.yazi.send(key)

    def finish(self, action="paste"):
        try:
            eventually(lambda: self.done.read_text() == action, "ucp %s did not complete" % action)
        except AssertionError as exc:
            raise AssertionError("%s\n%s" % (exc, self.yazi.text(self.offset)[-6000:].decode(errors="replace"))) from exc

    def paste(self, paths, action=None):
        self.files_clip(paths)
        if self.yazi is None:
            self.start()
        self.begin()
        if action:
            self.menu(action)
        self.finish()

    def no_staging(self):
        leftovers = [str(p.relative_to(self.root)) for p in self.root.rglob(".ucp-*")]
        self.assertEqual(leftovers, [], "private staging/backup entries leaked")
