"""Image loading (FITS, XISF, TIFF, PNG, JPEG) and the hints we can read from their headers."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import numpy as np

from . import debayer, histogram as hist_mod

# ASIFitsView's stretch levels on the 16-bit scale, read from its own log for a one-shot colour frame:
#   "autoStretch minValue: 1502, autoStretch midValue: 2317"
AWB_BLACK = 1502.0
AWB_MID = 2317.0
# A sky pedestal larger than this is not a white-balance residue but something else the mode locked onto
# (a nebula filling the frame), and subtracting it would invent a colour; the gains are used alone then.
AWB_MAX_PEDESTAL = 400.0

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
    debayered: str = ""           # the Bayer pattern that was interpolated on load, "" when none was
    bayered: bool = False         # single-channel data that still carries a Bayer pattern

    @property
    def can_choose_bayer(self) -> bool:
        """Whether choosing a Bayer arrangement by hand could do anything for this frame.

        Three-channel data has already been through this and a file that records where its CFA came from has
        too, so neither is ever reinterpreted. Single-channel data is where the choice belongs: either the
        header named a pattern (and the choice can correct it) or it named none, which is exactly the case
        this option exists for — a colour frame whose header is silent.
        """
        if self.data.ndim != 3 or self.data.shape[2] != 1:
            return False
        return not self.debayered and not debayer.done_already(self.keywords)

    @property
    def is_bayer_mosaic(self) -> bool:
        """Single channel and a Bayer pattern is known: a one-shot colour frame nobody has debayered yet."""
        return self.can_choose_bayer and bool(debayer.pattern_of(self.keywords))

    @property
    def is_color(self) -> bool:
        """Three channels that are not simply the same data repeated: a real colour frame."""
        if self.data.ndim != 3 or self.data.shape[2] < 3:
            return False
        c = self.data[::16, ::16]
        return bool(np.max(np.abs(c - c.mean(axis=2, keepdims=True))) > 1e-4)

    @property
    def width(self) -> int:
        return int(self.data.shape[1])

    @property
    def height(self) -> int:
        return int(self.data.shape[0])


def _to_unit(a: np.ndarray) -> np.ndarray:
    """Integer images to [0, 1]; float images are kept but clipped (some writers use 0..65535 floats).

    Float data is cleaned in place when the caller's array is writable: at load time nothing else holds the
    frame yet, and each of the three steps (clip, NaN sweep, scale) would otherwise be another 300 MB copy.
    """
    if np.issubdtype(a.dtype, np.integer):
        return np.multiply(a, np.float32(1.0 / float(np.iinfo(a.dtype).max)), dtype=np.float32)
    if a.dtype != np.float32 or not a.flags.writeable:
        a = a.astype(np.float32)
    mx = float(np.nanmax(a)) if a.size else 1.0
    if mx > 1.5:                                  # float data stored on an integer scale
        a /= (65535.0 if mx <= 65535.0 else mx)
    np.nan_to_num(a, copy=False, nan=0.0)
    np.clip(a, 0.0, 1.0, out=a)
    return a


def _channels_last(a: np.ndarray) -> np.ndarray:
    if a.ndim == 2:
        return a[..., None]
    if a.ndim == 3 and a.shape[0] in (1, 3, 4) and a.shape[2] not in (1, 3, 4):
        return np.moveaxis(a, 0, -1)
    return a


def load(path: str, want_debayer: bool = True, pattern: str = "") -> LoadedImage:
    """Read a frame.

    want_debayer=False leaves a Bayer mosaic as it is, for looking at the raw sensor data.
    pattern names the Bayer arrangement to use when the header does not (or when the user says the header is
    wrong); it is only ever applied to single-channel data, so an RGB frame is never reinterpreted.
    """
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
    # A one-shot colour frame arrives as a single-channel Bayer mosaic. Interpolating it here means the
    # viewer, the star detector and the annotated preview all see the same colour picture; the file on disk
    # is never touched.
    debayered = ""
    # the header first, then whatever the user chose; a chosen pattern also fills in for a header that names
    # none, which is the case this option exists for
    from_header = debayer.pattern_of(kw)
    chosen = debayer.normalise(pattern)
    single_channel = data.ndim == 3 and data.shape[2] == 1
    is_mosaic = single_channel and bool(from_header or chosen) and not debayer.done_already(kw)
    if is_mosaic and want_debayer:
        pattern_used = from_header or chosen
        # debayer() works in the 0..1 range the rest of the program uses, so the mosaic is scaled first:
        # handing it raw 16-bit values would have it clip everything above 1 and blow the frame out. Its own
        # result is already 0..1 and finite, so the second pass over 300 MB is not needed as well.
        data = debayer.debayer(_to_unit(data), pattern_used)
        debayered = pattern_used
    else:
        data = _to_unit(data)
    sample = data[::8, ::8]
    lum = sample.mean(axis=2) if sample.ndim == 3 else sample
    med = float(np.median(lum))
    mad = float(np.median(np.abs(lum - med))) * 1.4826
    near_black = float(np.count_nonzero(lum < 0.002)) / max(1.0, float(lum.size))
    # A linear frame (a sub or a stack) keeps its sky in a narrow peak well above zero. A picture that has
    # already been stretched was lifted out of the shadows, so its sky peak is broad relative to its own level
    # and nothing in it is truly black. The level alone cannot tell them apart: a long sub at high gain sits at
    # 0.05 and is still linear, while a finished master can have its sky as low as 0.02.
    sharp = mad <= 0.10 * max(med, 1e-6)
    linear = fmt != "raster" and near_black < 0.002 and (med < 0.03 or sharp)
    return LoadedImage(path=path, fmt=fmt, data=data, keywords=kw, linear=linear,
                       debayered=debayered, bayered=is_mosaic)


def _stf_levels(med: float, mad: float, target: float) -> hist_mod.Levels:
    """Screen transfer function as levels: black point at median - 2.8 MAD, midtone balance putting the
    median at `target` (the same rule as PixInsight's automatic STF)."""
    c0 = max(0.0, med - 2.8 * mad)
    m0 = (med - c0) / max(1e-6, 1.0 - c0)
    mb = m0 * (target - 1.0) / (2.0 * m0 * target - target - m0) if 0 < m0 < 1 else 0.5
    return hist_mod.Levels(black=c0, mid=float(mb), white=1.0)


def auto_mode(img: LoadedImage, max_cast: float = 0.05) -> str:
    """What "auto" means for this image: "none" for a picture that is already finished (PNG/JPEG) or has
    been stretched; for linear data "linked" unless a linked stretch would paint the sky a colour, and then
    "pedestal" — take out each channel's sky level, which neutralises the sky without rescaling the channels
    and so keeps the object's own colour.

    An uncalibrated one-shot-colour master has very different channel levels: a linked stretch puts its sky
    at R 0 / G 0.60 / B 0 (M31, 2600MC), a bright green. Colour-calibrated data has a neutral sky and must
    stay linked to keep its colours (BN+CC 0.198/0.198/0.209, SPCC 0.202/0.202/0.202). The sky level is taken
    from the darkest quarter of 64 cells, so a galaxy or nebula filling the frame is not mistaken for sky."""
    if plain_raster(img) or not img.linear:
        return "none"
    a = img.data
    if a.shape[2] < 3:
        return "linked"
    h, w = a.shape[:2]
    cells = []
    for gy in range(8):
        for gx in range(8):
            blk = a[gy * h // 8:(gy + 1) * h // 8:8, gx * w // 8:(gx + 1) * w // 8:8, :3].reshape(-1, 3)
            cells.append(np.median(blk, axis=0))
    cells = np.array(cells)
    sky = np.median(cells[np.argsort(cells.mean(axis=1))[:16]], axis=0)
    s = a[::4, ::4, :3].reshape(-1, 3)
    lum = s.mean(axis=1)
    med = float(np.median(lum))
    mad = float(np.median(np.abs(lum - med))) * 1.4826
    out = _stf_levels(med, mad, 0.20).apply(sky.astype(np.float32))
    if float(out.max() - out.min()) > max_cast:
        # an uncalibrated colour frame: white balance then one stretch, the way ASIFitsView shows it
        return "awb"
    return "linked"


def plain_raster(img: LoadedImage) -> bool:
    """A picture that is already a finished image (PNG, JPEG, ...): it has been stretched by whatever wrote
    it, so it is shown exactly as stored and no stretch mode applies to it."""
    return img.fmt == "raster"


def needs_sidecar(path: str) -> bool:
    """Formats with no header of their own (JPEG, PNG, TIFF) keep their solution in a sidecar .wcs file
    instead of having anything written into them."""
    return os.path.splitext(path)[1].lower() in RASTER_EXT + TIFF_EXT


def pixel_size(path: str):
    """(width, height) of the picture in `path`, read from its header, or None if it cannot be read.

    Header only: no pixels are decoded, so this stays a few milliseconds per file even for a 26-megapixel FITS.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in FITS_EXT:
            from astropy.io import fits
            with fits.open(path, memmap=True, lazy_load_hdus=True) as hdul:
                for hdu in hdul:
                    if int(hdu.header.get("NAXIS", 0) or 0) >= 2:
                        return int(hdu.header["NAXIS1"]), int(hdu.header["NAXIS2"])
            return None
        if ext in XISF_EXT:
            from .xisfio import XISF
            geom = (XISF(path).get_images_metadata()[0].get("geometry") or [])
            return (int(geom[0]), int(geom[1])) if len(geom) >= 2 else None
        from PIL import Image
        with Image.open(path) as im:                 # the header carries the size; the pixels stay unread
            return int(im.width), int(im.height)
    except Exception:
        return None


_PREVIEW_NAME = re.compile(r"_thn(\.[^.]+)?$", re.I)


def preview_name(name: str) -> bool:
    """True when the file name marks it as the preview a capture program wrote beside a frame.

    The ASIAIR names its thumbnails `..._thn.jpg`; the name alone is enough, so a listing can leave them out
    before a single header is read. This is the fast path — `drop_previews` also catches previews that are not
    named this way, by comparing picture sizes.
    """
    return bool(_PREVIEW_NAME.search(name))


def drop_previews(folder: str, names, fraction: float = 0.25):
    """`names` without the low-resolution previews of the bigger pictures beside them.

    A one-shot camera's folder holds every frame next to the preview its capture software wrote — the ASIAIR
    puts a 244 x 160 `*_thn.jpg` beside each 6248 x 4176 FITS. Those are recognised by their name at once; the
    rest are compared by picture size: a picture whose long side is less than a quarter of the folder's largest
    is a preview too. A file whose size cannot be read is kept: a missing frame is worse than a stray preview,
    and a folder of nothing but small pictures has no big one to be a preview of, so nothing is dropped there.
    """
    names = [n for n in names if not preview_name(n)]
    sizes = {}
    for n in names:
        sizes[n] = pixel_size(os.path.join(folder, n))
    known = [max(s) for s in sizes.values() if s]
    if len(known) < 2:
        return list(names)
    top = max(known)
    return [n for n in names if not sizes.get(n) or max(sizes[n]) >= fraction * top]


def _offsets(a: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Each channel's sky pedestal, estimated as the histogram mode of that channel.

    A one-shot colour frame that has not been background-calibrated carries a different black level per
    channel, because the three colour filters sit under a different amount of sky glow. Until that pedestal
    is taken out, the sky is a colour (usually green) and the object's own colour is buried under it.
    """
    return np.array([hist_mod.sky_mode(a[::4, ::4, c]) for c in range(3)], np.float32)


@dataclass
class Balance:
    """A white balance for a colour frame: per-channel gains, then per-channel sky pedestals.

    The gains are ASIFitsView's rule — divide each channel by its own average, times green's average — and
    they leave the three *averages* equal. The pedestals then take whatever colour the sky itself still has
    out of the picture; because that is a subtraction rather than another rescale, the object keeps the
    colour its own photons gave it.
    """
    gains: np.ndarray = None
    offsets: np.ndarray = None

    def __post_init__(self):
        if self.gains is None:
            self.gains = np.ones(3, np.float32)
        if self.offsets is None:
            self.offsets = np.zeros(3, np.float32)

    @property
    def is_identity(self) -> bool:
        return bool(np.allclose(self.gains, 1.0, atol=1e-6) and np.allclose(self.offsets, 0.0, atol=1e-9))

    def apply(self, a: np.ndarray) -> np.ndarray:
        """The balanced copy. One array is written twice rather than two allocated: a 26-megapixel frame is
        300 MB a copy. Clipped to the 0..1 the rest of the program uses, which is also what the statistics
        beside the histogram report — a gain of 1.33 on a saturated star must read 65535, not 87000."""
        out = np.asarray(a, np.float32) * self.gains.astype(np.float32)
        out -= self.offsets.astype(np.float32)
        np.clip(out, 0.0, 1.0, out=out)
        return out

    def as_dict(self) -> dict:
        return {"gains": [round(float(g), 6) for g in self.gains],
                "offsets": [round(float(o), 8) for o in self.offsets]}


def awb_balance(a: np.ndarray) -> Balance:
    """ASIFitsView's white balance for a colour frame, as a `Balance`.

    Its own statistics panel gives the rule exactly. With the balance off it reports channel averages of
    1259 / 1669 / 1236 for such a frame and with it on 1668 / 1669 / 1669 — so it does not suppress green,
    it *raises red and blue to green's level*: R x1.325, B x1.349, green untouched. After that the sky is
    still a percent or two apart between channels, and a curve as steep as a linear frame needs turns that
    into a visible cast, so each channel's own sky mode is subtracted as well.
    """
    a = np.asarray(a, np.float32)
    if a.ndim == 2:
        a = a[..., None]
    if a.shape[2] < 3:
        return Balance()
    means = hist_mod.channel_means(a, stride=4)[:3]
    if not np.all(np.isfinite(means)) or float(means[1]) <= 0:
        return Balance()
    gains = (means[1] / np.maximum(means, 1e-9)).astype(np.float32)
    sample = a[::4, ::4, :3]
    peaks = np.array([hist_mod.sky_mode(sample[..., c] * gains[c]) for c in range(3)], np.float32)
    offsets = (peaks - float(peaks.min())).astype(np.float32)
    # A calibrated frame can have a mode that is not the sky at all (a nebula filling the field). A pedestal
    # that big would be a colour of its own invention, and the gains alone are then the safer answer.
    if float(np.max(np.abs(offsets))) > AWB_MAX_PEDESTAL / 65535.0:
        offsets = np.zeros(3, np.float32)
    return Balance(gains=gains, offsets=offsets)


def awb_state(a: np.ndarray, target_median: float = 0.20):
    """The white balance and the levels that display a one-shot colour frame: (Balance, Levels).

    The levels come from the *balanced* frame, so the black and white markers the histogram panel draws are
    the ones the picture on screen was stretched with. Its minima are taken from the raw frame scaled by the
    gains — no 300 MB copy is made just to read four numbers off it.
    """
    a = np.asarray(a, np.float32)
    if a.ndim == 2:
        a = a[..., None]
    bal = awb_balance(a)
    if a.shape[2] < 3:
        return bal, hist_mod.auto_levels(a, target_median)
    # one pass for the three minima: three strided full-frame scans cost three times as much, and a single
    # reduction over both axes is slower still on a three-channel array than two ray reductions
    raw_mins = a.min(axis=0).min(axis=0)
    mins = [float(raw_mins[c]) * float(bal.gains[c]) - float(bal.offsets[c]) for c in range(3)]
    sample = bal.apply(a[::4, ::4])
    levels = hist_mod.auto_levels(sample, target_median, minima=mins)
    return bal, levels


def _sky_mode(col: np.ndarray) -> float:
    """The value a channel's sky has most often (kept as a name for callers of the old module)."""
    return hist_mod.sky_mode(col[::4, ::4] if col.ndim == 2 else col)


def _mtf(x: np.ndarray, m: float) -> np.ndarray:
    """PixInsight's midtone transfer function (kept as a name for callers of the old module)."""
    return hist_mod.mtf(x, m)


def _mtf_for(y_value: float, target: float) -> float:
    """The midtone balance that maps `y_value` to `target` (kept as a name for the old module)."""
    y = float(min(max(y_value, 1e-6), 1.0 - 1e-6))
    t = float(min(max(target, 1e-6), 1.0 - 1e-6))
    return (t * (y - 1.0)) / (y * (2.0 * t - 1.0) - t)


def _median_mad(s: np.ndarray):
    """Median and scaled median absolute deviation of a sample."""
    med = float(np.median(s))
    mad = float(np.median(np.abs(s - med))) * 1.4826
    return med, mad


def display_state(img: LoadedImage, mode: str = "auto", target_median: float = 0.20):
    """The display-ready picture, the balance and the levels it was made with: (array, Balance, Levels).

    `balance` and `levels` are None for the one mode that is not a single curve over one balanced frame (the
    per-channel "unlinked" stretch); every other mode can be handed to the histogram panel, so that its
    markers describe the picture actually on screen.

    mode: "auto" (see auto_mode), "awb" (white balance, then one stretch), "linked" (one stretch for all
    channels), "unlinked" (per channel), "pedestal" (take out each channel's sky level, then one stretch),
    "none". PNG/JPEG are never stretched, whatever the mode says: see plain_raster.

    The viewer does not call this: it wants 8-bit pixels and uses display_u8, which reaches the same picture
    through a 16-bit table instead of a dozen full-frame float passes. This is still the definition of what
    each mode means, and what the annotated preview and the histogram panel are built from.
    """
    changed, bal, lv = _plan(img, mode, target_median)
    a = img.data
    if not changed:
        return (np.repeat(a, 3, axis=2) if a.shape[2] == 1 else a), bal, lv
    if a.shape[2] == 1:
        a = np.repeat(a, 3, axis=2)
    if isinstance(lv, list):
        out = np.empty_like(a, np.float32)
        for c in range(3):
            out[..., c] = lv[c].apply(a[..., c])[..., 0]
        return out, None, None
    return lv.apply(bal.apply(a)), bal, lv


def _plan(img: LoadedImage, mode: str = "auto", target_median: float = 0.20):
    """What a display mode comes to: (pixels changed?, balance, levels).

    `levels` is one Levels for every mode but "unlinked", where it is a list of three (one per channel). Reading
    the numbers does not touch the frame, so both the float picture and the 8-bit one can be built from this.
    """
    a = img.data
    if plain_raster(img):
        return False, Balance(), hist_mod.Levels()
    if not img.linear:
        # A finished picture has already been stretched by whatever made it (PixInsight, Photoshop, a JPEG):
        # running another curve over it only blows out its highlights. It is shown exactly as stored, whatever
        # the stretch setting says.
        return False, Balance(), hist_mod.Levels()
    if mode == "auto":
        mode = auto_mode(img)
    if mode == "none":
        return False, Balance(), hist_mod.Levels()
    if a.shape[2] == 1:
        a = np.repeat(a, 3, axis=2)
    if mode == "awb":
        bal, lv = awb_state(a, target_median)
        return True, bal, lv
    s = a[::4, ::4]
    if mode == "pedestal":
        bal = Balance(offsets=_offsets(a, s.reshape(-1, 3)))
        sample = bal.apply(s)                      # the balance is pointwise: the sample is enough to measure it
        return True, bal, _stf_levels(*_median_mad(sample.reshape(-1, 3)), target_median)
    if mode == "unlinked":
        return True, Balance(), [_stf_levels(*_median_mad(s[..., c].reshape(-1)), target_median) for c in range(3)]
    return True, Balance(), _stf_levels(*_median_mad(s.reshape(-1, 3)), target_median)


def _channel_lut(balance: Balance, levels, c: int, n: int = 65536) -> np.ndarray:
    """The 0..255 the display shows for channel c's 0..1 input, as a table.

    Everything the display chain does to one channel — its white-balance gain and pedestal, then the black,
    white and midtone of the stretch — is a function of that channel's value alone, so it can be tabulated once
    and then applied to 26 megapixels with a gather instead of a dozen passes over 300 MB.
    """
    x = np.linspace(0.0, 1.0, n, dtype=np.float32)
    x *= float(balance.gains[c])
    x -= float(balance.offsets[c])
    span = max(levels.white - levels.black, 1e-9)
    x -= float(levels.black)
    x /= span
    np.clip(x, 0.0, 1.0, out=x)
    x = hist_mod._mtf_unit(x, levels.mid)
    x *= 255.0
    x += 0.5
    return np.clip(x, 0.0, 255.0).astype(np.uint8)


def apply_u8(a: np.ndarray, balance: Balance, levels) -> np.ndarray:
    """The 8-bit RGB for `a` through a balance and a Levels (or a list of three), as the viewer paints it.

    Quantising to 16 bits before the table loses nothing that survives the 8-bit output: the table itself has
    65536 entries between black and white, which is the resolution ASIFitsView works at as well.
    """
    a = np.asarray(a, np.float32)
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
    elif a.shape[2] == 1:
        a = np.repeat(a, 3, axis=2)
    luts = [_channel_lut(balance, levels[c] if isinstance(levels, list) else levels, c) for c in range(3)]
    q = np.clip(a * 65535.0 + 0.5, 0.0, 65535.0).astype(np.uint16)
    rgb = np.empty(q.shape[:2] + (3,), np.uint8)
    for c in range(3):
        rgb[..., c] = luts[c][q[..., c]]
    return rgb


def display_u8(img: LoadedImage, mode: str = "auto", target_median: float = 0.20):
    """The 8-bit picture the viewer paints, plus the balance and levels it came from.

    Same picture as `display_state`, built for the screen: the float array that path returns is 300 MB of
    intermediate that the viewer only ever converts to 8 bits, and each of its steps is a full-frame pass.
    """
    changed, bal, lv = _plan(img, mode, target_median)
    a = img.data
    if not changed:
        b = np.repeat(a, 3, axis=2) if a.shape[2] == 1 else a
        # a channel-first frame (XISF stores them that way) stays an F-contiguous view through the scaling,
        # and Qt cannot read a picture out of an array whose rows are not contiguous
        b = np.ascontiguousarray(np.clip(b * 255.0 + 0.5, 0.0, 255.0).astype(np.uint8))
        return b, bal, lv
    rgb = apply_u8(a, bal, lv)
    return rgb, (None if isinstance(lv, list) else bal), (None if isinstance(lv, list) else lv)


def display_stretch(img: LoadedImage, mode: str = "auto", target_median: float = 0.20) -> np.ndarray:
    """Display-ready copy in [0, 1] (h, w, 3); see display_state for what each mode does."""
    return display_state(img, mode, target_median)[0]


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


def header_keywords(path: str) -> dict:
    """A FITS file's header as the flat {keyword: text} map the viewer and the solvers read.

    Only the header is read, so this stays cheap even for a 26-megapixel frame.
    """
    from astropy.io import fits
    out = {}
    try:
        with fits.open(path, memmap=True, lazy_load_hdus=True) as hdul:
            # the header alone decides which HDU holds the picture: touching .data here would make astropy
            # decode the pixels (and refuse to, on a memmapped frame with BZERO set)
            hdu = next((h for h in hdul if int(h.header.get("NAXIS", 0) or 0) >= 2), None)
            if hdu is None:
                return out
            for k, v in hdu.header.items():
                if k and k not in ("COMMENT", "HISTORY", ""):
                    out[k] = str(v).strip()
    except Exception:
        return {}
    return out


def _sexa_ra(deg: float) -> str:
    h = (float(deg) / 15.0) % 24.0
    hh = int(h)
    mm = int((h - hh) * 60.0)
    ss = ((h - hh) * 60.0 - mm) * 60.0
    return "%02d %02d %05.2f" % (hh, mm, ss)


def _sexa_dec(deg: float) -> str:
    sign = "-" if float(deg) < 0 else "+"
    d = abs(float(deg))
    dd = int(d)
    mm = int((d - dd) * 60.0)
    ss = ((d - dd) * 60.0 - mm) * 60.0
    return "%s%02d %02d %04.1f" % (sign, dd, mm, ss)


def fix_header(path: str, focal_mm=None, pix_um=None, ra=None, dec=None) -> list:
    """Write corrected optics and pointing into a FITS file's header, and say which keywords were written.

    A frame can arrive with a focal length that belongs to another telescope — and with it a pixel scale that
    makes every solver search the wrong field size — and a wide-field rig may record no pointing at all. Both
    are what the solve runs on, so correcting them here is what makes the frame solvable afterwards.

    The file is rewritten through a temporary copy and swapped in, so an interrupted write cannot leave a
    half-written FITS in the folder.
    """
    from astropy.io import fits
    written = []
    with fits.open(path, memmap=False, lazy_load_hdus=False) as hdul:
        hdu = next((h for h in hdul if getattr(h, "data", None) is not None and h.data.ndim >= 2), None)
        if hdu is None:
            raise ValueError("no image data in FITS file")
        hd = hdu.header
        if focal_mm:
            hd["FOCALLEN"] = (float(focal_mm), "focal length, mm (corrected)")
            written.append("FOCALLEN")
        if pix_um:
            for key in ("XPIXSZ", "YPIXSZ"):
                hd[key] = (float(pix_um), "pixel size, um (corrected)")
            written += ["XPIXSZ", "YPIXSZ"]
        if ra is not None and dec is not None:
            hd["RA"] = (float(ra), "RA of frame centre, degrees (corrected)")
            hd["DEC"] = (float(dec), "Dec of frame centre, degrees (corrected)")
            hd["OBJCTRA"] = (_sexa_ra(ra), "RA of frame centre (corrected)")
            hd["OBJCTDEC"] = (_sexa_dec(dec), "Dec of frame centre (corrected)")
            if "CRVAL1" in hd and "CRVAL2" in hd:      # keep an existing WCS consistent with the new centre
                hd["CRVAL1"] = float(ra)
                hd["CRVAL2"] = float(dec)
            written += ["RA", "DEC", "OBJCTRA", "OBJCTDEC"]
        folder = os.path.dirname(os.path.abspath(path))
        tmp = os.path.join(folder, ".ttskyannot-header-%d.fits" % os.getpid())
        hdul.writeto(tmp, overwrite=True)
    # the file is closed here: Windows refuses to replace a file another handle still has open
    try:
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return written


def fix_header_batch(paths, focal_mm=None, pix_um=None, ra=None, dec=None,
                     progress=None, should_stop=None):
    """Correct the header of every file in `paths`; returns (done, failures).

    One unreadable or locked file must not cost the rest of the folder, so a failure is collected rather than
    raised. `progress(index, total, path)` is called after each file and `should_stop()` is asked before each
    one, which is what lets a long sequence be interrupted from the window.
    """
    paths = list(paths)
    done, failed = [], []
    for i, p in enumerate(paths):
        if should_stop is not None and should_stop():
            break
        try:
            fix_header(p, focal_mm=focal_mm, pix_um=pix_um, ra=ra, dec=dec)
            done.append(p)
        except Exception as exc:                       # a locked file, a broken FITS, a read-only folder
            failed.append((p, str(exc)))
        if progress is not None:
            progress(i + 1, len(paths), p)
    return done, failed
