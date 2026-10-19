"""Overlay colours and label placement shared by the viewer and the annotated preview image.

The palette is picked against what sits under the labels: nebulae are mostly red or pink in pictures, so
their outlines are cyan (the complementary colour); galaxies are yellow-white, so theirs are rose; clusters sit
on star fields and take gold. Every colour stays bright enough on a black sky. The image itself is never
recoloured: only the overlay is drawn on top.

Within each family every object takes its own shade, derived from its name: a field of twenty nebulae would
otherwise be twenty copies of the same cyan and read as one colour. The hue stays inside the family (a nebula
is always a cool colour, a galaxy warm), so the shade tells objects apart while the family still says what kind
of thing it is.
"""
from __future__ import annotations

import colorsys
import zlib

# hue°, saturation, value — the middle of each family, taken from the colours of the original palette
FAMILY = {
    "galaxy": (349.0, 0.52, 1.00),   # rose (#FF7B93)
    "nebula": (185.0, 0.70, 0.90),   # cyan (#45D9E6)
    "cluster": (44.0, 0.71, 1.00),   # gold (#FFCF4A)
    "dark": (254.0, 0.37, 1.00),     # lavender (#B7A2FF)
    "other": (212.0, 0.12, 0.82),    # silver: double stars, asterisms and other minor NGC/IC entries
}


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(max(0.0, min(1.0, c)) * 255.0 + 0.5) for c in rgb)


CATEGORY_COLORS = {key: _hex(colorsys.hsv_to_rgb(h / 360.0, s, v)) for key, (h, s, v) in FAMILY.items()}
STAR_COLOR = "#F4F6F8"
GRID_COLOR = "#8FA9C8"
GRID_LINE_ALPHA = 0.50        # grid lines stay in the background; their labels are drawn opaque


class Brightness:
    """Mean brightness of rectangles of the displayed picture, from a summed-area table (constant time per
    rectangle). gray: 2-D array in [0, 1]; scale: gray pixels per unit of the rectangles passed in."""

    def __init__(self, gray, scale=1.0):
        import numpy as np
        g = np.asarray(gray, np.float64)
        self.sat = np.pad(g.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        self.scale = float(scale)

    def __call__(self, box):
        h, w = self.sat.shape[0] - 1, self.sat.shape[1] - 1
        k = self.scale
        a, b = int(min(max(box[0] * k, 0), w)), int(min(max(box[2] * k, 0), w))
        c, d = int(min(max(box[1] * k, 0), h)), int(min(max(box[3] * k, 0), h))
        if b <= a or d <= c:
            return 0.0
        S = self.sat
        return float((S[d, b] - S[c, b] - S[d, a] + S[c, a]) / ((b - a) * (d - c)))


def choose_box(boxes, placed, brightness=None, pad=2.0, may_skip=False):
    """Pick a label rectangle (x0, y0, x1, y1) from candidates in order of preference: one that overlaps no
    placed label, on the darkest background (text on a bright galaxy disc is hard to read), earlier candidates
    winning when the backgrounds are alike. A candidate within `pad` pixels of a placed label counts as
    overlapping, so two labels never touch.

    With `may_skip`, None means every candidate is taken — the caller leaves that label out instead of
    printing it across another one. Otherwise the least-overlapping box is returned, which is what the
    bitmap preview wants (it must still name every object)."""
    def overlap(b):
        b0, b1, b2, b3 = b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad
        return sum(max(0.0, min(b2, p[2]) - max(b0, p[0])) * max(0.0, min(b3, p[3]) - max(b1, p[1]))
                   for p in placed)
    free = [(i, b) for i, b in enumerate(boxes) if overlap(b) == 0.0]
    if not free:
        return None if may_skip else min(boxes, key=overlap)
    if brightness is None:
        return free[0][1]
    return min(free, key=lambda ib: brightness(ib[1]) + 0.02 * ib[0])[1]


def rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def color_for(entry) -> str:
    """The overlay colour for an object: its type sets the family, its name sets the shade inside it.

    Colour still tells kinds of object apart at a glance (a cool outline is a nebula, a warm one a galaxy),
    but two nebulae side by side are no longer the same cyan — each name gets its own shade in the family, so
    a crowded field reads as many objects rather than one flat colour.
    """
    key = entry.get("type_group") or entry.get("category")
    if key not in FAMILY:
        key = "other"
    h, s, v = FAMILY[key]
    seed = zlib.crc32(str(entry.get("name") or entry.get("label") or "").encode("utf-8"))
    h = (h + (seed % 17) - 8) % 360.0
    s = min(1.0, max(0.20, s + (((seed >> 9) % 11) - 5) / 100.0))
    v = min(1.0, max(0.72, v + (((seed >> 17) % 7) - 3) / 100.0))
    return _hex(colorsys.hsv_to_rgb(h / 360.0, s, v))


def inside_anchor(polygons):
    """The middle of a shape and its extent: (cx, cy, width, height) in image pixels, or None.

    A large object — a degree-wide nebula drawn as a big circle — reads better with its name in the middle
    of its own shape than pinned to the outside of the circle, where it looks like a label for the sky next to
    it. The extent is returned as well so the caller can tell whether the text fits inside at all."""
    pts = [p for poly in polygons or [] for p in poly]
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return None
    return 0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys)), w, h


