"""Picture frames and furniture for the decorated annotation styles.

The plain styles draw only shapes, labels and the grid. The five "picture" styles also want a frame around the
photograph: a survey plate with edge coordinates and a scale bar, an observing journal with a paper border, a
neon HUD with corner brackets, an astrogation console with telemetry, a treasure map with X marks and a
legend. Those trimmings live here so render.py stays about the objects themselves.

Every size is expressed against a 1200-pixel-wide reference picture and scaled to the render size, so a
4000-pixel preview and a 1200-pixel one look the same. Everything is drawn from the frame's own WCS: the edge
coordinates, the scale bar, the north arrow and the nav bearings are computed, never written by hand.
"""
from __future__ import annotations

import math
import time

import numpy as np

PAPER = (236, 222, 190)


def _font(size, family="mono"):
    from PIL import ImageFont
    import os
    files = {"mono": (r"C:\Windows\Fonts\consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
             "serif": (r"C:\Windows\Fonts\georgia.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
             "script": (r"C:\Windows\Fonts\segoesc.ttf", r"C:\Windows\Fonts\comic.ttf"),
             "cjk": (r"C:\Windows\Fonts\msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")}
    for f in files.get(family, files["mono"]):
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, max(7, int(size)))
            except OSError:
                pass
    return ImageFont.load_default(size=max(7, int(size)))


def _dashed(dr, pts, fill, width=1, dash=9, gap=6):
    on = True
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1e-6:
            continue
        t = 0.0
        while t < seg:
            step = min(dash if on else gap, seg - t)
            if on:
                dr.line([(x0 + (x1 - x0) * t / seg, y0 + (y1 - y0) * t / seg),
                         (x0 + (x1 - x0) * (t + step) / seg, y0 + (y1 - y0) * (t + step) / seg)],
                        fill=fill, width=width)
            t += step
            on = not on


def _compass(dr, cx, cy, r, col, width=1, points=8):
    for i in range(points * 2):
        ang = math.pi * i / points - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.62
        dr.line([(cx, cy), (cx + rr * math.cos(ang), cy + rr * math.sin(ang))],
                fill=col, width=width if i % 2 == 0 else max(1, width - 1))
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=width)


def _pix_from_radec(ctx, ra, dec):
    wcs, fw = ctx.get("wcs"), ctx.get("full_w")
    if wcs is None or not fw:
        return None
    try:
        x, y = wcs.all_world2pix([[float(ra), float(dec)]], 0)[0]
    except Exception:
        return None
    k = ctx.get("W", fw) / float(fw)
    return float(x) * k, float(y) * k


def _radec_from_pix(ctx, x, y):
    wcs, fw = ctx.get("wcs"), ctx.get("full_w")
    if wcs is None or not fw:
        return None
    k = ctx.get("W", fw) / float(fw)
    try:
        ra, dec = wcs.all_pix2world([[x / k, y / k]], 0)[0]
    except Exception:
        return None
    return float(ra), float(dec)


def _north_east(ctx):
    wcs, fw, fh = ctx.get("wcs"), ctx.get("full_w"), ctx.get("full_h")
    if wcs is None:
        return (0.0, -1.0), (1.0, 0.0)
    try:
        ra, dec = wcs.pixel_to_world_values(fw / 2.0, fh / 2.0)
        x0, y0 = _pix_from_radec(ctx, ra, dec) or (0, 0)
        xn, yn = _pix_from_radec(ctx, ra, dec + 0.05) or (x0, y0 - 1)
        xe, ye = _pix_from_radec(ctx, ra + 0.05 / max(0.2, math.cos(math.radians(dec))), dec) or (x0 + 1, y0)
    except Exception:
        return (0.0, -1.0), (1.0, 0.0)
    vn = np.array([xn - x0, yn - y0], float)
    ve = np.array([xe - x0, ye - y0], float)
    if np.hypot(*vn) < 1e-9:
        vn = np.array([0.0, -1.0])
    if np.hypot(*ve) < 1e-9:
        ve = np.array([1.0, 0.0])
    return tuple(vn / np.hypot(*vn)), tuple(ve / np.hypot(*ve))


def _scale_bar(ctx, s):
    ps = ctx.get("pixscale") or 0.0
    if not ps or ps <= 0:
        return None
    k = ctx.get("W", ctx.get("full_w", 1)) / float(ctx.get("full_w") or 1)
    for arcmin in (5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300):
        px = arcmin * 60.0 / ps * k
        if 120 * s <= px <= 300 * s:
            return px, "%d′" % arcmin
    return 60 * 60.0 / ps * k, "1°"


def _hms_ra(ra_deg):
    h = (float(ra_deg) / 15.0) % 24
    return "%02dh%02dm" % (int(h), int(h * 60) % 60)


def _dms_dec(dec_deg):
    sign = "-" if dec_deg < 0 else "+"
    d = abs(float(dec_deg))
    return "%s%02d°%02d′" % (sign, int(d), int(d * 60) % 60)


def _scale(W, H):
    return max(W, H) / 1200.0


