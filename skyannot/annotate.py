"""Project catalogues onto the solved image and write PixInsight AnnotateImage-compatible text.

Text format = AnnotateImage "Write objects to a text file": one block per catalogue (name line, description
line, header line "Name;RA(deg);Dec(deg);PixelX;PixelY;<fields>", one ';'-separated row per object, blank
line). Readers identify blocks by their exact name line ("NamedStars", "Messier", "NGC-IC", "Sharpless",
"Hipparcos", "TYCHO-2") or, for Gaia, by "Gaia DR3" in the name line.

Coordinates:
  * RA/Dec columns are J2000.0 catalogue positions, as PixInsight writes them (Hipparcos is propagated from
    J1991.25 and Gaia from J2016.0 with their proper motions; Tycho-2 mean positions are already J2000).
  * PixelX/PixelY use PixInsight's convention (centre of the first pixel = 0.5) and are computed from the
    position at the observation epoch, so they land where the star actually is in the picture. PixInsight
    projects the J2000 position instead, which is off by the star's motion since 2000 (about 1 px for a
    10 mas/yr star on a 0.3"/px image).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import catalogs, refine, vizier


@dataclass
class Row:
    name: str
    ra: float          # J2000, degrees
    dec: float
    x: float           # PixInsight pixel convention
    y: float
    fields: list       # already formatted strings
    mag: float | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Block:
    name: str
    description: str
    columns: list
    rows: list


def _fmt(v, nd):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return ""
    return "%.*f" % (nd, v)


def _num(s):
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


class Projector:
    """World -> PixInsight pixel coordinates with an in-frame test. Points further than twice the field
    radius from the centre come back as NaN: a SIP solution is only valid near the image, and projecting the
    whole sky through it does not converge."""

    def __init__(self, wcs, width, height):
        self.wcs, self.w, self.h = wcs, width, height
        self.ra0, self.de0, self.rad, self.area = field_geometry(wcs, width, height)

    def xy(self, ra, dec):
        ra = np.atleast_1d(np.asarray(ra, float))
        dec = np.atleast_1d(np.asarray(dec, float))
        x = np.full(ra.shape, np.nan)
        y = np.full(ra.shape, np.nan)
        r0, d0 = math.radians(self.ra0), math.radians(self.de0)
        rr, dd = np.radians(ra), np.radians(dec)
        cosd = np.sin(d0) * np.sin(dd) + np.cos(d0) * np.cos(dd) * np.cos(rr - r0)
        near = np.isfinite(cosd) & (cosd > math.cos(math.radians(min(2.0 * self.rad + 0.5, 80.0))))
        if near.any():
            px, py = self.wcs.all_world2pix(ra[near], dec[near], 0, quiet=True)
            x[near], y[near] = np.asarray(px) + 0.5, np.asarray(py) + 0.5
        return x, y

    def inside(self, x, y, margin=0.0):
        return (x >= -margin) & (y >= -margin) & (x <= self.w + margin) & (y <= self.h + margin)


def field_geometry(wcs, width, height):
    """(centre ra, centre dec, radius to the corners in degrees, area in square degrees)."""
    c = wcs.pixel_to_world_values((width - 1) / 2.0, (height - 1) / 2.0)
    corners = wcs.pixel_to_world_values([0, width - 1, 0, width - 1], [0, 0, height - 1, height - 1])
    ra0, de0 = math.radians(float(c[0])), math.radians(float(c[1]))
    rad = 0.0
    for ra, de in zip(*corners):
        ra, de = math.radians(ra), math.radians(de)
        cosd = math.sin(de0) * math.sin(de) + math.cos(de0) * math.cos(de) * math.cos(ra - ra0)
        rad = max(rad, math.degrees(math.acos(max(-1.0, min(1.0, cosd)))))
    area = width * height * abs(np.linalg.det(wcs.pixel_scale_matrix))
    return float(c[0]), float(c[1]), rad * 1.02, float(area)


# ───────────────────────────── catalogue blocks ─────────────────────────────
def dso_blocks(P: Projector):
    """Messier, NGC-IC and Sharpless blocks (objects whose centre is in the frame)."""
    blocks = []
    ms = catalogs.messier()
    x, y = P.xy([d.ra for d in ms], [d.dec for d in ms])
    rows = [Row(d.messier, d.ra, d.dec, x[i], y[i],
                [_fmt(d.mag, 2), _fmt(d.maj / 60.0 if d.maj else None, 2), d.common, d.ngcic,
                 d.pgc[0] if d.pgc else ""], d.mag, {"dso": d})
            for i, d in enumerate(ms) if P.inside(x[i], y[i])]
    if rows:
        blocks.append(Block("Messier", "Messier catalog (OpenNGC, %d objects)" % len(ms),
                            ["Magnitude", "Diameter", "Common name", "NGC/IC", "PGC"], rows))
    ng = catalogs.ngcic()
    x, y = P.xy([d.ra for d in ng], [d.dec for d in ng])
    idx = [i for i in range(len(ng)) if P.inside(x[i], y[i])]

    def key(d):
        m = catalogs._NGC_RE.match(d.name)
        return (0 if m.group(1) == "NGC" else 1, int(m.group(2)), m.group(3)) if m else (2, 0, d.name)
    rows = [Row(ng[i].name, ng[i].ra, ng[i].dec, x[i], y[i],
                [_fmt(ng[i].mag, 2), _fmt(ng[i].maj / 60.0 if ng[i].maj else None, 2), ng[i].common,
                 ng[i].pgc[0] if ng[i].pgc else "", ng[i].pgc[1] if len(ng[i].pgc) > 1 else "", ng[i].messier],
                ng[i].mag, {"dso": ng[i]})
            for i in sorted(idx, key=lambda i: key(ng[i]))]
    if rows:
        blocks.append(Block("NGC-IC", "NGC and IC catalogs (OpenNGC, %d objects)" % len(ng),
                            ["Magnitude", "Diameter", "Common name", "PGC", "PGC2", "Messier"], rows))
    sh = catalogs.sharpless()
    x, y = P.xy([d.ra for d in sh], [d.dec for d in sh])
    rows = [Row(d.name, d.ra, d.dec, x[i], y[i], [], None, {"dso": d})
            for i, d in enumerate(sh) if P.inside(x[i], y[i])]
    if rows:
        blocks.append(Block("Sharpless", "Catalog of HII Regions - Sharpless (313 nebulae)", [], rows))
    return blocks


def _f(rows, k):
    return np.array([_num(r.get(k)) if r.get(k) not in (None, "") else np.nan for r in rows], float)


def star_blocks(P: Projector, ra0, de0, rad, area, epoch, gaia_mag=16.0, gaia_max_area=9.0,
                gaia_rows=None, log=print):
    """NamedStars, Hipparcos, Tycho-2 and Gaia DR3 blocks. Returns (blocks, notes)."""
    notes = []
    blocks = []
    hip = []
    try:
        hip = vizier.cone("hip", ra0, de0, rad, log=log)
    except Exception as e:
        notes.append("Hipparcos: %s" % e)
    hra, hde = refine.propagate(_f(hip, "RAICRS"), _f(hip, "DEICRS"), _f(hip, "pmRA"), _f(hip, "pmDE"), 1991.25, 2000.0)
    hra_e, hde_e = refine.propagate(_f(hip, "RAICRS"), _f(hip, "DEICRS"), _f(hip, "pmRA"), _f(hip, "pmDE"), 1991.25, epoch)

    # Named stars: Yale BSC designations + IAU names, positioned with Hipparcos where it matches
    ns = catalogs.named_stars()
    nra = np.array([s.ra for s in ns])
    nde = np.array([s.dec for s in ns])
    cosd = math.cos(math.radians(de0))
    near = np.nonzero((np.abs(((nra - ra0 + 180) % 360) - 180) * max(cosd, 0.05) < rad * 1.5) & (np.abs(nde - de0) < rad * 1.5))[0]
    rows = []
    if len(near):
        from scipy.spatial import cKDTree
        tree = cKDTree(np.column_stack([hra * cosd, hde])) if len(hip) else None
        for i in near:
            s = ns[i]
            ra, de, ra_e, de_e, hipno = s.ra, s.dec, s.ra, s.dec, s.hip
            if tree is not None:
                d, j = tree.query([s.ra * cosd, s.dec])
                if d < 60.0 / 3600:
                    ra, de, ra_e, de_e = hra[j], hde[j], hra_e[j], hde_e[j]
                    hipno = int(hip[j]["HIP"])
            x, y = P.xy(ra_e, de_e)
            if P.inside(x[0], y[0]):
                rows.append(Row(s.name, ra, de, x[0], y[0],
                                [_fmt(s.mag, 2), s.sptype, s.common, "HD%d" % s.hd if s.hd else "",
                                 "HIP%d" % hipno if hipno else ""], s.mag, {"common": s.common}))
    if rows:
        rows.sort(key=lambda r: (r.mag if r.mag is not None else 99))
        blocks.append(Block("NamedStars", "Named stars catalog, Yale Bright Star Catalogue with the IAU WGSN star "
                            "names as of 2022-04-04 (%d objects)" % len(ns),
                            ["Magnitude", "Spectral type", "Common name", "HD", "HIP"], rows))

    if len(hip):
        x, y = P.xy(hra_e, hde_e)
        rows = []
        for i, r in enumerate(hip):
            if not P.inside(x[i], y[i]) or not math.isfinite(hra[i]):
                continue
            rows.append(Row("HIP%s" % r["HIP"], hra[i], hde[i], x[i], y[i],
                            [_fmt(_num(r["Vmag"]), 2), _fmt(_num(r["BTmag"]), 3), _fmt(_num(r["VTmag"]), 3),
                             _fmt(_num(r["B-V"]), 3), _fmt(_num(r["V-I"]), 2), r["SpType"], _fmt(_num(r["Plx"]), 2)],
                            _num(r["Vmag"])))
        if rows:
            blocks.append(Block("Hipparcos", "Hipparcos Main catalog (118,218 stars)",
                                ["Magnitude", "BT magnitude", "VT magnitude", "B-V color", "V-I index",
                                 "Spectral type", "Parallax"], rows))

    try:
        tyc = vizier.cone("tyc", ra0, de0, rad, log=log)
    except Exception as e:
        tyc = []
        notes.append("Tycho-2: %s" % e)
    if tyc:
        tra, tde = _f(tyc, "RAmdeg"), _f(tyc, "DEmdeg")
        x, y = P.xy(tra, tde)
        rows = []
        for i, r in enumerate(tyc):
            if not math.isfinite(tra[i]) or not P.inside(x[i], y[i]):
                continue
            bt, vt = _num(r["BTmag"]), _num(r["VTmag"])
            if bt is not None and vt is not None:       # Tycho -> Johnson (ESA 1997, vol. 1, sect. 1.3)
                bv = 0.850 * (bt - vt)
                v = vt - 0.090 * (bt - vt)
                b = v + bv
            else:
                bv = v = b = None
            # VTmag/BTmag keep the catalogue's fixed width (" 9.804"), the HIP column is "HIP<n>": as PixInsight
            hipno = r["HIP"].strip()
            rows.append(Row("TYC%s-%s-%s" % (r["TYC1"], r["TYC2"], r["TYC3"]), tra[i], tde[i], x[i], y[i],
                            [_fmt(vt, 2), "%6.3f" % vt if vt is not None else "", "%6.3f" % bt if bt is not None else "",
                             ("HIP" + hipno) if hipno else "", _fmt(v, 3), _fmt(b, 3), _fmt(bv, 3)], vt))
        rows.sort(key=lambda r: tuple(int(p) for p in r.name[3:].split("-")))
        if rows:
            blocks.append(Block("TYCHO-2", "Tycho-2 catalog (2,539,913 stars)",
                                ["Magnitude", "VTmag", "BTmag", "HIP", "Vmag", "Bmag", "B-V index"], rows))

    if area > gaia_max_area:
        notes.append("Gaia DR3 skipped: field is %.1f square degrees (limit %.0f)" % (area, gaia_max_area))
    else:
        g = gaia_rows
        if g is None:
            try:
                g = vizier.cone("gaia", ra0, de0, rad, mag_max=gaia_mag, log=log)
            except Exception as e:
                g = []
                notes.append("Gaia DR3: %s" % e)
        if g:
            gm = _f(g, "Gmag")
            sel = gm <= gaia_mag + 1e-9
            gg = [r for r, s in zip(g, sel) if s]
            gra, gde, pmr, pmd = _f(gg, "RA_ICRS"), _f(gg, "DE_ICRS"), _f(gg, "pmRA"), _f(gg, "pmDE")
            r2000 = refine.propagate(gra, gde, pmr, pmd, 2016.0, 2000.0)
            rep = refine.propagate(gra, gde, pmr, pmd, 2016.0, epoch)
            x, y = P.xy(*rep)
            rows = []
            for i, r in enumerate(gg):
                if not P.inside(x[i], y[i]):
                    continue
                rows.append(Row("", r2000[0][i], r2000[1][i], x[i], y[i],
                                [_fmt(_num(r["RPmag"]), 3), _fmt(_num(r["Gmag"]), 3), _fmt(_num(r["BPmag"]), 3),
                                 _fmt(_num(r["Plx"]), 4), ""], _num(r["Gmag"]), {"source_id": r["Source"]}))
            rows.sort(key=lambda r: r.mag if r.mag is not None else 99)
            if rows:
                blocks.append(Block("Gaia DR3", "Gaia Data Release 3, VizieR I/355/gaiadr3 (Gaia Collaboration, "
                                    "2022), G <= %.1f" % gaia_mag,
                                    ["RPmag", "Gmag", "BPmag", "Parallax", "Flags"], rows))
    return blocks, notes


def named_local(P: Projector):
    """Named stars from the bundled catalogue only (no network), for the viewer's instant overlay."""
    ns = catalogs.named_stars()
    x, y = P.xy([s.ra for s in ns], [s.dec for s in ns])
    rows = [Row(s.name, s.ra, s.dec, x[i], y[i], [], s.mag, {"common": s.common})
            for i, s in enumerate(ns) if np.isfinite(x[i]) and P.inside(x[i], y[i])]
    return sorted(rows, key=lambda r: r.mag if r.mag is not None else 99)


def objects_text(blocks) -> str:
    out = []
    for b in blocks:
        lines = [b.name, b.description, ";".join(["Name", "RA(deg)", "Dec(deg)", "PixelX", "PixelY"] + b.columns)]
        for r in b.rows:
            lines.append(";".join(["%s" % r.name, "%.6f" % r.ra, "%.6f" % r.dec, "%.6f" % r.x, "%.6f" % r.y] + r.fields))
        out.append("\n".join(lines) + "\n\n")
    return "".join(out)
