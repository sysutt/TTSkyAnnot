"""Astrometric accuracy check against PixInsight.

Ground truth = star centroids measured on the image itself (15x15 window around each Hipparcos/Tycho-2
/Gaia star listed by PixInsight's AnnotateImage, local background removed, saturated/faint/crowded stars
skipped). Both PixInsight's positions and TTSkyAnnot's positions are compared with it.

    python tests/accuracy_vs_pi.py IMAGE PI_OBJECTS_TXT [TARGET_NAME] [EPOCH]
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from skyannot import imageio, starfind, solve, refine, vizier  # noqa: E402


def pi_stars(txt):
    rows, cur = [], None
    for line in open(txt, encoding="utf-8"):
        line = line.rstrip("\n")
        if line in ("Hipparcos", "TYCHO-2") or line.startswith("Gaia DR3"):
            cur = line
            continue
        if not line:
            cur = None
            continue
        if cur and ";" in line and not line.startswith(("Name;", "Hipparcos", "Tycho", "Gaia")):
            f = line.split(";")
            try:
                rows.append((float(f[1]), float(f[2]), float(f[3]), float(f[4])))
            except ValueError:
                pass
    return np.array(rows)


def centroids(L, pts, r=7):
    H, W = L.shape
    meas, keep = [], []
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    for i, (px, py) in enumerate(pts):
        cx, cy = int(round(px - 0.5)), int(round(py - 0.5))
        if cx < r + 2 or cy < r + 2 or cx > W - r - 3 or cy > H - r - 3:
            continue
        win = L[cy - r:cy + r + 1, cx - r:cx + r + 1]
        edge = np.concatenate([win[0], win[-1], win[:, 0], win[:, -1]])
        bg = np.median(edge)
        nz = np.median(np.abs(edge - bg)) * 1.4826 + 1e-4
        w = np.clip(win - bg, 0, None)
        if win.max() > 0.98 or w.max() < 8 * nz:
            continue
        w = np.where(w > 3 * nz, w, 0)
        s = w.sum()
        mx, my = (w * xx).sum() / s, (w * yy).sum() / s
        if np.hypot(mx, my) > 3:
            continue
        meas.append((cx + mx + 0.5, cy + my + 0.5))      # PixInsight convention
        keep.append(i)
    return np.array(meas), np.array(keep)


def main(img_path, txt_path, target=None, epoch=2026.0):
    img = imageio.load(img_path)
    disp = imageio.display_stretch(img)
    rows = pi_stars(txt_path)
    meas, keep = centroids(disp.mean(-1), rows[:, 2:4])
    st = starfind.find_stars(disp, max_stars=3000)
    hint = imageio.hints(img)
    if target:
        hint["target"] = target
    w0, info = solve.solve_ttastro(st, img.width, img.height, hint)
    c = w0.pixel_to_world_values(img.width / 2, img.height / 2)
    rad = 0.5 * np.hypot(img.width, img.height) * np.sqrt(abs(np.linalg.det(w0.pixel_scale_matrix))) * 1.03
    area = img.width * img.height * abs(np.linalg.det(w0.pixel_scale_matrix))
    g = vizier.cone("gaia", float(c[0]), float(c[1]), rad, mag_max=refine.reference_mag_limit(area))
    f = lambda k: np.array([float(r[k]) if r[k] else np.nan for r in g])  # noqa: E731
    ra, de = refine.propagate(f("RA_ICRS"), f("DE_ICRS"), f("pmRA"), f("pmDE"), 2016.0, epoch)
    w, stt = refine.refine(w0, np.array([[s[0], s[1]] for s in st]), img.width, img.height, ra, de, f("Gmag"))
    # Compare at the observation epoch. PixInsight lists J2000 positions; over 25 years a 10 mas/yr star moves
    # about 1 px at 0.29"/px, so its pixel coordinates are shifted by each star's proper motion (taken from
    # Gaia, rate converted with the local plate scale) before comparing. Stars without Gaia motion are dropped.
    from scipy.spatial import cKDTree
    gra, gde = f("RA_ICRS"), f("DE_ICRS")
    g2000 = np.column_stack(refine.propagate(gra, gde, f("pmRA"), f("pmDE"), 2016.0, 2000.0))
    cosd = np.cos(np.radians(rows[keep, 1]))
    d, j = cKDTree(g2000 * [1.0, 1.0]).query(np.column_stack([rows[keep, 0], rows[keep, 1]]))
    ok = (d < 1.5 / 3600) & np.isfinite(f("pmRA")[np.minimum(j, len(gra) - 1)])
    j = j[ok]
    ra_e, de_e = refine.propagate(rows[keep][ok, 0], rows[keep][ok, 1], f("pmRA")[j], f("pmDE")[j], 2000.0, epoch)
    meas_ok = meas[ok]
    pi_e = rows[keep][ok, 2:4] + (np.column_stack(w.all_world2pix(ra_e, de_e, 0)) -
                                  np.column_stack(w.all_world2pix(rows[keep][ok, 0], rows[keep][ok, 1], 0)))
    print("%s  %dx%d  truth stars %d (with Gaia motion %d)  epoch %.2f  (%s)" % (
        os.path.basename(img_path), img.width, img.height, len(meas), ok.sum(), epoch, stt))
    for name, p in (("PixInsight", pi_e),
                    ("TTSkyAnnot rough", np.column_stack(w0.all_world2pix(ra_e, de_e, 0)) + 0.5),
                    ("TTSkyAnnot refined", np.column_stack(w.all_world2pix(ra_e, de_e, 0)) + 0.5)):
        e = np.hypot(*(p - meas_ok).T)
        rr = np.hypot(meas_ok[:, 0] - img.width / 2, meas_ok[:, 1] - img.height / 2) / np.hypot(img.width / 2, img.height / 2)
        print("  %-17s median %.2f px  p90 %.2f px  outer third %.2f px" % (
            name, np.median(e), np.percentile(e, 90), np.median(e[rr > 0.66])))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None,
         float(sys.argv[4]) if len(sys.argv) > 4 else 2026.0)
