# Universal Copy/Paste for Yazi

Clipboard backends for **Wayland**, **Xorg**, **macOS**, and **native Windows**. Linux/Xorg and Wayland use real-Yazi integration tests; platform-specific verification is described below.

Integrates yazi copy/paste with system clipboard similar to GUI file managers.

## Features

- **Copy files to system clipboard** - Works with other file managers, browsers, and applications
- **Paste files from external sources** - Supports pasting files from other file managers and code editors (including VS Code)
- **Text paste to new file** - Automatically suggests creating new files when clipboard contains text
- **Smart collision handling** - Manages file conflicts during paste operations

- Native Yazi cuts retain priority over external clipboard contents.
- When no supported clipboard data is readable, copied files fall back to native Yazi paste.
- Cancelled, skipped, failed, or partially completed external pastes retain the native yank.
- Paste notifications report pasted, skipped, and failed top-level items, including unprocessed items after cancelling a batch.
- **Terminal diagnostics** - `ucp doctor` checks the environment without reading clipboard contents. No Yazi binding or running Yazi instance is needed.


## Previews

### Copy
![Copy preview](https://github.com/user-attachments/assets/8894ca14-402b-4885-b2b6-d9666a4661b4)

---

### Paste
![Paste preview](https://github.com/user-attachments/assets/ae29d283-fec1-4e56-8857-c57d17c0d4f8)

---

### File collision handling & paste into new file
![Collision preview](https://github.com/user-attachments/assets/92746b8d-e93e-4c0c-8bbb-7845be5f57e1)


## Installation

**Requires:**

- **Wayland**: `wl-clipboard`
- **Xorg**: `xclip`
- **macOS**: `osascript` with AppKit, plus `pbpaste` for text
- **Linux/macOS filesystem helpers on `PATH`**: `realpath`, `cp`, `chmod`, and `mktemp`.
- **Windows**: built-in Windows PowerShell 5.1 (`powershell.exe` on `PATH`), an interactive desktop, and a filesystem supporting Windows ACLs. Unix utilities are not required. Symlink creation needs Developer Mode or the corresponding Windows privilege; failure never substitutes a regular copy.
- **Diagnostics only**: Python 3. Ordinary plugin copy/paste does not require Python.

Transfers fail without changing an existing target if required preparation or canonicalization fails.

```bash
ya pkg add simla33/ucp
```

## Configuration

> [!NOTE]
> Requires Yazi 26.8.15 or newer. Selected and yanked entries are `File` objects;
> the plugin checks each entry's URL kind before exporting its local `url.path`.

Add these entries to the existing `[mgr].prepend_keymap` in `keymap.toml` (normally `~/.config/yazi/keymap.toml`, or `%APPDATA%\yazi\config\keymap.toml` on Windows). Do not duplicate an existing table/list.

```toml
[mgr]
prepend_keymap = [
    { on = "y", run = "plugin ucp -- copy notify", desc = "Copy to system clipboard" },
    { on = "p", run = "plugin ucp -- paste notify", desc = "Paste files, images or text" },
    { on = "x", run = "yank --cut", desc = "Cut inside Yazi" },
    { on = "<C-p>", run = "paste", desc = "Paste native yank, ignoring clipboard" },
    { on = "Y", run = "unyank", desc = "Clear native yank" },
    { on = "X", run = "unyank", desc = "Clear native yank" },
]
```

Only `y` and `p` call ucp. The native bindings are included for completeness; `<C-p>` is a convenient clipboard bypass. Yazi's existing `P` still means force-overwrite paste. Other Yazi bindings, including `c`, then `d` for copying the directory path, are untouched.

Remove `notify` for fewer notifications. Keep `--` when passing multiple plugin arguments. Restart Yazi after installation.

### Diagnostics — terminal only

Run this in your shell, not in Yazi:

```sh
~/.config/yazi/plugins/ucp.yazi/ucp doctor
```

To use the shorter `ucp doctor`, symlink that executable into a directory on your `PATH`. On Windows, run `python "$env:APPDATA\yazi\config\plugins\ucp.yazi\ucp" doctor` in PowerShell. Adjust paths if you use `YAZI_CONFIG_HOME`. Remove any previously configured `plugin ucp doctor` binding; that plugin action no longer exists.

The report prints helper versions/availability, session/backend information, supported formats, and unknown-type counts. It never reads clipboard payloads or changes the clipboard, and excludes paths, raw environment values, and unknown format names. A failed probe means unavailable/unreadable, not necessarily empty.

### Paste results

With `notify`, external paste reports **pasted, skipped, and failed** counts. A directory counts as one top-level item, not each nested file. `Q` marks the current and remaining batch items skipped and labels the summary cancelled. Earlier completed items remain in place.

Cancelling a filename prompt or collision, skipping any item, or encountering a failure preserves the native yank. Only a fully successful external paste clears the unchanged original yank. Native copy/cut fallbacks are delegated to Yazi, which owns their completion and yank lifecycle. To paste a retained yank instead of external clipboard contents, use Yazi's built-in `paste` action.

## Paste behavior and safety

- External copied files, images, or text take precedence over an internal copied selection. A native **cut** always uses Yazi's move operation instead.
- Linux/macOS file lists use percent-encoded local `file://` URLs, including `file://localhost/…`. Unicode, quotes, percent signs, CR/LF, and trailing filename whitespace are preserved. Remote authorities and ambiguous raw newline-separated POSIX lists are not interpreted as local file selections. Windows uses the native FileDrop format, with paths sent privately through stdin rather than interpolated into PowerShell.
- Copying local search results exports their filesystem paths, not `search://` URLs. If any selected entry belongs to a virtual filesystem (including SFTP), the entire external export is refused: the system clipboard is unchanged and the native yank is retained. Use Yazi's built-in paste action for that yank; ucp's normal external-clipboard precedence still applies.
- Source symlinks are copied **as links**, including dangling links and directory links with a trailing slash. Empty directories are retained. A directory symlink is not recursively dereferenced.
- Source/destination equality and ancestor/descendant overlap are rejected, including aliases through source-parent symlinks. Destination symlinks are rejected rather than followed. Ancestor aliases above a real chosen working directory are resolved first; choosing a symlink itself as the destination root is rejected.
- **Overwrite** replaces a directory only after its complete replacement is prepared. **Merge** prepares a merged tree and preserves unrelated entries. Overwriting a file onto an existing directory puts it inside that directory, using the same basename.
- **Create copy** chooses an unused name: `a_copy.txt`, then `a_copy_1.txt`, etc. Existing copies and dangling-link names are never reused.

| Collision key | Action |
| --- | --- |
| `o` / `O` | Overwrite this item / all later collisions. |
| `c` / `C` | Create a copy for this item / all later collisions. |
| `m` | Merge directory contents, keeping unrelated entries. |
| `q` / Escape | Skip this item; cancel a text/image save. |
| `Q` | Stop the remaining file batch; completed items stay in place. |

Text/image collision dialogs offer `o`, `c`, and `q`/Escape only. In the filename prompt, Enter submits and Ctrl-C cancels.

- Text filenames may contain subdirectories, but absolute paths and `.`/`..` components are rejected. New text/image files are private (`0600` on POSIX; current-user/SYSTEM-only DACL on Windows). POSIX copies/merges preserve ordinary `rwx` bits; Windows copies/merges preserve access DACLs and read-only attributes, making inherited access explicit. Ownership, special bits, extended attributes, and timestamps are not guaranteed preserved.
- Windows rejects device names, alternate data streams, drive-relative paths, and trailing dots/spaces. Drive and UNC paths are supported. Overlap checks resolve physical paths and compare case-insensitively. Destination junctions/reparse points are rejected; source symlinks and junctions stay links. Unknown reparse types fail safely rather than being traversed.

### Staging and recovery

Transfers prepare a complete result in a private directory on the destination filesystem (`0700` on POSIX; protected current-user/SYSTEM DACL on Windows). Existing targets are moved to a backup until the prepared result is installed. Reported install failures restore that backup; if rollback itself fails, the warning gives the retained backup path (`.ucp-stage*/backup`). Enough free space for the prepared tree is required.

This is **not crash-atomic or a filesystem snapshot**: process/power loss between renames can leave a recovery directory, and path-based checks do not defend against another process deliberately racing pathname changes. Do not remove a retained staging directory until any needed backup has been recovered.

macOS images are converted to PNG using a private temporary directory. Paths are passed as process arguments, never inserted into shell commands. Cleanup uses the exact created directory, including when `TMPDIR` contains quotes or newlines or the image writer fails.

## Regression verification

```bash
python -B -m unittest discover -s tests -v
```

The Linux suite launches real Yazi in a PTY with private Xvfb/Xauthority/clipboard state and a read-only-host bubblewrap sandbox. It covers copy/cut precedence, local search-result exports, refusal of virtual/mixed exports, all collision keys, unique names, links and empty directories, filename framing and traversal escapes, private directory modes, read/staging failures, and injected final-rename rollback. Virtual-selection cases use real Yazi URL/File objects with fixture metadata; they do not connect to an SFTP server.

Requirements: Python 3, `yazi`, `ya`, `bwrap`, `Xvfb`, `xclip`, and the filesystem helpers above. `fd` is required for search-view cases. A C compiler is optional for the rename-failure case; permission-denial cases require a non-root user.

Native Wayland tests start a private headless Sway compositor and use real `wl-copy`/`wl-paste` for multi-file copy/paste, image/text bytes, cancellation, and CLI diagnostics. Install `sway` and `wl-clipboard` to enable these cases; otherwise they are explicitly skipped. They never connect to your desktop compositor.

The macOS cases simulate the `osascript`/`pbpaste` executable boundary to exercise Lua framing, argv handling, private temporary paths, and cleanup; they do **not execute AppKit/JXA**. Alex Cicovic independently verified single/multi-file Finder copy on macOS 15.6 with Yazi 26.9.1 in [PR #18](https://github.com/simla33/ucp.yazi/pull/18). Native macOS image conversion still needs platform verification.

Native Windows coverage uses real Yazi in ConPTY and real WinForms clipboard data, with Unix tools excluded from the plugin's `PATH`. On a disposable Windows desktop with Python 3.12, `pywinpty==3.0.2`, and Yazi installed:

```powershell
$env:UCP_WINDOWS_TESTS = '1'
python -m unittest discover -s tests -p test_windows_native.py -v
```

These opt-in tests replace the desktop clipboard with synthetic data; do not enable them on your daily desktop. Ordinary discovery skips them. Use a disposable Windows desktop or Windows VM, including a Docker-managed KVM guest—not a normal Linux container.

## Credits

- **Paste into a new file** taken from [boydaihungst/save-clipboard-to-file.yazi](https://github.com/boydaihungst/save-clipboard-to-file.yazi)
- **Copy** based on [grappas/wl-clipboard.yazi](https://github.com/grappas/wl-clipboard.yazi)
- macOS clipboard contributions from **[Ze Huang (@Sutter099)](https://github.com/Sutter099)** in [PR #9](https://github.com/simla33/ucp.yazi/pull/9) and **[@Fun10165](https://github.com/Fun10165)** in [PR #16](https://github.com/simla33/ucp.yazi/pull/16), integrated selectively with the safety changes above.
- Directory path/merge work from **[@VigneshR387](https://github.com/VigneshR387)** in [PR #6](https://github.com/simla33/ucp.yazi/pull/6) and [PR #7](https://github.com/simla33/ucp.yazi/pull/7).
- Thanks to **[Alex Cicovic (@acicovic)](https://github.com/acicovic)** for the macOS multi-file copy fix and native Finder verification in [PR #18](https://github.com/simla33/ucp.yazi/pull/18), and the plugin-argument documentation fix in [PR #19](https://github.com/simla33/ucp.yazi/pull/19).
- Thanks to **[XYenon](https://github.com/XYenon)** and [clipboard.yazi](https://github.com/XYenon/clipboard.yazi) for demonstrating native Windows clipboard integration. ucp's Windows implementation was written independently; no AGPL source was copied.