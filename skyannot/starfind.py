"""Star detection.

The detector is the PhotoEdit starfinder (same author), unchanged: per-cell median/MAD background,
4-sigma threshold, 4-connected blobs, flux-weighted centroids, rejection of elongated/sparse/huge blobs.
The online solver (ttastro.com) is tuned on its output, and the accuracy figures in README were measured
with it.

Coordinates use the *index* convention: the centre of pixel (i, j) is (i, j). PixInsight's convention
is index + 0.5, FITS is index + 1.
"""
from __future__ import annotations

import math

import numpy as np

GRID = 16
MAX_STAR_PX = 900
MIN_STAR_PX = 3
MAX_ELONG = 2.6
MIN_FILL = 0.32
MIN_PEAK = 0.0           # peak / mean gate (off: measured on LBN777 it removed real stars)


def _background(luma):
    """estimateBackground: per-cell median + MAD, stored as float32."""
    h, w = luma.shape
    cellW, cellH = -(-w // GRID), -(-h // GRID)
    bg = np.zeros((GRID, GRID), np.float32)
    sg = np.zeros((GRID, GRID), np.float32)
    for gy in range(GRID):
        for gx in range(GRID):
            x0, y0 = gx * cellW, gy * cellH
            x1, y1 = min(w, x0 + cellW), min(h, y0 + cellH)
            if x1 <= x0 or y1 <= y0:
                bg[gy, gx] = 0
                sg[gy, gx] = 1
                continue
            sx = max(1, (x1 - x0) // 40)
            sy = max(1, (y1 - y0) // 40)
            s = np.sort(luma[y0:y1:sy, x0:x1:sx].astype(np.float64).ravel())
            med = s[len(s) >> 1]
            dev = np.sort(np.abs(s - med))
            mad = dev[len(dev) >> 1]
            bg[gy, gx] = med
            sg[gy, gx] = max(0.6, mad * 1.4826)
    return bg, sg, cellW, cellH


def _bilinear(m, w, h, cellW, cellH):
    m = m.astype(np.float64)
    fx = np.clip(np.arange(w) / cellW - 0.5, 0, GRID - 1)
    fy = np.clip(np.arange(h) / cellH - 0.5, 0, GRID - 1)
    x0 = np.floor(fx).astype(int)
    x1 = np.minimum(GRID - 1, x0 + 1)
    tx = (fx - x0)[None, :]
    y0 = np.floor(fy).astype(int)
    y1 = np.minimum(GRID - 1, y0 + 1)
    ty = (fy - y0)[:, None]
    a = m[np.ix_(y0, x0)]
    b = m[np.ix_(y0, x1)]
    c = m[np.ix_(y1, x0)]
    d = m[np.ix_(y1, x1)]
    return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty


def _measure(xs, ys, v):
    n = len(xs)
    s = float(v.sum())
    if s <= 0:
        return None
    cx = float((xs * v).sum()) / s
    cy = float((ys * v).sum()) / s
    mxx = float((xs * xs * v).sum()) / s - cx * cx
    myy = float((ys * ys * v).sum()) / s - cy * cy
    mxy = float((xs * ys * v).sum()) / s - cx * cy
    tr, det = mxx + myy, mxx * myy - mxy * mxy
    disc = max(0.0, tr * tr / 4 - det)
    l1, l2 = tr / 2 + math.sqrt(disc), tr / 2 - math.sqrt(disc)
    elong = math.sqrt(l1 / l2) if l2 > 1e-6 else 99.0
    fill = n / max(1, (int(xs.max()) - int(xs.min()) + 1) * (int(ys.max()) - int(ys.min()) + 1))
    peak = float(v.max())
    return n, s, cx, cy, elong, fill, peak


def detect(luma, sigma=4.0, max_stars=300):
    """luma: float32 brightness on a 0..255 scale. Returns [(x, y, flux, npix, elong)] sorted by flux."""
    from scipy import ndimage
    h, w = luma.shape
    bg, sg, cellW, cellH = _background(luma)
    B = _bilinear(bg, w, h, cellW, cellH)
    S = _bilinear(sg, w, h, cellW, cellH)
    L = luma.astype(np.float64)
    mask = (L - B) > sigma * S
    V = np.maximum(0.0, L - B)
    del B, S, L

    lbl, nl = ndimage.label(mask)
    if nl == 0:
        return []
    size = np.bincount(lbl.ravel(), minlength=nl + 1)
    ok_small = np.zeros(nl + 1, bool)
    ok_small[1:] = (size[1:] >= MIN_STAR_PX) & (size[1:] <= MAX_STAR_PX)
    ys_all, xs_all = np.nonzero(mask)
    lb = lbl[ys_all, xs_all]
    pick = ok_small[lb]
    ys, xs, lb = ys_all[pick], xs_all[pick], lb[pick]
    vv = V[ys, xs]
    order = np.argsort(lb, kind="stable")
    ys, xs, lb, vv = ys[order], xs[order], lb[order], vv[order]
    if len(lb) == 0:
        return []
    starts = np.flatnonzero(np.r_[True, lb[1:] != lb[:-1]])
    ends = np.r_[starts[1:], len(lb)]
    xf, yf = xs.astype(np.float64), ys.astype(np.float64)
    cand = []
    for st, en in zip(starts.tolist(), ends.tolist()):
        m = _measure(xf[st:en], yf[st:en], vv[st:en])
        if m:
            cand.append((int(ys[st]) * w + int(xs[st]), m))
    out = []
    for seed, (n, s, cx, cy, elong, fill, peak) in cand:
        if n < MIN_STAR_PX or elong > MAX_ELONG or fill < MIN_FILL:
            continue
        if peak / max(1e-6, s / n) < MIN_PEAK:
            continue
        if cx < 2 or cy < 2 or cx > w - 3 or cy > h - 3:
            continue
        out.append((seed, cx, cy, s, n, round(elong, 2)))
    out.sort(key=lambda t: t[0])
    out.sort(key=lambda t: -t[3])
    return [t[1:] for t in out[:max_stars]]


def luma_255(display_rgb) -> np.ndarray:
    """Rec.709 brightness on a 0..255 scale from a display-ready (already stretched) image in [0, 1]."""
    a = np.asarray(display_rgb, np.float32)
    if a.ndim == 3 and a.shape[2] >= 3:
        y = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    else:
        y = a[..., 0] if a.ndim == 3 else a
    return (np.clip(y, 0, 1) * 255.0).astype(np.float32)


def find_stars(display_rgb, max_stars=1500, sigma=4.0, max_edge=8000):
    """Detect stars on a display-ready image. Very large frames are detected on a reduced copy and the
    centroids scaled back, so memory stays bounded. Returns [(x, y, flux, npix, elong)] in full-resolution
    index coordinates, brightest first."""
    luma = luma_255(display_rgb)
    h, w = luma.shape
    k = 1.0
    if max(h, w) > max_edge:
        from PIL import Image
        k = max_edge / float(max(h, w))
        size = (int(round(w * k)), int(round(h * k)))
        luma = np.asarray(Image.fromarray(luma, "F").resize(size, Image.BOX), np.float32)
    st = detect(luma, sigma=sigma, max_stars=max_stars)
    if k == 1.0:
        return st
    inv = 1.0 / k
    # box filter maps pixel centres as (i + 0.5) / k - 0.5
    return [((x + 0.5) * inv - 0.5, (y + 0.5) * inv - 0.5, f, n, e) for x, y, f, n, e in st]
