"""Colour frames: telling a colour image from a monochrome one, and turning a Bayer mosaic into RGB.

A one-shot colour camera writes a single-channel frame where every pixel sits behind a red, green or blue
filter in a repeating 2x2 pattern (RGGB and friends). Displaying that as it arrives shows a grey chequerboard,
which is what this module exists to avoid: `needs_debayer` says whether a frame is still in that state, and
`debayer` interpolates it into three channels.

Two things decide it, and both are needed:

* the **shape of the data** — three channels already means someone has done the work (PixInsight writes an
  RGB image plus a leftover BAYERPAT keyword and a PCL:CFASourceFilePath pointing at the frame it came from);
* the **header** — BAYERPAT / BAYERPATTERN / PCL:CFASourcePattern name the pattern. A monochrome frame from
  the same camera can also carry BAYERPAT, so a file that records where its CFA came from is treated as
  already done rather than as a mosaic.
"""
from __future__ import annotations

import numpy as np

# how the three keys that name a pattern are spelled, on the way in
_KEYS = ("BAYERPAT", "BAYERPATTERN", "CFAPATTERN", "XBAYERPAT", "PCL:CFASOURCEPATTERN")
# the file a CFA was debayered from: PixInsight keeps this on the result, and it means "already done"
_SOURCE_KEYS = ("PCL:CFASOURCEFILEPATH", "PCL:CFASOURCEINTERPOLATION")

# (row parity, column parity) -> channel, for a pattern given as the pixels at (0,0), (0,1), (1,0), (1,1)
_CHANNEL = {"R": 0, "G": 1, "B": 2}

# Every arrangement a Bayer sensor can actually have: two greens per cell, one on each diagonal. The other
# twelve permutations of R, G, G, B describe a sensor that does not exist, so they are not offered — a wrong
# choice here swaps the colours, which is worse than leaving the frame alone.
PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")

# human-readable names, for the chooser
PATTERN_LABELS = {
    "RGGB": "RGGB（红 绿 / 绿 蓝）",
    "BGGR": "BGGR（蓝 绿 / 绿 红）",
    "GRBG": "GRBG（绿 红 / 蓝 绿）",
    "GBRG": "GBRG（绿 蓝 / 红 绿）",
}


def _cells(pattern: str):
    """The 2x2 cell as [[letter, letter], [letter, letter]], for drawing it."""
    return [[pattern[0], pattern[1]], [pattern[2], pattern[3]]]


def _get(keywords: dict, name: str):
    """Header values are looked up by their upper-case name, whatever case the caller used."""
    if name in keywords:
        return keywords[name]
    up = name.upper()
    for k, v in keywords.items():
        if str(k).upper() == up:
            return v
    return None


def normalise(raw) -> str:
    """A pattern given as text ("rggb", " RGGB ") as one of PATTERNS, or "" when it is not a real one.

    Only the four arrangements are accepted, and nothing else may be in the string: "RGGBX" is a typo or a
    different kind of pattern, and quietly reading it as RGGB would hide that.
    """
    text = str(raw or "").strip().upper()
    return text if text in PATTERNS else ""


def pattern_of(keywords: dict) -> str:
    """The Bayer pattern a frame's header names, as four letters of R/G/B, or "" when it names none.

    The known spellings are read in order; GPATTERN and friends are ignored because no colour camera writes
    them and guessing would be worse than doing nothing.
    """
    for key in _KEYS:
        found = normalise(_get(keywords, key))
        if found:
            return found
    return ""


def done_already(keywords: dict) -> bool:
    """True when the header says this frame was produced from a CFA frame rather than being one."""
    return any(str(_get(keywords, k) or "").strip() for k in _SOURCE_KEYS)


# kept for callers that only have the two questions to ask
def needs_debayer(data: np.ndarray, keywords: dict) -> bool:
    """Whether this frame is still a Bayer mosaic, so should be interpolated before it is shown.

    Three-channel data is never a mosaic. Single-channel data is one when the header names a pattern and does
    not also record a file it was debayered from — that combination is a PixInsight RGB result or a frame
    already processed by hand, and interpolating it again would smear the colour it already has.
    """
    a = np.asarray(data)
    if a.ndim == 3 and a.shape[2] >= 3:
        return False
    if a.ndim == 3 and a.shape[2] != 1:
        return False
    if not pattern_of(keywords):
        return False
    return not done_already(keywords)


def _cell(pattern: str) -> np.ndarray:
    """The 2x2 cell as channel numbers, [[at 0,0, at 0,1], [at 1,0, at 1,1]]."""
    return np.array([[_CHANNEL[pattern[0]], _CHANNEL[pattern[1]]],
                     [_CHANNEL[pattern[2]], _CHANNEL[pattern[3]]]])


