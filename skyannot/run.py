"""One image from start to finish: load, detect stars, solve, refine, annotate, write outputs."""
from __future__ import annotations

import datetime
import json
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np

from . import annotate, grid, imageio, refine, render, shapes, solve, starfind, vizier, wcsio


@dataclass
class Options:
    target: str = ""                 # optional hint: object name, e.g. "M31"
    solvers: tuple = ("ttastro", "astap", "nova")
    astap_path: str = ""
    nova_key: str = ""
    gaia_mag: float = 16.0
    gaia_max_rows: int = 200000      # brightest-first cap for very wide fields (the text keeps parallaxes)
    write_txt: bool = True
    write_preview: bool = True
    write_outlines: bool = True
    write_wcs: bool = True
    out_dir: str = ""                # empty = next to the image
    stretch: str = "auto"            # display stretch for star detection and the preview (imageio.display_stretch)
    categories: tuple = ("galaxy", "nebula", "cluster", "dark", "other")   # drawn in the preview
    named: bool = True               # named-star labels in the preview
    grid: bool = True                # RA/Dec grid in the preview


@dataclass
class Result:
    image: str
    ok: bool = False
    error: str = ""
    wcs: object = None
    stats: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    shapes: list = field(default_factory=list)
    named: list = field(default_factory=list)
    grid: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _epoch(date_text: str | None, path: str) -> float:
    """Observation epoch as a decimal year: DATE-OBS when present, otherwise the file's modification time."""
    if date_text:
        try:
            d = datetime.datetime.fromisoformat(date_text.strip().replace("Z", "")[:26])
            y0 = datetime.datetime(d.year, 1, 1)
            return d.year + (d - y0).total_seconds() / (365.25 * 86400)
        except ValueError:
            pass
    t = time.localtime(os.path.getmtime(path))
    return t.tm_year + (t.tm_yday - 1) / 365.25


def _reference_stars(wcs0, width, height, epoch, gaia_mag, max_rows, log):
    """One Gaia query serves both the refinement (deeper for small fields) and the annotation text.
    Very wide fields are capped brightest-first at max_rows. Returns (ra, dec, mag, rows, faintest_if_capped)."""
    ra0, de0, rad, area = annotate.field_geometry(wcs0, width, height)
    lim = max(gaia_mag, refine.reference_mag_limit(area))
    rows = vizier.cone("gaia", ra0, de0, rad, mag_max=lim, max_rows=max_rows, log=log)
    f = lambda k: np.array([float(r[k]) if r[k] else np.nan for r in rows], float)  # noqa: E731
    ra, de = refine.propagate(f("RA_ICRS"), f("DE_ICRS"), f("pmRA"), f("pmDE"), 2016.0, epoch)
    gm = f("Gmag")
    capped = float(np.nanmax(gm)) if len(rows) >= max_rows and len(gm) else None
    return ra, de, gm, rows, capped


def local_overlay(img, wcs):
    """Deep-sky shapes, named stars and the RA/Dec grid for an image that already carries a solution
    (bundled catalogues only, no network)."""
    P = annotate.Projector(wcs, img.width, img.height)
    return shapes.collect(P), annotate.named_local(P), grid.compute(wcs, img.width, img.height)


