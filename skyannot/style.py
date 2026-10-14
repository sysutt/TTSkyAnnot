"""Overlay colours and label placement shared by the viewer and the annotated preview image.

The palette is picked against what sits under the labels: nebulae are mostly red or pink in pictures, so
their outlines are cyan (the complementary colour); galaxies are yellow-white, so theirs are rose; clusters sit
on star fields and take gold. Every colour stays bright enough on a black sky. The image itself is never
recoloured: only the overlay is drawn on top.
"""
from __future__ import annotations

CATEGORY_COLORS = {
    "galaxy": "#FF7B93",      # rose
    "nebula": "#45D9E6",      # cyan
    "cluster": "#FFCF4A",     # gold
    "dark": "#B7A2FF",        # lavender
    "other": "#B8C4D2",       # silver: double stars, asterisms and other minor NGC/IC entries
}
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


def choose_box(boxes, placed, brightness=None):
    """Pick a label rectangle (x0, y0, x1, y1) from candidates in order of preference: one that overlaps no
    placed label, on the darkest background (text on a bright galaxy disc is hard to read), earlier candidates
    winning when the backgrounds are alike. When every spot is taken, the one overlapping the least."""
    def overlap(b):
        return sum(max(0.0, min(b[2], p[2]) - max(b[0], p[0])) * max(0.0, min(b[3], p[3]) - max(b[1], p[1]))
                   for p in placed)
    free = [(i, b) for i, b in enumerate(boxes) if overlap(b) == 0.0]
    if not free:
        return min(boxes, key=overlap)
    if brightness is None:
        return free[0][1]
    return min(free, key=lambda ib: brightness(ib[1]) + 0.02 * ib[0])[1]


def rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def color_for(entry) -> str:
    return CATEGORY_COLORS.get(entry.get("category"), CATEGORY_COLORS["other"])


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


def label_offsets(outward, bw, bh, gap):
    """Offsets (dx, dy) of a bw x bh label's top-left corner from its anchor. Beside an outline the label goes
    the way the outline faces, so it never lies across the shape; next to a point it tries right, left, above
    and below."""
    if outward is None:
        return ((gap, -bh / 2), (-bw - gap, -bh / 2), (-bw / 2, -bh - gap), (-bw / 2, gap))
    ux, uy = outward
    ox = gap * ux + (0.0 if ux > 0.4 else (-bw if ux < -0.4 else -bw / 2))
    oy = gap * uy + (0.0 if uy > 0.4 else (-bh if uy < -0.4 else -bh / 2))
    return ((ox, oy),)
