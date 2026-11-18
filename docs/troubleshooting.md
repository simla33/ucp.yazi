# Configuration and troubleshooting

[Back to the README](../README.md)

## Configuration

The usual config file is `~/.config/yazi/keymap.toml` on Linux/macOS, or `%APPDATA%\yazi\config\keymap.toml` on Windows. With `YAZI_CONFIG_HOME`, use that directory instead. Add entries to the existing `[mgr].prepend_keymap`; do not duplicate the table or list.

ucp has two plugin actions: `copy` and `paste`. `notify` enables copy feedback and paste outcome summaries. Use `--` before multiple arguments, for example `plugin ucp -- paste notify`. Add `--no-hover` to paste without revealing the newly created item.

## Native Yazi keys

`x`, `Y`, and `X` are Yazi defaults. Ctrl-P is an optional native-paste bypass. Merge any of these entries into your existing `[mgr].prepend_keymap`:

```toml
[mgr]
prepend_keymap = [
    { on = "x", run = "yank --cut", desc = "Cut inside Yazi" },
    { on = "<C-p>", run = "paste", desc = "Paste native yank, ignore clipboard" },
    { on = "Y", run = "unyank", desc = "Clear native yank" },
    { on = "X", run = "unyank", desc = "Clear native yank" },
]
```

Existing uppercase `P` still force-overwrites. Native `x` is an internal cut, not an external clipboard cut. Other Yazi bindings are unchanged.

## Diagnostics

Doctor is a **standalone Python 3 CLI**. It does not need a running Yazi instance and has no plugin action or keybinding. Remove a previously configured doctor binding, if present.

Linux/macOS:

```sh
~/.config/yazi/plugins/ucp.yazi/ucp doctor
```

Optional short command, if `~/.local/bin` is on your `PATH`:

```sh
mkdir -p ~/.local/bin
ln -s ~/.config/yazi/plugins/ucp.yazi/ucp ~/.local/bin/ucp
ucp doctor
```

The symlink command deliberately does not overwrite an existing executable. Adjust the source for a custom config location.

Windows PowerShell:

```powershell
python "$env:APPDATA\yazi\config\plugins\ucp.yazi\ucp" doctor
```

Run from the same shell environment as Yazi so helper availability and display access are comparable. Doctor prints versions where recognized, helper availability, session/backend metadata, known formats, and an unknown-format count. It never reads clipboard contents, writes clipboard data, or stores a report. Paths, raw environment values, unknown identifiers, and raw helper errors are excluded. Review output before sharing.

## Common problems

| Symptom | Check |
| --- | --- |
| Copy/paste does nothing | Run doctor; ensure the platform's helpers are on Yazi's `PATH`. |
| Backend unavailable | Check display/session access. Probe failure is not proof of an empty clipboard. |
| Missing notifications | Keep the `--` separator before `paste notify` or `copy notify`. |
| External data pastes instead of the retained yank | Add the optional [native Ctrl-P binding](#native-yazi-keys). |
| Windows link paste fails | Symlink creation needs Developer Mode or the corresponding privilege. No dereferencing fallback is used. |
| Recovery warning names a backup | Keep the staging directory; see [recovery](safety.md#staging-and-recovery). |

## Remote files

External copy refuses SFTP/VFS selections without changing the clipboard. Real SFTP downloads through Yazi are verified:

1. Copy the remote selection with `y`; the native yank is retained.
2. Open a local directory and use Yazi's native `paste`, for example the optional [Ctrl-P binding](#native-yazi-keys).
3. Select the downloaded files and press `y` to copy them to desktop apps.

There is no hidden download cache or automatic remote export. The local download remains yours to keep or remove.

**Transport warning:** Yazi 26.9.1's [SFTP handler accepts the server key unconditionally](https://github.com/sxyazi/yazi/blob/v26.9.1/yazi-vfs/src/engine/sftp/conn.rs#L19-L28). ucp does not add host authentication. Do not treat a successful download as proof of the server's identity; use a transport with host-key verification, such as a properly configured SSHFS mount, for security-sensitive transfers.

## Flatpak file copying

When **Python 3, PyGObject, GTK 4, X11/Xwayland, and the FileTransfer portal** are available, ucp automatically offers both ordinary file URIs and a portal transfer key. No extra keybinding or filesystem override is needed. `ucp doctor` lists `application/vnd.portal.filetransfer` when that offer is active.

The helper registers regular files/directories for **read-only** portal access, stays alive while it owns the clipboard, allows repeated paste, and closes the transfer when ownership changes. Symlinks, special files, or unavailable dependencies use the existing URI backend instead. Pure Wayland without Xwayland retains `wl-copy`; this optional portal backend requires X11 access.

A receiver inside Telegram Flatpak 7.0.9 successfully read otherwise inaccessible synthetic files through the portal. [Issue #8](https://github.com/simla33/ucp.yazi/issues/8) remains open because Telegram's actual attachment UI was not tested. No account data or messages were used; Xwayland bridging also needs compositor-specific confirmation.

If a particular app still fails, compare the same file from a desktop file manager into that app and report its version and permissions. Do not grant broad filesystem access as a supposed fix. [FileTransfer protocol](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.FileTransfer.html) · [Verification details](development.md#sftp-and-flatpak)

For other problems, [open an issue](https://github.com/simla33/ucp.yazi/issues/new) with OS, plugin revision, doctor findings, and minimal reproduction steps. Exclude private clipboard contents, paths, and credentials.
