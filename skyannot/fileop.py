"""Removing and moving the user's image files.

Deleting means the Recycle Bin, never an unconditional erase: this is someone's astrophotography, and the
button that gets rid of it sits next to the playback controls. On Windows that is SHFileOperation with
FOF_ALLOWUNDO; elsewhere the file goes to the platform's trash directory (or the XDG trash when there is
none) so a mis-click is always recoverable.
"""
from __future__ import annotations

import os
import shutil
import sys
import time


def _trash_dir() -> str:
    """Where this platform puts files that were thrown away."""
    if sys.platform == "darwin":
        return os.path.expanduser("~/.Trash")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "Trash", "files")


def _win_recycle_count(root: str) -> int:
    """How many items the Recycle Bin holds for a drive (or for all drives for ""), -1 when it cannot be
    queried. SHFileOperation reports success even when the Recycle Bin is unavailable and the file is then
    erased for good, so this is the evidence that it really was kept."""
    import ctypes
    from ctypes import wintypes

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong),
                    ("i64NumItems", ctypes.c_longlong)]

    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO), 0, 0)
    rc = ctypes.windll.shell32.SHQueryRecycleBinW(root or None, ctypes.byref(info))
    if rc != 0:
        return -1
    return int(info.i64NumItems)


def _to_recycle_bin(path: str) -> bool:
    """Windows Recycle Bin, through the shell. Returns False when the shell reported success but the file
    never turned up in the bin — a disabled Recycle Bin erases it for good and we must not pretend
    otherwise. Raises OSError when the shell refuses outright."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16),
                    ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
    drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    before = _win_recycle_count(drive)
    op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(path) + "\0\0", None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
                         False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise OSError("the shell refused to move the file to the Recycle Bin (code %d)" % rc)
    if before < 0:
        return True                                # cannot count items on this system: take the shell's word
    for _ in range(6):                             # the shell updates the count asynchronously
        if _win_recycle_count(drive) > before:
            return True
        time.sleep(0.2)
    return False                                   # it is gone, but not into the bin


def delete(path: str) -> bool:
    """Throw a file away. Returns True when it went to the Recycle Bin / trash and can still be brought
    back, False when the platform had no bin for it and it is gone for good. Raises OSError when the file
    could not be removed at all."""
    if sys.platform == "win32":
        return _to_recycle_bin(path)
    dest = os.path.join(_trash_dir(), os.path.basename(path))
    n = 1
    while os.path.exists(dest):                     # never overwrite an earlier namesake
        dest = os.path.join(_trash_dir(), "%s.%d" % (os.path.basename(path), n))
        n += 1
    os.makedirs(_trash_dir(), exist_ok=True)
    shutil.move(path, dest)
    return True


def move(path: str, folder: str) -> str:
    """Move a file into `folder` and return where it landed."""
    return shutil.move(path, os.path.join(folder, os.path.basename(path)))


def trash_path(path: str) -> str:
    """Where delete() put this file, or "" when it cannot be found — the way back for an undo. On Windows the
    Recycle Bin is opaque to us (the shell renames entries), so only the POSIX trash can be located."""
    if sys.platform == "win32":
        return ""
    dest = os.path.join(_trash_dir(), os.path.basename(path))
    return dest if os.path.exists(dest) else ""
