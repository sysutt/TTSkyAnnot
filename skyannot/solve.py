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
TTASTRO_STARS = "https://ttastro.com/api/i/starcone"     # the same service, star cones for refinement
NOVA_URL = "https://nova.astrometry.net"


class SolveError(RuntimeError):
    pass


TTASTRO_IMAGE_URL = "https://ttastro.com/api/i/solve_image"
TTASTRO_RESULT_URL = "https://ttastro.com/api/i/solve_result"


def remote_image(display_rgb, api_key="", timeout=540.0, log=print, pixscale=None,
                 image_url=None, result_url=None):
    """Solve a frame the local solvers could not: upload an 800 px thumbnail to our own service.

    The service extracts the stars itself and races its local astrometry.net against nova.astrometry.net,
    so this works for screenshots and heavily processed pictures where the local star lists fail.
    Returns (wcs, info) or raises SolveError."""
    import base64, io as _io, time as _time
    import requests
    from . import __version__
    from PIL import Image
    a = (np.clip(display_rgb, 0, 1) * 255).astype(np.uint8)
    h, w = a.shape[:2]
    k = min(1.0, 800.0 / float(max(h, w)))
    im = Image.fromarray(a)
    if k < 1.0:
        im = im.resize((int(round(w * k)), int(round(h * k))), Image.LANCZOS)
    buf = _io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    headers = {"User-Agent": "TTSkyAnnot/%s (Windows)" % __version__}
    if (api_key or "").strip():
        headers["X-API-Key"] = api_key.strip()
    log("  remote: uploading a %dx%d thumbnail" % im.size)
    thumb_scale = (float(pixscale) / k) if pixscale else None
    body = {"image": b64, "w": im.size[0], "h": im.size[1], "cpu_s": 120}
    if thumb_scale:
        body["pixscale"] = round(thumb_scale, 3)
    url = (image_url or TTASTRO_IMAGE_URL)
    res_url = (result_url or TTASTRO_RESULT_URL)
    d = None
    for attempt in range(4):
        r = requests.post(url, json=body, headers=headers, timeout=60)
        d = r.json()
        if d.get("ok"):
            break
        if d.get("code") == "busy" and attempt < 3:
            wait = float(d.get("retry_after") or 20)
            log("  remote: busy, retrying in %.0f s" % wait)
            _time.sleep(wait)
            continue
        raise SolveError(d.get("reason") or d.get("code") or "remote image solve failed")
    if not d or not d.get("ok"):
        raise SolveError("remote image solve failed")
    if not d.get("ok"):
        raise SolveError(d.get("reason") or d.get("code") or "remote image solve failed")
    job = d.get("job")
    t0 = _time.time()
    last_log = 0.0
    busy_left = 3
    while _time.time() - t0 < timeout:
        _time.sleep(3.0)
        q = requests.get(res_url, params={"job": job}, headers=headers, timeout=30).json()
        if q.get("status") == "running":
            waited = _time.time() - t0
            if waited - last_log >= 15.0:
                last_log = waited
                log("  remote: still solving, %.0f s" % waited)
            continue
        if not q.get("ok") and q.get("code") == "busy" and busy_left:
            busy_left -= 1
            _time.sleep(float(q.get("retry_after") or 20))
            continue
        if not q.get("ok"):
            raise SolveError(q.get("reason") or q.get("code") or "remote image solve failed")
        cd = q.get("cd_deg")
        if not cd:
            raise SolveError("remote image solve returned no WCS")
        # the service's WCS is for the thumbnail; map it back to full-resolution pixels
        cw, ch = im.size
        crpix = ((cw / 2.0 + 1.0 - 0.5) / k + 0.5, (ch / 2.0 + 1.0 - 0.5) / k + 0.5)
        cd_full = [[float(v) * k for v in row] for row in cd]
        wcs = _tan_wcs((q["ra"], q["dec"]), crpix, cd_full)
        log("  remote: 识别完成，用时 %.0f 秒" % (_time.time() - t0))
        info = {"solver": q.get("name") or "remote", "remote": True, "elo_ms": q.get("elapsed_ms")}
        return wcs, info
    raise SolveError("remote image solve timed out")


