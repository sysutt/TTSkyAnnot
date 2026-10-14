"""User settings, stored as JSON in %APPDATA%\\TTSkyAnnot\\config.json (or ~/.config/TTSkyAnnot)."""
from __future__ import annotations

import json
import os

DEFAULTS = {
    "language": "",            # "" = follow the system, "zh" or "en"
    "nova_api_key": "",
    "astap_path": "",
    "gaia_mag": 16.0,
    "write_txt": True,
    "write_preview": True,
    "write_outlines": True,
    "write_wcs": True,
    "out_dir": "",             # "" = next to the image
    "stretch": "auto",         # auto | linked | unlinked | none
    "show_overlay": True,
    "last_dir": "",
}


def _path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    d = os.path.join(base, "TTSkyAnnot")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "config.json")


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump({k: cfg.get(k, v) for k, v in DEFAULTS.items()}, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
