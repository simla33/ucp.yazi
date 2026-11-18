"""Opt-in native Windows consumers: actual Yazi/ConPTY and independent WinForms.

Only UCP_WINDOWS_TESTS=1 on Windows enables this harness. Use a dedicated
runner/desktop: Windows clipboard ownership is session-wide, not sandboxable.
No existing clipboard payload is read or saved; only test-owned data is read.
"""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import uuid

from .driver import DRIVER

REPO = Path(__file__).resolve().parents[2]
ENABLED = os.name == "nt" and os.environ.get("UCP_WINDOWS_TESTS") == "1"
SKIP_REASON = "requires native Windows and UCP_WINDOWS_TESTS=1 on a dedicated desktop"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")

# This fixture deliberately uses WinForms directly, never the plugin backend.
# Paths and clipboard data travel only through JSON stdin, not command text.
FIXTURE = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$r = [Console]::In.ReadToEnd() | ConvertFrom-Json
function Json($value) { [Console]::Write((ConvertTo-Json -InputObject $value -Depth 12 -Compress)) }
try {
    switch ($r.operation) {
        'clear' { [System.Windows.Forms.Clipboard]::Clear() }
        'set-files' {
            $files = New-Object System.Collections.Specialized.StringCollection
            foreach ($path in $r.paths) { [void]$files.Add([string]$path) }
            $data = New-Object System.Windows.Forms.DataObject
            $data.SetFileDropList($files)
            [System.Windows.Forms.Clipboard]::SetDataObject($data, $true, 10, 100)
        }
        'get-files' { Json @([System.Windows.Forms.Clipboard]::GetFileDropList()) }
        'set-text' {
            $data = New-Object System.Windows.Forms.DataObject
            $data.SetText([string]$r.text, [System.Windows.Forms.TextDataFormat]::UnicodeText)
            [System.Windows.Forms.Clipboard]::SetDataObject($data, $true, 10, 100)
        }
        'get-text' { Json ([System.Windows.Forms.Clipboard]::GetText([System.Windows.Forms.TextDataFormat]::UnicodeText)) }
        'set-image' {
            $bitmap = New-Object System.Drawing.Bitmap(2, 2)
            try {
                $bitmap.SetPixel(0, 0, [System.Drawing.Color]::Red)
                $bitmap.SetPixel(1, 0, [System.Drawing.Color]::Lime)
                $bitmap.SetPixel(0, 1, [System.Drawing.Color]::Blue)
                $bitmap.SetPixel(1, 1, [System.Drawing.Color]::White)
                $data = New-Object System.Windows.Forms.DataObject
                $data.SetImage($bitmap)
                [System.Windows.Forms.Clipboard]::SetDataObject($data, $true, 10, 100)
            } finally { $bitmap.Dispose() }
        }
        'image-info' {
            $bitmap = New-Object System.Drawing.Bitmap([string]$r.path)
            try {
                $pixels = @()
                for ($y = 0; $y -lt $bitmap.Height; $y++) {
                    for ($x = 0; $x -lt $bitmap.Width; $x++) {
                        $pixels += $bitmap.GetPixel($x, $y).ToArgb().ToString('X8')
                    }
                }
                Json @{width=$bitmap.Width; height=$bitmap.Height; pixels=$pixels}
            } finally { $bitmap.Dispose() }
        }
        'set-unknown' {
            $data = New-Object System.Windows.Forms.DataObject
            $data.SetData([string]$r.format, $false, [string]$r.text)
            [System.Windows.Forms.Clipboard]::SetDataObject($data, $true, 10, 100)
        }
        'get-unknown' {
            $data = [System.Windows.Forms.Clipboard]::GetDataObject()
            Json ($data.GetData([string]$r.format, $false))
        }
        'formats' { Json @([System.Windows.Forms.Clipboard]::GetDataObject().GetFormats($false)) }
        'private' {
            $item = Get-Item -LiteralPath $r.path -Force
            $acl = if ($item.PSIsContainer) {
                New-Object System.Security.AccessControl.DirectorySecurity
            } else { New-Object System.Security.AccessControl.FileSecurity }
            $acl.SetAccessRuleProtection($true, $false)
            $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
            $inherit = if ($item.PSIsContainer) {
                [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
            } else { [System.Security.AccessControl.InheritanceFlags]::None }
            foreach ($entry in @(@($sid, 'FullControl'), @('S-1-5-18', [string]$r.systemRights))) {
                $identity = New-Object System.Security.Principal.SecurityIdentifier([string]$entry[0])
                $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                    $identity, [System.Security.AccessControl.FileSystemRights]$entry[1],
                    $inherit, [System.Security.AccessControl.PropagationFlags]::None,
                    [System.Security.AccessControl.AccessControlType]::Allow)
                [void]$acl.AddAccessRule($rule)
            }
            Set-Acl -LiteralPath $r.path -AclObject $acl
        }
        'acl' {
            $acl = Get-Acl -LiteralPath $r.path
            $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | ForEach-Object {
                @{sid=$_.IdentityReference.Value; rights=[int]$_.FileSystemRights;
                  inheritance=[int]$_.InheritanceFlags; propagation=[int]$_.PropagationFlags;
                  kind=[int]$_.AccessControlType}
            } | Sort-Object { $_.sid }, { $_.rights }, { $_.inheritance }, { $_.propagation }, { $_.kind })
            Json @{rules=$rules; protected=$acl.AreAccessRulesProtected;
                readonly=(([System.IO.File]::GetAttributes($r.path) -band [System.IO.FileAttributes]::ReadOnly) -ne 0)}
        }
        'deny-read' {
            $acl = Get-Acl -LiteralPath $r.path
            $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                $sid, [System.Security.AccessControl.FileSystemRights]::ReadData,
                [System.Security.AccessControl.AccessControlType]::Deny)
            [void]$acl.AddAccessRule($rule)
            Set-Acl -LiteralPath $r.path -AclObject $acl
        }
        'readonly' {
            $attributes = [System.IO.File]::GetAttributes($r.path)
            [System.IO.File]::SetAttributes($r.path, ($attributes -bor [System.IO.FileAttributes]::ReadOnly))
        }
        'junction' { [void](New-Item -ItemType Junction -Path $r.path -Target $r.target) }
        default { throw 'Unknown fixture operation' }
    }
} catch { [Console]::Error.Write('Native fixture operation failed'); exit 1 }
'''


def eventually(check, message, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (OSError, ValueError, IndexError):
            pass
        time.sleep(0.05)
    # Neither terminal output nor clipboard/exception contents belong in logs.
    raise AssertionError(message)


def same_path(left, right):
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(str(right)))


def tree(root):
    """Snapshot bytes and reparse links without following directory aliases."""
    result = {}
    for path in sorted(Path(root).iterdir()):
        info = path.lstat()
        if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            result[path.name] = ("link", os.readlink(path))
        elif path.is_dir():
            result[path.name] = ("dir", tree(path))
        else:
            result[path.name] = ("file", path.read_bytes())
    return result


class ConPty:
    def __init__(self, argv, env, cwd):
        # Import only after the explicit opt-in gate. No POSIX support imports.
        from winpty import PtyProcess
        from winpty.enums import Backend

        self.process = PtyProcess.spawn(argv, env=env, cwd=str(cwd),
                                        dimensions=(45, 160), backend=Backend.ConPTY)
        self.output = ""
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self):
        pending = ""
        queries = re.compile(r"\x1b\[(0?c|6n)")
        while True:
            try:
                chunk = self.process.read(65536)
            except (EOFError, OSError):
                return
            with self.lock:
                self.output += chunk
            # ConPTY forwards terminal queries to its host. Answer the startup
            # handshake rather than racing Yazi's terminal-detection timeout.
            pending += chunk
            consumed = 0
            for query in queries.finditer(pending):
                reply = "\x1b[1;1R" if query[1] == "6n" else "\x1b[?1;2c"
                try:
                    self.process.write(reply)
                except (EOFError, OSError):
                    return
                consumed = query.end()
            pending = pending[max(consumed, len(pending) - 3):]

    def send(self, keys):
        self.process.write(keys.decode("utf-8") if isinstance(keys, bytes) else keys)

    def mark(self):
        with self.lock:
            return len(self.output)

    def text(self, offset=0):
        with self.lock:
            return ANSI.sub("", self.output[offset:])

    def wait(self, text, offset=0):
        return eventually(lambda: text in self.text(offset), "Yazi did not display the expected prompt")

    def close(self):
        # /T owns descendants, including a PowerShell blocked on clipboard IO.
        subprocess.run(["taskkill.exe", "/PID", str(self.process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        self.process.close(force=True)
        self.thread.join(timeout=3)
        with self.lock:
            self.output = ""


class WindowsIntegrationTest(unittest.TestCase):
    def setUp(self):
        if not ENABLED:
            self.skipTest(SKIP_REASON)
        self.yazi_bin = shutil.which("yazi")
        self.ya_bin = shutil.which("ya")
        self.powershell = shutil.which("powershell.exe")
        if not all((self.yazi_bin, self.ya_bin, self.powershell)):
            self.fail("native opt-in requires yazi, ya and Windows PowerShell 5.1 on PATH")
        # Windows which() may return a cwd-relative match; plugin cwd changes.
        self.yazi_bin = str(Path(self.yazi_bin).resolve())
        self.ya_bin = str(Path(self.ya_bin).resolve())
        self.powershell = str(Path(self.powershell).resolve())
        # Missing pinned dependency is a CI failure, not coverage silently skipped.
        import winpty  # noqa: F401

        self.root = Path(tempfile.mkdtemp(prefix="ucp-native-"))
        self.addCleanup(self._remove_workspace)
        self.fixture("private", path=str(self.root), systemRights="FullControl")
        self.src, self.dst = self.root / "src", self.root / "dst"
        self.config, self.tmpdir = self.root / "config", self.root / "tmp"
        home = self.root / "home"
        cache, data = home / "AppData" / "Local", home / "AppData" / "Roaming"
        # Windows known-folder APIs expand USERPROFILE and verify these paths;
        # APPDATA/LOCALAPPDATA alone do not redirect Yazi's cache/state lookup.
        for path in (self.src, self.dst, self.config, self.tmpdir, cache, data, self.root / "state"):
            path.mkdir(parents=True, exist_ok=True)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.upper().startswith(("YAZI_", "UCP_", "XDG_"))}
        self.env.update(HOME=str(home), USERPROFILE=str(home),
                        APPDATA=str(data), LOCALAPPDATA=str(cache),
                        YAZI_CONFIG_HOME=str(self.config), XDG_CACHE_HOME=str(cache),
                        XDG_STATE_HOME=str(self.root / "state"), XDG_DATA_HOME=str(data),
                        TEMP=str(self.tmpdir), TMP=str(self.tmpdir), TMPDIR=str(self.tmpdir),
                        TERM="xterm-256color", COLORTERM="truecolor", PYWINPTY_BACKEND="0",
                        UCP_PROBE=str(self.root / "probe"), UCP_DONE=str(self.root / "done"),
                        UCP_RESULT=str(self.root / "result"))
        # Exclude Git/MSYS/WSL tools: successful transfers must use Windows APIs,
        # not accidentally pass because a hosted image happens to provide cp.
        self.env["PATH"] = os.pathsep.join(dict.fromkeys([
            str(Path(self.yazi_bin).parent), str(Path(self.ya_bin).parent),
            str(Path(self.powershell).parent), str(Path(os.environ["SystemRoot"]) / "System32"),
            os.environ["SystemRoot"],
        ]))
        plugins = self.config / "plugins"
        plugin = plugins / "ucp.yazi"
        plugin.mkdir(parents=True)
        # Real plugin files, not copied fake fs/clipboard implementations.
        for source in REPO.glob("*.lua"):
            shutil.copyfile(source, plugin / source.name)
        driver = plugins / "ucp-test.yazi"
        driver.mkdir()
        (driver / "main.lua").write_text(DRIVER, encoding="utf-8")
        (self.config / "yazi.toml").write_text(
            '[mgr]\nshow_hidden = true\nsort_by = "alphabetical"\n', encoding="utf-8")
        (self.config / "keymap.toml").write_text('''[mgr]
