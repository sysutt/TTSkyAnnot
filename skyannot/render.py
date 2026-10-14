"""Annotated preview image (JPEG/PNG): deep-sky outlines/ellipses with labels and named stars."""
from __future__ import annotations

import os

import numpy as np

COLORS = {
    "galaxy": (255, 179, 71),
    "nebula": (255, 107, 138),
    "cluster": (255, 216, 77),
    "dark": (150, 170, 210),
    "outline": (94, 225, 255),
    "star": (235, 235, 235),
}


def _kind_color(entry):
    if entry.get("kind") == "outline":
        return COLORS["outline"]
    t = (entry.get("type") or "")
    name = entry.get("name", "")
    if entry.get("catalog") == "Sharpless":
        return COLORS["nebula"]
    if t.startswith("G") or t in ("GPair", "GTrpl", "GGroup"):
        return COLORS["galaxy"]
    if t in ("OCl", "GCl", "Cl+N", "*Ass"):
        return COLORS["cluster"]
    if t == "DrkN" or name.startswith(("B", "LDN")):
        return COLORS["dark"]
    return COLORS["nebula"]


def _font(size):
    from PIL import ImageFont
    for f in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)


def render(display_rgb, shapes, named_rows, out_path, max_edge=4000, quality=92):
    """display_rgb: (h, w, 3) in [0, 1]. shapes: shapes.collect() output. named_rows: annotate.Row list of
    named stars. Pixel inputs are in PixInsight convention at full resolution."""
    from PIL import Image, ImageDraw
    h, w = display_rgb.shape[:2]
    k = min(1.0, max_edge / float(max(h, w)))
    im = Image.fromarray((np.clip(display_rgb, 0, 1) * 255).astype(np.uint8))
    if k < 1.0:
        im = im.resize((int(round(w * k)), int(round(h * k))), Image.LANCZOS)
    dr = ImageDraw.Draw(im)
    lw = max(1, int(round(max(im.size) / 1600)))
    font = _font(max(13, int(max(im.size) / 110)))
    sfont = _font(max(11, int(max(im.size) / 140)))

    labels = []
    for e in sorted(shapes, key=lambda e: e.get("kind") != "outline"):
        col = _kind_color(e)
        for poly in e.get("polygons") or []:
            pts = [(x * k, y * k) for x, y in poly]
            if len(pts) >= 2:
                dr.line(pts + [pts[0]] if e["kind"] != "outline" else pts, fill=col, width=lw)
        if e.get("kind") == "point":
            x, y = e["x"] * k, e["y"] * k
            r = 6 * lw
            dr.ellipse([x - r, y - r, x + r, y + r], outline=col, width=lw)
        if e.get("label") and "x" in e:
            labels.append((e["x"] * k, e["y"] * k, e["label"], col, font))
    for r in named_rows or []:
        x, y = r.x * k, r.y * k
        rad = 9 * lw
        dr.ellipse([x - rad, y - rad, x + rad, y + rad], outline=COLORS["star"], width=lw)
        name = r.name + (" %s" % r.extra.get("common") if r.extra.get("common") else "")
        labels.append((x + rad + 2, y - rad, name, COLORS["star"], sfont))
    # greedy placement: try right, left, above, below the anchor and skip spots taken by earlier labels
    placed = []
    for x, y, text, col, f in labels:
        bw, bh = dr.textbbox((0, 0), text, font=f)[2:]
        best = None
        for ox, oy in ((4, 4), (-bw - 6, 4), (4, -bh - 6), (-bw / 2, 8), (-bw / 2, -bh - 10)):
            box = (x + ox, y + oy, x + ox + bw, y + oy + bh)
            if not any(box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1] for b in placed):
                best = box
                break
        if best is None:
            best = (x + 4, y + 4, x + 4 + bw, y + 4 + bh)
        placed.append(best)
        dr.text((best[0] + 1, best[1] + 1), text, fill=(0, 0, 0), font=f)
        dr.text((best[0], best[1]), text, fill=col, font=f)
    ext = os.path.splitext(out_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        im.save(out_path, "JPEG", quality=quality)
    else:
        im.save(out_path)
    return out_path
