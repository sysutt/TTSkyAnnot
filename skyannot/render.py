"""Annotated preview image (JPEG/PNG): deep-sky outlines/ellipses with labels and named stars.
The picture is only drawn on; its pixels are not changed (colours: style.py)."""
from __future__ import annotations

import os

import numpy as np

from . import shapes as shapes_mod, style

# Microsoft YaHei first: the labels carry Chinese catalogue names (暗星云/星团) and Segoe UI has no CJK
# glyphs, which drew them as empty boxes in the annotated picture.
_FONTS = (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc",
          r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
          "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Helvetica.ttc",
          "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_BOLD_FONTS = (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf",
               r"C:\Windows\Fonts\seguisb.ttf", r"C:\Windows\Fonts\arialbd.ttf",
               "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
SS = 2            # the overlay is drawn at twice the size and scaled down: smooth, anti-aliased curves


def _font(size, bold=False, family="sans"):
    from PIL import ImageFont
    if family == "serif":
        cands = (r"C:\Windows\Fonts\georgia.ttf", r"C:\Windows\Fonts\times.ttf",
                 "/System/Library/Fonts/Times.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
    elif family == "mono":
        cands = (r"C:\Windows\Fonts\consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    else:
        cands = ()
    for f in cands:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                pass
    for f in (_BOLD_FONTS if bold else ()) + _FONTS:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)


def _ascii_label(text):
    """Labels are drawn with fonts that carry no Chinese glyphs (Consolas, Georgia, Segoe Script), so a
    Chinese catalogue name comes out as a row of boxes. Keep the catalogue id and any Latin name, drop the
    Chinese part entirely — the same rule for every style."""
    import re
    s = re.sub(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", " ", str(text or ""))
    s = re.sub(r"\s+", " ", s).strip(" -·|")
    return s

def render(display_rgb, shapes, named_rows, out_path, max_edge=4000, quality=92, grid_lines=None,
           categories=None, shape_style="outline", theme_name=None, ctx=None):
    """display_rgb: (h, w, 3) in [0, 1]. shapes: shapes.collect() output. named_rows: annotate.Row list of
    named stars (None or [] = no star labels). grid_lines: grid.compute() output (None = no grid).
    categories: object groups to draw (None = all). shape_style: "outline" or "circle" (catalogue size, see
    shapes.display_polygons). Pixel inputs are in PixInsight convention at full
    resolution."""
    from PIL import Image, ImageDraw
    from . import decor
    if theme_name:
        style.set_theme(theme_name)
    tstyle = style.theme()
    h, w = display_rgb.shape[:2]
    k = min(1.0, max_edge / float(max(h, w)))
    im = Image.fromarray((np.clip(display_rgb, 0, 1) * 255).astype(np.uint8))
    if k < 1.0:
        im = im.resize((int(round(w * k)), int(round(h * k))), Image.LANCZOS)
    # the picture styles either leave the tones alone or shift them a little; the shift is part of the style
    sepia = tstyle.get("tint_sepia")
    if sepia:
        im = Image.blend(im, Image.new("RGB", im.size, (206, 178, 128)), float(sepia))
    tint = tstyle.get("tint")
    if tint:
        arr = np.asarray(im, np.float32) / 255.0 * np.array(tint, np.float32)
        im = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))
    dark = tstyle.get("darken")
    if dark:
        im = Image.eval(im, lambda v: int(v * float(dark)))
    W2, H2 = im.size
    edge = max(W2, H2)
    ks = k * SS                                    # full-resolution pixels -> overlay pixels
    OW, OH = W2 * SS, H2 * SS
    ov = Image.new("RGBA", (OW, OH), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov, "RGBA")
    lw = max(2, int(round(edge / 1500))) * SS
    lw = max(1, int(round(lw * float(tstyle.get("line_scale") or 1.0))))
    fam = tstyle.get("font") or "sans"
    font = _font(max(14, int(edge / 110)) * SS, bold=True, family=fam)
    sfont = _font(max(12, int(edge / 140)) * SS, family=fam)
    gfont = _font(max(11, int(edge / 150)) * SS, family=fam)

    gcol = style.rgb(style.grid_color())
    grid_boxes = []                                # object labels keep clear of these
    for g in grid_lines or []:
        for seg in g["polylines"]:
            dr.line([(x * ks, y * ks) for x, y in seg], fill=gcol + (int(255 * style.grid_alpha()),),
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

    glow = Image.new("RGBA", (OW, OH), (0, 0, 0, 0)) if tstyle.get("glow") else None
    gdr = ImageDraw.Draw(glow, "RGBA") if glow is not None else None
    shape_mode = tstyle.get("shape_mode") or "outline"
    labels = []
    for e in sorted(shapes, key=lambda e: e.get("kind") != "outline"):
        if categories is not None and not (e.get("category") in categories
                                           or e.get("type_group") in categories):
            continue
        col = style.rgb(style.color_for(e)) + (255,)
        if shape_mode == "x":
            # treasure-map style: an X marks the object instead of an outline
            x, y = e.get("x"), e.get("y")
            if x is None or y is None:
                continue
            x, y = x * ks, y * ks
            r = max(2.5 * lw, max(16 * SS, int(edge / 75.0)))
            pen = gdr if gdr is not None else dr
            # 奶油色描边 + 原色芯：深红 X 落在红色星云上也能分辨
            halo = (243, 228, 192, 220)
            pen.line([(x - r, y - r), (x + r, y + r)], fill=halo, width=max(4, lw * 2 + 1))
            pen.line([(x - r, y + r), (x + r, y - r)], fill=halo, width=max(4, lw * 2 + 1))
            pen.line([(x - r, y - r), (x + r, y + r)], fill=col, width=max(3, lw * 2))
            pen.line([(x - r, y + r), (x + r, y - r)], fill=col, width=max(3, lw * 2))
            labels.append(([(x, y, None)], e.get("name") or e.get("label") or "", col, font,
                           r + 4 * SS, None))
            continue
        polys, closed = shapes_mod.display_polygons(e, shape_style)
        pen = gdr if gdr is not None else dr
        for poly in polys:
            # a catalogue entry with a broken size must never paint across the picture: drop non-finite
            # points and keep the rest near the canvas (this is what turned a bad shape into a flat fill)
            pts = [(x * ks, y * ks) for x, y in poly if np.isfinite(x) and np.isfinite(y)]
            if len(pts) < 2:
                continue
            lim_x, lim_y = 4.0 * OW, 4.0 * OH
            pts = [(min(max(x, -lim_x), 2 * lim_x), min(max(y, -lim_y), 2 * lim_y)) for x, y in pts]
            if tstyle.get("sketch"):
                # A hand-drawn outline, not a trembling one: the deviation is a smooth function of the position
                # along the shape (two low-frequency waves), so the line curves gently instead of buzzing. Two
                # or three loops, each starting elsewhere and overshooting its start, cross like a pen going
                # round twice.
                import math as _m, random as _rnd
                base_amp = max(1.5 * SS, edge / 700.0)
                n = len(pts)
                loops = ((max(2, lw), 1.0), (max(1, lw // 2), 2.2), (max(1, lw // 2), 3.4))
                for wid, amp_k in loops:
                    amp = base_amp * amp_k
                    w1 = _rnd.uniform(1.5, 3.5)          # waves around the outline
                    w2 = _rnd.uniform(3.0, 6.0)
                    p1, p2 = _rnd.uniform(0, 2 * _m.pi), _rnd.uniform(0, 2 * _m.pi)
                    dx, dy = _rnd.uniform(-amp, amp), _rnd.uniform(-amp, amp)
                    if closed and n >= 6:
                        a0 = _rnd.randint(0, n - 2)
                        span = int(n * _rnd.uniform(1.06, 1.22))
                        seq = [pts[(a0 + i) % n] for i in range(span)]
                        length = float(n)
                    else:
                        a0 = _rnd.randint(0, max(0, n - 4))
                        span = int(n * _rnd.uniform(0.55, 1.0))
                        seq = pts[a0:a0 + max(2, span)]
                        length = float(max(2, len(seq)))
                    j = []
                    for i, (x, y) in enumerate(seq):
                        th = 2 * _m.pi * i / length
                        d = amp * (_m.sin(w1 * th + p1) + 0.5 * _m.sin(w2 * th + p2))
                        # offset along the local normal so the curve shifts, not the points scatter
                        nx, ny = j[-1] if j else (0.0, 0.0)
                        j.append((x + dx + d * 0.6, y + dy + d * 0.4))
                        _ = (nx, ny)
                    if len(j) >= 2:
                        pen.line(j, fill=col, width=wid)
            else:
                pen.line(pts + [pts[0]] if closed else pts, fill=col, width=lw)
        if e.get("kind") == "point":
            x, y = e["x"] * ks, e["y"] * ks
            r = 6 * lw
            if tstyle.get("sketch"):
                # a casual pen circle: a smooth loop whose radius wobbles gently with the angle, drawn one
                # and a bit times, with a lighter partial retrace. No per-pixel tremor.
                import math as _m, random as _rnd
                cx = x + _rnd.uniform(-0.05, 0.05) * r
                cy = y + _rnd.uniform(-0.05, 0.05) * r
                a1, a2 = _rnd.uniform(0.02, 0.05), _rnd.uniform(0.01, 0.03)
                p1, p2 = _rnd.uniform(0, 2 * _m.pi), _rnd.uniform(0, 2 * _m.pi)
                for p_i, (frac, wid) in enumerate(((1.0, max(2, lw)), (0.55, max(1, lw // 2)))):
                    a0 = _rnd.uniform(0, 2 * _m.pi)
                    sweep = 2 * _m.pi * (_rnd.uniform(1.03, 1.12) if p_i == 0 else _rnd.uniform(0.5, 0.75))
                    rr_k = 1.0 if p_i == 0 else _rnd.uniform(1.02, 1.06)
                    steps = max(12, int(40 * frac))
                    ring = []
                    for k in range(steps + 1):
                        a = a0 + sweep * k / steps
                        rr = r * rr_k * (1.0 + a1 * _m.sin(a + p1) + a2 * _m.sin(2 * a + p2))
                        ring.append((cx + rr * _m.cos(a), cy + rr * _m.sin(a)))
                    dr.line(ring, fill=col, width=wid)
            else:
                dr.ellipse([x - r, y - r, x + r, y + r], outline=col, width=lw)
        anchors = style.label_anchors(e, w, h, polys) if e.get("label") else []
        if anchors:
            gap = 6 * lw + 3 * SS if e.get("kind") == "point" else 3 * lw
            ins = style.inside_anchor(polys)
            if ins is not None:
                ins = (ins[0] * ks, ins[1] * ks, ins[2] * ks, ins[3] * ks)
            text = _ascii_label(e.get("name") or e["label"])
            labels.append(([(x * ks, y * ks, u) for x, y, u in anchors], text, col, font, gap, ins))
    if glow is not None:
        gl = glow.resize((W2, H2), Image.LANCZOS).filter(__import__("PIL.ImageFilter", fromlist=["ImageFilter"]).GaussianBlur(max(2, int(edge / 900))))
        im = Image.alpha_composite(im.convert("RGBA"), gl)
        im = Image.alpha_composite(im, gl).convert("RGB")
    star = style.rgb(style.star_color()) + (255,)
    for r in named_rows or []:
        x, y = r.x * ks, r.y * ks
        rad = 9 * lw
        dr.ellipse([x - rad, y - rad, x + rad, y + rad], outline=star, width=lw)
        name = r.name + (" %s" % r.extra.get("common") if r.extra.get("common") else "")
        labels.append(([(x, y, None)], name, star, sfont, rad + 3 * SS, None))
    # greedy placement: inside a big object's own shape, otherwise outside an outline (or around a point),
    # inside the frame, clear of labels already placed and preferably on a dark background
    placed = list(grid_boxes)
    bright = style.Brightness(np.asarray(im.convert("L"), np.float32) / 255.0, 1.0 / SS)
    # Messier objects choose first, then the other objects, then star names
    labels.sort(key=lambda lb: (lb[3] is sfont, not (lb[1][:1] == "M" and lb[1][1:2].isdigit())))
    LP = max(1, int(round(3 * SS)))                # padding around the text: the chip the viewer draws too
    for anchors, text, col, f, gap, inside in labels:
        tw, th = dr.textbbox((0, 0), text, font=f)[2:]
        bw, bh = tw + 2 * LP, th + 2 * LP
        best = None
        if inside is not None:
            cx, cy, iw, ih = inside
            if bw <= 0.6 * iw and bh <= 0.6 * ih:
                bx = min(max(cx - bw / 2, 2 * SS), OW - bw - 2 * SS)
                by = min(max(cy - bh / 2, 2 * SS), OH - bh - 2 * SS)
                box = (bx, by, bx + bw, by + bh)
                if style.choose_box([box], placed, None, pad=2.0 * SS, may_skip=True) is not None \
                        and bright(box) < 0.55:
                    best = box
        if best is None:
            boxes = []
            for x, y, outward in anchors:
                for ox, oy in style.label_offsets(outward, bw, bh, gap, rings=4, step=4 * lw):
                    bx = min(max(x + ox, 2 * SS), OW - bw - 2 * SS)
                    by = min(max(y + oy, 2 * SS), OH - bh - 2 * SS)
                    boxes.append((bx, by, bx + bw, by + bh))
            best = style.choose_box(boxes, placed, bright, pad=2.0 * SS, may_skip=True)
        if best is None:
            continue                     # crowded patch: better one name missing than two on top of each other
        placed.append(best)
        if tstyle.get("label_bg") == "cream":
            dr.rounded_rectangle(best, radius=3 * SS, fill=(236, 222, 190, 208),
                                 outline=(58, 38, 18, 170), width=max(1, int(round(SS))))
        elif tstyle.get("chip"):
            dr.rounded_rectangle(best, radius=3 * SS, fill=(2, 4, 10, 168),
                                 outline=col[:3] + (200,), width=max(1, int(round(SS))))
        if tstyle.get("glow"):
            dr.text((best[0] + LP, best[1] + LP), text, font=f, fill=col,
                    stroke_width=max(1, SS), stroke_fill=col[:3] + (90,))
        dr.text((best[0] + LP, best[1] + LP), text, fill=col, font=f)
    ov = ov.resize((W2, H2), Image.LANCZOS)
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    if tstyle.get("decor"):
        c2 = dict(ctx or {})
        c2["W"], c2["H"] = im.size
        if "objects" not in c2:
            # only the catalogues that are switched on: the X marks follow the filter, so the route must too
            def _shown(e):
                if categories is None:
                    return True
                return (e.get("category") in categories) or (e.get("type_group") in categories)
            c2["objects"] = [(e.get("name") or e.get("label") or "",
                              (e.get("x") or 0) * k, (e.get("y") or 0) * k) for e in shapes if _shown(e)]
        im = decor.draw(im, c2, tstyle)
    ext = os.path.splitext(out_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        im.save(out_path, "JPEG", quality=quality)
    else:
        im.save(out_path)
    return out_path
