"""Self-update for the packaged build: read a feed, fetch the new package, swap it in and restart.

The shape of it is what every updater does in the end:

1. read a small `version.json` from the update source (a URL, a UNC share or a folder);
2. compare its version with the one compiled into this build;
3. download the package to a temporary folder and check its SHA-256;
4. unpack it beside the running program, then hand the swap to a tiny batch file, because Windows will not let
   a running executable — or the folder holding it — be replaced. The batch waits for this process to end,
   moves the old folder aside, moves the new one in, keeps the user's `config.json`, and starts the program.

Everything here is usable without the window (and is what the tests drive); `gui.py` only adds the dialogs.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from . import __version__

EXE_NAME = "TTSkyAnnot.exe"
FEED_NAME = "version.json"
LOCAL = ("", "local")


class UpdateError(Exception):
    """Something went wrong on the way to a new build; the message is meant for the user."""


# ───────────────────────────── versions and the feed ─────────────────────────────
def parse_version(text: str):
    """(major, minor, patch) from "1.2.3"; any suffix ("1.2.3-beta") is ignored."""
    parts = []
    for piece in str(text).strip().lstrip("vV").split("."):
        digits = "".join(c for c in piece if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
        if len(parts) == 3:
            break
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def _is_url(base: str) -> bool:
    return base.lower().startswith(("http://", "https://"))


def _local_path(where: str) -> str:
    """A plain folder path or a file:// URL as a filesystem path."""
    if where.lower().startswith("file://"):
        import urllib.request
        return urllib.request.url2pathname(where[7:])
    return where


def join(base: str, name: str) -> str:
    """`name` addressed under `base`, whether base is a URL, a file:// URL, a folder or a UNC share."""
    base = (base or "").strip()
    if not base:
        raise UpdateError("no update source is configured")
    if _is_url(base):
        return base.rstrip("/") + "/" + name.lstrip("/")
    return os.path.join(_local_path(base), name)


def _read(where: str, timeout: float = 15.0) -> bytes:
    if _is_url(where):
        req = urllib.request.Request(where, headers={"User-Agent": "TTSkyAnnot/%s" % __version__})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            raise UpdateError("could not reach %s: %s" % (where, e))
    path = _local_path(where)
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise UpdateError("could not read %s: %s" % (path, e))


def fetch_feed(base: str, timeout: float = 15.0) -> dict:
    """The published `version.json`, checked just enough to be usable."""
    raw = _read(join(base, FEED_NAME), timeout=timeout)
    try:
        feed = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise UpdateError("the update feed is not valid JSON: %s" % e)
    if not isinstance(feed, dict) or not feed.get("version") or not feed.get("url"):
        raise UpdateError("the update feed is missing 'version' or 'url'")
    return feed


# ───────────────────────────── download and unpack ─────────────────────────────
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def package_url(base: str, feed: dict) -> str:
    """The package address: as published, or relative to the feed."""
    url = str(feed.get("url") or "").strip()
    if _is_url(url) or url.lower().startswith("file://") or os.path.isabs(url):
        return url
    return join(base, url)


def download(base: str, feed: dict, folder: str, progress=None, should_stop=None,
             timeout: float = 60.0) -> str:
    """Fetch the package into `folder`, check its SHA-256, and return the file's path.

    `progress(done, total)` is called as the bytes arrive; `should_stop()` is asked between chunks so the
    window can cancel a slow download.
    """
    url = package_url(base, feed)
    name = os.path.basename(_local_path(url)) or "package.zip"
    dest = os.path.join(folder, name)
    expect = str(feed.get("sha256") or "").strip().lower()
    total = int(feed.get("size") or 0)
    h = hashlib.sha256()
    done = 0
    t0 = time.time()
    try:
        if _is_url(url) or url.lower().startswith("file://"):
            if _is_url(url):
                req = urllib.request.Request(url, headers={"User-Agent": "TTSkyAnnot/%s" % __version__})
                src = urllib.request.urlopen(req, timeout=timeout)
            else:
                src = open(_local_path(url), "rb")
        else:
            src = open(url, "rb")
    except Exception as e:
        raise UpdateError("could not open the package %s: %s" % (url, e))
    try:
        with open(dest, "wb") as out:
            while True:
                if should_stop is not None and should_stop():
                    raise UpdateError("cancelled")
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
                if total and done > total * 2 + (1 << 20):
                    raise UpdateError("the download is larger than the feed says; stopping")
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError("download failed: %s" % e)
    finally:
        try:
            src.close()
        except Exception:
            pass
    if expect and h.hexdigest().lower() != expect:
        raise UpdateError("the downloaded package does not match its SHA-256 (expected %s, got %s)"
                          % (expect[:12], h.hexdigest()[:12]))
    if total and done < total:
        raise UpdateError("the download ended early (%d of %d bytes)" % (done, total))
    return dest


