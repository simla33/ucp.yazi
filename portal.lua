-- Optional Linux/X11 clipboard owner. Xwayland can bridge these MIME offers
-- to Wayland clients; pure Wayland keeps the existing wl-copy backend.
local M = {}

local OWNER = [=[
import os
import select
import signal
import stat
import sys
from urllib.parse import unquote_to_bytes

if not os.environ.get('DISPLAY') or not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
    sys.exit(3)
data = sys.stdin.buffer.read()
read_fd, ready_fd = os.pipe()
pid = os.fork()
if pid:
    os.close(ready_fd)
    ready = select.select([read_fd], [], [], 10)[0]
    success = bool(ready) and os.read(read_fd, 1) == b'1'
    os.close(read_fd)
    if not success:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
    sys.exit(0 if success else 3)

os.close(read_fd)
os.setsid()
os.chdir('/')
with open(os.devnull, 'r+b', buffering=0) as null:
    for descriptor in (0, 1, 2):
        os.dup2(null.fileno(), descriptor)
os.environ['GDK_BACKEND'] = 'x11'
connection = None
key = None
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gtk, Gdk, Gio, GLib

    if not Gtk.init_check():
        raise RuntimeError('No X11 clipboard')
    display = Gdk.Display.get_default()
    if display is None:
        raise RuntimeError('No X11 display')
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    destination = 'org.freedesktop.portal.Documents'
    object_path = '/org/freedesktop/portal/documents'
    interface = 'org.freedesktop.portal.FileTransfer'

    def call(method, arguments):
        return connection.call_sync(destination, object_path, interface, method,
                                    arguments, None, Gio.DBusCallFlags.NONE, 3000, None)

    key = call('StartTransfer', GLib.Variant('(a{sv})', ({
        'writable': GLib.Variant('b', False),
        'autostop': GLib.Variant('b', False),
    },))).unpack()[0]
    uris = data.split(b'\r\n')
    if not uris[-1]:
        uris.pop()
    if not uris:
        raise RuntimeError('No files')
    for start in range(0, len(uris), 16):
        descriptors = Gio.UnixFDList.new()
        handles = []
        for uri in uris[start:start + 16]:
            if not uri.startswith(b'file:///'):
                raise RuntimeError('Not a local file')
            path = unquote_to_bytes(uri[7:])
            before = os.stat(path, follow_symlinks=False)
            if not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)):
                raise RuntimeError('Portal requires a regular file or directory')
            # The portal rejects O_PATH|O_NOFOLLOW; compare non-link identity.
            descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                after = os.fstat(descriptor)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise RuntimeError('Source changed during clipboard export')
                handles.append(descriptors.append(descriptor))
            finally:
                os.close(descriptor)
        connection.call_with_unix_fd_list_sync(
            destination, object_path, interface, 'AddFiles',
            GLib.Variant('(saha{sv})', (key, handles, {})), None,
            Gio.DBusCallFlags.NONE, 3000, descriptors, None)
        del descriptors

    clipboard = display.get_clipboard()
    provider = Gdk.ContentProvider.new_union([
        Gdk.ContentProvider.new_for_bytes('text/uri-list', GLib.Bytes.new(data)),
        Gdk.ContentProvider.new_for_bytes('application/vnd.portal.filetransfer',
                                         GLib.Bytes.new(key.encode('utf-8'))),
    ])
    loop = GLib.MainLoop()

    def changed(clipboard):
        if not clipboard.is_local():
            loop.quit()

    if not clipboard.set_content(provider):
        raise RuntimeError('Clipboard ownership refused')
    clipboard.connect('changed', changed)
    display.connect('closed', lambda *_: loop.quit())
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, loop.quit)
    display.flush()
    os.write(ready_fd, b'1')
    os.close(ready_fd)
    ready_fd = None
    loop.run()
except Exception:
    pass
finally:
    if ready_fd is not None:
        os.close(ready_fd)
    if key is not None and connection is not None:
        try:
            call('StopTransfer', GLib.Variant('(s)', (key,)))
        except Exception:
            pass
os._exit(0)
]=]

function M.copy(content)
	local child, err = Command("python3"):arg({ "-c", OWNER }):stdin(Command.PIPED):spawn()
	if not child then
		return nil, err
	end
	local ok
	ok, err = child:write_all(content)
	if ok then
		ok, err = child:flush()
	end
	if not ok then
		child:start_kill()
		child:wait()
		return nil, err
	end
	return child:wait()
end

return M
