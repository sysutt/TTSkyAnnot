"""Equatorial coordinate grid (RA/Dec lines with edge labels), like PixInsight's AnnotateImage grid layer.

Lines are projected through the full solution (distortion included), so they bend with the optics. Spacing is
picked from "round" values so that roughly 4-8 lines cross the field in each direction. A celestial pole
inside the frame is handled (RA lines then fan out from it). Pixel coordinates use PixInsight's convention.
"""
from __future__ import annotations

import math

import numpy as np

_DEC_STEPS = [s / 3600.0 for s in (5, 10, 15, 30)] + [m / 60.0 for m in (1, 2, 5, 10, 15, 20, 30)] + \
             [1, 2, 5, 10, 15, 30]                                   # degrees
_RA_STEPS = [s * 15 / 3600.0 for s in (1, 2, 5, 10, 15, 20, 30)] + \
            [m * 15 / 60.0 for m in (1, 2, 5, 10, 15, 20, 30)] + [h * 15.0 for h in (1, 2, 3, 6)]   # degrees


def _pick(steps, span, target=5):
    for s in steps:
        if span / s <= target + 2:
            return s
    return steps[-1]


def _fmt_ra(deg, step):
    sec = round((deg % 360.0) / 15.0 * 3600.0 / max(1e-9, step / 15.0 * 3600.0)) * (step / 15.0 * 3600.0)
    sec %= 86400
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if step * 240 < 60 - 1e-6:                 # step below one minute of time: show seconds
        return "%02dh%02dm%02ds" % (h, m, round(s))
    return "%02dh%02dm" % (h, m)


def _fmt_dec(deg, step):
    sign = "-" if deg < -1e-9 else "+"
    a = round(abs(deg) * 3600)
    d, rem = divmod(a, 3600)
    m, s = divmod(rem, 60)
    if step * 3600 < 60 - 1e-6:
        return "%s%02d°%02d′%02d″" % (sign, d, m, s)
    if step < 1 - 1e-9:
        return "%s%02d°%02d′" % (sign, d, m)
    return "%s%02d°" % (sign, d)


def _edge_hit(xs, ys, W, H):
    """First point where the polyline enters the frame, and which edge. Returns (x, y, edge) or None."""
    inside = (xs >= 0) & (ys >= 0) & (xs <= W) & (ys <= H) & np.isfinite(xs) & np.isfinite(ys)
    idx = np.nonzero(inside)[0]
    if len(idx) == 0:
        return None
    hits = []
    for i in (idx[0], idx[-1]):
        x, y = float(xs[i]), float(ys[i])
        d = {"left": x, "right": W - x, "top": y, "bottom": H - y}
        e = min(d, key=d.get)
        hits.append((d[e], x, y, e))
    return hits


def _clip_polyline(seg, W, H):
    """Cut a polyline to the frame [0, W] x [0, H] (Liang-Barsky per segment). Returns a list of polylines."""
    out, cur = [], []
    for (x0, y0), (x1, y1) in zip(seg[:-1], seg[1:]):
        dx, dy = x1 - x0, y1 - y0
        t0, t1, ok = 0.0, 1.0, True
        for p, q in ((-dx, x0), (dx, W - x0), (-dy, y0), (dy, H - y0)):
            if p == 0:
                if q < 0:
                    ok = False
                    break
                continue
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                ok = False
                break
        if not ok:
            if len(cur) > 1:
                out.append(cur)
            cur = []
            continue
        a = [round(x0 + t0 * dx, 2), round(y0 + t0 * dy, 2)]
        b = [round(x0 + t1 * dx, 2), round(y0 + t1 * dy, 2)]
        if cur and (abs(cur[-1][0] - a[0]) > 1e-6 or abs(cur[-1][1] - a[1]) > 1e-6):
            if len(cur) > 1:
                out.append(cur)
            cur = []
        if not cur:
            cur = [a]
        cur.append(b)
    if len(cur) > 1:
        out.append(cur)
    return out