def label_anchors(entry, width, height, polygons=None):
    """Where an object's label may go, in image pixels, best first: [(x, y, outward), ...]. An object with an
    outline is labelled outside its outline (right-most point inside the frame first, then the top, left and
    bottom ones and points in between), so the text does not sit on a bright galaxy core or nebula centre and
    a crowded spot can be avoided; `outward` is the unit vector pointing away from the shape there. A
    point-like object is labelled next to its position (outward None). polygons: what is drawn for the
    object (default: its outline)."""
    polys = entry.get("polygons") if polygons is None else polygons
    pts = [(x, y) for poly in (polys or []) for x, y in poly]
    inside = [(x, y) for x, y in pts if 0 <= x <= width and 0 <= y <= height]
    if inside:
        cx = sum(x for x, _ in pts) / len(pts)
        cy = sum(y for _, y in pts) / len(pts)
        order = [max(inside, key=lambda p: p[0]), min(inside, key=lambda p: p[1]),
                 min(inside, key=lambda p: p[0]), max(inside, key=lambda p: p[1])]
        order += inside[::max(1, len(inside) // 8)]
        out = []
        for x, y in order:
            if any(x == a and y == b for a, b, _u in out):
                continue
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 or 1.0
            out.append((x, y, ((x - cx) / d, (y - cy) / d)))
        return out
    if "x" in entry:
        return [(entry["x"], entry["y"], None)]
    return []


def label_offsets(outward, bw, bh, gap, rings=4, step=18.0):
    """Offsets (dx, dy) of a bw x bh label's top-left corner from its anchor, nearest first.

    Beside an outline the label goes the way the outline faces, so it never lies across the shape; next to a
    point it tries right, left, above and below. `rings` repeats every direction at increasing distances: a
    label whose nearest spot is taken by another label steps outwards instead of overprinting it (a wide
    nebula caption and a star name landing on the same patch of sky is the case this exists for)."""
    out = []
    for k in range(max(1, int(rings))):
        g = gap + k * float(step)
        if outward is None:
            out.extend(((g, -bh / 2), (-bw - g, -bh / 2), (-bw / 2, -bh - g), (-bw / 2, g)))
            continue
        ux, uy = outward
        ox = g * ux + (0.0 if ux > 0.4 else (-bw if ux < -0.4 else -bw / 2))
        oy = g * uy + (0.0 if uy > 0.4 else (-bh if uy < -0.4 else -bh / 2))
        out.append((ox, oy))
    return tuple(out)