def unpack(zip_path: str) -> str:
    """Extract the package and return the folder that holds `TTSkyAnnot.exe`.

    The zip made by `packaging/publish.ps1` holds one `TTSkyAnnot` folder; a hand-made one may hold the files
    at its root. Both are found by looking for the executable instead of trusting the layout.
    """
    stage = tempfile.mkdtemp(prefix="ttskyannot-update-")
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(stage)
    except (OSError, zipfile.BadZipFile) as e:
        shutil.rmtree(stage, ignore_errors=True)
        raise UpdateError("the package could not be unpacked: %s" % e)
    for root, _dirs, files in os.walk(stage):
        if EXE_NAME in files:
            return root
    shutil.rmtree(stage, ignore_errors=True)
    raise UpdateError("the package holds no %s" % EXE_NAME)


# ───────────────────────────── the swap ─────────────────────────────
def app_dir() -> str:
    """The folder the packaged program lives in; "" when running from source."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return ""


def cleanup_old(target: str) -> None:
    """Remove the previous install a swap left behind (`<folder>.old`), if it is still there."""
    if not target:
        return
    old = target.rstrip("\\/") + ".old"
    if os.path.isdir(old):
        shutil.rmtree(old, ignore_errors=True)


def install_script(new_dir: str, target: str, pid: int, exe: str = EXE_NAME) -> str:
    """Write the batch file that performs the swap once this process has ended, and return its path.

    It is written next to the temporary download (in %TEMP%), never inside the folder being replaced, so the
    batch can move that folder: Windows refuses to rename a directory while a running program sits in it.
    """
    target = os.path.abspath(target).rstrip("\\/")
    new_dir = os.path.abspath(new_dir).rstrip("\\/")
    old = target + ".old"
    log = os.path.join(os.path.dirname(new_dir), "update.log")
    probe = os.path.join(os.path.dirname(new_dir), "update-probe.txt")
    cmd = os.path.join(os.path.dirname(new_dir), "apply-update.cmd")
    body = r'''@echo off
setlocal enableextensions
rem A process cannot rename the folder it is working in, and the program's own working directory may well be
rem its install folder (Explorer launches it that way). Step outside before touching anything.
cd /d "%SystemRoot%" 2>nul
echo [%DATE% %TIME%] waiting for pid {pid} > "{log}"
:wait
tasklist /FI "PID eq {pid}" /NH > "{probe}" 2>nul
find /I "{exe}" "{probe}" >nul 2>&1
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
ping -n 3 127.0.0.1 >nul
echo [%DATE% %TIME%] swapping in {new} >> "{log}"
set /a tries=0
:tryswap
if exist "{old}" rmdir /s /q "{old}" >> "{log}" 2>&1
move "{target}" "{old}" >> "{log}" 2>&1
if not exist "{target}" goto swapped
set /a tries+=1
if %tries% geq 45 (
  echo [%DATE% %TIME%] could not move the old install aside after %tries% tries; starting it again >> "{log}"
  start "" "{target}\{exe}"
  goto done
)
ping -n 2 127.0.0.1 >nul
goto tryswap
:swapped
move "{new}" "{target}" >> "{log}" 2>&1
if errorlevel 1 goto restore
if not exist "{target}\{exe}" goto restore
if exist "{old}\config.json" copy /y "{old}\config.json" "{target}\config.json" >> "{log}" 2>&1
echo [%DATE% %TIME%] starting >> "{log}"
start "" "{target}\{exe}"
goto done
:restore
echo [%DATE% %TIME%] putting the old install back >> "{log}"
if exist "{old}" move "{old}" "{target}" >> "{log}" 2>&1
:done
del "{probe}" >nul 2>&1
del "%~f0"
'''.format(pid=pid, target=target, new=new_dir, old=old, exe=exe, log=log, probe=probe)
    with open(cmd, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(body)
    return cmd


def install_and_restart(new_dir: str, target: str, pid: int, exe: str = EXE_NAME) -> str:
    """Launch the swap batch hidden, so the caller can quit the program and let it run."""
    if not os.path.isfile(os.path.join(new_dir, exe)):
        raise UpdateError("%s is not in the unpacked package" % exe)
    if os.path.abspath(new_dir) == os.path.abspath(target):
        raise UpdateError("the package was unpacked over the running install")
    cmd = install_script(new_dir, target, pid, exe=exe)
    # CREATE_NO_WINDOW only: adding DETACHED_PROCESS gives the batch a stray console and leaves any pipeline
    # in it waiting for input that never comes. The std handles go to the null device instead.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    devnull = subprocess.DEVNULL
    try:
        subprocess.Popen(["cmd", "/c", cmd], close_fds=True, creationflags=flags,
                         cwd=os.path.dirname(os.path.abspath(new_dir)),
                         stdin=devnull, stdout=devnull, stderr=devnull)
    except OSError as e:
        raise UpdateError("could not start the update: %s" % e)
    try:
        from . import config
        config._diag("update: launched %s (target %s)" % (cmd, target))
    except Exception:
        pass
    return cmd
