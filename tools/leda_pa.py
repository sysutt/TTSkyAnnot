"""Fill OpenNGC's missing position angles from HyperLEDA (VizieR table VII/237).

OpenNGC leaves `PosAng` empty for about 3200 entries, and a missing angle is not a harmless blank: drawing an
ellipse at 0 degrees asserts a vertical direction the catalogue never gave.

HyperLEDA carries an adopted position angle for close to a million galaxies. This tool downloads it once,
matches it to OpenNGC by position, checks itself against the angles OpenNGC already has, and writes
`data/openngc/posang.csv` — an addendum rather than an edit of `NGC.csv`, so the upstream file stays pristine
and this can be regenerated or deleted at any time.

    python tools/leda_pa.py --validate          # download, match, report, write nothing
    python tools/leda_pa.py --write             # also write the addendum

The download is cached in `.leda_cache/`, so the second run is offline.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CACHE = os.path.join(ROOT, ".leda_cache")
RAW = os.path.join(CACHE, "hyperleda_pa.tsv")
ADDENDUM = os.path.join(ROOT, "data", "openngc", "posang.csv")
ASU = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
UA = {"User-Agent": "Mozilla/5.0 (TTSkyAnnot data import)"}
# PGC, the adopted position angle, and the axes; positions are needed to match, since OpenNGC has no PGC.
COLUMNS = "PGC,RAJ2000,DEJ2000,PA,logD25,logR25"

# A match is only accepted when LEDA's galaxy centre is this close to OpenNGC's position. One arcminute covers
# the usual disagreement between catalogues for the same object while staying far smaller than the typical
# separation between two galaxies.
MAX_SEP_ARCMIN = 1.0


def download(force: bool = False) -> str:
    os.makedirs(CACHE, exist_ok=True)
    if os.path.exists(RAW) and not force and os.path.getsize(RAW) > 1_000_000:
        print("using the cached download: %s" % RAW)
        return RAW
    url = "%s?-source=VII/237&-out=%s&-out.max=unlimited" % (ASU, COLUMNS)
    print("downloading HyperLEDA from VizieR ...")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = resp.read()
    print("   %.1f MB" % (len(raw) / 1e6))
    with open(RAW, "wb") as fh:
        fh.write(raw)
    return RAW


def _sexa_to_deg(text: str, hours: bool = False):
    """'12 34 56.7' -> degrees. VizieR writes these as fixed-width fields with blanks."""
    parts = (text or "").split()
    if len(parts) < 2:
        return None
    try:
        v = float(parts[0]) + float(parts[1]) / 60.0
        if len(parts) > 2:
            v += float(parts[2]) / 3600.0
    except ValueError:
        return None
    return v * 15.0 if hours else v


def load_leda(path: str):
    """(ra, dec, pa, logd25, logr25, pgc) as float arrays, blanks dropped."""
    ra, dec, pa, d25, r25, pgc = [], [], [], [], [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        started = False
        for line in fh:
            if line.startswith("#"):
                continue
            if not started:
                if line.strip().startswith("PGC"):
                    started = True                 # the header row
                continue
            if not line.strip() or line.startswith("\t"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 6:
                continue
            a = _sexa_to_deg(f[1], hours=True)
            b = _sexa_to_deg(f[2], hours=False)
            if a is None or b is None:
                continue
            ra.append(a)
            dec.append(b)
            pa.append(_float(f[3]))
            d25.append(_float(f[4]))
            r25.append(_float(f[5]))
            pgc.append(_float(f[0]))
    return (np.array(ra, np.float64), np.array(dec, np.float64), np.array(pa, np.float64),
            np.array(d25, np.float64), np.array(r25, np.float64), np.array(pgc, np.float64))


def _float(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return np.nan


def match(target_ra, target_dec, leda, max_arcmin: float = MAX_SEP_ARCMIN):
    """(index into the sorted LEDA arrays, separation in arcmin, that galaxy's PA) for each target.

    Deciding by position rather than by name because OpenNGC ships no PGC column and its designations differ
    from LEDA's (LEDA prefers PGC numbers for anonymous galaxies). The LEDA arrays are sorted by declination so
    each lookup only examines the strip that could be in range.
    """
    ra, dec, pa = leda[0], leda[1], leda[2]
    order = np.argsort(dec, kind="stable")
    dec_s, ra_s, pa_s = dec[order], ra[order], pa[order]
    n = len(target_ra)
    idx = np.full(n, -1, np.int64)
    sep = np.full(n, np.nan, np.float64)
    found_pa = np.full(n, np.nan, np.float64)
    tol = max_arcmin / 60.0
    for i, (tra, tdec) in enumerate(zip(np.asarray(target_ra, np.float64),
                                        np.asarray(target_dec, np.float64))):
        if not (np.isfinite(tra) and np.isfinite(tdec)):
            continue
        lo = int(np.searchsorted(dec_s, tdec - tol, "left"))
        hi = int(np.searchsorted(dec_s, tdec + tol, "right"))
        if hi <= lo:
            continue
        band_ra = ra_s[lo:hi]
        band_dec = dec_s[lo:hi]
        # a degree of right ascension shrinks with the cosine of the declination
        dra = (band_ra - tra) * np.cos(np.radians(tdec))
        dde = band_dec - tdec
        d = np.hypot(dra, dde) * 60.0                       # arcminutes
        k = int(np.argmin(d))
        if d[k] <= max_arcmin:
            idx[i] = lo + k
            sep[i] = float(d[k])
            found_pa[i] = pa_s[lo + k]
    return idx, sep, found_pa


def _openngc_rows():
    import csv
    rows = []
    for fn in ("NGC.csv", "addendum.csv"):
        path = os.path.join(ROOT, "data", "openngc", fn)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                rows.append(r)
    return rows


def _pgc_of(row) -> int | None:
    """The PGC number OpenNGC records in its Identifiers column, if any."""
    for part in (row.get("Identifiers") or "").split(","):
        part = part.strip()
        if part.startswith("PGC "):
            tail = part.split()[1]
            if tail.isdigit():
                return int(tail)
    return None


def _sexa(text: str, hours: bool):
    parts = (text or "").strip().split(":")
    try:
        v = float(parts[0]) + float(parts[1]) / 60.0 + (float(parts[2]) / 3600.0 if len(parts) > 2 else 0.0)
    except (IndexError, ValueError):
        return None
    return v * 15.0 if hours else v


def run(write: bool):
    path = download()
    leda = load_leda(path)
    finite = np.isfinite(leda[0]) & np.isfinite(leda[1])
    leda = tuple(a[finite] for a in leda)
    print("LEDA galaxies: %d   with a position angle: %d"
          % (len(leda[0]), int(np.isfinite(leda[2]).sum())))

    rows = _openngc_rows()
    ra, dec, have, names, pgcs = [], [], [], [], []
    for r in rows:
        a, b = _sexa(r.get("RA", ""), True), _sexa(r.get("Dec", ""), False)
        if a is None or b is None:
            continue
        ra.append(a)
        dec.append(b)
        have.append(bool((r.get("PosAng") or "").strip()))
        names.append((r.get("Name") or "").strip())
        pgcs.append(_pgc_of(r))
    ra, dec = np.array(ra, np.float64), np.array(dec, np.float64)
    have = np.array(have, bool)
    idx, sep, pa = match(ra, dec, leda)

    # an exact PGC match beats a positional one, and OpenNGC carries the PGC number in its Identifiers column
    by_pgc = {int(p): i for i, p in enumerate(leda[5]) if np.isfinite(p)}
    exact = 0
    for j, p in enumerate(pgcs):
        if p is None or p not in by_pgc:
            continue
        k = by_pgc[p]
        if np.isfinite(leda[2][k]):
            idx[j] = k
            sep[j] = 0.0
            pa[j] = leda[2][k]
            exact += 1
    print("matched by PGC number: %d" % exact)

    found = idx >= 0
    print("OpenNGC entries: %d   already have PosAng: %d   matched to LEDA: %d"
          % (len(ra), int(have.sum()), int(found.sum())))

    # self-check: for the entries that already carry an angle, how close is LEDA's?
    pos_idx = [i for i, r in enumerate(rows)
               if _sexa(r.get("RA", ""), True) is not None and _sexa(r.get("Dec", ""), False) is not None]
    truth, guess = [], []
    for j, i in enumerate(pos_idx):
        if have[j] and idx[j] >= 0 and np.isfinite(pa[j]):
            try:
                truth.append(float(rows[i]["PosAng"]))
            except (TypeError, ValueError):
                continue
            guess.append(pa[j])
    truth, guess = np.array(truth), np.array(guess)
    if len(truth):
        d = np.abs(truth - guess) % 180.0
        d = np.minimum(d, 180.0 - d)
        print()
        print("check against OpenNGC's own angles (%d entries):" % len(truth))
        print("   median difference %.1f deg    within 10 deg: %.0f%%    within 20 deg: %.0f%%"
              % (float(np.median(d)), 100 * float(np.mean(d <= 10)), 100 * float(np.mean(d <= 20))))

    blanks = (~have) & found & np.isfinite(pa)
    # Rows typed Dup/NonEx are skipped by the loader, so filling them would write values nothing ever reads.
    usable = np.array([(rows[i].get("Type") or "").strip() not in ("Dup", "NonEx") for i in pos_idx], bool)
    blanks = blanks & usable
    print()
    print("missing angles that LEDA can fill: %d of %d missing (rows the loader actually uses)"
          % (int(blanks.sum()), int((~have).sum())))

    if not write:
        print("(nothing written; pass --write to create the addendum)")
        return
    os.makedirs(os.path.dirname(ADDENDUM), exist_ok=True)
    n = 0
    with open(ADDENDUM, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("Name;PosAng;SepArcmin;logD25;logR25;Source\n")
        for j, i in enumerate(pos_idx):
            if not blanks[j]:
                continue
            name = rows[i].get("Name", "").strip()
            if not name:
                continue
            fh.write("%s;%.1f;%.2f;%s;%s;HyperLEDA VII/237\n"
                     % (name, pa[j] % 180.0, sep[j],
                        "" if not np.isfinite(leda[3][idx[j]]) else "%.2f" % leda[3][idx[j]],
                        "" if not np.isfinite(leda[4][idx[j]]) else "%.2f" % leda[4][idx[j]]))
            n += 1
    print("wrote %d rows to %s" % (n, ADDENDUM))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="write data/openngc/posang.csv")
    ap.add_argument("--validate", action="store_true", help="download and report, writing nothing")
    ap.add_argument("--force", action="store_true", help="ignore the cache and download again")
    args = ap.parse_args(argv)
    if args.force:
        download(force=True)
    run(write=args.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