def compute(wcs, width, height, samples=160):
    """[{kind: "ra"|"dec", value, label, points: [[x, y], ...], label_at: (x, y, edge) or None}]."""
    W, H = float(width), float(height)
    gx, gy = np.meshgrid(np.linspace(0, W - 1, 40), np.linspace(0, H - 1, 40))
    ra, dec = wcs.all_pix2world(gx.ravel(), gy.ravel(), 0)
    ok = np.isfinite(ra) & np.isfinite(dec)
    ra, dec = ra[ok], dec[ok]
    if len(ra) < 10:
        return []
    c = wcs.all_pix2world([[W / 2 - 0.5, H / 2 - 0.5]], 0)[0]
    ra_c = float(c[0])
    dra = ((ra - ra_c + 180.0) % 360.0) - 180.0
    dec_min, dec_max = float(dec.min()), float(dec.max())
    ra_min, ra_max = ra_c + float(dra.min()), ra_c + float(dra.max())
    # celestial pole inside the frame?
    pole = None
    for p in (90.0, -90.0):
        px, py = wcs.all_world2pix([[ra_c, p]], 0, quiet=True)[0]
        if np.isfinite(px) and 0 <= px <= W and 0 <= py <= H:
            pole = p
    if pole is not None:
        ra_min, ra_max = 0.0, 360.0
        dec_min, dec_max = (dec_min, 90.0) if pole > 0 else (-90.0, dec_max)
    cosd = max(0.05, math.cos(math.radians(0.5 * (dec_min + dec_max)))) if pole is None else 1.0
    s_dec = _pick(_DEC_STEPS, dec_max - dec_min)
    s_ra = _pick(_RA_STEPS, (ra_max - ra_min) * cosd)
    if pole is not None:
        s_ra = max(s_ra, 30.0)
    lines = []

    def project(r, d):
        x, y = wcs.all_world2pix(np.asarray(r) % 360.0, np.asarray(d), 0, quiet=True)
        return np.asarray(x, float) + 0.5, np.asarray(y, float) + 0.5

    pad_d = (dec_max - dec_min) * 0.1 + s_dec
    pad_r = (ra_max - ra_min) * 0.1 + s_ra
    d_lo, d_hi = max(-90.0, dec_min - pad_d), min(90.0, dec_max + pad_d)
    # RA lines (constant RA, dec varies)
    r0 = math.floor(ra_min / s_ra) * s_ra
    r = r0
    while r <= ra_max + 1e-9 and len(lines) < 80:
        dd = np.linspace(d_lo, d_hi, samples)
        x, y = project(np.full(samples, r), dd)
        lines.append(("ra", r % 360.0, _fmt_ra(r, s_ra), x, y))
        r += s_ra
    # Dec lines (constant Dec, RA varies)
    d = math.floor(dec_min / s_dec) * s_dec
    while d <= dec_max + 1e-9 and len(lines) < 160:
        if abs(d) < 90 - 1e-9:
            rr = np.linspace(ra_min - pad_r, ra_max + pad_r, samples * 2) if pole is None else np.linspace(0, 360, samples * 2)
            x, y = project(rr, np.full(len(rr), d))
            lines.append(("dec", d, _fmt_dec(d, s_dec), x, y))
        d += s_dec
    out = []
    for kind, val, label, x, y in lines:
        good = np.isfinite(x) & np.isfinite(y)
        if not np.any(good & (x >= 0) & (y >= 0) & (x <= W) & (y <= H)):
            continue
        # break the polyline where it jumps (projection wrap) or leaves far outside the frame
        pts, seg = [], []
        far = 2.0 * max(W, H)
        for xi, yi, gi in zip(x, y, good):
            if not gi or xi < -far or yi < -far or xi > W + far or yi > H + far:
                if len(seg) > 1:
                    pts.append(seg)
                seg = []
                continue
            if seg and math.hypot(xi - seg[-1][0], yi - seg[-1][1]) > 0.5 * max(W, H):
                if len(seg) > 1:
                    pts.append(seg)
                seg = []
            seg.append([round(float(xi), 2), round(float(yi), 2)])
        if len(seg) > 1:
            pts.append(seg)
        hits = _edge_hit(x, y, W, H) or []
        # RA labels prefer the bottom/top edge, Dec labels the left/right edge
        pref = ("bottom", "top", "left", "right") if kind == "ra" else ("left", "right", "bottom", "top")
        best = None
        for e in pref:
            cand = [h for h in hits if h[3] == e and h[0] < 0.02 * max(W, H)]
            if cand:
                best = cand[0]
                break
        pts = [c for seg in pts for c in _clip_polyline(seg, W, H)]
        out.append({"kind": kind, "value": float(val), "label": label, "polylines": pts,
                    "label_at": (best[1], best[2], best[3]) if best else None})
    return out
