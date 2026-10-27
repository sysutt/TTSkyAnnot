"""Deep-sky object shapes in pixel space: OpenNGC/Stellarium outlines where available, otherwise the
catalogue ellipse (major/minor axis, position angle) or a circle (Sharpless diameter)."""
from __future__ import annotations

import math

import numpy as np

from . import catalogs, catalogue


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


# An outline is trustworthy as a direction only when it is actually elongated. Below this ratio its "long axis"
# is a property of the vertex noise, not of the object, so the shape stays a circle.
MIN_AXIS_ELONGATION = 1.30


def _outline_axis(pts):
    """(position angle north through east, elongation) of an outline's long axis.

    OpenNGC leaves `PosAng` empty for 2586 entries — every nebula in the sample, NGC1499 among them. Drawing
    those as an ellipse at 0 degrees produces a vertical oval, which asserts a direction the catalogue never
    gave. When an outline is available its own long axis stands in, and its elongation says whether that axis
    means anything.
    """
    a = np.asarray(pts, np.float64)
    if a.ndim != 2 or a.shape[0] < 3:
        return None, 0.0
    ra, de = a[:, 0], a[:, 1]
    ok = np.isfinite(ra) & np.isfinite(de)
    if int(ok.sum()) < 3:
        return None, 0.0
    ra, de = ra[ok], de[ok]
    east = (ra - ra.mean()) * math.cos(math.radians(de.mean()))
    north = de - de.mean()
    cee = float(np.dot(east, east))
    cnn = float(np.dot(north, north))
    cen = float(np.dot(east, north))
    tr, det = cee + cnn, cee * cnn - cen * cen
    disc = max(0.0, tr * tr / 4.0 - det)
    l1 = tr / 2.0 + math.sqrt(disc)
    l2 = max(1e-30, tr / 2.0 - math.sqrt(disc))
    if not np.isfinite(l1) or l1 <= 0:
        return None, 0.0
    # the major axis, measured from north towards east
    pa = math.degrees(0.5 * math.atan2(2.0 * cen, cnn - cee)) % 180.0
    return float(pa), float(math.sqrt(l1 / l2))


def _axis_for(d, outlined_pts) -> tuple:
    """(position angle or None, major arcmin, minor arcmin) for a catalogue-sized shape.

    The catalogue's own angle wins. Failing that, the outline's long axis is used when the outline is elongated
    enough for the direction to mean anything, with the minor axis taken from the same outline. When neither
    exists the angle comes back as None, and the callers draw a circle: an object with no known direction must
    not be drawn as if it had one.
    """
    maj = d.maj or 0.0
    if not maj:
        return None, 0.0, 0.0
    # 目录尺寸常常只是核心：M76 目录 1.12'，实际约 2.7'。轮廓是画面上真实可见的范围，
    # 它比目录尺寸更大时就用轮廓的角半径——否则「圆圈」会比画面上的天体小一大截。
    if outlined_pts:
        try:
            import math as _m
            dec0 = float(d.dec)
            cosd = max(0.05, _m.cos(_m.radians(dec0)))
            rmax = 0.0
            for ra_v, dec_v in outlined_pts:
                dra = ((float(ra_v) - float(d.ra) + 180.0) % 360.0) - 180.0
                dd = float(dec_v) - dec0
                rmax = max(rmax, _m.hypot(dra * cosd, dd))
            maj_outline = 2.0 * rmax * 60.0          # 角分（直径）
            if maj_outline > maj:
                maj = maj_outline
        except Exception:
            pass
    minor = d.minr or maj
    if getattr(d, "pa", None) is not None:
        return float(d.pa), maj, minor
    if outlined_pts:
        pa, elong = _outline_axis(outlined_pts)
        if pa is not None and elong >= MIN_AXIS_ELONGATION:
            return pa, maj, max(maj / elong, 1e-6)
    return None, maj, maj


CATEGORIES = ("galaxy", "nebula", "cluster", "dark", "other")


def object_type(entry) -> str:
    """The kind of object this is — galaxy, nebula, cluster, dark nebula or other.

    This decides the overlay colour only. Nebulae are mostly red or pink in pictures so their outlines are
    cyan, galaxies are yellow-white so theirs are rose, and clusters sit on star fields so theirs are gold;
    see style.py. The type is a property of the object itself, not of the catalogue that lists it.
    """
    t = entry.get("type") or ""
    name = entry.get("name") or ""
    if entry.get("catalog") == "Sharpless":
        return "nebula"
    if t in ("G", "GPair", "GTrpl", "GGroup"):
        return "galaxy"
    if t in ("OCl", "GCl", "*Ass", "Cl+N"):
        return "cluster"
    if t == "DrkN" or name.startswith(("B", "LDN", "LDN")):
        return "dark"
    if t in ("PN", "HII", "EmN", "Neb", "RfN", "SNR", "BN") or entry.get("kind") == "outline":
        return "nebula"                    # BN: Lynds' bright nebulae, which the online catalogue uses
    return "other"                         # stars, double stars, novae and other NGC/IC entries


