"""Entry point.

    skyannot                       open the window
    skyannot IMAGE [IMAGE...]      open the window with these images (used by "Open with")
    skyannot --annotate IMAGE...   solve and annotate in the console, no window
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ttskyannot", description="Astro image viewer, plate solver and annotator.")
    ap.add_argument("images", nargs="*", help="FITS, XISF, TIFF, JPEG or PNG files")
    ap.add_argument("--annotate", action="store_true", help="process the images in the console (no window)")
    ap.add_argument("--target", default="", help="object name hint, e.g. M31")
    ap.add_argument("--out", default="", help="output folder (default: next to each image)")
    ap.add_argument("--gaia-mag", type=float, default=None, help="Gaia magnitude limit in the text (default 16)")
    ap.add_argument("--no-txt", action="store_true")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--no-outlines", action="store_true")
    ap.add_argument("--no-wcs", action="store_true", help="do not write the solution back to the image")
    ap.add_argument("--shapes", choices=("outline", "circle"), default=None,
                    help="objects in the preview: outlines where available, or circles at the catalogue size")
    a = ap.parse_args(argv)
    if not a.annotate:
        from .gui import main as gui_main
        return gui_main(a.images)
    from . import config, run
    cfg = config.load()
    opt = run.Options(target=a.target, astap_path=cfg.get("astap_path", ""), nova_key=cfg.get("nova_api_key", ""),
                      gaia_mag=a.gaia_mag if a.gaia_mag is not None else float(cfg.get("gaia_mag", 16)),
                      write_txt=not a.no_txt, write_preview=not a.no_preview, write_outlines=not a.no_outlines,
                      write_wcs=not a.no_wcs, out_dir=a.out,
                      shape_style=a.shapes or cfg.get("shape_style", "outline"))
    bad = 0
    for p in a.images:
        res = run.process(p, opt)
        if res.ok:
            for k, v in res.outputs.items():
                print("  %-9s %s" % (k, v))
        else:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
