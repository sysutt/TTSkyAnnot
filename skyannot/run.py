"""One image from start to finish: load, detect stars, solve, refine, annotate, write outputs."""
from __future__ import annotations

import datetime
import json
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np

from . import annotate, catalogue, grid, imageio, refine, render, shapes, starfind, vizier, wcsio
# the solver module is bound as `solver`: this file also defines a public function called solve(), and the two
# names collided — inside solve() the name referred to the function, so every solver lookup died with an
# AttributeError that the loop recorded as "ttastro/nova failed" and quietly moved on from
from . import solve as solver


@dataclass
class Options:
    target: str = ""                 # optional hint: object name, e.g. "M31"
    target_radec: tuple = ()         # its catalogued position, when the user picked one from the database
    target_object: dict = None       # the online record behind the target (name, position, size, type)
    tickwhale_base: str = ""         # the online database, for the region query that fills a field
    solvers: tuple = ("ttastro", "astap", "nova")
    astap_path: str = ""
    nova_key: str = ""
    api_key: str = ""                # ttastro online solve key (empty = anonymous allowance)
    gaia_mag: float = 16.0
    debayer: bool = True             # interpolate a one-shot colour mosaic before solving or annotating
    bayer_pattern: str = "auto"      # "auto" = the header's BAYERPAT, else one of debayer.PATTERNS
    use_existing: bool = True        # reuse a solution the frame's own header already carries
    dso_cats: tuple = ()             # deep-sky catalogues to annotate, in precedence order
                                     # (empty = the bundled local ones, see catalogue.LOCAL)
    gaia_max_rows: int = 200000      # brightest-first cap for very wide fields (the text keeps parallaxes)
    cats: tuple = ("hip", "tyc", "gaia")   # star catalogues to annotate from (vizier.CATALOGS keys)
    write_txt: bool = True
    write_preview: bool = True
    write_outlines: bool = True
    write_wcs: bool = True
    out_dir: str = ""                # empty = next to the image
    stretch: str = "auto"            # display stretch for star detection and the preview (imageio.display_stretch)
    categories: tuple = ("galaxy", "nebula", "cluster", "dark", "other")   # drawn in the preview
    named: bool = True               # named-star labels in the preview
    grid: bool = True                # RA/Dec grid in the preview
    shape_style: str = "outline"     # "outline" or "circle" (catalogue size) in the preview


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
    blocks: list = field(default_factory=list)   # catalogue blocks: what the text file is written from
    online: list = field(default_factory=list)   # online records inside the field (region query)
    saved: bool = False                          # the outputs were written to disk


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


def local_overlay(img, wcs, cats=None, target=None):
    """Deep-sky shapes, named stars and the RA/Dec grid for an image that already carries a solution
    (bundled catalogues only, no network)."""
    P = annotate.Projector(wcs, img.width, img.height)
    found = shapes.collect(P, cats=cats)
    if target:
        key = catalogue.catalogue_of(target)
        extra = shapes.online_shape(P, target, key) if key else None
        if extra is not None:
            found.append(extra)
    return (found, annotate.named_local(P), grid.compute(wcs, img.width, img.height))


def process(path: str, opt: Options, log=print, progress=lambda p, msg: None) -> Result:
    """Solve, annotate and write, in one go: what the console entry point uses."""
    img, res, disp = solve(path, opt, log=log, progress=progress)
    if res.ok:
        progress(0.80, "write")
        write_outputs(img, res, opt, disp, log=log)
    return res


def output_paths(path: str, opt: Options) -> list:
    """The files a save would write for this image, in the order they are produced. Empty when every output
    is switched off. Used to tell the user exactly what is about to land in the folder."""
    out_dir = opt.out_dir or os.path.dirname(os.path.abspath(path))
    stem = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0])
    out = [stem + ext for on, ext in ((opt.write_txt, ".objects.txt"),
                                      (opt.write_outlines, ".outlines.json"),
                                      (opt.write_preview, ".annotated.jpg")) if on]
    if opt.write_wcs:
        # a format with no header of its own gets a sidecar; everything else is written into the file itself
        out.append(stem + ".wcs" if imageio.needs_sidecar(path) else path)
    return out