def category(entry) -> str:
    """Which annotation group an object is listed under: the catalogue its designation belongs to.

    The groups are catalogues (Messier, NGC, IC, Sharpless, ...), so this is the key that switches them on and
    off. `object_type` above remains what colours the overlay.
    """
    from . import catalogue
    return catalogue.catalogue_of(entry) or "other"


def collect(P, include_others=True, min_size_px=6.0, cats=None):
    """Shapes of objects that touch the frame. Returns a list of dicts:
    {name, label, catalog, kind: outline|ellipse|circle, source, ra, dec, polygons: [[[x, y], ...], ...]}.
    Pixel coordinates use PixInsight's convention.

    `cats` is the tuple of enabled deep-sky catalogues, in precedence order (None or empty = all of them).
    An object that several catalogues list is kept under the first *enabled* one, so switching Messier off
    hands M31 to NGC and its label becomes NGC224; an object no enabled catalogue lists is left out. This is
    the same rule annotate.dso_blocks applies to the text output.
    """
    on = tuple(cats) if cats else tuple(catalogue.KEYS)
    entries = catalogue.unique(list(catalogs.openngc()) + list(catalogs.sharpless()))
    assigned = catalogue.assign(entries, on)
    by_name = {e.name: e for e in entries}
    out = []
    W, H = P.w, P.h
    diag = math.hypot(W, H)
    outlined = set()
    outline_pts: dict = {}          # name -> sky vertices, so a missing PosAng can be taken from the outline
    m2n = {o.messier: o.name for o in catalogs.messier() if o.ngcic}
    groups: dict = {}
    for name, src, pts in catalogs.outlines():
        ra = np.array([p[0] for p in pts])
        dec = np.array([p[1] for p in pts])
        x, y = P.xy(ra, dec)
        # The projector returns NaN for points beyond about twice the field radius, because a SIP solution is
        # only trustworthy near the frame. A big nebula's outline normally runs well outside the frame — all
        # of NGC7000 does — so the vertices that cannot be projected are dropped and what is left is drawn.
        # Rejecting the whole outline over one such vertex would throw away the only true shape available and
        # fall back to the catalogue ellipse, which has no position angle for exactly these large nebulae.
        good = np.isfinite(x) & np.isfinite(y)
        if good.sum() < 3:                            # nothing of it is near enough to draw
            continue
        x, y = x[good], y[good]
        if x.max() < 0 or y.max() < 0 or x.min() > W or y.min() > H:
            continue
        if max(x.max() - x.min(), y.max() - y.min()) > 20 * diag:     # wrapped around the projection
            continue
        key = m2n.get(name, name)
        g = groups.setdefault((key, src), {"name": key, "label": "" if name.isdigit() else name,
                                           "kind": "outline", "source": src, "polygons": [],
                                           "ra": float(np.mean(ra)), "dec": float(np.mean(dec))})
        g["polygons"].append([[round(float(a), 2), round(float(b), 2)] for a, b in zip(x, y)])
        outline_pts.setdefault(key, pts)
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
        # an outlined object is matched even when its catalogue centre is off the frame (M8 below M20): its
        # outline is in the picture, and the "circles" style needs its catalogue size
        if not np.isfinite(cx[i]) or (d.name not in outlined and not P.inside(cx[i], cy[i], margin=r_px)):
            continue
        label = d.messier if d.messier else d.name
        if d.messier and d.ngcic and d.ngcic != d.messier:
            label = "%s (%s)" % (d.messier, d.ngcic)
        entry = {"name": d.name, "label": label + (" %s" % d.common if d.common else ""), "catalog": cat,
                 "type": d.type, "ra": d.ra, "dec": d.dec, "x": round(float(cx[i]), 2), "y": round(float(cy[i]), 2)}
        if d.name in outlined:
            circle = None
            if d.maj and 2 * r_px >= min_size_px:          # catalogue size, for the "circles" display style
                pa, maj, minor = _axis_for(d, outline_pts.get(d.name))
                er, ed = _ellipse_world(d.ra, d.dec, maj, minor, pa)
                x, y = P.xy(er, ed)
                circle = [[[round(float(a), 2), round(float(b), 2)] for a, b in zip(x, y)]]
            for o in out:
                if o["name"] == d.name and "x" not in o:
                    o.update({k: v for k, v in entry.items() if k not in ("name",)})
                    o["label"] = entry["label"]
                    if circle:
                        o["circle"] = circle
            continue
        if d.maj and 2 * r_px >= min_size_px:
            pa, maj, minor = _axis_for(d, None)
            er, ed = _ellipse_world(d.ra, d.dec, maj, minor, pa)
            x, y = P.xy(er, ed)
            # a Sharpless entry is a diameter, not a pair of axes, so it stays a circle; for OpenNGC the shape
            # is an ellipse only when a direction is actually known
            round_object = cat == "Sharpless" or pa is None
            entry.update(kind="circle" if round_object else "ellipse",
                         source="Sharpless" if cat == "Sharpless" else "OpenNGC",
                         polygons=[[[round(float(a), 2), round(float(b), 2)] for a, b in zip(x, y)]])
        else:
            entry.update(kind="point", source="OpenNGC" if cat != "Sharpless" else "Sharpless", polygons=[])
        out.append(entry)
    kept = []
    for o in out:
        o.setdefault("catalog", "Outline")
        entry = by_name.get(o["name"])
        key = assigned.get(catalogue.key_of(entry)) if entry is not None else ""
        if not key:
            continue                        # no enabled catalogue lists it
        o["category"] = key                 # the catalogue it is listed under (the switchable group)
        o["label"] = _label_for(entry, key)
        o["type_group"] = object_type(o)    # what it is, which is what colours it
        if "x" not in o and o["polygons"]:
            xs = [p[0] for poly in o["polygons"] for p in poly]
            ys = [p[1] for poly in o["polygons"] for p in poly]
            o["x"], o["y"] = round(float(np.mean(xs)), 2), round(float(np.mean(ys)), 2)
        if o["kind"] == "outline" and "circle" not in o and o["polygons"]:
            # no catalogue size: the circle around the outline
            pts = np.array([p for poly in o["polygons"] for p in poly], float)
            cx, cy = (pts.min(axis=0) + pts.max(axis=0)) / 2.0
            r = float(np.max(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)))
            t = np.linspace(0, 2 * np.pi, 72)
            o["circle"] = [[[round(float(cx + r * np.cos(a)), 2), round(float(cy + r * np.sin(a)), 2)] for a in t]]
        kept.append(o)
    return kept