def process(path: str, opt: Options, log=print, progress=lambda p, msg: None) -> Result:
    res = Result(image=path)
    t0 = time.time()
    try:
        progress(0.02, "load")
        img = imageio.load(path)
        disp = imageio.display_stretch(img, opt.stretch)
        hint = imageio.hints(img)
        if opt.target:
            hint["target"] = opt.target.strip()
        epoch = _epoch(hint.get("date"), path)
        log("%s: %d x %d %s%s" % (os.path.basename(path), img.width, img.height, img.fmt.upper(),
                                  ", linear data (auto-stretched)" if img.linear else ""))
        progress(0.08, "stars")
        st = starfind.find_stars(disp, max_stars=3000)
        log("  %d stars detected" % len(st))
        if len(st) < 8:
            raise RuntimeError("too few stars detected (%d)" % len(st))
        det = np.array([[s[0], s[1]] for s in st])

        wcs = stats = None
        gaia_rows = gaia_capped = None
        errors = []
        for name in opt.solvers:
            progress(0.15, "solve:" + name)
            try:
                if name == "ttastro":
                    w0, info = solve.solve_ttastro(st, img.width, img.height, hint, log=log)
                elif name == "astap":
                    if not solve.find_astap(opt.astap_path):
                        continue
                    w0, info = solve.solve_astap(disp, hint, exe=opt.astap_path or None, log=log)
                elif name == "nova":
                    if not opt.nova_key:
                        continue
                    w0, info = solve.solve_nova(disp, opt.nova_key, log=log)
                else:
                    continue
                log("  solved by %s" % name)
                progress(0.40, "refine")
                ra, de, mag, gaia_rows, gaia_capped = _reference_stars(w0, img.width, img.height, epoch,
                                                                       opt.gaia_mag, opt.gaia_max_rows, log)
                wcs, stats = refine.refine(w0, det, img.width, img.height, ra, de, mag, log=log)
                stats["solver"] = name
                break
            except Exception as e:                     # wrong or failed solve -> next solver
                errors.append("%s: %s" % (name, e))
                log("  %s failed: %s" % (name, e))
        if wcs is None:
            raise RuntimeError("could not solve the image (" + "; ".join(errors or ["no solver available"]) + ")")
        res.wcs, res.stats = wcs, stats
        ra0, de0, rad, area = annotate.field_geometry(wcs, img.width, img.height)
        stats.update(center_ra=ra0, center_dec=de0, fov_area_deg2=area, epoch=round(epoch, 3))

        progress(0.60, "catalogues")
        P = annotate.Projector(wcs, img.width, img.height)
        blocks = annotate.dso_blocks(P)
        sblocks, notes = annotate.star_blocks(P, ra0, de0, rad, area, epoch, opt.gaia_mag,
                                              gaia_rows=gaia_rows, log=log)
        if gaia_capped is not None:
            notes.append("Gaia DR3 limited to the brightest %d stars of this wide field (down to G %.2f)"
                         % (opt.gaia_max_rows, gaia_capped))
        blocks += sblocks
        res.notes = notes
        for n in notes:
            log("  note: " + n)
        res.named = next((b.rows for b in blocks if b.name == "NamedStars"), [])
        res.shapes = shapes.collect(P)
        res.grid = grid.compute(wcs, img.width, img.height)
        stats["objects"] = {b.name: len(b.rows) for b in blocks}
        log("  " + ", ".join("%s %d" % kv for kv in stats["objects"].items()))

        out_dir = opt.out_dir or os.path.dirname(os.path.abspath(path))
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0])
        progress(0.80, "write")
        if opt.write_txt:
            p = stem + ".objects.txt"
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(annotate.objects_text(blocks))
            res.outputs["txt"] = p
        if opt.write_outlines:
            p = stem + ".outlines.json"
            doc = {"image": os.path.basename(path), "width": img.width, "height": img.height,
                   "pixel_convention": "PixInsight: centre of the first pixel is (0.5, 0.5), y grows downwards",
                   "solution": {k: v for k, v in stats.items() if not isinstance(v, dict)},
                   "objects": res.shapes}
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=1)
            res.outputs["outlines"] = p
        if opt.write_preview:
            p = stem + ".annotated.jpg"
            render.render(disp, res.shapes, res.named if opt.named else None, p,
                          grid_lines=res.grid if opt.grid else None, categories=set(opt.categories))
            res.outputs["preview"] = p
        if opt.write_wcs:
            p, how = wcsio.write_back(img, wcs, "%s + Gaia DR3 refinement" % stats.get("solver", ""), opt.out_dir)
            res.outputs["wcs"] = p
            log("  solution written back: " + how)
        res.ok = True
        log("  done in %.0f s" % (time.time() - t0))
        progress(1.0, "done")
    except Exception as e:
        res.error = str(e)
        log("  ERROR: %s" % e)
    return res
