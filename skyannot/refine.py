"""Turn a rough linear solution into an accurate TAN-SIP solution.

Detected stars are matched to Gaia stars (mutual nearest neighbours, shrinking match radius) and a WCS with
up to 3rd-order SIP distortion is fitted. A random fifth of the matches is held out of the fit and used to
report an honest accuracy figure. Measured on a DWARF 3 frame of M38 against 104 held-out stars: rough
solution 1.02 px median error, after refinement 0.32 px (PixInsight's own solution: 0.30 px).

All pixel coordinates here use the index convention (centre of the first pixel = 0).
"""
from __future__ import annotations

import numpy as np


def _match(det_xy, cat_xy, radius):
    """Mutual nearest neighbours within radius. Returns (det_idx, cat_idx)."""
    from scipy.spatial import cKDTree
    if len(det_xy) == 0 or len(cat_xy) == 0:
        return np.array([], int), np.array([], int)
    d, i = cKDTree(det_xy).query(cat_xy, distance_upper_bound=radius)
    ok = np.isfinite(d)
    ci = np.nonzero(ok)[0]
    di = i[ok]
    _d2, back = cKDTree(cat_xy).query(det_xy[di])
    mutual = back == ci
    return di[mutual], ci[mutual]


def _project(wcs, ra, dec):
    x, y = wcs.all_world2pix(ra, dec, 0)
    return np.column_stack([x, y])


def _fit(det_xy, ra, dec, sip_degree):
    from astropy.coordinates import SkyCoord
    from astropy.wcs.utils import fit_wcs_from_points
    import astropy.units as u
    world = SkyCoord(ra * u.deg, dec * u.deg)
    return fit_wcs_from_points((det_xy[:, 0], det_xy[:, 1]), world, proj_point="center",
                               projection="TAN", sip_degree=sip_degree)


def _choose_degree(xy, ra, dec, seed=7, folds=5):
    """Pick None (TAN) / 2 / 3 by k-fold cross-validated median error. Returns (degree, cv_errors)."""
    n = len(xy)
    cands = [None] + ([2] if n >= 20 else []) + ([3] if n >= 40 else [])
    if n < 15:
        return None, None
    fold = np.random.default_rng(seed).permutation(n) % folds
    best = (None, None, np.inf)
    for deg in cands:
        errs = []
        for k in range(folds):
            tr, te = fold != k, fold == k
            try:
                w = _fit(xy[tr], ra[tr], dec[tr], deg)
            except Exception:
                errs = None
                break
            errs.append(np.hypot(*(_project(w, ra[te], dec[te]) - xy[te]).T))
        if errs is None:
            continue
        e = np.concatenate(errs)
        if np.median(e) < best[2] - 1e-3:          # prefer the simpler model on ties
            best = (deg, e, float(np.median(e)))
    return best[0], best[1]


def reference_mag_limit(area_deg2: float) -> float:
    """Gaia depth that gives a few hundred reference stars at least: deeper for small fields."""
    if area_deg2 < 0.08:
        return 18.0
    if area_deg2 < 0.3:
        return 17.0
    return 16.0


def refine(wcs0, det_xy, width, height, cat_ra, cat_dec, cat_mag=None, log=print, seed=7):
    """wcs0: rough solution (FITS convention). det_xy: (N, 2) detected stars, index convention, brightest
    first. cat_ra/cat_dec/cat_mag: reference stars (degrees, at the observation epoch).
    The first pass matches only the brightest few hundred stars on both sides with a generous radius
    (a dense catalogue would otherwise pair wrong neighbours), then every star with a shrinking radius.
    Returns (wcs, stats) or raises RuntimeError when too few stars match (the rough solve is wrong)."""
    det_xy = np.asarray(det_xy, float)
    cat_ra, cat_dec = np.asarray(cat_ra, float), np.asarray(cat_dec, float)
    cat_mag = np.zeros(len(cat_ra)) if cat_mag is None else np.nan_to_num(np.asarray(cat_mag, float), nan=99.0)
    xy = _project(wcs0, cat_ra, cat_dec)
    m = 8.0
    inside = (xy[:, 0] > -m) & (xy[:, 1] > -m) & (xy[:, 0] < width + m) & (xy[:, 1] < height + m)
    cat_ra, cat_dec, cat_mag = cat_ra[inside], cat_dec[inside], cat_mag[inside]
    if len(cat_ra) < 10:
        raise RuntimeError("only %d reference stars in the field" % len(cat_ra))
    order = np.argsort(cat_mag, kind="stable")
    cat_ra, cat_dec = cat_ra[order], cat_dec[order]

    wcs = wcs0
    scale = max(width, height)
    n_bright = 300
    passes = [(max(4.0, 0.004 * scale), n_bright), (2.5, None), (1.5, None)]
    di = ci = np.array([], int)
    for step, (r, nb) in enumerate(passes):
        dsel = det_xy[:nb] if nb else det_xy
        cra, cde = (cat_ra[:nb], cat_dec[:nb]) if nb else (cat_ra, cat_dec)
        di, ci = _match(dsel, _project(wcs, cra, cde), r)
        n = len(di)
        if n < 6:
            raise RuntimeError("only %d stars matched the catalogue (radius %.1f px)" % (n, r))
        deg = None if step == 0 else _choose_degree(dsel[di], cra[ci], cde[ci], seed)[0]
        wcs = _fit(dsel[di], cra[ci], cde[ci], deg)

    # Distortion order chosen by 5-fold cross-validation: with few stars a 3rd-order SIP fits the matched
    # stars better but predicts the others worse (M1 at 0.29"/px, 63 stars: fit 0.25 px, held out 0.42 px).
    n = len(di)
    deg, cv = _choose_degree(det_xy[di], cat_ra[ci], cat_dec[ci], seed)
    wcs = _fit(det_xy[di], cat_ra[ci], cat_dec[ci], deg)
    stats = {"matched": int(n), "sip_degree": deg or 0}
    if cv is not None:
        stats.update(median_px=float(np.median(cv)), p90_px=float(np.percentile(cv, 90)), holdout=int(len(cv)))
    e_all = np.hypot(*(_project(wcs, cat_ra[ci], cat_dec[ci]) - det_xy[di]).T)
    stats["fit_median_px"] = float(np.median(e_all))
    stats["pixscale_arcsec"] = float(np.sqrt(abs(np.linalg.det(wcs.pixel_scale_matrix))) * 3600.0)
    log("  refined: %d stars matched, SIP order %d, %s" % (
        n, stats["sip_degree"],
        ("held-out error %.2f px median / %.2f px p90" % (stats["median_px"], stats["p90_px"]))
        if "median_px" in stats else "fit error %.2f px" % stats["fit_median_px"]))
    return wcs, stats


def propagate(ra, dec, pmra, pmdec, epoch_from, epoch_to):
    """Linear proper motion (pmra includes cos(dec), mas/yr). NaN motions leave positions unchanged."""
    ra, dec = np.asarray(ra, float), np.asarray(dec, float)
    pmra = np.nan_to_num(np.asarray(pmra, float))
    pmdec = np.nan_to_num(np.asarray(pmdec, float))
    dt = float(epoch_to) - float(epoch_from)
    dec2 = dec + pmdec * dt / 3.6e6
    ra2 = ra + pmra * dt / 3.6e6 / np.cos(np.radians(dec))
    return ra2 % 360.0, dec2
