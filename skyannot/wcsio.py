"""Write the astrometric solution back to the image.

FITS and XISF get standard WCS keywords (TAN-SIP) in their own header; everything else in the file is kept
(XISF: pixel data, thumbnails and ICC profiles are copied byte for byte, see xisfedit.py). A stale
PixInsight astrometric solution stored as XISF properties is removed, otherwise PixInsight would keep using
it instead of the new keywords. Formats without a header (JPEG, PNG, TIFF) get a sidecar '<name>.wcs' file
(header-only FITS, the same convention astrometry.net uses).

The fit is done in the array's own pixel indices, so the keywords follow the FITS rule that pixel (1, 1) is
the first stored pixel.
"""
from __future__ import annotations

import os
import re
import tempfile

_WCS_KEY = re.compile(r"^(CTYPE\d|CRVAL\d|CRPIX\d|CDELT\d|CROTA\d|CUNIT\d|CD\d_\d|PC\d_\d|PV\d_\d+|"
                      r"A_\w+|B_\w+|AP_\w+|BP_\w+|LONPOLE|LATPOLE|WCSAXES|RADESYS|EQUINOX|MJDREF|DATEREF|PLTSOLVD)$")


def wcs_cards(wcs, solver_note=""):
    """[(name, raw FITS value text, comment)] for the solution."""
    from astropy.io import fits
    hdr = wcs.to_header(relax=True)
    # CD matrix form (CD1_1..CD2_2) instead of PC + CDELT: what PixInsight and most tools expect
    cd = wcs.pixel_scale_matrix
    for k in [k for k in list(hdr.keys()) if re.match(r"^(PC\d_\d|CDELT\d|CROTA\d)$", k)]:
        del hdr[k]
    pos = hdr.index("CRVAL2") + 1 if "CRVAL2" in hdr else len(hdr)
    for n, (i, j) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
        hdr.insert(pos + n, ("CD%d_%d" % (i + 1, j + 1), float(cd[i, j]), "linear transformation matrix"))
    cards = []
    for c in hdr.cards:
        if not c.keyword or c.keyword in ("COMMENT", "HISTORY", "DATE-OBS", "MJD-OBS", "MJDREF", "DATEREF"):
            continue
        v = c.value
        if isinstance(v, str):
            txt = "'%s'" % v.replace("'", "''")
        elif isinstance(v, bool):
            txt = "T" if v else "F"
        else:
            txt = repr(float(v)) if isinstance(v, float) else str(v)
        cards.append((c.keyword, txt, c.comment or ""))
    cards.append(("PLTSOLVD", "T", ("TTSkyAnnot " + solver_note).strip()))
    return cards


def write_fits(path, wcs, note=""):
    from astropy.io import fits
    with fits.open(path, memmap=False) as hdul:
        hdul.verify("silentfix")
        hdu = next(h for h in hdul if getattr(h, "data", None) is not None and h.data.ndim >= 2)
        for k in [k for k in list(hdu.header.keys()) if _WCS_KEY.match(k)]:
            del hdu.header[k]
        for name, txt, comment in wcs_cards(wcs, note):
            val = {"T": True, "F": False}.get(txt, None)
            if val is None:
                val = txt[1:-1].replace("''", "'") if txt.startswith("'") else float(txt) if "." in txt or "e" in txt.lower() else int(txt)
            hdu.header[name] = (val, comment)
        hdu.header.add_history("Astrometric solution (TAN-SIP) written by TTSkyAnnot")
        fd, tmp = tempfile.mkstemp(prefix=".skyannot_", suffix=".fits", dir=os.path.dirname(os.path.abspath(path)))
        os.close(fd)
        try:
            hdul.writeto(tmp, overwrite=True, output_verify="silentfix")
        except BaseException:
            os.unlink(tmp)
            raise
    os.replace(tmp, path)
    return path


def write_xisf(path, wcs, note=""):
    from .xisfedit import update_header
    info = update_header(path, set_keywords=wcs_cards(wcs, note),
                         remove_keywords=lambda n: bool(_WCS_KEY.match(n)),
                         remove_property=lambda i: "AstrometricSolution" in i)
    return path, info


def write_sidecar(path, wcs, width, height, note="", out_dir=""):
    from astropy.io import fits
    hdr = fits.Header()
    hdr["IMAGEW"] = (width, "image width in pixels")       # astrometry.net convention
    hdr["IMAGEH"] = (height, "image height in pixels")
    for name, txt, comment in wcs_cards(wcs, note):
        val = {"T": True, "F": False}.get(txt, None)
        if val is None:
            val = txt[1:-1] if txt.startswith("'") else float(txt) if "." in txt or "e" in txt.lower() else int(txt)
        hdr[name] = (val, comment)
    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(out_dir or os.path.dirname(os.path.abspath(path)), stem + ".wcs")
    fits.PrimaryHDU(header=hdr).writeto(out, overwrite=True)
    return out


def read_existing(img):
    """An astrometric solution already present for this image: WCS keywords in its own header, otherwise a
    sidecar '<name>.wcs'. Returns an astropy WCS or None."""
    from astropy.io import fits
    from astropy.wcs import WCS
    hdr = None
    kw = img.keywords or {}
    if str(kw.get("CTYPE1", "")).strip("'").strip().startswith("RA---"):
        hdr = fits.Header()
        for k, v in kw.items():
            if _WCS_KEY.match(k) or k in ("A_ORDER", "B_ORDER", "AP_ORDER", "BP_ORDER"):
                t = str(v).strip()
                if t.startswith("'"):
                    val = t.strip("'").strip()
                elif t in ("T", "F"):
                    val = t == "T"
                else:
                    try:
                        val = float(t) if any(c in t for c in ".eE") else int(t)
                    except ValueError:
                        val = t
                try:
                    hdr[k] = val
                except Exception:
                    pass
    else:
        side = os.path.splitext(img.path)[0] + ".wcs"
        if os.path.exists(side):
            try:
                hdr = fits.getheader(side)
            except OSError:
                hdr = None
    if hdr is None:
        return None
    try:
        w = WCS(hdr, naxis=2, relax=True)
        return w if w.has_celestial else None
    except Exception:
        return None


def write_back(img, wcs, note="", out_dir=""):
    """Returns (path written, description)."""
    if img.fmt == "fits":
        return write_fits(img.path, wcs, note), "FITS header updated"
    if img.fmt == "xisf":
        p, info = write_xisf(img.path, wcs, note)
        extra = (", stale PixInsight solution removed" if info["properties_removed"] else "")
        return p, "XISF header updated%s" % extra
    return write_sidecar(img.path, wcs, img.width, img.height, note, out_dir), "sidecar .wcs file"