def fetch_stars(ra, dec, radius, mag=16.0, limit=80000, timeout=20.0, log=print):
    """A cone of catalogue stars from the online service: [[ra, dec, G], ...] brightest first, or [].

    The service reads its own Gaia DR3 G<16 index, so the app refines a plate solution without waiting for
    VizieR (seconds against minutes) and without depending on a third-party mirror being up.
    """
    import requests
    from . import __version__
    payload = {"ra": float(ra) % 360.0, "dec": max(-89.5, min(89.5, float(dec))),
               "radius": round(float(radius), 4), "mag": float(mag), "limit": int(limit)}
    try:
        r = requests.post(TTASTRO_STARS, json=payload,
                          headers={"User-Agent": "TTSkyAnnot/%s (Windows)" % __version__}, timeout=timeout)
        if r.status_code != 200:
            log("  star cone: HTTP %d" % r.status_code)
            return []
        d = r.json()
        if not d.get("ok"):
            log("  star cone: %s" % (d.get("reason") or "not available"))
            return []
        return d.get("stars") or []
    except Exception as e:
        log("  star cone: %s" % e)
        return []


def _tan_wcs(crval, crpix, cd):
    from astropy.wcs import WCS
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [float(crval[0]), float(crval[1])]
    w.wcs.crpix = [float(crpix[0]), float(crpix[1])]
    w.wcs.cd = np.asarray(cd, float)
    return w


# ───────────────────────────── ttastro.com ─────────────────────────────
def solve_ttastro(stars, width, height, hint=None, log=print, timeout=60.0, max_wait=180.0, api_key=""):
    """stars: [(x, y, flux, ...)] in index coordinates, brightest first.
    The server has a single solve slot and answers {"code": "busy"} when it is taken: back off and retry.
    api_key: the key from ttastro.com/ttskyannot/key; without one the server allows only a few anonymous
    solves a day, and its 401/429 reasons say what to do about it."""
    import requests
    from . import __version__
    payload = {"w": int(width), "h": int(height),
               "stars": [[round(float(s[0]), 2), round(float(s[1]), 2), round(float(s[2]), 1)] for s in stars[:200]],
               "hint": {k: v for k, v in (hint or {}).items() if k in ("target", "ra", "dec", "pixscale") and v is not None}}
    if len(payload["stars"]) < 8:
        raise SolveError("too few stars detected (%d)" % len(payload["stars"]))
    t0, delay = time.time(), 3.0
    while True:
        headers = {"User-Agent": "TTSkyAnnot/%s (Windows)" % __version__}
        if (api_key or "").strip():
            headers["X-API-Key"] = api_key.strip()
        # 网络抖动（SSL EOF、连接重置）不该直接让 ttastro 判负：短退避重试两次
        last_err = None
        r = None
        for attempt in range(3):
            try:
                r = requests.post(TTASTRO_URL, json=payload, headers=headers, timeout=timeout)
                break
            except Exception as e:
                last_err = e
                if attempt < 2:
                    log("  ttastro: 连接失败，%.0f 秒后重试（%d/2）" % (1.5 * (attempt + 1), attempt + 1))
                    time.sleep(1.5 * (attempt + 1))
        if r is None:
            raise SolveError("ttastro unreachable: %s" % str(last_err)[:120])
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
    # The service answers cd in radians around the image centre (Nebula3D blind-solve spec); FITS wants
    # degrees. cd_deg is sent as well, so an engine that still answers degrees keeps working.
    cd = d.get("cd_deg")
    if not cd:
        import math
        cd = [[float(v) * 180.0 / math.pi for v in row] for row in (d.get("cd") or [])]
    # The reference point is the image centre in index coordinates (W/2, H/2) -> FITS adds 1.
    w = _tan_wcs((d["ra"], d["dec"]), (width / 2.0 + 1.0, height / 2.0 + 1.0), cd)
    name = d.get("name") or (d.get("matched") if isinstance(d.get("matched"), str) else "")
    info = {"solver": "ttastro", "name": name, "matched": d.get("matched"), "nstars": d.get("nstars"),
            "rms_arcsec": d.get("rms_arcsec"), "objects": d.get("objects") or [],
            "parity": d.get("parity"), "elapsed_ms": d.get("elapsed_ms"),
            "rotation": d.get("rotation"), "north_angle": d.get("north_angle")}
    return w, info


