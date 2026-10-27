"""Register the app as a handler for astro image files, so it shows up in Explorer's "Open with" list.

Windows keeps that list in the registry: an ``Applications\\<exe>`` key with the command line to run, the
types it accepts, and an icon. Everything here is written under HKEY_CURRENT_USER, so the app never needs
administrator rights and nothing machine-wide is touched. The entry is written once on start-up; rewriting it
on every launch keeps the path correct if the program is moved or updated.

The command line is ``"<exe>" "%1"``, which the CLI already understands: ``skyannot IMAGE`` opens the window
with that image loaded.
"""
from __future__ import annotations

import os
import sys

APP_NAME = "TTSkyAnnot"
FRIENDLY = "TTSkyAnnot 星图标注"
# the extensions the loader really reads (see imageio.load); listing anything else would put the app in front
# of files it cannot open
IMAGE_EXTS = (
    ".fits", ".fit", ".fts", ".xisf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
)


def _exe_path() -> str:
    """The program to launch: the frozen exe when bundled, otherwise this interpreter plus the module."""
    exe = os.path.abspath(sys.executable or "")
    if getattr(sys, "frozen", False):
        return exe
    return exe


def _command() -> str:
    if getattr(sys, "frozen", False):
        return '"%s" "%%1"' % _exe_path()
    # running from source: python -m skyannot <file>
    return '"%s" -m skyannot "%%1"' % _exe_path()


def register() -> bool:
    """Write the "Open with" entry. Returns True when the registry was written."""
    if os.name != "nt":
        return False
    try:
        import winreg
    except Exception:
        return False
    exe = _exe_path()
    if not exe:
        return False
    key = r"Software\Classes\Applications\%s.exe" % APP_NAME
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r"\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _command())
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, "FriendlyAppName", 0, winreg.REG_SZ, FRIENDLY)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r"\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, '"%s",0' % exe)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r"\SupportedTypes") as k:
            for ext in IMAGE_EXTS:
                winreg.SetValueEx(k, ext, 0, winreg.REG_SZ, "")
        # an OpenWithProgids hint per extension: this is what makes the app appear in the list even for
        # extensions another program already owns
        for ext in IMAGE_EXTS:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\Classes\%s\OpenWithProgids" % ext) as k:
                winreg.SetValueEx(k, "%s%s" % (APP_NAME, ext.replace(".", "_")), 0, winreg.REG_SZ, "")
        return True
    except Exception:
        return False


def registered_command() -> str:
    """What the registry currently says, for the settings dialog / diagnostics."""
    if os.name != "nt":
        return ""
    try:
        import winreg
        key = r"Software\Classes\Applications\%s.exe\shell\open\command" % APP_NAME
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return str(winreg.QueryValueEx(k, "")[0])
    except Exception:
        return ""


def unregister() -> bool:
    """Remove the entries again (used by the settings toggle)."""
    if os.name != "nt":
        return False
    try:
        import winreg
    except Exception:
        return False

    def _drop(path):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except Exception:
            pass

    base = r"Software\Classes\Applications\%s.exe" % APP_NAME
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", r"\DefaultIcon", r"\SupportedTypes", ""):
        _drop(base + sub)
    for ext in IMAGE_EXTS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Classes\%s\OpenWithProgids" % ext, 0, winreg.KEY_ALL_ACCESS) as k:
                winreg.DeleteValue(k, "%s%s" % (APP_NAME, ext.replace(".", "_")))
        except Exception:
            pass
    return True
