"""Deep-sky object shapes in pixel space: OpenNGC/Stellarium outlines where available, otherwise the
catalogue ellipse (major/minor axis, position angle) or a circle (Sharpless diameter)."""
from __future__ import annotations

import math

import numpy as np

from . import catalogs


def _ellipse_world(ra, dec, maj_arcmin, min_arcmin, pa_deg, n=72):
    """Points of an ellipse on the sky (tangent-plane approximation, fine for objects of a few degrees)."""
    a = maj_arcmin / 120.0          # semi-axes in degrees
    b = (min_arcmin or maj_arcmin) / 120.0
    pa = math.radians(pa_deg or 0.0)
    t = np.linspace(0, 2 * np.pi, n, endpoint=True)
    # offsets: north (+dec) and east (+ra); position angle measured from north towards east
    u, v = a * np.cos(t), b * np.sin(t)
    north = u * math.cos(pa) - v * math.sin(pa)
    east = u * math.sin(pa) + v * math.cos(pa)
    dec_p = dec + north
    ra_p = ra + east / max(math.cos(math.radians(dec)), 1e-6)
    return ra_p, dec_p


CATEGORIES = ("galaxy", "nebula", "cluster", "dark", "other")


def category(entry) -> str:
    """Which annotation group an object belongs to (the user switches groups on and off)."""
    t = entry.get("type") or ""
    name = entry.get("name") or ""
    if entry.get("catalog") == "Sharpless":
        return "nebula"
    if t in ("G", "GPair", "GTrpl", "GGroup"):
        return "galaxy"
    if t in ("OCl", "GCl", "*Ass", "Cl+N"):
        return "cluster"
    if t == "DrkN" or name.startswith(("B", "LDN")):
        return "dark"
    if t in ("PN", "HII", "EmN", "Neb", "RfN", "SNR") or entry.get("kind") == "outline":
        return "nebula"
    return "other"                         # stars, double stars, novae and other NGC/IC entries


def collect(P, include_others=True, min_size_px=6.0):
    """Shapes of objects that touch the frame. Returns a list of dicts:
    {name, label, catalog, kind: outline|ellipse|circle, source, ra, dec, polygons: [[[x, y], ...], ...]}.
    Pixel coordinates use PixInsight's convention."""
    out = []
    W, H = P.w, P.h
    diag = math.hypot(W, H)
    outlined = set()
    m2n = {o.messier: o.name for o in catalogs.messier() if o.ngcic}
    groups: dict = {}
    for name, src, pts in catalogs.outlines():
        ra = np.array([p[0] for p in pts])
        dec = np.array([p[1] for p in pts])
        x, y = P.xy(ra, dec)
        if not np.all(np.isfinite(x)):
            continue
        if x.max() < 0 or y.max() < 0 or x.min() > W or y.min() > H:
            continue
        if max(x.max() - x.min(), y.max() - y.min()) > 20 * diag:     # wrapped around the projection
            continue
        key = m2n.get(name, name)
        g = groups.setdefault((key, src), {"name": key, "label": "" if name.isdigit() else name,
                                           "kind": "outline", "source": src, "polygons": [],
                                           "ra": float(np.mean(ra)), "dec": float(np.mean(dec))})
        g["polygons"].append([[round(float(a), 2), round(float(b), 2)] for a, b in zip(x, y)])
    for g in groups.values():
        outlined.add(g["name"])
        out.append(g)

    dsos = [(d, "Messier" if d.messier else ("NGC-IC" if d.name.startswith(("NGC", "IC")) else "Other"))
            for d in catalogs.openngc() if (include_others or d.name.startswith(("NGC", "IC")) or d.messier)]
    dsos += [(d, "Sharpless") for d in catalogs.sharpless()]
    scale = math.sqrt(abs(np.linalg.det(P.wcs.pixel_scale_matrix))) * 60.0   # arcmin per pixel
    ra = np.array([d.ra for d, _ in dsos])
    dec = np.array([d.dec for d, _ in dsos])
    cx, cy = P.xy(ra, dec)
    for i, (d, cat) in enumerate(dsos):
        r_px = ((d.maj or 0) / 2.0) / scale if d.maj else 0.0
        if not (np.isfinite(cx[i]) and P.inside(cx[i], cy[i], margin=r_px)):
            continue
        label = d.messier if d.messier else d.name
        if d.messier and d.ngcic and d.ngcic != d.messier:
            label = "%s (%s)" % (d.messier, d.ngcic)
        entry = {"name": d.name, "label": label + (" %s" % d.common if d.common else ""), "catalog": cat,
                 "type": d.type, "ra": d.ra, "dec": d.dec, "x": round(float(cx[i]), 2), "y": round(float(cy[i]), 2)}
        if d.name in outlined:
            for o in out:
                if o["name"] == d.name and "x" not in o:
                    o.update({k: v for k, v in entry.items() if k not in ("name",)})
                    o["label"] = entry["label"]
            continue
        if d.maj and 2 * r_px >= min_size_px:
            er, ed = _ellipse_world(d.ra, d.dec, d.maj, d.minr, d.pa)
            x, y = P.xy(er, ed)
            entry.update(kind="circle" if cat == "Sharpless" else "ellipse",
                         source="Sharpless" if cat == "Sharpless" else "OpenNGC",
                         polygons=[[[round(float(a), 2), round(float(b), 2)] for a, b in zip(x, y)]])
        else:
            entry.update(kind="point", source="OpenNGC" if cat != "Sharpless" else "Sharpless", polygons=[])
        out.append(entry)
    for o in out:
        o.setdefault("catalog", "Outline")
        o["category"] = category(o)
        if "x" not in o and o["polygons"]:
            xs = [p[0] for poly in o["polygons"] for p in poly]
            ys = [p[1] for poly in o["polygons"] for p in poly]
            o["x"], o["y"] = round(float(np.mean(xs)), 2), round(float(np.mean(ys)), 2)
    return out