def _sample(grid: np.ndarray, dy: int, dx: int, shape) -> np.ndarray:
    """grid[i + dy, j + dx] over a target of `shape`, the edge repeated where that lands outside.

    `grid` holds one colour of one cell parity and one value per 2x2 cell, so a neighbour one pixel away in the
    full frame is at cell offset 0 or +-1 — which is all this ever needs. Pass the already padded grid to
    `_window` when the same one is read more than once: padding is a copy, and the debayer reads each quadrant
    up to twelve times.
    """
    p = np.pad(grid, 1, mode="edge")
    return p[1 + dy:1 + dy + shape[0], 1 + dx:1 + dx + shape[1]]


def _window(padded: np.ndarray, dy: int, dx: int, shape) -> np.ndarray:
    """The same window as `_sample`, out of a grid that was padded once already."""
    return padded[1 + dy:1 + dy + shape[0], 1 + dx:1 + dx + shape[1]]


def debayer(mosaic: np.ndarray, pattern: str) -> np.ndarray:
    """A Bayer mosaic (h, w) or (h, w, 1) as RGB (h, w, 3), bilinear.

    Bilinear rather than an edge-aware method on purpose: it cannot invent a colour that is not in the data,
    it is stable on the noisy short exposures this program is pointed at, and it is what the CFA frames here
    need before being looked at. It is not a final processing step — PixInsight remains the place for that.

    The frame is split into its four cell quadrants first, so each quadrant's missing colours are filled from
    whole-array arithmetic on a quarter-size grid rather than from per-pixel masks over 26 megapixels. A pixel
    keeps its own measured channel; a missing one is the mean of the same colour two pixels away along its row
    or column, or — at the corner diagonally opposite its own sites (red against blue) — of the four diagonal
    ones. That is what makes the channel means, and so the colour balance, exactly what the sensor recorded.
    """
    a = np.asarray(mosaic)
    if a.ndim == 3:
        a = a[..., 0]
    if a.dtype != np.float32:
        a = a.astype(np.float32)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return np.repeat(a[..., None], 3, axis=2)
    h, w = a.shape
    cell = _cell(pattern)
    quad = {(r, c): a[r::2, c::2] for r in (0, 1) for c in (0, 1)}
    # each quadrant is read up to twelve times: pad it once here, not in every _sample call
    padded = {key: np.pad(g, 1, mode="edge") for key, g in quad.items()}
    out = np.empty((h, w, 3), np.float32)
    for tr in (0, 1):
        for tc in (0, 1):
            shape = quad[(tr, tc)].shape
            for ch in (0, 1, 2):
                dst = out[tr::2, tc::2, ch]
                if cell[tr, tc] == ch:
                    dst[...] = quad[(tr, tc)]
                elif cell[tr, 1 - tc] == ch:
                    # the left and right neighbours: same row, one cell over
                    src = padded[(tr, 1 - tc)]
                    np.add(_window(src, 0, tc - 1, shape), _window(src, 0, tc, shape), out=dst)
                    dst *= 0.5
                elif cell[1 - tr, tc] == ch:
                    # the neighbours above and below: same column, one cell over
                    src = padded[(1 - tr, tc)]
                    np.add(_window(src, tr - 1, 0, shape), _window(src, tr, 0, shape), out=dst)
                    dst *= 0.5
                else:
                    # the opposite corner of the cell: the four diagonals are all this colour
                    src = padded[(1 - tr, 1 - tc)]
                    np.add(_window(src, tr - 1, tc - 1, shape), _window(src, tr - 1, tc, shape), out=dst)
                    dst += _window(src, tr, tc - 1, shape)
                    dst += _window(src, tr, tc, shape)
                    dst *= 0.25
    # `out` is ours: clip it in place rather than copying 300 MB just to bound the values
    return np.clip(out, 0.0, 1.0, out=out)


def describe(data: np.ndarray, keywords: dict, debayered: str = "") -> str:
    """A short phrase for the status bar: what kind of colour this frame is.

    `debayered` is the pattern a caller already interpolated; without it a mosaic that has just been turned
    into RGB would be described as a mosaic again, since the header still names one.
    """
    a = np.asarray(data)
    if debayered:
        return "CFA %s → debayered" % debayered
    if needs_debayer(a, keywords):
        return "CFA %s (raw)" % pattern_of(keywords)
    if a.ndim == 3 and a.shape[2] >= 3:
        return "RGB"
    return "mono"
