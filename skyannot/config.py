"""User settings, as JSON beside the program (a portable copy) or in %APPDATA%\\TTSkyAnnot (a read-only install).

Why beside the program first: this is a self-contained one-folder build. Keeping its settings in its own folder
means the folder can be moved or copied to another machine with everything intact, and a copy in a writable
place never depends on the user profile. When the folder is read-only — an install under Program Files, for
instance — the settings go to %APPDATA% instead, where every user gets their own.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time


def _diag(msg: str) -> None:
    """A line about where the settings went, in %TEMP%\\ttskyannot-diag.log.

    The window has no console, so when a setting does not stick there is nothing to look at otherwise. TEMP is
    used rather than the config folder so a path that cannot be written still leaves a record.
    """
    try:
        path = os.path.join(tempfile.gettempdir(), "ttskyannot-diag.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass

DEFAULTS = {
    "language": "",            # "" = follow the system, "zh" or "en"
    "theme": "dark",           # dark | graphite | night, see gui.THEMES
    "nova_api_key": "",
    "astap_path": "",
    "pixinsight_path": "",    # "" = look for PixInsight in the usual install location
    "gaia_mag": 16.0,
    "tickwhale_base": "https://app.tickwhale.com",   # "" turns the online catalogue search off
    "cats": ["hip", "tyc", "gaia"],   # star catalogues to annotate from, see vizier.CATALOGS
    "write_txt": True,
    "write_preview": True,
    "write_outlines": True,
    "write_wcs": True,
    "out_dir": "",             # "" = next to the image
    "api_key": "",                                   # online solve key (ttastro); empty = anonymous allowance
    "update_url": "https://www.ttastro.com/ttskyannot/updates/",
                               # where version.json lives; "" turns the startup check off
    "skipped_version": "",     # a version the user asked not to be told about again
    "ask_before_save": True,
    "confirm_delete": True,    # ask before sending a frame to the Recycle Bin
    "debayer": True,           # interpolate a Bayer mosaic (one-shot colour) before showing it
    "use_existing": True,      # reuse a solution the frame's own header already carries
    "bayer_pattern": "auto",   # "auto" = use the header's BAYERPAT; otherwise one of debayer.PATTERNS   # solving writes nothing until the user agrees in the save dialog
    "stretch": "auto",         # auto | linked | unlinked | none
    "show_overlay": True,
    "show_image_bar": True,    # the bar of screening controls along the bottom of the picture
    "groups": None,            # deep-sky catalogues annotated (None = all bundled), see catalogue.CATALOGUES
    "shape_style": "outline",  # "outline" (OpenNGC/Stellarium outlines) or "circle" (catalogue size)
    "slide_interval": 2.0,     # seconds each frame stays on screen during automatic playback
    "last_dir": "",
}


def _app_dir() -> str:
    """The folder the program lives in, for a packaged build; "" when running from source (tests, development)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return ""


def _writable(folder: str) -> bool:
    try:
        probe = os.path.join(folder, ".ttskyannot-write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def _appdata_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return os.path.join(base, "TTSkyAnnot", "config.json")


def _path() -> str:
    """The settings file: beside the program when that folder is writable, otherwise in %APPDATA%.

    `TTSKYANNOT_CONFIG` overrides both, which is what the tests and a portable launcher use.
    """
    override = os.environ.get("TTSKYANNOT_CONFIG")
    if override:
        return os.path.abspath(override)
    app = _app_dir()
    if app:
        portable = os.path.join(app, "config.json")
        if os.path.exists(portable) or _writable(app):
            return portable
    path = _appdata_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def bring_settings_over() -> None:
    """First run of a portable copy: take the settings an older build left in %APPDATA% along with it."""
    path = _path()
    if os.path.exists(path):
        return
    old = _appdata_path()
    if os.path.dirname(path) == os.path.dirname(old) or not os.path.exists(old):
        return
    try:
        shutil.copyfile(old, path)
        _diag("migrated %s -> %s" % (old, path))
    except OSError as exc:
        _diag("migration failed %s -> %s: %r" % (old, path, exc))


_diag("config module loaded; APPDATA=%r" % os.environ.get("APPDATA"))


def load() -> dict:
    bring_settings_over()                  # a packaged copy picks up settings left in %APPDATA% on first run
    cfg = dict(DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError) as exc:
        _diag("load failed %s: %r" % (_path(), exc))
    else:
        _diag("load %s theme=%s" % (_path(), cfg.get("theme")))
    return cfg


def save(cfg: dict) -> bool:
    """Write the settings, and say whether they landed.

    Written to a temporary file and swapped in, so a reader never sees half a file, and retried: a scanner or a
    sync client holding the file for a moment used to eat the write (and with it the colour scheme the user had
    just picked). Returns False — after logging why — so the caller can tell the user instead of failing
    silently.
    """
    path = _path()
    data = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    tmp = path + ".tmp"
    last = None
    for attempt in range(5):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as exc:
            last = exc
            time.sleep(0.1 * (attempt + 1))
            continue
        _diag("saved %s theme=%s" % (path, data.get("theme")))
        return True
    _diag("save failed %s: %r" % (path, last))
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False
