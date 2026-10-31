"""Build a deep star index for the plate solver, in the format ``solver.StarIndex`` reads.

The bundled ``gaia_stars.bin`` is a *bright* subset: measured on the live server it holds about 480 万 stars
and any 0.6° cone returns the same ~160 stars whether the magnitude limit is 13 or 20 — the file simply has
nothing fainter. Sparse fields then have too few catalogue stars to vote on, which is why a 1.6° M94 frame
(61 catalogue stars) never solved even with a correct centre and pixel scale.

This script builds a **deeper** index (default G < 15.5, about 30 M stars, ~360 MB) so those fields have
thousands of stars to match against.

File format (see ``solver.StarIndex``): a flat little-endian float32 array, three values per star
``ra_deg, dec_deg, gmag``. No header, no padding. ``StarIndex`` re-sorts by declination on load, so the write
order here does not matter; writing in declination bands keeps the download memory-bounded anyway.

Data source: VizieR ``I/350/gaiaedr3`` through the asu-tsv interface, one declination band at a time. The
public mirrors are tried in order — CDS was unreachable for weeks during the 2026-10 network incident, so a
single hard-coded host is not good enough.

    python3 tools/build_deep_index.py --out /home/ubuntu/astro/gaia_deep.bin --mag 15.5
    python3 tools/build_deep_index.py --out /tmp/test.bin --dec-min 40 --dec-max 42 --mag 16   # small test
    python3 tools/build_deep_index.py --verify /home/ubuntu/astro/gaia_deep.bin

The output is written to ``<out>.part`` and renamed when complete, so a crash never leaves a half index in
place. ``--verify`` re-reads the file, prints the star count and the per-cone density that the solver will see.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
import urllib.parse
import urllib.request

import numpy as np

MIRRORS = (
    "https://vizier.cds.unistra.fr/viz-bin/asu-tsv",
    "https://vizier.cfa.harvard.edu/viz-bin/asu-tsv",
    "http://vizier.china-vo.org/viz-bin/asu-tsv",
    "http://vizier.nao.ac.jp/viz-bin/asu-tsv",
    "https://vizier.idia.ac.za/viz-bin/asu-tsv",
)


def fetch_band(dec0: float, dec1: float, mag: float, timeout: float, ra_step: float) -> np.ndarray:
    """All stars with dec in [dec0, dec1) and G < mag, as an (N, 3) float32 array.

    The query is split into right-ascension stripes: a whole declination band at 15.5 mag can be tens of
    millions of rows, far beyond what one synchronous VizieR request will answer.
    """
    ra = 0.0
    out = []
    while ra < 360.0:
        q = {
            "-source": "I/350/gaiaedr3",
            "-out": "RA_ICRS,DE_ICRS,Gmag",
            "-out.max": "2000000",
            "RA_ICRS": "%.3f..%.3f" % (ra, min(360.0, ra + ra_step)),
            "DE_ICRS": "%.3f..%.3f" % (dec0, dec1),
            "Gmag": "<%.2f" % mag,
        }
        blob = None
        for base in MIRRORS:
            try:
                url = base + "?" + urllib.parse.urlencode(q)
                with urllib.request.urlopen(url, timeout=timeout) as r:
                    blob = r.read().decode("utf-8", "replace")
                break
            except Exception:
                continue
        if blob is None:
            print("  RA %.0f..%.0f°: every mirror failed, skipping this stripe" % (ra, ra + ra_step),
                  file=sys.stderr)
            ra += ra_step
            continue
        rows = []
        for line in blob.splitlines():
            if not line or line.startswith("#") or line.startswith("---") or line.startswith(" "):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            try:
                rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError:
                continue
        if rows:
            out.append(np.array(rows, dtype="<f4"))
        print("  RA %3.0f..%3.0f°: %d stars" % (ra, ra + ra_step, len(rows)), flush=True)
        ra += ra_step
    if not out:
        return np.empty((0, 3), dtype="<f4")
    return np.concatenate(out, axis=0)


def build(out_path: str, mag: float, dec_band: float, ra_step: float, timeout: float,
          dec_min: float = -90.0, dec_max: float = 90.0) -> int:
    tmp = out_path + ".part"
    total = 0
    t0 = time.time()
    with open(tmp, "wb") as fh:
        dec = dec_min
        while dec < dec_max:
            dec1 = min(dec_max, dec + dec_band)
            print("band %+.1f .. %+.1f" % (dec, dec1), flush=True)
            a = fetch_band(dec, dec1, mag, timeout, ra_step)
            if len(a):
                a.tofile(fh)                     # flat float32, exactly what StarIndex reads
                total += len(a)
            dec = dec1
    os.replace(tmp, out_path)
    dt = time.time() - t0
    print("wrote %s: %d stars, %.0f MB, %.0f min" % (out_path, total, total * 12 / 1e6, dt / 60.0))
    return total


def verify(path: str, cones: int = 8) -> None:
    """Read the file back and report the density the solver will actually see."""
    raw = np.fromfile(path, dtype="<f4")
    if raw.size % 3:
        raise SystemExit("not a StarIndex file: length is not a multiple of 12 bytes")
    a = raw.reshape(-1, 3)
    print("stars: %d | size: %.0f MB" % (len(a), raw.size * 4 / 1e6))
    print("mag range: %.1f .. %.1f" % (float(a[:, 2].min()), float(a[:, 2].max())))
    print("RA range: %.1f .. %.1f | Dec range: %.1f .. %.1f"
          % (float(a[:, 0].min()), float(a[:, 0].max()), float(a[:, 1].min()), float(a[:, 1].max())))
    rnd = random.Random(0)
    ra = a[:, 0].astype(np.float64)
    dec = a[:, 1].astype(np.float64)
    counts = []
    for _ in range(cones):
        ra0, dec0 = rnd.uniform(0, 360), rnd.uniform(-75, 75)
        cosd = np.cos(np.radians(dec0))
        dra = np.abs((ra - ra0 + 180.0) % 360.0 - 180.0) * cosd
        hit = np.hypot(dra, dec - dec0) <= 0.6
        counts.append(int(hit.sum()))
    print("cone 0.6°: mean %.0f stars (min %d, max %d)" % (np.mean(counts), min(counts), max(counts)))
    if np.mean(counts) < 800:
        print("WARNING: still sparse — the solver wants ≥1000 per 0.6° cone in most fields", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="/home/ubuntu/astro/gaia_deep.bin")
    ap.add_argument("--mag", type=float, default=15.5, help="magnitude limit (default 15.5 ≈ 30 M stars)")
    ap.add_argument("--dec-band", type=float, default=2.0, help="degrees of declination per download band")
    ap.add_argument("--ra-step", type=float, default=15.0, help="degrees of right ascension per request")
    ap.add_argument("--timeout", type=float, default=60.0, help="per-request timeout in seconds")
    ap.add_argument("--dec-min", type=float, default=-90.0)
    ap.add_argument("--dec-max", type=float, default=90.0)
    ap.add_argument("--verify", metavar="FILE", help="only read an existing index back and report it")
    a = ap.parse_args(argv)
    if a.verify:
        verify(a.verify)
        return 0
    build(a.out, a.mag, a.dec_band, a.ra_step, a.timeout, a.dec_min, a.dec_max)
    verify(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
