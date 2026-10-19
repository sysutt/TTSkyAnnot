"""Hand an image to PixInsight.

PixInsight registers itself as the handler for its file types with the command line

    "C:\\Program Files\\PixInsight\\bin\\PixInsight.exe" "%1"

(registry: HKEY_CLASSES_ROOT\\Pleiades Astrophoto.PixInsight.xisf\\shell\\open\\command), so passing the
image path as an argument is exactly what Explorer's "Open with" does. PixInsight runs as a single instance,
so this hands the file to the copy that is already open instead of starting a second one.

The executable is taken from the settings when set, otherwise looked for in the usual places.
"""
from __future__ import annotations

import os
import subprocess

# where PixInsight keeps its core executable, per platform
_CANDIDATES = (
    r"C:\Program Files\PixInsight\bin\PixInsight.exe",
    r"C:\Program Files (x86)\PixInsight\bin\PixInsight.exe",
    r"C:\Program Files\PixInsight\PixInsight.exe",
    "/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight",
    "/opt/PixInsight/bin/PixInsight",
)


def find(configured: str = "") -> str:
    """The PixInsight executable: the configured path when it exists, otherwise the usual install location.
    Returns "" when PixInsight does not appear to be installed."""
    if configured and os.path.isfile(configured):
        return configured
    return next((p for p in _CANDIDATES if os.path.isfile(p)), "")


def open_image(path: str, exe: str = "", launch=None) -> str:
    """Ask PixInsight to open `path`. Returns the executable used; raises RuntimeError when there is none.

    `launch` exists for tests: it is called as launch([exe, path]) instead of starting a process."""
    prog = find(exe)
    if not prog:
        raise RuntimeError("PixInsight executable not found; set its path in the settings")
    path = os.path.abspath(path)
    (launch or _launch)([prog, path])
    return prog


def _launch(cmd) -> None:
    subprocess.Popen(cmd)