def _label_for(entry, key: str) -> str:
    """The designation of the catalogue the object is listed under, plus its common name.

    Under Messier a dual object keeps its NGC number in brackets (M31 (NGC224)); under NGC the Messier number
    is not shown at all, because the user asked for the NGC catalogue, not for a Messier index.
    """
    if key == "m":
        base = entry.messier or entry.name
        ngcic = getattr(entry, "ngcic", "")
        if ngcic and ngcic != base:
            base = "%s (%s)" % (base, ngcic)
    else:
        base = entry.name
    common = getattr(entry, "common", "")
    return base + (" %s" % common if common else "")


def online_shape(P, rec, category):
    """A shape for a record that is not in the bundled data (the object named in the target field).

    Without a position angle the catalogue size becomes a circle: an ellipse would have to invent a direction,
    which is exactly what the bundled data avoids when PosAng is missing.
    """
    if not rec:
        return None
    from . import catalogue as cat
    ra, dec = cat.fix_ra_hours(float(rec["ra"]), category), float(rec["dec"])
    if category in ("lbn", "ldn"):
        ra, dec = cat.precess_b1950(ra, dec)
    x, y = P.xy(ra, dec)
    if not np.isfinite(x[0]) or not P.inside(x[0], y[0]):
        return None
    name = str(rec["name"])
    common = str(rec.get("common") or "").strip()
    shape = {"name": name, "label": name + (" " + common if common and common != name else ""),
             "catalog": "Online", "category": category, "type": rec.get("type") or "Neb",
             "ra": ra, "dec": dec, "x": round(float(x[0]), 2), "y": round(float(y[0]), 2),
             "source": "tickwhale", "kind": "point", "polygons": []}
    maj = float(rec.get("maj") or 0.0)
    # 在线库的尺寸有时比本地 OpenNGC 更小（NGC1499 在线 120'，本地 160'）：取较大者，
    # 免得同一个天体在「在线目录」开关打开时反而画出更小的圈
    try:
        from . import catalogs as _cats
        key = name.replace(" ", "").upper()
        for d in _cats.openngc():
            dn = (d.name or "").replace(" ", "").upper()
            if dn == key or (d.messier or "").replace(" ", "").upper() == key:
                if (d.maj or 0) > maj:
                    maj = float(d.maj)
                break
    except Exception:
        pass
    if maj > 0:
        er, ed = _ellipse_world(ra, dec, maj, maj, 0.0)
        ex, ey = P.xy(er, ed)
        shape["polygons"] = [[[round(float(a), 2), round(float(b), 2)] for a, b in zip(ex, ey)]]
        shape["kind"] = "circle"
    shape["type_group"] = object_type(shape)
    return shape


def display_polygons(entry, style="outline"):
    """(polygons, closed) to draw for an object. style "outline": OpenNGC/Stellarium outlines where they
    exist; "circle": the catalogue size instead (circles, ellipses for elongated objects)."""
    if style == "circle" and entry.get("circle"):
        return entry["circle"], True
    return entry.get("polygons") or [], entry.get("kind") != "outline"
