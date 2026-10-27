"""Merge OpenNGC's object sizes into the DSO index the web solver answers from.

The index at ``/var/www/ttastro-tiles/dso/index.json`` carries ``ax``/``ay`` (arcmin) per object, but the
numbers came from a different source than the desktop app, which reads OpenNGC. The two disagreed on the
objects that matter most: NGC1499 was 120' on the server and 160' in OpenNGC, so the web drew a circle a
quarter too small.

Rule: **the larger size wins**. A catalogue's major axis is often only the bright core (OpenNGC lists M76 at
1.12' while it spans about 2.7'), and the index sometimes already had the better value, so the merge only
grows a value and never shrinks one.

Run on the server (it needs write access to the index):

    python3 tools/merge_openngc_sizes.py --index /var/www/ttastro-tiles/dso/index.json
    python3 tools/merge_openngc_sizes.py --dry-run                  # report what would change
    python3 tools/merge_openngc_sizes.py --from-csv NGC.csv addendum.csv   # no download

The index is backed up to ``<index>.bak`` before anything is written; restart
``ttastro-interactions`` afterwards so the service re-reads it.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import sys
import urllib.request

DEFAULT_INDEX = "/var/www/ttastro-tiles/dso/index.json"
OPENNGC = (
    "https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/NGC.csv",
    "https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/addendum.csv",
)
GROWTH = 1.02          # only touch a row when OpenNGC is at least 2% larger


def norm(name: str) -> str | None:
    """Catalogue name → the key both sources agree on (NGC0650 / IC2005 style)."""
    k = (name or "").replace(" ", "").upper()
    if k.startswith("NGC") and k[3:].isdigit():
        return "NGC%04d" % int(k[3:])
    if k.startswith("IC") and k[2:].isdigit():
        return "IC%04d" % int(k[2:])
    return None


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=120) as fh:
        return fh.read().decode("utf-8", "replace")


def read_sizes(texts) -> dict:
    """{key: (maj_arcmin, min_arcmin or None)} from OpenNGC CSV text."""
    out = {}
    for text in texts:
        for row in csv.DictReader(io.StringIO(text), delimiter=";"):
            key = norm(row.get("Name"))
            if not key:
                continue
            try:
                maj = float(row["MajAx"]) if row.get("MajAx") else None
                mn = float(row["MinAx"]) if row.get("MinAx") else None
            except (TypeError, ValueError):
                continue
            if maj:
                out[key] = (maj, mn)
    return out


def merge(index: dict, sizes: dict, growth: float = GROWTH) -> list:
    """Grow ax/ay in place; returns the ids that changed."""
    col = {n: i for i, n in enumerate(index["fields"])}
    ia, ib = col["ax"], col["ay"]
    iid, iother = col["id"], col.get("other")
    changed = []
    for r in index["rows"]:
        keys = [norm(r[iid])]
        if iother is not None:
            keys.append(norm(r[iother]))
        for key in keys:
            if not key or key not in sizes:
                continue
            maj, mn = sizes[key]
            if maj > (r[ia] or 0) * growth:
                r[ia] = maj
                if mn and (not r[ib] or mn > r[ib]):
                    r[ib] = mn
                changed.append(r[iid])
            break
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--index", default=os.environ.get("TTASTRO_DSO_JSON", DEFAULT_INDEX))
    ap.add_argument("--from-csv", nargs="*", metavar="CSV",
                    help="OpenNGC CSV files to use instead of downloading them")
    ap.add_argument("--dry-run", action="store_true", help="report the changes, write nothing")
    ap.add_argument("--growth", type=float, default=GROWTH,
                    help="only grow a value when the new one is this many times larger (default 1.02)")
    a = ap.parse_args(argv)

    if a.from_csv:
        texts = [open(p, encoding="utf-8", errors="replace").read() for p in a.from_csv]
    else:
        texts = [fetch(u) for u in OPENNGC]
    sizes = read_sizes(texts)
    if not sizes:
        print("no sizes read from OpenNGC", file=sys.stderr)
        return 1
    print("OpenNGC sizes: %d objects" % len(sizes))

    with open(a.index, encoding="utf-8") as fh:
        index = json.load(fh)
    print("index: %s (%d rows)" % (a.index, len(index["rows"])))

    changed = merge(index, sizes, a.growth)
    print("rows changed: %d" % len(changed))
    for name in ("NGC1499", "M76", "NGC7000", "IC1396"):
        for r in index["rows"]:
            if r[0] == name:
                col = {n: i for i, n in enumerate(index["fields"])}
                print("  %-8s ax=%s ay=%s" % (name, r[col["ax"]], r[col["ay"]]))

    if a.dry_run:
        print("dry run: nothing written")
        return 0
    if not changed:
        print("nothing to write")
        return 0
    shutil.copyfile(a.index, a.index + ".bak")
    tmp = a.index + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False)
    os.replace(tmp, a.index)
    print("written; backup at %s.bak  (restart ttastro-interactions to reload)" % a.index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