def write_outputs(img, res: Result, opt: Options, display_rgb=None, log=print) -> dict:
    """Write everything the options ask for. Separate from process() so the caller can ask the user first:
    nothing is written anywhere until this is called."""
    path = res.image
    out_dir = opt.out_dir or os.path.dirname(os.path.abspath(path))
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0])
    if opt.write_txt:
        p = stem + ".objects.txt"
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(annotate.objects_text(res.blocks))
        res.outputs["txt"] = p
    if opt.write_outlines:
        p = stem + ".outlines.json"
        doc = {"image": os.path.basename(path), "width": img.width, "height": img.height,
               "pixel_convention": "PixInsight: centre of the first pixel is (0.5, 0.5), y grows downwards",
               "solution": {k: v for k, v in res.stats.items() if not isinstance(v, dict)},
               "objects": res.shapes}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        res.outputs["outlines"] = p
    if opt.write_preview:
        p = stem + ".annotated.jpg"
        render.render(display_rgb if display_rgb is not None else imageio.display_stretch(img, opt.stretch),
                      res.shapes, res.named if opt.named else None, p,
                      grid_lines=res.grid if opt.grid else None, categories=set(opt.categories),
                      shape_style=opt.shape_style)
        res.outputs["preview"] = p
    if opt.write_wcs:
        try:
            p, how = wcsio.write_back(img, res.wcs, "%s + Gaia DR3 refinement" % res.stats.get("solver", ""),
                                      opt.out_dir)
        except Exception as e:
            # writing the header is the one output that touches the user's own file: a failure here must be
            # reported, never taken for a crash (a broken solution, a read-only file, a locked one, ...)
            log("  could not write the solution back: %s: %s" % (type(e).__name__, e))
        else:
            res.outputs["wcs"] = p
            log("  solution written back: " + how)
    res.saved = True
    return res.outputs


def _existing_check(w0, det, width, height, log=print):
    """(array of distances in px, number within 5 px) for a header solution, or None.

    Bright catalogued stars are projected through the header solution and each one's nearest detection is
    measured. A solution that is wrong cannot fake this: no catalogue star lands on a detection. The count of
    close ones is reported as well as the distances, because a wide field may hold only one or two bright
    stars to judge by — see _trusts_existing for how few is enough.
    """
    try:
        from scipy.spatial import cKDTree
        from . import catalogs
        stars = [d for d in catalogs.named_stars() if getattr(d, "mag", None) is not None and d.mag <= 6.5]
        if len(stars) < 4:
            return None
        ra0, de0, rad, _area = annotate.field_geometry(w0, width, height)
        ra = np.array([d.ra for d in stars])
        de = np.array([d.dec for d in stars])
        cosd = (np.sin(np.radians(de0)) * np.sin(np.radians(de)) +
                np.cos(np.radians(de0)) * np.cos(np.radians(de)) *
                np.cos(np.radians(ra) - np.radians(ra0)))
        near = cosd > np.cos(np.radians(min(2.0 * rad, 80.0)))
        if near.sum() < 1:
            return None
        # quiet=True: the inverse of a SIP solution does not converge for points far outside the frame, and
        # those come back as NaN rather than raising — the same treatment Projector.xy uses
        px, py = w0.all_world2pix(ra[near], de[near], 0, quiet=True)
        good = np.isfinite(px) & np.isfinite(py)
        cat = np.column_stack([px[good], py[good]])
        inside = ((cat[:, 0] >= 0) & (cat[:, 0] <= width) & (cat[:, 1] >= 0) & (cat[:, 1] <= height))
        cat = cat[inside]
        if len(cat) < 1:
            return None
        d, _j = cKDTree(det).query(cat)
        d = d[np.isfinite(d)]
        if d.size < 1:
            return None
        return d, int((d <= 5.0).sum())
    except Exception as e:                     # a check that cannot run must not stop the solve
        log("  (could not check the header solution: %s)" % e)
        return None


def _trusts_existing(check, width, height) -> bool:
    """Whether a header solution is good enough to use instead of solving.

    Two ways to qualify, because a wide field can hold very few bright stars:

    * at least three stars lining up within a tolerance scaled to the frame — a real consensus;
    * or a single star landing under a pixel. That is not a coincidence: the chance of a wrong solution
      putting a catalogue star within a pixel of the right detection is tiny, and on a 3.9-degree field there
      may genuinely be only one star to test with.
    """
    if check is None:
        return False
    d, close = check
    if close < 1:
        return False
    tol = max(2.0, 0.005 * max(width, height))
    if close >= 3 and float(np.median(d[d <= 5.0])) <= tol:
        return True
    return float(np.min(d)) <= 1.0