def _plate(im, ctx, ink=(228, 236, 248)):
    from PIL import Image, ImageDraw
    W, H = im.size
    s = _scale(W, H)
    m = int(52 * s)
    canvas = Image.new("RGB", (W + 2 * m, H + 2 * m), (6, 8, 12))
    canvas.paste(im, (m, m))
    d = ImageDraw.Draw(canvas)
    f_tick = _font(9 * s)
    f_head = _font(13 * s)
    f_small = _font(11 * s)
    step = max(1, int(100 * s))
    for x in range(m, m + W, step):
        d.line([(x, m - 8 * s), (x, m - 2 * s)], fill=(150, 168, 190), width=1)
        d.line([(x, m + H + 2 * s), (x, m + H + 8 * s)], fill=(150, 168, 190), width=1)
        rd = _radec_from_pix(ctx, x - m, 0)
        if rd:
            d.text((x + 2 * s, m - 22 * s), _hms_ra(rd[0]), font=f_tick, fill=(140, 158, 180))
    for y in range(m, m + H, step):
        d.line([(m - 8 * s, y), (m - 2 * s, y)], fill=(150, 168, 190), width=1)
        d.line([(m + W + 2 * s, y), (m + W + 8 * s, y)], fill=(150, 168, 190), width=1)
        rd = _radec_from_pix(ctx, 0, y - m)
        if rd:
            d.text((m - 48 * s, y - 5 * s), _dms_dec(rd[1]), font=f_tick, fill=(140, 158, 180))
    target = (ctx.get("target") or "").strip()
    d.text((m, 12 * s), "TTSKYANNOT · FIELD PLATE" + (" · " + target.upper() if target else ""),
           font=f_head, fill=(206, 216, 228))
    right = []
    if ctx.get("full_w"):
        right.append("%d × %d" % (ctx["full_w"], ctx["full_h"]))
    if ctx.get("pixscale"):
        right.append("%.2f″/px" % ctx["pixscale"])
    if ctx.get("rotation") is not None:
        right.append("PA %.1f°" % float(ctx["rotation"]))
    if ctx.get("matched"):
        right.append("%d inliers" % int(ctx["matched"]))
    if right:
        rx = m + W - int(340 * s)
        d.text((rx, 12 * s), " · ".join(right[:3]), font=f_small, fill=(160, 176, 196))
        if len(right) > 3:
            d.text((rx, 28 * s), " · ".join(right[3:]), font=f_small, fill=(160, 176, 196))
    bar = _scale_bar(ctx, s)
    if bar:
        px, label = bar
        sx, sy = m + 40 * s, m + H - 46 * s
        d.line([(sx, sy), (sx + px, sy)], fill=ink, width=max(2, int(2 * s)))
        d.line([(sx, sy - 5 * s), (sx, sy + 5 * s)], fill=ink, width=max(2, int(2 * s)))
        d.line([(sx + px, sy - 5 * s), (sx + px, sy + 5 * s)], fill=ink, width=max(2, int(2 * s)))
        d.text((sx + 4 * s, sy - 22 * s), label, font=_font(12 * s), fill=ink)
    vn, ve = _north_east(ctx)
    cx, cy = m + W - 90 * s, m + H - 86 * s
    for vec, lab in ((vn, "N"), (ve, "E")):
        ex, ey = cx + vec[0] * 36 * s, cy + vec[1] * 36 * s
        d.line([(cx, cy), (ex, ey)], fill=ink, width=max(2, int(2 * s)))
        d.text((ex - 4 * s, ey - 6 * s), lab, font=_font(12 * s), fill=ink)
    return canvas


def _journal(im, ctx, ink=(59, 46, 30)):
    from PIL import Image, ImageDraw
    W, H = im.size
    s = _scale(W, H)
    border, strip = int(30 * s), int(90 * s)
    canvas = Image.new("RGB", (W + 2 * border, H + border + strip), (233, 223, 200))
    canvas.paste(im, (border, border))
    d = ImageDraw.Draw(canvas)
    target = (ctx.get("target") or "").strip()
    date = ctx.get("date") or time.strftime("%Y.%m.%d")
    caption = ("%s · %s · TTSkyAnnot" % (target, date)) if target else ("%s · TTSkyAnnot" % date)
    d.text((border, border + H + int(16 * s)), caption, font=_font(30 * s, "script"), fill=ink)
    return canvas


