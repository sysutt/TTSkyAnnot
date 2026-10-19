"""Showing a file in the system file manager, with the file highlighted.

Windows Explorer can be asked to highlight a file with `explorer /select,PATH`, but that relies on
explorer.exe re-parsing a single argument that contains a comma, spaces and backslashes. When that parse goes
wrong Explorer opens its default folder instead — the user sees Documents rather than the folder they asked
for. So the shell API is used here instead: the path is turned into a shell item and handed over directly,
with nothing for Explorer to mis-read. The command line stays as a fallback for the cases the API refuses.
"""
from __future__ import annotations

import os
import subprocess
import sys

_WINDOWS = sys.platform == "win32"


def _reveal_windows(path: str) -> bool:
    """Highlight `path` in Explorer via shell32. True when the shell accepted it."""
    import ctypes

    shell32 = ctypes.windll.shell32
    shell32.SHParseDisplayName.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong,
                                           ctypes.POINTER(ctypes.c_ulong)]
    shell32.SHParseDisplayName.restype = ctypes.c_long
    shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                   ctypes.c_void_p, ctypes.c_ulong]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    shell32.ILFree.argtypes = [ctypes.c_void_p]

    pidl = ctypes.c_void_p()
    attrs = ctypes.c_ulong(0)
    if shell32.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, ctypes.byref(attrs)) != 0 or not pidl:
        return False
    try:
        # 0 children to select separately, 0 flags: the item itself is what gets highlighted
        return shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0) == 0
    finally:
        shell32.ILFree(pidl)


def reveal(path: str) -> bool:
    """Open the folder holding `path` with the file highlighted. True when a method succeeded.

    A path that no longer exists reveals its folder instead, which is more useful than an error: the file was
    deleted or moved and the user wants to see where it was.
    """
    path = os.path.abspath(os.fspath(path))
    if not _WINDOWS:
        # no highlight to ask for: open the containing folder
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        try:
            subprocess.Popen(["xdg-open", folder])
            return True
        except OSError:
            return False
    if not os.path.exists(path):
        path = os.path.dirname(path)
    if _reveal_windows(path):
        return True
    # fall back to the command line, quoted so a path with spaces survives
    try:
        subprocess.Popen('explorer /select,"%s"' % os.path.normpath(path))
        return True
    except OSError:
        return False
