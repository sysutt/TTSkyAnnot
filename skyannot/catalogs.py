"""Bundled catalogues: OpenNGC (Messier, NGC, IC + addendum, ellipses and nebula outlines), Sharpless,
the Yale Bright Star Catalogue with IAU star names, and Stellarium's nebula outlines (used only where
OpenNGC has no outline). Sources and licences: THIRD_PARTY_NOTICES.md.
"""
from __future__ import annotations

import csv
import functools
import os
import re
import sys
from dataclasses import dataclass, field


def data_dir() -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    return os.path.join(base, "data")


def _sexa(s: str, hours: bool):
    s = (s or "").strip()
    if not s:
        return None
    sign = -1.0 if s.startswith("-") else 1.0
    p = [float(x) for x in s.lstrip("+-").split(":")]
    v = p[0] + (p[1] if len(p) > 1 else 0) / 60.0 + (p[2] if len(p) > 2 else 0) / 3600.0
    return sign * v * (15.0 if hours else 1.0)


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


@dataclass
class DSO:
    name: str                  # canonical, e.g. "NGC598", "IC1396", "M33", "Sh2-131", "B33"
    ra: float
    dec: float
    type: str = ""
    mag: float | None = None
    maj: float | None = None   # arcmin
    minr: float | None = None  # arcmin
    pa: float | None = None    # degrees, north through east
    common: str = ""
    messier: str = ""          # "M1"
    ngcic: str = ""            # the NGC/IC designation of a Messier object
    pgc: list = field(default_factory=list)


_NGC_RE = re.compile(r"^(NGC|IC)0*(\d+)(.*)$")


def canonical(name: str) -> str:
    """'NGC0598' / 'NGC 598' -> 'NGC598', 'M 42' -> 'M42', 'IC 0434' -> 'IC434', 'SH 2-155' -> 'Sh2-155'."""
    s = re.sub(r"\s+", " ", (name or "").strip())
    m = re.match(r"^(NGC|IC|M|LBN|LDN|B|Ced|VdB|vdB|VDB|RCW|PGC|Mel|Cr|C)\s*0*(\d+)(.*)$", s)
    if m:
        pre = {"VDB": "vdB", "VdB": "vdB"}.get(m.group(1), m.group(1))
        return "%s%s%s" % (pre, m.group(2), m.group(3).strip())
    m = re.match(r"^(SH|Sh)\s*2\s*-\s*0*(\d+)$", s)
    if m:
        return "Sh2-%s" % m.group(2)
    return s


