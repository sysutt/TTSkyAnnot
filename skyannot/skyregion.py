"""Sky/foreground split for nightscapes.

A nightscape is half sky, half land: the terrain has no stars, but the annotation engine still labels
whatever deep-sky object the sky coordinates project onto it, and those labels land on a building or a
tree. Two things the frame already tells us separate the two halves:

* detected stars — the sky is full of them, the terrain is not;
* texture — buildings, trees and lit rock are busy at the cell scale, the sky is smooth.

Neither works alone: a black cliff has no texture but also no stars, and a lit building can fool the
star detector with reflections, but it is far busier than the sky. Together they give a coarse mask
that is good enough to decide whether a label belongs on this part of the picture.

Only very wide frames use this (see run.solve): on an ordinary deep-sky frame the whole picture is
sky, the mask covers everything, and nothing changes.
"""
from __future__ import annotations

import numpy as np

GX, GY = 48, 36      # cells across/down; at a 5472 px frame one cell is ~115 x 100 px
DENSITY_FRAC = 0.12  # a cell needs this share of the frame's own star density to count as sky
TEXTURE_K = 3.0      # ... and no more than this multiple of the sky's median texture


def build(det, display, width, height, gx=GX, gy=GY):
    """(grid, gx, gy, width, height) or None when the frame has no clear foreground."""
    det = np.asarray(det, float)
    if len(det) < 200 or width <= 0 or height <= 0 or display is None:
        return None
    count = _cell_counts(det, width, height, gx, gy)
    dens = _smooth(count)
    nz = dens[dens > 0.15]
    if nz.size < 8:
        return None
    sky_dense = dens >= DENSITY_FRAC * float(np.percentile(nz, 90))
    tex = _cell_texture(display, gx, gy)
    limit = float(np.median(tex[sky_dense])) * TEXTURE_K if sky_dense.any() else 0.05
    smooth = tex <= limit
    sky = sky_dense & smooth
    # 只允许「纹理也像天空」的格子被补回来，否则房子/树的一小块会被周围的天空填成天空
    for _ in range(2):
        sky = _fill(sky, smooth)
    sky = _drop_specks(sky)
    frac = float(sky.mean())
    if frac > 0.90 or frac < 0.05:
        return None                      # all sky (or nothing worth calling sky): leave the frame alone
    return sky, gx, gy, width, height


def prepare(det, display, width, height):
    """The mask for this frame, or None when the whole picture is sky."""
    return build(det, display, width, height)


def allows(mask, x, y):
    """Is this pixel in the sky part of the frame? No mask -> everything is allowed."""
    if not mask:
        return True
    grid, gx, gy, width, height = mask
    if x is None or y is None:
        return False
    try:
        cx = int(float(x) / float(width) * gx)
        cy = int(float(y) / float(height) * gy)
    except (TypeError, ValueError):
        return False
    if not (0 <= cx < gx and 0 <= cy < gy):
        return False
    return bool(grid[cy, cx])


def coverage(mask):
    return 1.0 if not mask else float(mask[0].mean())


def _cell_counts(det, width, height, gx, gy):
    ix = np.clip((det[:, 0] / float(width) * gx).astype(int), 0, gx - 1)
    iy = np.clip((det[:, 1] / float(height) * gy).astype(int), 0, gy - 1)
    c = np.zeros((gy, gx))
    np.add.at(c, (iy, ix), 1.0)
    return c


def _smooth(a):
    """Two 3x3 [1 2 1] passes: kills the single-star noise without moving the horizon."""
    k = np.array([1.0, 2.0, 1.0])
    k = k / k.sum()
    out = a
    for _ in range(2):
        out = np.apply_along_axis(lambda r: np.convolve(np.pad(r, 1, mode="edge"), k, "valid"), 0, out)
        out = np.apply_along_axis(lambda r: np.convolve(np.pad(r, 1, mode="edge"), k, "valid"), 1, out)
    return out


def _cell_texture(display, gx, gy):
    """Per-cell standard deviation of the luminance, measured on an 8x-downsampled copy."""
    from PIL import Image
    lum = np.asarray(display, np.float32).mean(axis=2)
    lum = np.clip(lum, 0.0, 1.0)
    small = Image.fromarray((lum * 255).astype(np.uint8)).resize((gx * 8, gy * 8), Image.BILINEAR)
    small = np.asarray(small, np.float32) / 255.0
    tex = np.zeros((gy, gx))
    for j in range(gy):
        for i in range(gx):
            tex[j, i] = small[j * 8:(j + 1) * 8, i * 8:(i + 1) * 8].std()
    return tex


def _drop_specks(sky):
    """A single masked cell in the middle of the sky is noise (one bright star lifts the texture of its
    cell); the terrain is a contiguous mass. Unmask isolated cells rather than losing a label to them."""
    nb = np.zeros(sky.shape, int)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dy:
                nb += np.roll(np.roll(sky, dy, 0), dx, 1)
    return sky | (nb >= 7)


def _fill(sky, allowed):
    """Fill a cell enclosed by sky (a star-poor dust lane looks exactly like that) — but only where the
    texture also looks like sky, so a patch of roof or foliage is never filled back in."""
    nb = np.zeros(sky.shape, int)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dy:
                nb += np.roll(np.roll(sky, dy, 0), dx, 1)
    return sky | ((nb >= 6) & allowed)