def solve(image_path: str, opt: Options, log=print, progress=lambda p, msg: None):
    """Everything up to (but not including) writing: load, detect, solve, refine, project catalogues.
    Returns (LoadedImage or None, Result, display-ready array or None) and touches no user file.

    The solver result is kept in `w0`, never in a name that shadows the `solve` module imported above: a local
    called `solve` here used to mask it, so the ASTAP branch died with an AttributeError that was recorded as
    "astap failed" and silently skipped.
    """
    res = Result(image=image_path)
    t0 = time.time()
    img = disp = None
    try:
        progress(0.02, "load")
        img = imageio.load(image_path, want_debayer=opt.debayer,
                           pattern="" if opt.bayer_pattern == "auto" else opt.bayer_pattern)
        disp = imageio.display_stretch(img, opt.stretch)
        hint = imageio.hints(img)
        if opt.target:
            hint["target"] = opt.target.strip()
        if opt.target_radec:
            # the position of the object the user picked wins over the header's pointing: a mount's own
            # coordinates are the target it was sent to, not necessarily where the frame is centred, and the
            # online solver searches around this hint
            hint["ra"], hint["dec"] = float(opt.target_radec[0]), float(opt.target_radec[1])
        epoch = _epoch(hint.get("date"), image_path)
        log("%s: %d x %d %s%s" % (os.path.basename(image_path), img.width, img.height, img.fmt.upper(),
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
        # A frame that already carries an astrometric solution — the ASIAIR, NINA and PixInsight all write one
        # into the header — does not need solving again. The solution is checked against the stars actually in
        # the picture before it is trusted, because a stale or wrong header solution is worse than none.
        if opt.use_existing:
            progress(0.15, "existing")
            w0 = wcsio.read_existing(img)
            if w0 is None:
                log("  no solution in the header to reuse")
            else:
                check = _existing_check(w0, det, img.width, img.height, log=log)
                if check is None:
                    log("  the header's solution could not be checked against the stars; solving instead")
                elif _trusts_existing(check, img.width, img.height):
                    d, close = check
                    log("  using the solution already in the header (%d of the field's bright stars line up, "
                        "closest %.2f px)" % (close, float(np.min(d))))
                    stats = {"solver": "header", "existing_matched": close,
                             "existing_closest_px": round(float(np.min(d)), 3)}
                    wcs = w0
                else:
                    d, close = check
                    log("  the header's solution does not match the stars (%d matched, closest %.1f px); "
                        "solving instead" % (close, float(np.min(d))))
        for name in opt.solvers:
            if wcs is not None:
                break
            progress(0.15, "solve:" + name)
            try:
                if name == "ttastro":
                    w0, info = solver.solve_ttastro(st, img.width, img.height, hint, log=log, api_key=opt.api_key)
                elif name == "astap":
                    found = solver.find_astap(opt.astap_path)
                    if not found:
                        # say so rather than skipping in silence: a path set in the settings that cannot be
                        # used is the most common reason the offline solver never runs
                        tried = solver.astap_candidates(opt.astap_path)
                        log("  astap not found - looked at: %s" % ", ".join(tried or ["(no path configured)"]))
                        errors.append("astap: not found, looked at %s" % ", ".join(tried) if tried
                                      else "astap: no path configured and none installed")
                        continue
                    w0, info = solver.solve_astap(disp, hint, exe=found, log=log)
                elif name == "nova":
                    if not opt.nova_key:
                        continue
                    w0, info = solver.solve_nova(disp, opt.nova_key, log=log)
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
        blocks = annotate.dso_blocks(P, cats=opt.dso_cats)
        sblocks, notes = annotate.star_blocks(P, ra0, de0, rad, area, epoch, opt.gaia_mag,
                                              gaia_rows=gaia_rows, log=log, cats=opt.cats)
        if gaia_capped is not None:
            notes.append("Gaia DR3 limited to the brightest %d stars of this wide field (down to G %.2f)"
                         % (opt.gaia_max_rows, gaia_capped))
        blocks += sblocks
        # The online catalogues are filled by a cone query around the solved centre: the frame's own geometry is
        # the best query there is, and the radius covers the corners, so nothing in the picture is missed.
        online_on = [k for k in (opt.dso_cats or ()) if k in catalogue.ONLINE]
        if online_on and opt.tickwhale_base:
            from . import tickwhale
            recs = tickwhale.region(opt.tickwhale_base, ra0, de0, rad, catalogs=online_on)
            if recs:
                blocks.extend(annotate.online_region_blocks(P, recs, opt.dso_cats))
                res.online = recs
                log("  online: %d objects inside the field" % len(recs))
        if opt.target_object:
            extra = annotate.online_target_block(P, opt.target_object, opt.dso_cats)
            if extra is not None:
                blocks.append(extra)
                log("  %s %s" % (extra.name, str(opt.target_object.get("name"))))
        res.notes = notes
        for n in notes:
            log("  note: " + n)
        res.named = next((b.rows for b in blocks if b.name == "NamedStars"), [])
        res.blocks = blocks
        res.shapes = shapes.collect(P, cats=opt.dso_cats)
        for rec in res.online:
            key = catalogue.catalogue_of(rec)
            sh = shapes.online_shape(P, rec, key) if key else None
            if sh is not None:
                res.shapes.append(sh)
        if opt.target_object:
            key = catalogue.catalogue_of(opt.target_object)
            extra = shapes.online_shape(P, opt.target_object, key) if key else None
            if extra is not None:
                res.shapes.append(extra)
        res.grid = grid.compute(wcs, img.width, img.height)
        stats["objects"] = {b.name: len(b.rows) for b in blocks}
        log("  " + ", ".join("%s %d" % kv for kv in stats["objects"].items()))

        res.ok = True
        log("  done in %.0f s" % (time.time() - t0))
        progress(1.0, "done")
    except Exception as e:
        res.error = str(e)
        log("  ERROR: %s" % e)
    return img, res, disp
