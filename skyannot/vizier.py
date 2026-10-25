"""VizieR cone searches (Hipparcos, Tycho-2, Gaia DR3) with an on-disk cache."""
from __future__ import annotations

import hashlib
import json
import os
import time

MIRRORS = ("https://vizier.cds.unistra.fr/viz-bin/asu-tsv",
           "https://vizier.cfa.harvard.edu/viz-bin/asu-tsv",
           "http://vizier.china-vo.org/viz-bin/asu-tsv",          # these two refuse TLS from some networks
           "http://vizier.nao.ac.jp/viz-bin/asu-tsv")

# One table for the astrometric catalogues a run can annotate from, in the order their blocks appear in the
# .objects.txt. "block" is that block's name line — readers, PixInsight's included, identify a block by
# exactly that line — and "label" is what the interface shows. The bundled NamedStars catalogue is not part
# of this: it needs no query.
CATALOGS = {
    "hip": {"block": "Hipparcos", "label": "Hipparcos", "source": "I/239/hip_main",
            "out": ["HIP", "RAICRS", "DEICRS", "Vmag", "BTmag", "VTmag", "B-V", "V-I", "SpType", "Plx", "pmRA", "pmDE"],
            "sort": "HIP"},
    "tyc": {"block": "TYCHO-2", "label": "Tycho-2", "source": "I/259/tyc2",
            "out": ["TYC1", "TYC2", "TYC3", "RAmdeg", "DEmdeg", "BTmag", "VTmag", "HIP"],
            "sort": None},
    "gaia": {"block": "Gaia DR3", "label": "Gaia DR3", "source": "I/355/gaiadr3",
             "out": ["Source", "RA_ICRS", "DE_ICRS", "Gmag", "BPmag", "RPmag", "Plx", "pmRA", "pmDE"],
             "sort": "Gmag"},
}
HIP, TYC, GAIA = "hip", "tyc", "gaia"
HIP_BLOCK, TYC_BLOCK, GAIA_BLOCK = (CATALOGS[k]["block"] for k in (HIP, TYC, GAIA))


def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
    d = os.path.join(base, "TTSkyAnnot", "vizier")
    os.makedirs(d, exist_ok=True)
    return d


def _parse_tsv(text: str):
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    if len(lines) < 3:
        return []
    names = lines[0].split("\t")
    rows = []
    for ln in lines[3:]:                        # names, units, dashes, then data
        parts = ln.split("\t")
        if len(parts) != len(names):
            continue
        rows.append({n: p.strip() for n, p in zip(names, parts)})
    return rows


def cone(cat: str, ra: float, dec: float, radius_deg: float, mag_max: float | None = None,
         max_rows: int = 300000, timeout: float = 25.0, log=print):
    """Rows (dicts of strings) within radius_deg of (ra, dec). Cached for 180 days."""
    import requests
    spec = CATALOGS[cat]
    params = {"-source": spec["source"], "-out": ",".join(spec["out"]),
              "-c": "%.6f %+.6f" % (ra, dec), "-c.rd": "%.5f" % radius_deg,
              "-out.max": str(max_rows), "-oc.form": "d"}
    if spec["sort"]:
        params["-sort"] = spec["sort"]
    if mag_max is not None:
        magcol = {"hip": "Vmag", "tyc": "VTmag", "gaia": "Gmag"}[cat]
        params[magcol] = "<%.2f" % mag_max
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:20]
    fp = os.path.join(cache_dir(), "%s_%s.tsv" % (cat, key))
    if os.path.exists(fp) and time.time() - os.path.getmtime(fp) < 180 * 86400:
        with open(fp, encoding="utf-8") as f:
            return _parse_tsv(f.read())
    last = None
    pref = _preferred()
    for url in sorted(MIRRORS, key=lambda u: u != pref):     # the mirror that worked last time first
        try:
            r = requests.get(url, params=params, timeout=(10, timeout))
            err = _server_error(r.text) if r.status_code == 200 else "HTTP %d" % r.status_code
            if err is None:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(r.text)
                _preferred(url)
                return _parse_tsv(r.text)
            last = "%s: %s" % (url.split("/")[2], err)   # e.g. "The database is not currently reachable"
            log("  VizieR %s failed (%s), trying another mirror" % (url.split("/")[2], err))
        except Exception as e:                   # try the next mirror
            last = "%s: %s" % (url.split("/")[2], e)
            log("  VizieR %s unreachable, trying another mirror" % url.split("/")[2])
    raise RuntimeError("VizieR %s query failed on every mirror (%s)" % (spec["source"], last))


def _preferred(url=None):
    fp = os.path.join(cache_dir(), "mirror.txt")
    if url:
        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(url)
        except OSError:
            pass
        return url
    try:
        with open(fp, encoding="utf-8") as f:
            u = f.read().strip()
        return u if u in MIRRORS else None
    except OSError:
        return None


def _server_error(text: str):
    """VizieR reports its own failures with HTTP 200. A valid answer, even one with no rows, describes the
    table ('#Table ...' and '#Column ...' lines); a failure has '#INFO Error=...' lines or no table at all
    ('#No server is available for connection'). Returns an error message or None."""
    lines = text.splitlines()
    for ln in lines:
        if ln.startswith("#INFO") and "Error=" in ln:
            msg = ln.split("Error=", 1)[1].strip()
            if msg:
                return msg
    if any(ln.startswith("#Table") for ln in lines):
        return None
    hint = next((ln.lstrip("#").strip() for ln in lines if ln.startswith("#") and len(ln.strip("# ")) > 3), "")
    return "no table in the answer" + (" (%s)" % hint[:80] if hint else "")
