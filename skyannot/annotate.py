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

from . import catalogs, catalogue, refine, vizier

# the exact name lines the text format uses for the queried star catalogues (catalogs.py holds the bundled
# ones); readers identify a block by its name line, and vizier.CATALOGS is the one table behind all of this
HIP_BLOCK, TYC_BLOCK, GAIA_BLOCK = vizier.HIP_BLOCK, vizier.TYC_BLOCK, vizier.GAIA_BLOCK


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
def _dso_rows(P, entries, catalogue_key):
    """Rows for the entries that belong to one catalogue, in catalogue-number order."""
    if not entries:
        return []
    x, y = P.xy([d.ra for d in entries], [d.dec for d in entries])
    idx = [i for i in range(len(entries)) if P.inside(x[i], y[i])]
    rows = []
    for i in sorted(idx, key=lambda i: catalogue.sort_key(entries[i], catalogue_key)):
        d = entries[i]
        pgc = getattr(d, "pgc", None) or []
        rows.append(Row(d.name, d.ra, d.dec, x[i], y[i],
                        [_fmt(d.mag, 2), _fmt(d.maj / 60.0 if d.maj else None, 2), d.common,
                         d.messier, pgc[0] if pgc else "", pgc[1] if len(pgc) > 1 else ""],
                        d.mag, {"dso": d}))
    return rows


def dso_blocks(P: Projector, cats=None):
    """One block per enabled deep-sky catalogue (objects whose centre is in the frame).

    Grouping follows the catalogue an object's designation puts it in, and each object is listed under exactly
    one: the first enabled catalogue in precedence order that holds it. M31 is a Messier object and is not
    listed again under NGC; NGC 7000 is an NGC object and is not listed again under Sharpless. `cats` is the
    tuple of enabled catalogue keys (None = everything local), see catalogue.CATALOGUES.
    """
    on = tuple(cats) if cats else catalogue.LOCAL
    # unique(): OpenNGC lists 107 objects in both messier() and ngcic(), and without this they would be counted
    # twice and annotated twice
    entries = catalogue.unique(list(catalogs.messier()) + list(catalogs.ngcic())
                               + list(catalogs.sharpless()))
    mine = catalogue.assign(entries, on)
    blocks = []
    for key in catalogue.KEYS:
        if key not in on:
            continue
        sel = [e for e in entries if mine.get(catalogue.key_of(e)) == key]
        rows = _dso_rows(P, sel, key)
        if rows:
            blocks.append(Block("dso:" + key, catalogue.NOTES[key],
                                ["Magnitude", "Diameter", "Common name", "Messier", "PGC", "PGC2"], rows))
    return blocks


def online_target_block(P, rec, cats=None):
    """The block for the object named in the target field when it comes from the online catalogue.

    The online database is searched by designation, not by region, so it cannot fill a field on its own — but
    the object the user named is usually the reason the frame exists (LBN777, say), and until now it was the
    one object missing from the annotation. It gets the same treatment as a catalogue object: its position,
    size, common name and type, in the block of its own catalogue.
    """
    if not rec:
        return None
    from . import catalogue as cat
    key = cat.catalogue_of(rec)
    on = set(cats) if cats else set(cat.KEYS)
    if not key or key not in on:
        return None
    ra, de = float(rec["ra"]), float(rec["dec"])
    x, y = P.xy([ra], [de])
    if not np.isfinite(x[0]) or not P.inside(x[0], y[0]):
        return None
    maj = float(rec.get("maj") or 0.0) or None
    row = Row(str(rec["name"]), ra, de, float(x[0]), float(y[0]),
              [_fmt(rec.get("mag"), 2), _fmt(maj / 60.0 if maj else None, 2),
               str(rec.get("common") or ""), "", "", ""],
              rec.get("mag"), {"dso": rec})
    return Block("dso:" + key, cat.NOTES[key],
                 ["Magnitude", "Diameter", "Common name", "Messier", "PGC", "PGC2"], [row])


