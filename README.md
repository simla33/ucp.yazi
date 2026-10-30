# Universal Copy/Paste for Yazi

Clipboard backends for **Wayland**, **Xorg**, and **macOS**. Linux/Xorg is exercised with real Yazi; native macOS and Wayland execution are not verified by the current suite.

Integrates yazi copy/paste with system clipboard similar to GUI file managers.

## Features

- **Copy files to system clipboard** - Works with other file managers, browsers, and applications
- **Paste files from external sources** - Supports pasting files from other file managers and code editors (including VS Code)
- **Text paste to new file** - Automatically suggests creating new files when clipboard contains text
- **Smart collision handling** - Manages file conflicts during paste operations

- Native Yazi cuts retain priority over external clipboard contents.
- When no supported clipboard data is readable, copied files fall back to native Yazi paste.


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
- **Filesystem helpers on `PATH`**: `realpath`, `cp`, `chmod`, and `mktemp`. Transfers fail without changing an existing target if required preparation/canonicalization fails.

```bash
ya pkg add simla33/ucp
```

## Configuration

> [!NOTE]
> Requires Yazi 26.8.15 or newer. Selected and yanked entries are `File` objects;
> the plugin reads each entry's `url` when collecting file paths.

```toml
[mgr]
prepend_keymap = [
    { on = "p", run = "plugin ucp paste", desc = "Paste" },
    { on = "y", run = "plugin ucp copy", desc = "Copy" }
]
```

You can enable notifications for successful/failed operations. Yazi passes only the first word after the plugin name unless the arguments follow `--`:

```toml
[mgr]
prepend_keymap = [
    { on = "p", run = "plugin ucp -- paste notify", desc = "Paste" },
    { on = "y", run = "plugin ucp -- copy notify", desc = "Copy" }
]
```

## Paste behavior and safety

- External copied files, images, or text take precedence over an internal copied selection. A native **cut** always uses Yazi's move operation instead.
- File lists use percent-encoded local `file://` URLs, including `file://localhost/…`. Unicode, quotes, percent signs, CR/LF, and trailing filename whitespace are preserved. Remote authorities and ambiguous raw newline-separated POSIX lists are not interpreted as local file selections.
- Source symlinks are copied **as links**, including dangling links and directory links with a trailing slash. Empty directories are retained. A directory symlink is not recursively dereferenced.
- Source/destination equality and ancestor/descendant overlap are rejected, including aliases through source-parent symlinks. Destination symlinks are rejected rather than followed. Ancestor aliases above a real chosen working directory are resolved first; choosing a symlink itself as the destination root is rejected.
- **Overwrite** replaces a directory only after its complete replacement is prepared. **Merge** prepares a merged tree and preserves unrelated entries. Overwriting a file onto an existing directory puts it inside that directory, using the same basename.
- **Create copy** chooses an unused name: `a_copy.txt`, then `a_copy_1.txt`, etc. Existing copies and dangling-link names are never reused. `q`/Escape skip the current collision; `O`/`C` apply overwrite/copy to later collisions; `Q` stops the remaining batch.
- Text filenames may contain subdirectories, but absolute paths and `.`/`..` components are rejected. New text/image files are private (`0600`). Copies and merges preserve ordinary `rwx` permission bits; ownership, special bits, ACLs, extended attributes, and timestamps are not guaranteed preserved.

### Staging and recovery

Transfers prepare a complete result in a private `0700` directory on the destination filesystem. Existing targets are moved to a backup until the prepared result is installed. Reported install failures restore that backup; if rollback itself fails, the warning gives the retained backup path (`.ucp-stage*/backup`). Enough free space for the prepared tree is required.

This is **not crash-atomic or a filesystem snapshot**: process/power loss between renames can leave a recovery directory, and path-based checks do not defend against another process deliberately racing pathname changes. Do not remove a retained staging directory until any needed backup has been recovered.

macOS images are converted to PNG using a private temporary directory. Paths are passed as process arguments, never inserted into shell commands. Cleanup uses the exact created directory, including when `TMPDIR` contains quotes or newlines or the image writer fails.

## Regression verification

```bash
python -B -m unittest discover -s tests -v
```

The Linux suite launches real Yazi in a PTY with private Xvfb/Xauthority/clipboard state and a read-only-host bubblewrap sandbox. It covers copy/cut precedence, all collision keys, unique names, links and empty directories, filename framing and traversal escapes, private directory modes, read/staging failures, and injected final-rename rollback.

Requirements: Python 3, `yazi`, `ya`, `bwrap`, `Xvfb`, `xclip`, and the filesystem helpers above. A C compiler is optional for the rename-failure case; permission-denial cases require a non-root user.

The macOS cases simulate the unavailable `osascript`/`pbpaste` executable boundary to exercise Lua framing, argv handling, private temporary paths, and cleanup. They **do not execute AppKit/JXA**. Native macOS clipboard/image conversion and a real Wayland compositor still require platform verification before claiming those backends are validated.

## Credits

- **Paste into a new file** taken from [boydaihungst/save-clipboard-to-file.yazi](https://github.com/boydaihungst/save-clipboard-to-file.yazi)
- **Copy** based on [grappas/wl-clipboard.yazi](https://github.com/grappas/wl-clipboard.yazi)
- macOS clipboard contributions from **[Ze Huang (@Sutter099)](https://github.com/Sutter099)** in [PR #9](https://github.com/simla33/ucp.yazi/pull/9) and **[@Fun10165](https://github.com/Fun10165)** in [PR #16](https://github.com/simla33/ucp.yazi/pull/16), integrated selectively with the safety changes above.
- Directory path/merge work from **[@VigneshR387](https://github.com/VigneshR387)** in [PR #6](https://github.com/simla33/ucp.yazi/pull/6) and [PR #7](https://github.com/simla33/ucp.yazi/pull/7).