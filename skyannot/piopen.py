"""Hand an image to PixInsight.

PixInsight registers itself as the handler for its file types with the command line

    "C:\\Program Files\\PixInsight\\bin\\PixInsight.exe" "%1"

(registry: HKEY_CLASSES_ROOT\\Pleiades Astrophoto.PixInsight.xisf\\shell\\open\\command), so passing the
image path as an argument is exactly what Explorer's "Open with" does. PixInsight runs as a single instance,
so this hands the file to the copy that is already open instead of starting a second one.

The executable is taken from the settings when set, otherwise looked for in the usual places.
"""
from __future__ import annotations

import functools
import os
import subprocess
import sys

# the last resort: the usual install locations, per platform
_CANDIDATES = (
    r"C:\Program Files\PixInsight\bin\PixInsight.exe",
    r"C:\Program Files (x86)\PixInsight\bin\PixInsight.exe",
    r"C:\Program Files\PixInsight\PixInsight.exe",
    "/Applications/PixInsight/PixInsight.app/Contents/MacOS/PixInsight",
    "/opt/PixInsight/bin/PixInsight",
)

# per-drive layouts PixInsight has been seen in; Windows installs often live on a data drive, so every fixed
# drive is looked at rather than only C:
_LAYOUTS = (
    r"Program Files\PixInsight\bin\PixInsight.exe",
    r"Program Files (x86)\PixInsight\bin\PixInsight.exe",
    r"Program Files\PixInsight\PixInsight.exe",
    r"PixInsight\bin\PixInsight.exe",
    r"PixInsight\PixInsight.exe",
    r"Astro\PixInsight\bin\PixInsight.exe",
)


def _exe_from_command(cmd: str) -> str:
    r"""A registered command line, e.g. "C:\...\PixInsight.exe" "%1", reduced to the executable."""
    cmd = str(cmd or "").strip()
    if not cmd:
        return ""
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 0 else ""
    low = cmd.lower()
    cut = low.find(".exe")
    return cmd[:cut + 4] if cut >= 0 else cmd


def _registry_candidates() -> list:
    """Where Windows records PixInsight: the file-type handler and the uninstall entries.

    Explorer knows where PixInsight is — it opens .xisf files with it — so reading the same keys is the most
    reliable detection there is, and it also catches installs in non-standard folders.
    """
    if not sys.platform.startswith("win"):
        return []
    try:
        import winreg
    except ImportError:
        return []
    out = []

    def value(root, key, name=None):
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                return winreg.QueryValue(k, name) if name is None else winreg.QueryValueEx(k, name)[0]
        except OSError:
            return ""

    for prog in ("Pleiades Astrophoto.PixInsight.xisf", "Pleiades Astrophoto.PixInsight",
                 "PixInsight.xisf"):
        out.append(_exe_from_command(value(winreg.HKEY_CLASSES_ROOT, prog + r"\shell\open\command")))
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        out.append(_exe_from_command(
            value(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\PixInsight.exe")))
        out.append(value(root, r"SOFTWARE\PixInsight", "InstallPath"))
    # the uninstall records: InstallLocation / DisplayIcon point at the folder or at its uninstaller
    uninstall_roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for root, base in uninstall_roots:
        try:
            with winreg.OpenKey(root, base) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        name = winreg.EnumKey(k, i)
                        with winreg.OpenKey(k, name) as sub:
                            disp = winreg.QueryValueEx(sub, "DisplayName")[0]
                            if "pixinsight" not in str(disp).lower():
                                continue
                            loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                    except OSError:
                        continue
                    if loc:
                        out.append(os.path.join(str(loc), "bin", "PixInsight.exe"))
                        out.append(os.path.join(str(loc), "PixInsight.exe"))
        except OSError:
            continue
    return [p for p in out if p]


def _drive_candidates() -> list:
    """The layouts above on every fixed local drive, so an install on D: or E: is found as well."""
    out = []
    if sys.platform.startswith("win"):
        try:
            import ctypes
            fixed = ctypes.windll.kernel32.GetDriveTypeW
        except Exception:
            fixed = None
    else:
        fixed = None
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = letter + ":\\"
        if sys.platform.startswith("win"):
            if fixed is not None:
                try:
                    if fixed(root) != 3:          # 3 = DRIVE_FIXED
                        continue
                except Exception:
                    continue
            elif not os.path.isdir(root):
                continue
        elif not os.path.isdir(root):
            continue
        out.extend(root + layout for layout in _LAYOUTS)
    return out


def _detected() -> str:
    """The first existing install the registry, the drives and the defaults point at ("" if none)."""
    for p in _registry_candidates():
        if p and os.path.isfile(p):
            return p
    for p in _drive_candidates():
        if os.path.isfile(p):
            return p
    return next((p for p in _CANDIDATES if os.path.isfile(p)), "")


def find(configured: str = "") -> str:
    """The PixInsight executable: the configured path when it exists, otherwise what auto-detection finds.
    Returns "" when PixInsight does not appear to be installed."""
    if configured and os.path.isfile(configured):
        return configured
    return _detected()


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