def online_region_blocks(P, recs, cats=None):
    """Blocks for the online objects inside the field, one per catalogue, rows in catalogue order.

    The region query returns records in the same shape the single-object lookup does, so each one is turned
    into a row by the same code; grouping follows the catalogue the designation belongs to, exactly like the
    bundled blocks.
    """
    from . import catalogue as cat
    on = set(cats) if cats else set(cat.KEYS)
    groups = {}
    for rec in recs or []:
        key = cat.catalogue_of(rec)
        if not key or key not in on:
            continue
        block = online_target_block(P, rec, on)
        if block is None:
            continue
        groups.setdefault(key, []).extend(block.rows)
    out = []
    for key in cat.KEYS:
        rows = groups.get(key)
        if rows:
            rows.sort(key=lambda r: cat.sort_key(r.extra["dso"], key))
            out.append(Block("dso:" + key, cat.NOTES[key],
                             ["Magnitude", "Diameter", "Common name", "Messier", "PGC", "PGC2"], rows))
    return out


def _f(rows, k):
    return np.array([_num(r.get(k)) if r.get(k) not in (None, "") else np.nan for r in rows], float)


def star_blocks(P: Projector, ra0, de0, rad, area, epoch, gaia_mag=16.0, gaia_rows=None, log=print,
                cats=None):
    """The star blocks the user asked for. Returns (blocks, notes).

    cats: which of vizier.CATALOGS to write ("hip", "tyc", "gaia"; None = all of them). A catalogue that is
    not asked for is not even queried, so a run with only, say, Gaia selected does not wait for Tycho-2.
    The reference stars for the refinement come from run.py, not from here, so the plate solution keeps its
    accuracy whatever is switched off for the text.

    NamedStars is always included: it is a bundled catalogue, not one of the three queried ones.
    """
    cats = tuple(vizier.CATALOGS) if cats is None else tuple(c for c in cats if c in vizier.CATALOGS)
    notes = []
    blocks = []
    hip = []
    if "hip" in cats:
        try:
            hip = vizier.cone("hip", ra0, de0, rad, log=log)
        except Exception as e:
            notes.append("Hipparcos: %s" % e)
    hra, hde = refine.propagate(_f(hip, "RAICRS"), _f(hip, "DEICRS"), _f(hip, "pmRA"), _f(hip, "pmDE"),
                                1991.25, 2000.0)
    # the epoch of a named star whose catalogue position is kept: the Hipparcos list is also what positions
    # the named stars, so 2000.0 is the honest choice when Hipparcos is switched off
    hra_e, hde_e = refine.propagate(_f(hip, "RAICRS"), _f(hip, "DEICRS"), _f(hip, "pmRA"), _f(hip, "pmDE"),
                                    1991.25, epoch if "hip" in cats else 2000.0)

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
            blocks.append(Block(vizier.HIP_BLOCK, "Hipparcos Main catalog (118,218 stars)",
                                ["Magnitude", "BT magnitude", "VT magnitude", "B-V color", "V-I index",
                                 "Spectral type", "Parallax"], rows))

    tyc = []
    if "tyc" in cats:
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
            blocks.append(Block(vizier.TYC_BLOCK, "Tycho-2 catalog (2,539,913 stars)",
                                ["Magnitude", "VTmag", "BTmag", "HIP", "Vmag", "Bmag", "B-V index"], rows))

    # PixInsight's annotation skips Gaia above 9 square degrees because its local catalogue is slow; here the
    # query is capped brightest-first instead (run.Options.gaia_max_rows), so wide fields keep their parallaxes.
    g = gaia_rows if "gaia" in cats else None
    if "gaia" in cats and g is None:
        try:
            g = vizier.cone("gaia", ra0, de0, rad, mag_max=gaia_mag, log=log)
        except Exception as e:
            g = []
            notes.append("Gaia DR3: %s" % e)
    if g:
        gm = _f(g, "Gmag")
        gg = [r for r, s in zip(g, gm <= gaia_mag + 1e-9) if s]
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
            blocks.append(Block(vizier.GAIA_BLOCK, "Gaia Data Release 3, VizieR I/355/gaiadr3 (Gaia Collaboration, "
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
