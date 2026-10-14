"""Image loading (FITS, XISF, TIFF, PNG, JPEG) and the hints we can read from their headers."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

FITS_EXT = (".fit", ".fits", ".fts")
XISF_EXT = (".xisf",)
TIFF_EXT = (".tif", ".tiff")
RASTER_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
SUPPORTED = FITS_EXT + XISF_EXT + TIFF_EXT + RASTER_EXT


@dataclass
class LoadedImage:
    path: str
    fmt: str                      # "fits" | "xisf" | "tiff" | "raster"
    data: np.ndarray              # float32, (h, w, c), values in [0, 1] as stored (may be linear)
    keywords: dict = field(default_factory=dict)   # FITS-style keywords, values as plain strings
    linear: bool = False          # looks like unstretched data (needs a display stretch)

    @property
    def width(self) -> int:
        return int(self.data.shape[1])

    @property
    def height(self) -> int:
        return int(self.data.shape[0])


def _to_unit(a: np.ndarray) -> np.ndarray:
    """Integer images to [0, 1]; float images are kept but clipped (some writers use 0..65535 floats)."""
    if np.issubdtype(a.dtype, np.integer):
        return a.astype(np.float32) / float(np.iinfo(a.dtype).max)
    a = a.astype(np.float32)
    mx = float(np.nanmax(a)) if a.size else 1.0
    if mx > 1.5:                                  # float data stored on an integer scale
        a = a / (65535.0 if mx <= 65535.0 else mx)
    return np.clip(np.nan_to_num(a, nan=0.0), 0.0, 1.0)


def _channels_last(a: np.ndarray) -> np.ndarray:
    if a.ndim == 2:
        return a[..., None]
    if a.ndim == 3 and a.shape[0] in (1, 3, 4) and a.shape[2] not in (1, 3, 4):
        return np.moveaxis(a, 0, -1)
    return a


def load(path: str) -> LoadedImage:
    path = os.fspath(path)
    ext = os.path.splitext(path)[1].lower()
    kw: dict = {}
    if ext in XISF_EXT:
        from .xisfio import XISF
        x = XISF(path)
        data = np.asarray(x.read_image(0))
        for k, v in (x.get_images_metadata()[0].get("FITSKeywords") or {}).items():
            if v:
                kw[k] = str(v[0].get("value", "")).strip()
        fmt = "xisf"
    elif ext in FITS_EXT:
        from astropy.io import fits
        with fits.open(path, memmap=False) as hdul:
            hdu = next((h for h in hdul if getattr(h, "data", None) is not None and h.data.ndim >= 2), None)
            if hdu is None:
                raise ValueError("no image data in FITS file")
            data = np.asarray(hdu.data)
            for k, v in hdu.header.items():
                if k and k not in ("COMMENT", "HISTORY", ""):
                    kw[k] = str(v).strip()
        data = _channels_last(data)
        fmt = "fits"
    elif ext in TIFF_EXT:
        import tifffile
        data = _channels_last(np.asarray(tifffile.imread(path)))
        fmt = "tiff"
    elif ext in RASTER_EXT:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            data = np.asarray(im.convert("RGB"))
        fmt = "raster"
    else:
        raise ValueError("unsupported file type: %s" % ext)
    data = _channels_last(np.asarray(data))
    if data.shape[2] > 3:
        data = data[..., :3]
    data = _to_unit(data)
    med = float(np.median(data[::8, ::8]))
    # stacked linear masters sit far below 0.03; a stretched picture with a dark background does not
    return LoadedImage(path=path, fmt=fmt, data=data, keywords=kw, linear=(fmt != "raster" and med < 0.03))


def _stf(x: np.ndarray, med: float, mad: float, target: float):
    """Screen transfer function: black point at median - 2.8 MAD, midtones balance putting the median at
    `target` (the same rule as PixInsight's automatic STF)."""
    c0 = max(0.0, med - 2.8 * mad)
    y = np.clip((x - c0) / max(1e-6, 1.0 - c0), 0.0, 1.0)
    m0 = (med - c0) / max(1e-6, 1.0 - c0)
    mb = m0 * (target - 1.0) / (2.0 * m0 * target - target - m0) if 0 < m0 < 1 else 0.5
    return ((mb - 1.0) * y / ((2.0 * mb - 1.0) * y - mb)).astype(np.float32)


def display_stretch(img: LoadedImage, mode: str = "auto", target_median: float = 0.20) -> np.ndarray:
    """Display-ready copy in [0, 1] (h, w, 3).
    mode: "auto" (linked STF for linear data, unchanged otherwise), "linked", "unlinked" (per channel,
    neutralises a colour cast in linear data), "none"."""
    a = img.data
    if a.shape[2] == 1:
        a = np.repeat(a, 3, axis=2)
    if mode == "none" or (mode == "auto" and not img.linear):
        return a
    s = a[::4, ::4].reshape(-1, 3)
    if mode == "unlinked":
        out = np.empty_like(a, dtype=np.float32)
        for c in range(3):
            med = float(np.median(s[:, c]))
            mad = float(np.median(np.abs(s[:, c] - med))) * 1.4826
            out[..., c] = _stf(a[..., c], med, mad, target_median)
        return out
    lum = s.mean(axis=1)
    med = float(np.median(lum))
    mad = float(np.median(np.abs(lum - med))) * 1.4826
    return _stf(a, med, mad, target_median)


# ───────────────────────────── header hints ─────────────────────────────
def _num(v):
    try:
        return float(str(v).strip().strip("'").strip())
    except (TypeError, ValueError):
        return None


def _sexa(v, hours: bool):
    """'05 34 31.9' / '05:34:31.9' / '+22 00 52' -> degrees."""
    if v is None:
        return None
    s = str(v).strip().strip("'").strip()
    f = _num(s)
    if f is not None:
        return f
    parts = s.replace(":", " ").replace("h", " ").replace("m", " ").replace("s", " ").replace("d", " ").split()
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if not nums:
        return None
    sign = -1.0 if s.startswith("-") else 1.0
    val = abs(nums[0]) + (nums[1] if len(nums) > 1 else 0) / 60.0 + (nums[2] if len(nums) > 2 else 0) / 3600.0
    return sign * val * (15.0 if hours else 1.0)


def hints(img: LoadedImage) -> dict:
    """What the header tells us: target name, approximate centre, pixel scale, observation date."""
    kw = img.keywords
    out = {}
    obj = (kw.get("OBJECT") or "").strip().strip("'").strip()
    if obj and obj.lower() not in ("unknown", "none", "object"):
        out["target"] = obj
    ra = _num(kw.get("RA")) if kw.get("RA") is not None else None
    dec = _num(kw.get("DEC")) if kw.get("DEC") is not None else None
    if ra is None or dec is None:
        ra = _sexa(kw.get("OBJCTRA"), hours=True)
        dec = _sexa(kw.get("OBJCTDEC"), hours=False)
    if ra is not None and dec is not None and 0 <= ra < 360 and -90 <= dec <= 90:
        out["ra"], out["dec"] = ra, dec
    focal = _num(kw.get("FOCALLEN"))
    pix = _num(kw.get("XPIXSZ")) or _num(kw.get("PIXSIZE1"))
    if focal and pix and focal > 1:
        out["pixscale"] = 206.264806 * pix / focal       # XPIXSZ already includes binning
    date = (kw.get("DATE-OBS") or kw.get("DATE-AVG") or "").strip().strip("'")
    if date:
        out["date"] = date
    return out
