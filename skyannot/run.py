"""One image from start to finish: load, detect stars, solve, refine, annotate, write outputs."""
from __future__ import annotations

import datetime
import functools
import json
import math
import os
import re
import time
from dataclasses import dataclass, field

import numpy as np

from . import annotate, catalogs, catalogue, grid, imageio, refine, render, shapes, skyregion, starfind, vizier, wcsio
# the solver module is bound as `solver`: this file also defines a public function called solve(), and the two
# names collided — inside solve() the name referred to the function, so every solver lookup died with an
# AttributeError that the loop recorded as "ttastro/nova failed" and quietly moved on from
from . import solve as solver


@dataclass
class Options:
    target: str = ""                 # optional hint: object name, e.g. "M31"
    target_radec: tuple = ()         # its catalogued position, when the user picked one from the database
    target_object: dict = None       # the online record behind the target (name, position, size, type)
    remote_fallback: bool = False    # the window turns this on: upload an 800 px thumbnail when all solvers fail
    annotation_style: str = "science"  # overlay/annotated-image theme
    solve_image_url: str = ""        # defaults to the ttastro direct endpoint
    solve_result_url: str = ""
    guess_target_from_name: bool = True   # read the target out of the file/folder name when there is no hint
    tickwhale_base: str = ""         # the online database, for the region query that fills a field
    solvers: tuple = ("ttastro", "astap")
    astap_path: str = ""
    api_key: str = ""
    astrometry_key: str = ""      # 用户自己的 astrometry.net key（可选，服务端优先用）                # ttastro online solve key (empty = anonymous allowance)
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
    sky: object = None                           # nightscape sky/foreground grid (skyregion.build), None = all sky
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

    The refinement needs a few hundred stars, not a few hundred thousand: a very wide field is capped
    brightest-first so a 3-degree frame does not pull 200 000 rows over VizieR before it can be annotated.
    Returns (ra, dec, mag, rows, faintest_if_capped, cap)."""
    ra0, de0, rad, area = annotate.field_geometry(wcs0, width, height)
    lim = max(gaia_mag, refine.reference_mag_limit(area))
    cap = max_rows
    if area > 3.0:
        cap = min(cap, 30000)
    elif area > 1.0:
        cap = min(cap, 60000)
    # The online service has the same Gaia DR3 G<16 index and answers in about a second; VizieR is the
    # fallback for when the service is unreachable, not the normal path.
    rows = []
    cone = solver.fetch_stars(ra0, de0, rad, mag=min(16.0, lim), limit=cap, log=log)
    if cone:
        rows = [{"RA_ICRS": "%.6f" % a, "DE_ICRS": "%.6f" % d, "Gmag": "%.3f" % m, "pmRA": "0", "pmDE": "0",
                 "BPmag": "", "RPmag": "", "Plx": "", "Source": ""}
                for a, d, m in cone]
        log("  reference stars: %d from the online star index" % len(rows))
    else:
        rows = vizier.cone("gaia", ra0, de0, rad, mag_max=lim, max_rows=cap, log=log)
    f = lambda k: np.array([float(r[k]) if r[k] else np.nan for r in rows], float)  # noqa: E731
    ra, de = refine.propagate(f("RA_ICRS"), f("DE_ICRS"), f("pmRA"), f("pmDE"), 2016.0, epoch)
    gm = f("Gmag")
    capped = float(np.nanmax(gm)) if len(rows) >= cap and len(gm) else None
    return ra, de, gm, rows, capped, cap


def online_records_cone(ra, dec, radius, cats, tickwhale_base):
    """The online objects inside a cone, for the catalogues that are switched on (empty without a base)."""
    on = [k for k in (cats or ()) if k in catalogue.ONLINE]
    if not on or not tickwhale_base:
        return []
    from . import tickwhale
    try:
        recs = tickwhale.region(tickwhale_base, ra, dec, radius, catalogs=on)
    except Exception:
        return []
    return [r for r in recs if catalogue.catalogue_of(r) in catalogue.ONLINE]


def online_records(P, cats, tickwhale_base):
    """The online objects inside the field of a projector (see online_records_cone)."""
    return online_records_cone(P.ra0, P.de0, P.rad, cats, tickwhale_base)


_DSO_TOKEN = re.compile(r"(?<![A-Za-z])(M|NGC|IC|SH2|SH-|LBN|LDN|VDB|CED|RCW)\s*-?\s*(\d{1,4}[A-Za-z]?)(?![0-9A-Za-z])",
                         re.IGNORECASE)


def _normalise_token(prefix: str, number: str) -> str:
    prefix = prefix.upper()
    number = number.upper()
    if prefix in ("SH2", "SH-"):
        return "SH2-" + number
    if prefix == "M":
        return "M" + number
    return prefix + number


@functools.lru_cache(maxsize=1)
def _local_aliases():
    """Every name a bundled object has, lower-cased and without spaces, for exact lookups."""
    out = {}
    for keys, d in catalogs.searchable():
        for alias in str(keys).split("|"):
            alias = alias.strip()
            if alias:
                out.setdefault(alias.replace(" ", "").lower(), d)
    return out


def guess_target(path: str, tickwhale_base: str = "") -> tuple:
    """The object named by a file or its folder: (name, (ra, dec), online record or None), or ().

    Astro files are named after what they show — "Light_M 31_300s...", "261007-261020_2600mm_LBN777" — and a
    solve with a position hint takes seconds where a blind one of a wide field can time out. Only exact
    catalogue designations are accepted, checked against the bundled catalogues first and then the online one;
    a name that resolves to nothing is ignored, so the solve stays blind instead of being pointed at the wrong
    object.
    """
    names = []
    stem = os.path.splitext(os.path.basename(path))[0]
    names.append(stem)
    folder = os.path.dirname(os.path.abspath(path))
    for _ in range(2):                                 # the folder and its parent: targets are often there
        if not folder:
            break
        names.append(os.path.basename(folder))
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    candidates = []
    for text in names:
        for prefix, number in _DSO_TOKEN.findall(text or ""):
            token = _normalise_token(prefix, number)
            if token not in candidates:
                candidates.append(token)
    if not candidates:
        return ()
    local = _local_aliases()
    for token in candidates:
        d = local.get(token.replace(" ", "").lower())
        if d is not None:
            return (catalogs.label_for(d), (float(d.ra), float(d.dec)), None)
    if tickwhale_base:
        from . import tickwhale
        for token in candidates[:3]:
            try:
                recs = tickwhale.search(token, tickwhale_base, limit=3)
            except Exception:
                recs = []
            for rec in recs:
                if str(rec.get("name", "")).upper().replace(" ", "") == token:
                    return (rec["name"], (float(rec["ra"]), float(rec["dec"])), rec)
    return ()


def _designation_keys(shape) -> set:
    """Every name a shape can be recognised by, so a local M76 and an online "NGC 650" count as one object."""
    out = set()
    for field in ("name", "label"):
        raw = str(shape.get(field) or "")
        for part in raw.replace("(", " ").replace(")", " ").split():
            k = part.replace(" ", "").upper()
            if k:
                out.add(k)
    return out


def local_overlay_with_online(img, wcs, cats=None, target=None, tickwhale_base=""):
    """Like local_overlay, plus the online records the region query returns (for the overlay and a re-filter)."""
    P = annotate.Projector(wcs, img.width, img.height)
    found = shapes.collect(P, cats=cats)
    # 本地已经有了的对象不再加一遍：同一目标同时来自 OpenNGC 与在线库时，画面上只能出现一个标注
    seen = set()
    for e in found:
        seen.add(_designation_keys(e))
    recs = online_records(P, cats, tickwhale_base) if tickwhale_base else []
    for rec in recs:
        key = catalogue.catalogue_of(rec)
        extra = shapes.online_shape(P, rec, key) if key else None
        if extra is None:
            continue
        dup = _designation_keys(extra)
        if dup & seen:
            continue                      # 本地已有同一目标（可能编号不同），跳过在线那份
        seen |= dup
        found.append(extra)
    if target:
        key = catalogue.catalogue_of(target)
        extra = shapes.online_shape(P, target, key) if key else None
        if extra is not None and not (_designation_keys(extra) & seen):
            seen |= _designation_keys(extra)
            found.append(extra)
    return found, annotate.named_local(P), grid.compute(wcs, img.width, img.height), recs


def local_overlay(img, wcs, cats=None, target=None):
    """Deep-sky shapes, named stars and the RA/Dec grid for an image that already carries a solution."""
    found, named, grid_, _recs = local_overlay_with_online(img, wcs, cats, target)
    return found, named, grid_


def process(path: str, opt: Options, log=print, progress=lambda p, msg: None) -> Result:
    """Solve, annotate and write, in one go: what the console entry point uses."""
    img, res, disp = solve(path, opt, log=log, progress=progress)
    if res.ok:
        progress(0.80, "write")
        write_outputs(img, res, opt, disp, log=log)
    return res


def preview_path(path, style="science", out_dir=None):
    """The annotated picture for this frame and style. The style goes into the name, so switching styles
    adds a file instead of overwriting the previous one; out_dir is where the save was pointed at."""
    name = os.path.splitext(os.path.basename(path))[0]
    base = out_dir or os.path.dirname(os.path.abspath(path))
    key = (style or "science").strip() or "science"
    return os.path.join(base, name + (".annotated.jpg" if key == "science"
                                      else ".annotated.%s.jpg" % key))


def output_paths(path: str, opt: Options) -> list:
    """The files a save would write for this image, in the order they are produced. Empty when every output
    is switched off. Used to tell the user exactly what is about to land in the folder."""
    out_dir = opt.out_dir or os.path.dirname(os.path.abspath(path))
    stem = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0])
    style = getattr(opt, "annotation_style", "science") or "science"
    prev = ".annotated.jpg" if style == "science" else ".annotated.%s.jpg" % style
    out = [stem + ext for on, ext in ((opt.write_txt, ".objects.txt"),
                                      (opt.write_outlines, ".outlines.json"),
                                      (opt.write_preview, prev)) if on]
    if opt.write_wcs:
        # a format with no header of its own gets a sidecar; everything else is written into the file itself
        out.append(stem + ".wcs" if imageio.needs_sidecar(path) else path)
    return out


def write_preview(img, res, opt, display_rgb=None, out_path=None, max_edge=4000):
    """Render only the annotated picture — the way back after switching the style, without re-writing the
    lists and outlines that have not changed."""
    p = out_path or preview_path(res.image, getattr(opt, "annotation_style", "science"),
                                 (getattr(opt, "out_dir", "") or "").strip() or None)
    ctx = {"wcs": res.wcs, "full_w": img.width, "full_h": img.height,
           "rotation": res.stats.get("rotation"), "target": opt.target,
           "matched": res.stats.get("matched"), "date": __import__("time").strftime("%Y-%m-%d")}
    try:
        cd = res.wcs.wcs.cd
        ctx["pixscale"] = float(abs(cd[0][0] * cd[1][1] - cd[0][1] * cd[1][0]) ** 0.5) * 3600.0
    except Exception:
        pass
    render.render(display_rgb if display_rgb is not None else imageio.display_stretch(img, opt.stretch),
                  res.shapes, res.named if opt.named else None, p,
                  grid_lines=res.grid if opt.grid else None, categories=set(opt.categories),
                  shape_style=opt.shape_style, theme_name=opt.annotation_style, ctx=ctx, max_edge=max_edge)
    return p


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
        p = write_preview(img, res, opt, display_rgb)
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


def solve(image_path: str, opt: Options, log=print, progress=lambda p, msg: None, should_stop=None):
    """Everything up to (but not including) writing: load, detect, solve, refine, project catalogues.
    Returns (LoadedImage or None, Result, display-ready array or None) and touches no user file.

    The solver result is kept in `w0`, never in a name that shadows the `solve` module imported above: a local
    called `solve` here used to mask it, so the ASTAP branch died with an AttributeError that was recorded as
    "astap failed" and silently skipped.
    """
    res = Result(image=image_path)
    t0 = time.time()
    img = disp = None

    def _check():
        """Called between stages: a cancelled run stops before it spends another minute solving."""
        if should_stop is not None and should_stop():
            raise RuntimeError("已取消")

    try:
        _check()
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
        elif opt.guess_target_from_name and hint.get("ra") is None:
            # no position anywhere — but the file may well be named after what it shows
            guess = guess_target(image_path, opt.tickwhale_base)
            if guess:
                gname, (gra, gdec), grep = guess
                hint["ra"], hint["dec"] = gra, gdec
                hint.setdefault("target", gname)
                log("  target from the file name: %s (RA %.3f Dec %.3f)" % (gname, gra, gdec))
                if grep is not None and not opt.target_object:
                    opt.target_object = grep
        epoch = _epoch(hint.get("date"), image_path)
        log("%s: %d x %d %s%s" % (os.path.basename(image_path), img.width, img.height, img.fmt.upper(),
                                  ", linear data (auto-stretched)" if img.linear else ""))
        _check()
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
        # The solvers are tried at the same time and the first answer wins: ttastro and ASTAP both take
        # seconds, and running them one after the other made every failed attempt cost the full budget.
        # The thumbnail upload is slower and puts load on the service, so it only joins when the fast
        # pair has had a head start.
        import threading
        import time as _time
        winner = {}
        errlock = threading.Lock()

        def _note(name, e):
            with errlock:
                errors.append("%s: %s" % (name, e))
            log("  %s failed: %s" % (name, e))

        def _ttastro():
            try:
                w0, info = solver.solve_ttastro(st, img.width, img.height, hint, log=log, api_key=opt.api_key)
                winner.setdefault("w", (w0, info, "ttastro"))
            except Exception as e:
                _note("ttastro", e)

        def _astap():
            try:
                found = solver.find_astap(opt.astap_path)
                if not found:
                    tried = solver.astap_candidates(opt.astap_path)
                    log("  astap not found - looked at: %s" % ", ".join(tried or ["(no path configured)"]))
                    _note("astap", "not found, looked at %s" % ", ".join(tried)
                          if tried else "astap: no path configured and none installed")
                    return
                w0, info = solver.solve_astap(disp, hint, exe=found, log=log)
                winner.setdefault("w", (w0, info, "astap"))
            except Exception as e:
                _note("astap", e)

        def _remote():
            try:
                w0, rinfo = solver.remote_image(disp, api_key=opt.api_key, astrometry_key=opt.astrometry_key,
                                                log=log, pixscale=hint.get("pixscale"),
                                                image_url=opt.solve_image_url or None,
                                                result_url=opt.solve_result_url or None)
                if w0 is not None:
                    winner.setdefault("w", (w0, rinfo, rinfo.get("solver", "remote")))
            except Exception as e:
                log("  remote image solver failed: %s" % e)

        fast = [n for n in opt.solvers if n in ("ttastro", "astap")]
        threads = []
        names = []
        for name in fast:
            fn = _ttastro if name == "ttastro" else _astap
            th = threading.Thread(target=fn, daemon=True)
            th.start()
            threads.append(th)
            names.append(name)
        t_start = _time.time()
        remote_started = False
        while threads:
            threads = [th for th in threads if th.is_alive()]
            if winner:
                break
            if should_stop is not None and should_stop():
                try:
                    solver.kill_astap()
                except Exception:
                    pass
                raise RuntimeError("已取消")
            if not remote_started and opt.remote_fallback and _time.time() - t_start > 12.0:
                remote_started = True
                log("  fast solvers still running, the image upload joins them")
                th = threading.Thread(target=_remote, daemon=True)
                th.start()
                threads.append(th)
            if not threads and (remote_started or not opt.remote_fallback):
                break
            if not threads and opt.remote_fallback and not remote_started:
                _time.sleep(0.2)
                continue
            _time.sleep(0.2)
        if not winner:
            try:
                solver.kill_astap()
            except Exception:
                pass
        if not winner and opt.remote_fallback and not remote_started:
            # nothing was left running (every fast solver refused immediately): give the upload its turn
            th = threading.Thread(target=_remote, daemon=True)
            th.start()
            th.join()
        if winner:
            # 已经有引擎给出结果，别的解析（ASTAP 最慢，可能还在跑）立刻收手，不占 CPU
            try:
                solver.kill_astap()
            except Exception:
                pass
            w0, info, name = winner["w"]
            log("  solved by %s" % name)
            progress(0.40, "refine")
            try:
                ra, de, mag, gaia_rows, gaia_capped, gaia_cap = _reference_stars(
                    w0, img.width, img.height, epoch, opt.gaia_mag, opt.gaia_max_rows, log)
                wcs, stats = refine.refine(w0, det, img.width, img.height, ra, de, mag, log=log)
            except Exception as cat_err:
                log("  refinement skipped: %s" % cat_err)
                ra, de, mag, gaia_rows, gaia_capped, gaia_cap = None, None, None, [], None, None
                wcs, stats = w0, {"refined": False}
            stats["solver"] = name
            for key in ("rotation", "north_angle", "parity"):
                if info.get(key) is not None:
                    stats[key] = info[key]
        weak = bool(wcs is not None and not stats.get("remote") and (stats.get("matched") or 0) < 12)
        if weak:
            # a solution where only a handful of catalogue stars line up is usually a wrong match (it can
            # even be rotated 180 degrees); it must not be trusted just because the solver returned it
            log("  本地方案只有 %d 颗参考星吻合，改用缩略图接口复核…" % (stats.get("matched") or 0))
        # 超广域/星野图：整幅匹配解不出时，交给服务端分块 + 亮星复核（mode = "wide"）
        if wcs is None:
            log("  广域定位：分块盲解 + 全画幅亮星复核，约需 30~60 秒…")
            progress(0.35, "wide")
            try:
                w0, winfo = solver.solve_wide(st, img.width, img.height, log=log,
                                              pixscale=solver.exif_pixscale(image_path))
                wcs, stats = w0, dict(winfo)
                stats["solver"] = "wide-tiles"
            except Exception as e:
                if "too few stars" not in str(e):
                    log("  wide-field solver failed: %s" % e)
        # The upload already ran as part of the race; a weak solution is left to the caller to reject.
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
                         % (gaia_cap or opt.gaia_max_rows, gaia_capped))
        if stats.get("mode") == "wide":
            notes.append("广域定位：%s 颗亮星复核通过，全画幅拟合残差 %.3f°%s"
                         % (stats.get("bright_stars") or 0, stats.get("rms_deg") or 0,
                            "；地景占半幅，画面中心可能再差 0.3°" if (stats.get("sky_coverage") or 1) < 0.6 else ""))
        blocks += sblocks
        # The online catalogues are filled by a cone query around the solved centre: the frame's own geometry is
        # the best query there is, and the radius covers the corners, so nothing in the picture is missed. The
        # object named in the target field is added when the region query does not already return it (it may
        # sit just outside the radius, or the database may not have answered).
        online_on = [k for k in (opt.dso_cats or ()) if k in catalogue.ONLINE]
        recs = []
        if online_on and opt.tickwhale_base:
            from . import tickwhale
            recs = [r for r in tickwhale.region(opt.tickwhale_base, ra0, de0, rad, catalogs=online_on)
                    if catalogue.catalogue_of(r) in catalogue.ONLINE]
        target_rec = opt.target_object
        if target_rec and catalogue.catalogue_of(target_rec) in catalogue.ONLINE:
            if not any(r.get("name") == target_rec.get("name") for r in recs):
                recs.append(target_rec)
        if recs:
            res.online = recs
            blocks.extend(annotate.online_region_blocks(P, recs, opt.dso_cats))
            log("  online: %d objects inside the field" % len(recs))
        res.notes = notes
        for n in notes:
            log("  note: " + n)
        res.named = next((b.rows for b in blocks if b.name == "NamedStars"), [])
        res.blocks = blocks
        res.shapes = shapes.collect(P, cats=opt.dso_cats)
        on_set = set(opt.dso_cats or catalogue.KEYS)
        for rec in res.online:
            key = catalogue.catalogue_of(rec)
            if key not in on_set:
                continue
            sh = shapes.online_shape(P, rec, key) if key else None
            if sh is not None:
                res.shapes.append(sh)
        res.grid = grid.compute(wcs, img.width, img.height)
        # 星野图：地景盖住的那部分不标注。判据来自画面本身（星点密度 + 纹理，见 skyregion.py），
        # 只在很广的画幅上启用；普通深空图整幅都是天空，掩膜返回 None，什么都不变。
        if area >= 500.0:
            res.sky = skyregion.prepare(det, disp, img.width, img.height)
        if res.sky:
            hidden = 0
            kept = []
            for sh in res.shapes:
                if skyregion.allows(res.sky, sh.get("x"), sh.get("y")):
                    kept.append(sh)
                else:
                    hidden += 1
            res.shapes = kept
            named = []
            for row in res.named:
                if skyregion.allows(res.sky, row.x, row.y):
                    named.append(row)
                else:
                    hidden += 1
            res.named = named
            notes.append("地景占画面的 %.0f%%，其上的标注已隐藏（%d 个）"
                         % (100.0 * (1.0 - skyregion.coverage(res.sky)), hidden))
            log("  sky/foreground: %.0f%% sky, %d annotations hidden"
                % (100.0 * skyregion.coverage(res.sky), hidden))
        stats["objects"] = {b.name: len(b.rows) for b in blocks}
        log("  " + ", ".join("%s %d" % kv for kv in stats["objects"].items()))

        res.ok = True
        log("  done in %.0f s" % (time.time() - t0))
        progress(1.0, "done")
    except Exception as e:
        res.error = str(e)
        log("  ERROR: %s" % e)
    return img, res, disp
