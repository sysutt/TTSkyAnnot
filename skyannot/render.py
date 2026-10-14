"""Annotated preview image (JPEG/PNG): deep-sky outlines/ellipses with labels and named stars.
The picture is only drawn on; its pixels are not changed (colours: style.py)."""
from __future__ import annotations

import os

import numpy as np

from . import style

_FONTS = (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
          "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_BOLD_FONTS = (r"C:\Windows\Fonts\seguisb.ttf", r"C:\Windows\Fonts\arialbd.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
SS = 2            # the overlay is drawn at twice the size and scaled down: smooth, anti-aliased curves


def _font(size, bold=False):
    from PIL import ImageFont
    for f in (_BOLD_FONTS if bold else ()) + _FONTS:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)


def render(display_rgb, shapes, named_rows, out_path, max_edge=4000, quality=92, grid_lines=None,
           categories=None):
    """display_rgb: (h, w, 3) in [0, 1]. shapes: shapes.collect() output. named_rows: annotate.Row list of
    named stars (None or [] = no star labels). grid_lines: grid.compute() output (None = no grid).
    categories: object groups to draw (None = all). Pixel inputs are in PixInsight convention at full
    resolution."""
    from PIL import Image, ImageDraw
    h, w = display_rgb.shape[:2]
    k = min(1.0, max_edge / float(max(h, w)))
    im = Image.fromarray((np.clip(display_rgb, 0, 1) * 255).astype(np.uint8))
    if k < 1.0:
        im = im.resize((int(round(w * k)), int(round(h * k))), Image.LANCZOS)
    W2, H2 = im.size
    edge = max(W2, H2)
    ks = k * SS                                    # full-resolution pixels -> overlay pixels
    OW, OH = W2 * SS, H2 * SS
    ov = Image.new("RGBA", (OW, OH), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov, "RGBA")
    lw = max(2, int(round(edge / 1500))) * SS
    font = _font(max(14, int(edge / 110)) * SS, bold=True)
    sfont = _font(max(12, int(edge / 140)) * SS)
    gfont = _font(max(11, int(edge / 150)) * SS)

    gcol = style.rgb(style.GRID_COLOR)
    grid_boxes = []                                # object labels keep clear of these
    for g in grid_lines or []:
        for seg in g["polylines"]:
            dr.line([(x * ks, y * ks) for x, y in seg], fill=gcol + (int(255 * style.GRID_LINE_ALPHA),),
                    width=max(SS, lw - SS))
    for g in grid_lines or []:
        if g.get("label_at"):
            x, y, side = g["label_at"]
            x, y = x * ks, y * ks
            bw, bh = dr.textbbox((0, 0), g["label"], font=gfont)[2:]
            x = min(max(x + 4 * SS, 2 * SS), OW - bw - 2 * SS)
            y = min(max(y - bh - 4 * SS if side == "bottom" else y + 4 * SS, 2 * SS), OH - bh - 2 * SS)
            dr.text((x, y), g["label"], fill=gcol + (255,), font=gfont)
            grid_boxes.append((x, y, x + bw, y + bh))

    labels = []
    for e in sorted(shapes, key=lambda e: e.get("kind") != "outline"):
        if categories is not None and e.get("category") not in categories:
            continue
        col = style.rgb(style.color_for(e)) + (255,)
        for poly in e.get("polygons") or []:
            pts = [(x * ks, y * ks) for x, y in poly]
            if len(pts) >= 2:
                dr.line(pts + [pts[0]] if e["kind"] != "outline" else pts, fill=col, width=lw)
        if e.get("kind") == "point":
            x, y = e["x"] * ks, e["y"] * ks
            r = 6 * lw
            dr.ellipse([x - r, y - r, x + r, y + r], outline=col, width=lw)
        anchors = style.label_anchors(e, w, h) if e.get("label") else []
        if anchors:
            gap = 6 * lw + 3 * SS if e.get("kind") == "point" else 3 * lw
            labels.append(([(x * ks, y * ks, u) for x, y, u in anchors], e["label"], col, font, gap))
    star = style.rgb(style.STAR_COLOR) + (255,)
    for r in named_rows or []:
        x, y = r.x * ks, r.y * ks
        rad = 9 * lw
        dr.ellipse([x - rad, y - rad, x + rad, y + rad], outline=star, width=lw)
        name = r.name + (" %s" % r.extra.get("common") if r.extra.get("common") else "")
        labels.append(([(x, y, None)], name, star, sfont, rad + 3 * SS))
    # greedy placement: outside each outline (or around a point), inside the frame, clear of labels already
    # placed and preferably on a dark background
    placed = list(grid_boxes)
    bright = style.Brightness(np.asarray(im.convert("L"), np.float32) / 255.0, 1.0 / SS)
    # Messier objects choose first, then the other objects, then star names
    labels.sort(key=lambda lb: (lb[3] is sfont, not (lb[1][:1] == "M" and lb[1][1:2].isdigit())))
    for anchors, text, col, f, gap in labels:
        bw, bh = dr.textbbox((0, 0), text, font=f)[2:]
        boxes = []
        for x, y, outward in anchors:
            for ox, oy in style.label_offsets(outward, bw, bh, gap):
                bx = min(max(x + ox, 2 * SS), OW - bw - 2 * SS)
                by = min(max(y + oy, 2 * SS), OH - bh - 2 * SS)
                boxes.append((bx, by, bx + bw, by + bh))
        best = style.choose_box(boxes, placed, bright)
        placed.append(best)
        dr.text((best[0], best[1]), text, fill=col, font=f)
    ov = ov.resize((W2, H2), Image.LANCZOS)
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    ext = os.path.splitext(out_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        im.save(out_path, "JPEG", quality=quality)
    else:
        im.save(out_path)
    return out_path
