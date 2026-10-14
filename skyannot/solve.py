"""Plate solving: an initial (linear) WCS for the image.

Order: the ttastro.com solver (only star coordinates are sent), then ASTAP if installed (offline),
then nova.astrometry.net if an API key is configured. Every WCS returned here is a plain TAN solution in
FITS pixel convention (1-based, pixel centres on integers); refine.py turns it into an accurate TAN-SIP
solution against Gaia and is also what decides whether a solve is trustworthy.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import time

import numpy as np

TTASTRO_URL = "https://ttastro.com/api/i/solve"
NOVA_URL = "https://nova.astrometry.net"


class SolveError(RuntimeError):
    pass


def _tan_wcs(crval, crpix, cd):
    from astropy.wcs import WCS
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [float(crval[0]), float(crval[1])]
    w.wcs.crpix = [float(crpix[0]), float(crpix[1])]
    w.wcs.cd = np.asarray(cd, float)
    return w


# ───────────────────────────── ttastro.com ─────────────────────────────
def solve_ttastro(stars, width, height, hint=None, log=print, timeout=60.0, max_wait=180.0):
    """stars: [(x, y, flux, ...)] in index coordinates, brightest first.
    The server has a single solve slot and answers {"code": "busy"} when it is taken: back off and retry."""
    import requests
    payload = {"w": int(width), "h": int(height),
               "stars": [[round(float(s[0]), 2), round(float(s[1]), 2), round(float(s[2]), 1)] for s in stars[:200]],
               "hint": {k: v for k, v in (hint or {}).items() if k in ("target", "ra", "dec", "pixscale") and v is not None}}
    if len(payload["stars"]) < 8:
        raise SolveError("too few stars detected (%d)" % len(payload["stars"]))
    t0, delay = time.time(), 3.0
    while True:
        r = requests.post(TTASTRO_URL, json=payload, timeout=timeout)
        try:
            d = r.json()
        except ValueError:
            raise SolveError("solver returned HTTP %d" % r.status_code)
        if d.get("ok"):
            break
        if d.get("code") == "busy" and time.time() - t0 < max_wait:
            log("  solver busy, retrying in %.0f s" % delay)
            time.sleep(delay)
            delay = min(delay * 1.6, 20.0)
            continue
        raise SolveError(d.get("reason") or d.get("code") or "not solved")
    # The reference point is the image centre in index coordinates (W/2, H/2) -> FITS adds 1.
    w = _tan_wcs((d["ra"], d["dec"]), (width / 2.0 + 1.0, height / 2.0 + 1.0), d["cd"])
    info = {"solver": "ttastro", "matched": d.get("matched"), "nstars": d.get("nstars"),
            "rms_arcsec": d.get("rms_arcsec"), "objects": d.get("objects") or []}
    return w, info


# ───────────────────────────── ASTAP (offline) ─────────────────────────────
def find_astap(configured: str | None = None) -> str | None:
    cands = [configured] if configured else []
    cands += [shutil.which("astap"), shutil.which("astap_cli"),
              r"C:\Program Files\astap\astap.exe", r"C:\Program Files\astap\astap_cli.exe",
              r"C:\Program Files (x86)\astap\astap.exe"]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def solve_astap(display_rgb, hint=None, exe=None, log=print, timeout=180):
    """Write a temporary 16-bit FITS of the display luminance and let ASTAP solve it."""
    from astropy.io import fits
    from astropy.wcs import WCS
    exe = find_astap(exe)
    if not exe:
        raise SolveError("ASTAP not installed")
    lum = np.asarray(display_rgb, np.float32).mean(axis=2)
    tmpd = tempfile.mkdtemp(prefix="skyannot_astap_")
    try:
        fp = os.path.join(tmpd, "frame.fits")
        fits.PrimaryHDU((np.clip(lum, 0, 1) * 65535).astype(np.uint16)).writeto(fp)
        args = [exe, "-f", fp, "-r", "50", "-z", "0", "-wcs"]
        hint = hint or {}
        if hint.get("ra") is not None and hint.get("dec") is not None:
            args += ["-ra", "%.5f" % (hint["ra"] / 15.0), "-spd", "%.5f" % (hint["dec"] + 90.0)]
        if hint.get("pixscale"):
            fov = hint["pixscale"] * lum.shape[0] / 3600.0
            args += ["-fov", "%.4f" % fov]
        else:
            args += ["-fov", "0"]
        subprocess.run(args, capture_output=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        wf = os.path.join(tmpd, "frame.wcs")
        if not os.path.exists(wf):
            raise SolveError("ASTAP could not solve the image")
        try:
            hdr = fits.getheader(wf)                     # ASTAP writes a header-only FITS file
        except OSError:
            hdr = fits.Header.fromfile(wf, sep="\n", endcard=False, padding=False)
        w = WCS(hdr, naxis=2)
        w.sip = None
        return _tan_wcs(w.wcs.crval, w.wcs.crpix, w.pixel_scale_matrix), {"solver": "astap"}
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


# ───────────────────────────── nova.astrometry.net ─────────────────────────────
def solve_nova(display_rgb, api_key, log=print, max_edge=2000, timeout=600):
    """Upload a reduced JPEG (never public) and poll for the WCS. Slow (minutes), last resort."""
    import requests
    from astropy.io import fits
    from astropy.wcs import WCS
    from PIL import Image
    if not api_key:
        raise SolveError("no nova.astrometry.net API key configured")
    a = (np.clip(display_rgb, 0, 1) * 255).astype(np.uint8)
    h, w = a.shape[:2]
    k = min(1.0, max_edge / float(max(h, w)))
    im = Image.fromarray(a)
    if k < 1.0:
        im = im.resize((int(round(w * k)), int(round(h * k))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    r = requests.post(NOVA_URL + "/api/login", data={"request-json": json.dumps({"apikey": api_key})}, timeout=60)
    sess = r.json().get("session")
    if not sess:
        raise SolveError("nova login failed")
    meta = {"session": sess, "publicly_visible": "n", "allow_commercial_use": "n", "allow_modifications": "n"}
    r = requests.post(NOVA_URL + "/api/upload", data={"request-json": json.dumps(meta)},
                      files={"file": ("frame.jpg", buf.getvalue(), "image/jpeg")}, timeout=180)
    subid = r.json().get("subid")
    if not subid:
        raise SolveError("nova upload failed")
    log("  nova: submitted %s, waiting..." % subid)
    t0, job = time.time(), None
    while time.time() - t0 < timeout:
        time.sleep(8)
        jobs = requests.get(NOVA_URL + "/api/submissions/%s" % subid, timeout=60).json().get("jobs") or []
        job = next((j for j in jobs if j), None)
        if job:
            st = requests.get(NOVA_URL + "/api/jobs/%s" % job, timeout=60).json().get("status")
            if st == "success":
                break
            if st == "failure":
                raise SolveError("nova could not solve the image")
    else:
        raise SolveError("nova timed out")
    raw = requests.get(NOVA_URL + "/wcs_file/%s" % job, timeout=120).content
    hdr = fits.Header.fromstring(raw)
    ws = WCS(hdr, naxis=2)
    # back to full resolution: X_full = (X_small - 0.5) / k + 0.5 in FITS convention
    crpix = (np.asarray(ws.wcs.crpix) - 0.5) / k + 0.5
    return _tan_wcs(ws.wcs.crval, crpix, ws.pixel_scale_matrix * k), {"solver": "nova", "job": job}
