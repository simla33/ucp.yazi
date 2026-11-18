# ucp.yazi

Copy files between **Yazi and desktop apps**. Paste files, save clipboard images, or turn text into a new file.

**Yazi 26.8.15+** · [MIT](LICENSE)

## Install

```sh
ya pkg add simla33/ucp
```

| Platform | Requirements and verification |
| --- | --- |
| X11 | `xclip`. Tested with real Yazi and a private X11 clipboard. |
| Wayland | `wl-clipboard`. Tested with real Yazi and headless Sway. |
| macOS | `osascript`/AppKit and `pbpaste`. |
| Windows | Built-in `powershell.exe` 5.1. [Native tests run in a disposable VM](docs/development.md#windows). |

Linux/macOS also need `realpath`, `cp`, `chmod`, and `mktemp`. Windows needs an interactive desktop and an ACL-capable filesystem. [Platform details](docs/safety.md#windows)

## Keys

Add these entries to your existing `[mgr].prepend_keymap` in `keymap.toml`, then restart Yazi:

```toml
[mgr]
prepend_keymap = [
    { on = "y", run = "plugin ucp -- copy notify", desc = "Copy to clipboard" },
    { on = "p", run = "plugin ucp -- paste notify", desc = "Paste files, images or text" },
]
```

Only `y` and `p` invoke ucp. [Native Yazi keys and an optional clipboard bypass](docs/troubleshooting.md#native-yazi-keys)

Remove `notify` for fewer notifications; keep `--` when passing multiple arguments. [Config locations and options](docs/troubleshooting.md#configuration)

## Demos

![Copying a selected file from Yazi into a desktop file manager.](https://github.com/user-attachments/assets/8894ca14-402b-4885-b2b6-d9666a4661b4)

[Paste files](https://github.com/user-attachments/assets/ae29d283-fec1-4e56-8857-c57d17c0d4f8) · [Resolve collisions and save text](https://github.com/user-attachments/assets/92746b8d-e93e-4c0c-8bbb-7845be5f57e1)

## Paste behavior

**Native cuts win.** Otherwise, clipboard files, images, then text take priority. With no supported readable clipboard data, ucp uses Yazi's native yank. Text asks for a filename; images use a timestamped name.

| Collision key | Action |
| --- | --- |
| `o` / `O` | Overwrite one / all later collisions. |
| `c` / `C` | Create a uniquely named copy, such as `note_copy.txt`, once / for all later collisions. |
| `m` | Merge directories, preserving unrelated entries. |
| `q` / Escape | Skip one item; cancel a text/image save. |
| `Q` | Stop the remaining file batch. |

Text/image dialogs offer `o`, `c`, and `q`/Escape. In the filename prompt, Enter submits and Ctrl-C cancels.

## Diagnostics

```sh
~/.config/yazi/plugins/ucp.yazi/ucp doctor
```

No keybinding, no running Yazi, no clipboard payload reads. The report shows helper/session metadata and recognized formats—not paths, environment values, or unknown format names.

[Use `ucp doctor` from PATH or Windows PowerShell](docs/troubleshooting.md#diagnostics) · [Troubleshooting](docs/troubleshooting.md)

## Limits and safety

Local search results work. [Remote files](docs/troubleshooting.md#remote-files) must be downloaded locally before external copy. [Flatpak copying](docs/troubleshooting.md#flatpak-file-copying) has optional portal support.

Transfers are staged, **not crash-atomic**. Keep any backup named in a recovery warning. [Safety and recovery](docs/safety.md) · [Tests and verification limits](docs/development.md)

Update with `ya pkg upgrade simla33/ucp`, then restart Yazi.

## Thanks

- **[Alex Cicovic (@acicovic)](https://github.com/acicovic)** — macOS multi-file copy and Finder verification in [#18](https://github.com/simla33/ucp.yazi/pull/18), plus argument documentation in [#19](https://github.com/simla33/ucp.yazi/pull/19).
- **[XYenon](https://github.com/XYenon)** and [clipboard.yazi](https://github.com/XYenon/clipboard.yazi) — Windows clipboard inspiration. ucp's implementation is independent; no AGPL source was copied.
- **[boydaihungst/save-clipboard-to-file.yazi](https://github.com/boydaihungst/save-clipboard-to-file.yazi)** — the original paste-to-file implementation.
- **[grappas/wl-clipboard.yazi](https://github.com/grappas/wl-clipboard.yazi)** — the basis for clipboard copy.
- **[Ze Huang (@Sutter099)](https://github.com/Sutter099)** and **[@Fun10165](https://github.com/Fun10165)** — macOS contributions in [#9](https://github.com/simla33/ucp.yazi/pull/9) and [#16](https://github.com/simla33/ucp.yazi/pull/16).
- **[@VigneshR387](https://github.com/VigneshR387)** — directory/merge work in [#6](https://github.com/simla33/ucp.yazi/pull/6) and [#7](https://github.com/simla33/ucp.yazi/pull/7).
