"""The popular-field index: the hottest fields of the works library, each with its own Gaia star pattern.

The works library on the server (astrobin table) knows where 12,633 of its 14,200 solved photographs point,
clustered into 1,304 distinct fields. For every field the 80 brightest Gaia stars in the magnitude range the
annotator's detector actually sees (G 9-15) were fetched from the online star index and bundled here: 104,320
stars in 1.25 MB, compressed. A frame that matches one of these patterns can be solved with a position and a
scale instead of from scratch; the matcher lives in match.py and is still being tuned, but the data is final.
"""
import gzip
import json
import os

from . import catalogs

_CACHE = None


def path():
    """The bundled index file."""
    return os.path.join(catalogs.data_dir(), "field_index.json.gz")


def load(path=None):
    """Every field: [{"ra", "dec", "n", "fov", "stars": [[ra, dec, G], ...]}, ...], cached after the first call."""
    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE
    with gzip.open(path or globals()["path"](), "rb") as f:
        rows = json.loads(f.read().decode("utf-8"))
    if path is None:
        _CACHE = rows
    return rows


def coverage(rows=None):
    """(fields, stars, photographs) in the index, for about and for tests."""
    rows = load() if rows is None else rows
    return len(rows), sum(len(r.get("stars") or []) for r in rows), sum(int(r.get("n") or 0) for r in rows)


def nearest(ra, dec, rows=None):
    """The field closest to a position, for diagnostics: (field, separation in degrees) or (None, 999)."""
    rows = load() if rows is None else rows
    best, bestd = None, 1e9
    from math import cos, hypot, radians
    for r in rows:
        d = hypot((r["ra"] - ra) * cos(radians(dec)), r["dec"] - dec)
        if d < bestd:
            best, bestd = r, d
    return best, bestd