# ───────────────────────────── ASTAP (offline) ─────────────────────────────
def astap_candidates(configured: str | None = None) -> list:
    """Every path ASTAP is looked for, in order, so a miss can be reported honestly."""
    cands = [configured] if configured else []
    cands += [shutil.which("astap"), shutil.which("astap_cli"),
              r"C:\Program Files\astap\astap.exe", r"C:\Program Files\astap\astap_cli.exe",
              r"C:\Program Files (x86)\astap\astap.exe"]
    return [c for c in cands if c]


def find_astap(configured: str | None = None) -> str | None:
    return next((c for c in astap_candidates(configured) if os.path.isfile(c)), None)


def astap_db_hint(exe: str, look=None) -> str:
    """A note about ASTAP's star database when the one installed cannot match the field, else "".

    ASTAP's star databases are built for a range of field sizes, and they are separate downloads. A telescope
    long enough to give a small field (a 2170 mm SCT gives 0.28 x 0.15 deg) needs H17 or H18; the wide-field
    D-bases start at 0.6 deg and simply cannot match such a field, which ASTAP reports only as "no solution".
    Detecting it here turns an unexplained failure into something the user can act on.
    """
    try:
        names = look(os.path.dirname(os.path.abspath(exe))) if look else os.listdir(
            os.path.dirname(os.path.abspath(exe)))
    except OSError:
        return ""
    db = {n.split("_")[0].lower() for n in names if "_" in n}
    if db & {"h17", "h18"}:
        return ""
    wide = sorted(d for d in db if d and d[0] == "d" and d[1:].isdigit())
    if not db:
        return ("ASTAP has no star database next to its program file, so it cannot solve anything. "
                "Download one (H17 or H18 for small fields) from the ASTAP site.")
    return ("ASTAP only has the %s star database(s), which cover fields from about 0.6 degrees up. For a "
            "field this small it needs H17 or H18 as well - a separate download from the ASTAP site."
            % "/".join(wide or sorted(db)))


def solve_astap(display_rgb, hint=None, exe=None, log=print, timeout=180):
    """Write a temporary 16-bit FITS of the display luminance and let ASTAP solve it."""
    from astropy.io import fits
    from astropy.wcs import WCS
    exe = find_astap(exe)
    if not exe:
        raise SolveError("ASTAP not found")
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
            # -fov is the field WIDTH in degrees: it must come from the long side, not the height, or a
            # landscape frame gets a field too small for its scale and ASTAP searches the wrong place
            fov = hint["pixscale"] * max(lum.shape) / 3600.0
            args += ["-fov", "%.4f" % fov]
        else:
            args += ["-fov", "0"]
        proc = subprocess.run(args, capture_output=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        wf = os.path.join(tmpd, "frame.wcs")
        if not os.path.exists(wf):
            # ASTAP keeps its verdict in an INI beside the FITS; its own text and that verdict say far more
            # than "could not solve", and the database is the usual reason on a small field
            said = b" ".join(x for x in (proc.stdout, proc.stderr) if x).decode("utf-8", "replace").strip()
            note = astap_db_hint(exe)
            detail = " ".join(x for x in (said[:300], note) if x)
            raise SolveError("ASTAP did not find a solution" + (": " + detail if detail else ""))
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
