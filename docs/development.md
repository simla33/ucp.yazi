# Development and verification

[Back to the README](../README.md)

## Run the regression suite

From the repository root:

```sh
python -B -m unittest discover -s tests -v
```

The Linux suite launches real Yazi in a PTY with private Xvfb/Xauthority/clipboard state and a read-only-host bubblewrap sandbox. Optional Wayland cases launch a private headless Sway compositor. Neither uses your desktop clipboard.

Required tools:

- Python 3, `yazi`, `ya`, `bwrap`, `Xvfb`, and `xclip`.
- `realpath`, `cp`, `chmod`, and `mktemp`.
- `fd` for the real search-view cases.
- `sway` and `wl-clipboard` for native Wayland cases.

A C compiler is optional for the rename-failure and process-interruption cases. OpenSSH enables real SFTP cases. Permission-denial cases require a non-root user. Check the output for skips; a skipped platform case is not a verification result.

## Windows

Use a **disposable x64 Windows desktop or VM**, with Yazi, Python 3.12, `pywinpty==3.0.2`, and the [Microsoft Visual C++ x64 runtime](https://aka.ms/vs/17/release/vc_redist.x64.exe) installed:

```powershell
$env:UCP_WINDOWS_TESTS = '1'
python -B -m unittest discover -s tests -p test_windows_native.py -v
```

The opt-in suite replaces the desktop clipboard with synthetic data. Never enable it on your daily desktop. Without the opt-in, the native Windows cases are skipped.

Tests run real Yazi in ConPTY and use independent WinForms fixtures, with Unix utilities excluded from the plugin's `PATH`. Coverage includes file/image/text bytes, large Unicode payloads across binary-frame boundaries, collisions, merges, cancellation, yank state, DACLs, read-only attributes, symbolic links, junctions, path aliases, and CLI privacy.

Verified: 27 native cases on Windows 10 (build 19045), PowerShell 5.1, and Yazi 26.9.1 in a disposable KVM VM. Other Windows versions have not been tested.

Docker can host the verification **through a Windows VM with KVM**, not through an ordinary Linux container. The test workspace must live on the guest's ACL-capable filesystem. Do not share your personal clipboard or home directory with the guest.

## SFTP and Flatpak

SFTP tests launch an isolated loopback OpenSSH server with temporary keys. They exercise real remote selection, native downloads, and Lua `fs.copy`, not fabricated remote entries.

Portal tests require Python 3, PyGObject, GTK 4, the FileTransfer portal, and an installed Flatpak with Python/GTK 4:

```sh
UCP_FLATPAK_TESTS=1 python -B -m unittest discover -s tests -p test_portal.py -v
```

The default receiver is `org.telegram.desktop`; override it with `UCP_FLATPAK_APP`. Tests run a small GTK receiver inside that sandbox on a private X11 display. They do not start Telegram, use an account, send messages, or grant broad filesystem access. Ordinary tests use an inaccessible session-bus address; only this opt-in connects to the real portal with synthetic files.


## What the suite exercises

- Copy/cut precedence and local search-result exports, with exact clipboard URIs and destination bytes.
- Virtual/mixed export refusal using real Yazi URL/File objects with fixture metadata.
- Filename framing, encoded names, traversal escapes, symlinks, empty directories, and private modes.
- Collision keys, unique names, overwrite/merge behavior, staging failures, and injected final-rename rollback.
- Retained yank after cancellation, skips, failures, and partial batches; exact top-level outcome counts.
- Standalone CLI metadata, missing helpers, failed probes, privacy filtering, and unchanged clipboard contents.

The harness calls the real plugin entry point and records its returned outcome after completion. Clipboard and filesystem APIs are not replaced for the X11 cases. The simulated macOS cases are explicitly labelled.

## Coverage and limits

**Native macOS:** automated cases simulate `osascript`/`pbpaste`; they do not execute AppKit/JXA. Alex Cicovic verified single/multi-file Finder copy on macOS 15.6 with Yazi 26.9.1 in [PR #18](https://github.com/simla33/ucp.yazi/pull/18). Native image conversion remains unverified: this environment has no authenticated macOS runner.

**Wayland:** private Sway tests passed for multi-file copy/paste, image/text bytes, cancellation, and CLI metadata. An isolated Hyprland 0.55 startup failed before scenarios could run, including with a render-only device exposed. No Hyprland or Xwayland clipboard-bridge result is claimed.

**Remote files:** three real-SFTP cases passed: hover/selection export refusal with an unchanged clipboard, native file/directory downloads, and byte-exact Lua `fs.copy`. Automatic remote-to-desktop export is not implemented; use the [local download workflow](troubleshooting.md#remote-files). These tests do not establish SSH host authentication.

**Flatpak:** a real GTK receiver inside Telegram Flatpak 7.0.9 read 17 files plus a directory with nested/empty children twice, while the original host paths were inaccessible. Portal paths denied write access; replacing the clipboard invalidated the transfer key. URI fallback preserved symbolic links. This verifies the portal mechanism, not Telegram's attachment UI; [#8](https://github.com/simla33/ucp.yazi/issues/8) remains open for application-level confirmation.

**Filesystem:** a deterministic `SIGKILL` case confirms that the original backup, prepared replacement, and source survive interruption, and manual recovery restores the original. The target can be absent between renames. Crash atomicity, power-loss durability, and resistance to hostile pathname races are **not implementation guarantees**, rather than merely untested claims. See [recovery](safety.md#staging-and-recovery).

## Reporting a problem

Include the OS, Yazi version, plugin revision, and a minimal reproduction using non-sensitive files. Run the terminal [diagnostic CLI](troubleshooting.md#diagnostics) and review its report before sharing. For interoperability problems, compare the same file from a desktop file manager into the same receiving application.

Do not post clipboard payloads, private paths, credentials, or raw environment dumps. State the exact environment and what you actually verified; a skipped or simulated platform case does not establish native support.