prepend_keymap = [
  { on = "p", run = "plugin ucp-test -- paste notify" },
  { on = "y", run = "plugin ucp-test -- copy notify" },
  { on = "r", run = "plugin ucp-test probe" },
]
''', encoding="utf-8")
        self.done, self.probe_file = self.root / "done", self.root / "probe"
        self.client_id = str(uuid.uuid4().int % (10 ** 15) + 10 ** 15)
        self.yazi = None
        # Never save/read the developer's previous clipboard. Opt-in acknowledges
        # dedicated desktop ownership; clear test-owned clipboard before cleanup.
        self.fixture("clear")
        self.addCleanup(lambda: self.fixture("clear"))

    def _remove_workspace(self):
        def writable(path):
            info = path.lstat()
            if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                return
            path.chmod(stat.S_IREAD | stat.S_IWRITE)
            if path.is_dir():
                for child in path.iterdir():
                    writable(child)
        writable(self.root)
        shutil.rmtree(self.root)

    def fixture(self, operation, **request):
        payload = json.dumps(dict(operation=operation, **request), ensure_ascii=False).encode("utf-8")
        try:
            result = subprocess.run(
                [self.powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-Command", FIXTURE],
                input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=25)
        except subprocess.TimeoutExpired:
            raise AssertionError("native fixture timed out: " + operation) from None
        if result.returncode:
            raise AssertionError("native fixture failed: " + operation)
        return json.loads(result.stdout.decode("utf-8-sig")) if result.stdout else None

    def write(self, path, data=b"incoming"):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def files_clip(self, paths):
        self.fixture("set-files", paths=list(map(str, paths)))

    def start(self, cwd=None):
        cwd = Path(cwd or self.dst)
        self.yazi = ConPty([self.yazi_bin, "--client-id", self.client_id, str(cwd)], self.env, cwd)
        self.addCleanup(self.yazi.close)
        nonempty = any(cwd.iterdir())
        self.state(lambda s: same_path(s[0], cwd) and (not nonempty or bool(s[1])), "Yazi readiness")

    def state(self, predicate=lambda s: True, message="Yazi state"):
        def check():
            self.probe_file.unlink(missing_ok=True)
            self.yazi.send("r")
            try:
                state = eventually(lambda: self.probe_file.read_text(encoding="utf-8").split("\0"),
                                   "probe response", timeout=0.5)
            except AssertionError:
                return False
            return state if len(state) == 5 and predicate(state) else False
        return eventually(check, message)

    def emit(self, command, *args):
        result = subprocess.run([self.ya_bin, "emit-to", self.client_id, command, *map(str, args)],
                                env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        self.assertEqual(result.returncode, 0, "Yazi DDS command failed")

    def cd(self, path):
        self.emit("cd", path)
        self.state(lambda s: same_path(s[0], path), "Yazi directory change")

    def select_all(self, count):
        self.emit("toggle_all", "--state=on")
        self.state(lambda s: int(s[2]) == count, "Yazi selection count")

    def yank(self, cut=False):
        self.emit("yank", *(["--cut"] if cut else []))
        self.state(lambda s: int(s[3]) > 0 and s[4] == str(cut).lower(), "native yank state")

    def begin(self, action="paste"):
        self.done.unlink(missing_ok=True)
        (self.root / "result").unlink(missing_ok=True)
        self.offset = self.yazi.mark()
        self.yazi.send("p" if action == "paste" else "y")

    def menu(self, key, label="Overwrite"):
        eventually(lambda: label in self.yazi.text(self.offset) or self.done.exists(),
                   "collision menu or safety rejection did not complete")
        if not self.done.exists():
            self.yazi.send(key)

    def finish(self, action="paste"):
        eventually(lambda: self.done.read_text(encoding="utf-8") == action,
                   "ucp operation did not complete: " + action)

    def counts(self, pasted, skipped=0, failed=0, cancelled=False):
        expected = [str(pasted), str(skipped), str(failed), str(cancelled).lower()]
        self.assertEqual((self.root / "result").read_text(encoding="utf-8").split("\0"), expected)

    def paste(self, paths, action=None):
        self.files_clip(paths)
        if self.yazi is None:
            self.start()
        self.begin()
        if action is not None:
            self.menu(action)
        self.finish()

    def save_text(self, text, name, action=None):
        self.fixture("set-text", text=text)
        if self.yazi is None:
            self.start()
        self.begin()
        self.yazi.wait("Enter file name", self.offset)
        self.yazi.send(name + "\r")
        if action is not None:
            self.menu(action)
        self.finish()

    def no_staging(self):
        def leftovers(root):
            for path in root.iterdir():
                if path.name.startswith(".ucp-"):
                    return True
                info = path.lstat()
                if not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT and path.is_dir():
                    if leftovers(path):
                        return True
            return False
        self.assertFalse(leftovers(self.root), "private stage or backup leaked")

    def junction(self, path, target):
        self.fixture("junction", path=str(path), target=str(target))

    def symlink(self, path, target, directory=False):
        try:
            os.symlink(target, path, target_is_directory=directory)
        except OSError as exc:
            if exc.winerror == 1314:
                self.skipTest("symbolic links require Developer Mode or SeCreateSymbolicLinkPrivilege")
            raise

    def acl(self, path):
        return self.fixture("acl", path=str(path))

    def private(self, path, system_rights="FullControl"):
        self.fixture("private", path=str(path), systemRights=system_rights)