@functools.lru_cache(maxsize=1)
def openngc() -> list:
    out = []
    for fn in ("NGC.csv", "addendum.csv"):
        with open(os.path.join(data_dir(), "openngc", fn), encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["Type"] in ("Dup", "NonEx"):
                    continue
                ra, dec = _sexa(r["RA"], True), _sexa(r["Dec"], False)
                if ra is None or dec is None:
                    continue
                name = canonical(r["Name"])
                ids = [x.strip() for x in (r["Identifiers"] or "").split(",") if x.strip()]
                pgc = ["PGC%d" % int(x.split()[1]) for x in ids if x.startswith("PGC ") and x.split()[1].isdigit()]
                m = r["M"].strip()
                out.append(DSO(
                    name=name, ra=ra, dec=dec, type=r["Type"],
                    mag=_f(r["V-Mag"]) if _f(r["V-Mag"]) is not None else _f(r["B-Mag"]),
                    maj=_f(r["MajAx"]), minr=_f(r["MinAx"]), pa=_f(r["PosAng"]),
                    common=(r["Common names"] or "").split(",")[0].strip(),
                    messier=("M%d" % int(m)) if m.isdigit() else "",
                    ngcic=name if name.startswith(("NGC", "IC")) else "",
                    pgc=pgc))
    return out


def messier() -> list:
    rows = []
    for d in openngc():
        if d.messier:
            rows.append(d)
        elif re.match(r"^M\d+$", d.name):             # Messier objects with no NGC number (addendum)
            d.messier = d.name
            rows.append(d)
    seen, out = set(), []
    for d in sorted(rows, key=lambda d: int(d.messier[1:])):
        if d.messier not in seen:
            seen.add(d.messier)
            out.append(d)
    return out


def ngcic() -> list:
    return [d for d in openngc() if d.name.startswith(("NGC", "IC"))]


def others() -> list:
    """Addendum objects that are neither NGC/IC nor Messier (Barnard, Caldwell, Melotte, ...): preview only."""
    return [d for d in openngc() if not d.name.startswith(("NGC", "IC")) and not d.messier]


@functools.lru_cache(maxsize=1)
def sharpless() -> list:
    out = []
    with open(os.path.join(data_dir(), "vizier", "sharpless_VII20.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(DSO(name="Sh2-%s" % r["Sh2"], ra=float(r["_RAJ2000"]), dec=float(r["_DEJ2000"]),
                           type="HII", maj=_f(r["Diam"]), minr=_f(r["Diam"]), pa=0.0))
    return out


# ───────────────────────────── named stars ─────────────────────────────
_GREEK = {"Alp": "alf", "Bet": "bet", "Gam": "gam", "Del": "del", "Eps": "eps", "Zet": "zet", "Eta": "eta",
          "The": "the", "Iot": "iot", "Kap": "kap", "Lam": "lam", "Mu": "mu.", "Nu": "nu.", "Xi": "ksi",
          "Omi": "omi", "Pi": "pi.", "Rho": "rho", "Sig": "sig", "Tau": "tau", "Ups": "ups", "Phi": "phi",
          "Chi": "chi", "Psi": "psi", "Ome": "ome"}
_BSC_NAME = re.compile(r"^\s*(\d+)?\s*([A-Z][a-z]{1,2})?(\d)?\s*([A-Z][A-Za-z]{2})\s*$")


def _designation(bsc_name: str) -> str | None:
    """Yale BSC 'Name' -> PixInsight-style designation: '21Alp And' -> 'alf And', 'Mu  Cep' -> 'mu. Cep',
    'Kap1Scl' -> 'kap1 Scl', '33    Psc' -> '33 Psc'."""
    m = _BSC_NAME.match(bsc_name or "")
    if not m:
        return None
    flam, bayer, sup, con = m.groups()
    if bayer and bayer in _GREEK:
        return "%s%s %s" % (_GREEK[bayer], sup or "", con)
    if flam:
        return "%s %s" % (flam, con)
    return None


@functools.lru_cache(maxsize=1)
def iau_names() -> dict:
    """HD number -> (IAU name, HIP number) from the WGSN list."""
    tail = re.compile(r"(\S+)\s+(-?[\d.]+|_)\s+(\S+)\s+(\d+|_)\s+(\d+|_)\s+([\d.]+)\s+(-?[\d.]+)\s+(\d{4}-\d{2}-\d{2})")
    out = {}
    with open(os.path.join(data_dir(), "iau", "IAU-CSN.txt"), encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line[0] in "#$":
                continue
            m = None
            for m in tail.finditer(line):
                pass
            if not m:
                continue
            hip, hd = m.group(4), m.group(5)
            if hd.isdigit():
                out[int(hd)] = (line.split()[0], int(hip) if hip.isdigit() else None)
    return out


@dataclass
class NamedStar:
    name: str
    ra: float
    dec: float
    mag: float | None
    sptype: str
    common: str
    hd: int | None
    hip: int | None = None


@functools.lru_cache(maxsize=1)
def named_stars() -> list:
    names = iau_names()
    out = []
    with open(os.path.join(data_dir(), "vizier", "bsc5_V50.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            des = _designation(r["Name"])
            if not des or not r["RAJ2000"]:
                continue
            ra = _sexa(r["RAJ2000"].replace(" ", ":"), True)
            dec = _sexa(r["DEJ2000"].replace(" ", ":"), False)
            hd = int(r["HD"]) if r["HD"].strip().isdigit() else None
            common, hip = names.get(hd, ("", None)) if hd else ("", None)
            out.append(NamedStar(des, ra, dec, _f(r["Vmag"]), (r["SpType"] or "").strip(), common, hd, hip))
    return out


# ───────────────────────────── outlines ─────────────────────────────
def _read_outlines(path: str, source: str) -> list:
    """Stellarium outline format: 'RA_hours Dec_deg start|vertex|end [name]'. Returns [(name, source, [(ra, dec)])]."""
    polys, cur, name = [], None, ""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 3 or line.lstrip().startswith("#"):
                continue
            try:
                ra, dec = float(parts[0]) * 15.0, float(parts[1])
            except ValueError:
                continue
            kind = parts[2]
            if kind == "start":
                name = canonical(" ".join(parts[3:]))
                cur = [(ra, dec)]
            elif kind == "vertex" and cur is not None:
                cur.append((ra, dec))
            elif kind == "end" and cur is not None:
                cur.append((ra, dec))
                polys.append((name, source, cur))
                cur = None
    return polys


@functools.lru_cache(maxsize=1)
def outlines() -> list:
    """OpenNGC outlines first; Stellarium outlines only for objects OpenNGC does not outline (names are
    compared after mapping Messier numbers to their NGC/IC designation, so 'M42' and 'NGC1976' match)."""
    d = data_dir()
    onc = _read_outlines(os.path.join(d, "openngc", "outlines_stellarium.dat"), "OpenNGC")
    stel = _read_outlines(os.path.join(d, "stellarium", "outlines.dat"), "Stellarium")
    m2n = {o.messier: o.name for o in messier() if o.ngcic}
    covered = {m2n.get(n, n) for n, _, _ in onc}
    return onc + [p for p in stel if m2n.get(p[0], p[0]) not in covered]
