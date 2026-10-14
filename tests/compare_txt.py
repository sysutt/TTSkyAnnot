"""Compare a TTSkyAnnot .objects.txt with one written by PixInsight's AnnotateImage.

    python tests/compare_txt.py OURS.objects.txt PI.objects.txt
For each catalogue block: objects only on one side, and for objects on both sides the differences in
RA/Dec (arcsec), pixel position and every other column.
"""
import sys

import numpy as np


def parse(path):
    blocks, cur, lines = {}, None, open(path, encoding="utf-8").read().split("\n")
    i = 0
    while i < len(lines):
        if lines[i] and i + 2 < len(lines) and lines[i + 2].startswith("Name;RA(deg)"):
            name = "Gaia DR3" if "Gaia DR3" in lines[i] else lines[i]
            cols = lines[i + 2].split(";")
            rows = []
            i += 3
            while i < len(lines) and lines[i]:
                rows.append(lines[i].split(";"))
                i += 1
            blocks[name] = (cols, rows)
        i += 1
    return blocks


def main(ours, pi):
    A, B = parse(ours), parse(pi)
    for name in sorted(set(A) | set(B)):
        if name not in A or name not in B:
            print("%-11s only in %s (%d rows)" % (name, "ours" if name in A else "PixInsight",
                                                  len((A.get(name) or B.get(name))[1])))
            continue
        (ca, ra), (cb, rb) = A[name], B[name]
        if name == "Gaia DR3":                   # no names: match by position
            from scipy.spatial import cKDTree
            pa = np.array([[float(r[1]), float(r[2])] for r in ra])
            pb = np.array([[float(r[1]), float(r[2])] for r in rb])
            d, j = cKDTree(pb).query(pa)
            pairs = [(ra[i], rb[j[i]]) for i in range(len(ra)) if d[i] < 0.5 / 3600]
            only_a, only_b = len(ra) - len(pairs), len(rb) - len(pairs)
        else:
            ka, kb = {r[0]: r for r in ra}, {r[0]: r for r in rb}
            pairs = [(ka[k], kb[k]) for k in ka if k in kb]
            only_a = sorted(set(ka) - set(kb))
            only_b = sorted(set(kb) - set(ka))
        dra = [abs(float(a[1]) - float(b[1])) * 3600 * np.cos(np.radians(float(a[2]))) for a, b in pairs]
        dde = [abs(float(a[2]) - float(b[2])) * 3600 for a, b in pairs]
        dpx = [np.hypot(float(a[3]) - float(b[3]), float(a[4]) - float(b[4])) for a, b in pairs]
        print("%-11s ours %4d  PI %4d  common %4d | RA/Dec max diff %.3f\" / %.3f\" | pixel diff median %.2f max %.2f" % (
            name, len(ra), len(rb), len(pairs), max(dra or [0]), max(dde or [0]),
            np.median(dpx) if dpx else 0, max(dpx or [0])))
        if not isinstance(only_a, int):
            if only_a:
                print("    only ours: %s" % ", ".join(only_a[:12]))
            if only_b:
                print("    only PI:   %s" % ", ".join(only_b[:12]))
        else:
            print("    unmatched: ours %d, PI %d" % (only_a, only_b))
        if ca != cb:
            print("    columns differ:\n      ours %s\n      PI   %s" % (ca, cb))
        diffs = {}
        for a, b in pairs:
            for k in range(5, min(len(a), len(b))):
                if a[k] != b[k]:
                    diffs.setdefault(cb[k] if k < len(cb) else k, []).append((a[0] or a[1], a[k], b[k]))
        for col, lst in diffs.items():
            print("    column %-14s differs in %d/%d rows, e.g. %s" % (col, len(lst), len(pairs), lst[:3]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
