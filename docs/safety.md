# Safety, permissions, and recovery

[Back to the README](../README.md)

## File URLs and paths

On Linux/macOS, clipboard file lists use percent-encoded local `file://` URLs, including `file://localhost/…`. Unicode, quotes, percent signs, CR/LF, and trailing filename whitespace are preserved. Remote authorities and ambiguous raw newline-separated POSIX lists are not interpreted as local file selections. Windows uses native FileDrop data.

Selected and yanked entries are Yazi `File` objects. External copy checks each entry's URL kind before exporting its local `url.path`. Local search results therefore export filesystem paths, not `search://` URLs. If any entry belongs to a virtual filesystem, the entire external export is refused; the system clipboard is unchanged and the native yank is retained. This is not SFTP/VFS clipboard transfer support.

Text filenames may contain subdirectories, but absolute paths and `.`/`..` components are rejected.

## Links and overlapping paths

- Source symlinks are copied **as links**, including dangling links and directory links with a trailing slash. A directory symlink is not recursively dereferenced. Empty directories are retained.
- Source/destination equality and ancestor/descendant overlap are rejected, including aliases through source-parent symlinks.
- Destination symlinks are rejected rather than followed. Ancestor aliases above a real chosen working directory are resolved first; choosing a symlink itself as the destination root is rejected.
- Existing copy names, including dangling-link names, are never reused.

## Overwrite, merge, and permissions

**Overwrite** replaces a directory only after its complete replacement is prepared. **Merge** prepares a merged tree and preserves unrelated entries. Overwriting a file onto an existing directory puts it inside that directory, using the same basename.

New text/image files are private: `0600` on POSIX, or a protected current-user/SYSTEM-only DACL on Windows. POSIX copies and merges preserve ordinary `rwx` bits, not ACLs. Windows copies/merges preserve source access DACLs and read-only attributes, turning inherited rules into explicit rules so destination inheritance cannot widen access.

Ownership, special bits, extended attributes, and timestamps are **not guaranteed preserved**.

Transfers fail without changing an existing target if required preparation or canonicalization fails. Linux/macOS use `realpath`, `cp`, `chmod`, and `mktemp`; Windows uses built-in PowerShell and Windows APIs.

## Staging and recovery

Transfers prepare a complete result in a private directory on the destination filesystem: `0700` on POSIX, protected current-user/SYSTEM DACL on Windows. Enough free space for the prepared tree is required.

Existing targets are moved to a backup until the prepared result is installed. Reported install failures restore that backup. If rollback itself fails, the warning gives the retained backup path, such as `.ucp-stage*/backup`.

**Do not remove a retained staging directory until any needed backup has been recovered.** Inspect the exact path from the warning before taking action; there is no automatic recovery or cleanup command.

This is **not crash-atomic or a filesystem snapshot**. Process or power loss between renames can leave a recovery directory. Path-based checks do not defend against another process deliberately racing pathname changes.

A native `SIGKILL` regression verifies this interruption window: the target is absent, while the original backup and complete prepared payload remain intact. If the original destination is still absent, restoring `backup` to its original name recovers the old tree. Inspect both versions before removing staging; there is no automatic crash recovery.

## Cancellation and partial results

Cancelling a batch does not undo already completed transfers. The current and unprocessed entries count as skipped; completed items stay installed. A directory counts as one top-level item in the summary.

Cancelled, skipped, failed, and partially completed external pastes preserve the native yank. Only a completely successful external paste clears the unchanged original yank. Yazi owns the lifecycle of native fallback copy/cut operations.

A retained yank does not override ucp's usual external-clipboard precedence. Use Yazi's built-in `paste` action when you explicitly want that yank.

## Windows

Native Windows copy/paste requires Windows PowerShell 5.1 (`powershell.exe` on `PATH`), an interactive desktop, and a filesystem that supports persistent ACLs. Python is needed only for the optional diagnostic CLI.

- FileDrop paths, Unicode text, and PNG image bytes travel over process pipes. Clipboard payloads and filenames are never interpolated into PowerShell code or written to temporary clipboard files.
- Drive and UNC paths are accepted. Physical-path resolution follows ancestor aliases; overlap comparisons use Windows case-insensitive rules. Ambiguous case-sensitive directory entries are rejected rather than combined.
- Relative save names reject traversal, drive-relative paths, device names, alternate data streams, invalid characters, and trailing dots/spaces.
- Destination junctions/reparse points are rejected. Source symbolic links and junctions are copied as links, including dangling links. Unsupported reparse tags fail closed.
- Creating symbolic links requires Developer Mode or Windows symlink privilege. Without it, the paste fails and retains the native yank; it never substitutes a dereferenced copy.
- Windows releases its own empty destination reservation before a no-replace rename. A failed install restores the original backup; a failed restore retains that backup for recovery.

## macOS image temporary files

macOS images are converted to PNG using a private temporary directory. Paths are passed as process arguments, never inserted into shell commands. Cleanup uses the exact created directory, including when `TMPDIR` contains quotes or newlines or the image writer fails.

The automated macOS tests simulate the executable boundary; they do not establish native AppKit behavior. See [coverage and limits](development.md#coverage-and-limits).
