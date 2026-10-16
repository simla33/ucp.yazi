"""Probe the host for the binaries the integration suite needs.

The suite requires Linux with real ``yazi``, ``ya``, ``bwrap`` (bubblewrap),
``Xvfb`` and ``xclip``. ``realpath``, ``mktemp``, ``chmod`` and ``cp`` must
also be available inside the read-only host bind. A C compiler is optional:
only the one-shot libc-rename failure injection case needs it.
"""

import shutil
import sys

REQUIRED = ("yazi", "ya", "bwrap", "Xvfb", "xclip", "realpath", "mktemp", "chmod", "cp")


def missing_required():
    return [name for name in REQUIRED if shutil.which(name) is None]


def linux_ok():
    return sys.platform.startswith("linux") and not missing_required()


def skip_reason():
    problems = []
    if not sys.platform.startswith("linux"):
        problems.append("non-Linux host")
    problems.extend("missing binary: %s" % n for n in missing_required())
    return "; ".join(problems) or "environment ok"


def bin_path(name):
    p = shutil.which(name)
    if p is None:
        raise RuntimeError("required binary not found: %s" % name)
    return p
