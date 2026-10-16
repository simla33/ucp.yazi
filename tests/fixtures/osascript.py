#!/usr/bin/python3
"""SIMULATED macOS process boundary; this is not native macOS validation.

The real plugin sends its actual JXA program/argv/stdin to this executable.
Fixture data stands in only for unavailable Cocoa pasteboard/image services.
"""
import base64
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote

state = json.loads(Path(os.environ["UCP_MAC_STATE"]).read_text())
if Path(sys.argv[0]).name == "pbpaste":
    with open(os.environ["UCP_MAC_LOG"], "a") as log:
        log.write(json.dumps({"kind": "text", "argv": sys.argv[1:]}) + "\n")
    sys.stdout.write(state.get("text", ""))
    sys.exit(0)

args = sys.argv[1:]
assert args[:3] == ["-l", "JavaScript", "-e"], args
script = args[3]
extra = args[4:]
entry = {"argv": args, "extra": extra}
exit_code = 0
output = ""
if "NSImage" in script and "writeToFileAtomically" in script:
    entry["kind"] = "image"
    assert len(extra) == 1, "image path must be a separate argv argument"
    target = Path(extra[0])
    entry["target"] = str(target)
    entry["parent_mode"] = target.parent.stat().st_mode & 0o777
    entry["unexpected_entries"] = sorted(p.name for p in target.parent.iterdir())
    if state.get("image_failure"):
        # A partial real file must also be cleaned, but never consumed as data.
        if state.get("partial_write"):
            target.write_bytes(b"this must not be pasted")
        exit_code = 23
    else:
        target.write_bytes(base64.b64decode(state["image"]))
elif "var types = pb.types;" in script:
    entry["kind"] = "types"
    # This result emulates JXA's return value; tests do not claim to execute
    # Cocoa's native type-conversion code on the Linux host.
    output = "\n".join(state.get("types", []))
elif "readObjectsForClassesOptions" in script and "$.NSURL" in script:
    entry["kind"] = "files"
    urls = state.get("files", [])
    output = "\n".join(urls)
else:
    raise AssertionError("unrecognized actual Cocoa boundary program")
with open(os.environ["UCP_MAC_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\n")
# osascript adds a protocol newline to its returned string.
if output:
    sys.stdout.write(output + "\n")
sys.exit(exit_code)