def _neon(im, ctx):
    from PIL import Image, ImageDraw
    W, H = im.size
    s = _scale(W, H)
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    cyan = (60, 240, 255, 255)
    d.rectangle([6 * s, 6 * s, W - 7 * s, H - 7 * s], outline=(60, 240, 255, 110), width=max(1, int(s)))
    for (cx, cy, dx, dy) in ((14 * s, 14 * s, 1, 1), (W - 14 * s, 14 * s, -1, 1),
                             (14 * s, H - 14 * s, 1, -1), (W - 14 * s, H - 14 * s, -1, -1)):
        d.line([(cx, cy), (cx + dx * 34 * s, cy)], fill=cyan, width=max(2, int(2 * s)))
        d.line([(cx, cy), (cx, cy + dy * 34 * s)], fill=cyan, width=max(2, int(2 * s)))
    m = ctx.get("matched")
    if m:
        d.text((W - 300 * s, 18 * s), "SIGNAL LOCKED · %d INLIERS" % int(m),
               font=_font(11 * s), fill=cyan)
    step = max(1, int(3 * s))
    for y in range(0, H, step):
        d.line([(0, y), (W, y)], fill=(0, 0, 0, 22))
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def _nav(im, ctx):
    from PIL import Image, ImageDraw
    W, H = im.size
    s = _scale(W, H)
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    cyan = (120, 255, 214, 255)
    dim = (120, 180, 190, 110)
    cx, cy = W / 2.0, H / 2.0
    f_label = _font(10 * s)
    for name, x, y in (ctx.get("objects") or []):
        ang = math.degrees(math.atan2(x - cx, cy - y)) % 360
        rng = math.hypot(x - cx, y - cy) * (ctx.get("pixscale") or 0) / 60.0
        d.line([(cx, cy), (x, y)], fill=dim, width=max(1, int(s)))
        d.text((x + 18 * s, y + 8 * s), "BRG %03d°  RNG %04.1f′" % (ang, rng), font=f_label, fill=dim)
    lines = ["ASTROMETRIC LOCK", "FRAME ICRS / J2000"]
    if (ctx.get("target") or "").strip():
        lines.append("FIELD  %s" % ctx["target"].upper())
    if ctx.get("pixscale"):
        lines.append("SCALE  %.2f\"/px" % ctx["pixscale"])
    if ctx.get("rotation") is not None:
        lines.append("ROT  %.1f°" % float(ctx["rotation"]))
    if ctx.get("matched"):
        lines.append("INLIERS  %d" % int(ctx["matched"]))
    for i, line in enumerate(lines):
        d.text((18 * s, 18 * s + i * 15 * s), line, font=f_label, fill=cyan if i == 0 else dim)
    for i in range(0, W, max(1, int(80 * s))):
        d.text((i + 2 * s, H - 20 * s), "%03d" % int(i / W * 360), font=_font(8 * s), fill=dim)
    for (ccx, ccy, dx, dy) in ((12 * s, 12 * s, 1, 1), (W - 12 * s, 12 * s, -1, 1),
                               (12 * s, H - 12 * s, 1, -1), (W - 12 * s, H - 12 * s, -1, -1)):
        d.line([(ccx, ccy), (ccx + dx * 40 * s, ccy)], fill=cyan, width=max(2, int(2 * s)))
        d.line([(ccx, ccy), (ccx, ccy + dy * 40 * s)], fill=cyan, width=max(2, int(2 * s)))
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def _treasure(im, ctx):
    from PIL import Image, ImageDraw
    W, H = im.size
    s = _scale(W, H)
    ink = (58, 38, 18, 235)
    red = (168, 40, 28, 235)
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    pts = [(x, y) for _n, x, y in (ctx.get("objects") or [])]
    if len(pts) > 1:
        for i in range(len(pts) - 1):
            _dashed(d, [pts[i], pts[i + 1]], red, max(1, int(2 * s)),
                    max(6, int(10 * s)), max(4, int(7 * s)))
    d.rectangle([6 * s, 6 * s, W - 7 * s, H - 7 * s], outline=ink, width=max(1, int(2 * s)))
    lx, ly = 30 * s, H - 140 * s
    d.rectangle([lx, ly, lx + 240 * s, ly + 92 * s], fill=PAPER + (205,), outline=ink,
                width=max(1, int(2 * s)))
    d.text((lx + 10 * s, ly + 8 * s), "LEGEND", font=_font(13 * s, "serif"), fill=ink)
    d.line([(lx + 12 * s, ly + 36 * s), (lx + 30 * s, ly + 36 * s)], fill=red, width=max(2, int(3 * s)))
    d.text((lx + 36 * s, ly + 28 * s), "X = object", font=_font(15 * s, "script"), fill=ink)
    d.line([(lx + 12 * s, ly + 60 * s), (lx + 30 * s, ly + 60 * s)], fill=red, width=max(1, int(2 * s)))
    d.text((lx + 36 * s, ly + 52 * s), "dashed = route", font=_font(14 * s, "script"), fill=ink)
    cr = 44 * s
    d.ellipse([W - 150 * s - cr, H - 150 * s - cr, W - 150 * s + cr, H - 150 * s + cr],
              fill=PAPER + (210,), outline=ink, width=max(1, int(2 * s)))
    _compass(d, W - 150 * s, H - 150 * s, cr, ink, max(1, int(2 * s)), 8)
    d.text((W - 150 * s - 5 * s, H - 150 * s - cr - 20 * s), "N", font=_font(14 * s, "serif"), fill=ink)
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def draw(im, ctx, th):
    """Paint the frame furniture for the active theme; returns the finished picture."""
    kind = th.get("decor")
    try:
        if kind == "plate":
            return _plate(im, ctx)
        if kind == "journal":
            return _journal(im, ctx)
        if kind == "neon":
            return _neon(im, ctx)
        if kind == "nav":
            return _nav(im, ctx)
        if kind == "treasure":
            return _treasure(im, ctx)
    except Exception:
        pass
    return im
