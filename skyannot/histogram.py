"""Histogram and the levels that stretch a picture for display.

Three parts, kept apart so each can be tested on its own:

* `histogram` — the counts a plot draws, per channel, over whatever range the plot is showing;
* `stats` — the numbers ASIFitsView puts beside the plot (min, max, mean, standard deviation);
* `Levels` — black point, midtone and white point, and the transform they define.

The transform is PixInsight's: normalise black..white to 0..1, then a midtone transfer function pushes the
midtones towards black or white. That is the same construction ASIFitsView's histogram panel exposes with two
draggable bounds plus a midtone, and it is the reason a picture can be stretched without destroying the colour
relationship between channels: one curve is applied to all three.

Every reduction here runs in float64. On a 26-megapixel frame numpy's float32 accumulator drifts by several
per cent — enough to tilt a white balance — and the histograms and statistics of an astro frame are exactly
where such a drift turns into a colour cast.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# The data is on 0..65535 when it is displayed, so every readout in the panel is in those units.
ADU = 65535.0


def mtf(x: np.ndarray, m: float) -> np.ndarray:
    """The midtone transfer function. m < 0.5 brightens, m > 0.5 darkens."""
    x = np.clip(np.asarray(x, np.float32), 0.0, 1.0)
    m = float(min(max(m, 1e-6), 1.0 - 1e-6))
    return np.clip(((m - 1.0) * x) / ((2.0 * m - 1.0) * x - m), 0.0, 1.0)


def _mtf_unit(x: np.ndarray, m: float) -> np.ndarray:
    """The midtone curve over values already in 0..1, in place.

    The whole-frame path calls this on 26 megapixels: `mtf` above allocates a fresh 300 MB array for each of its
    four steps, which is half the cost of showing a picture. Here the numerator is the only array made; the
    denominator reuses the input, which the caller has just filled and no longer needs.
    """
    m = float(min(max(m, 1e-6), 1.0 - 1e-6))
    num = (m - 1.0) * x
    x *= (2.0 * m - 1.0)
    x -= m
    np.divide(num, x, out=num)
    np.clip(num, 0.0, 1.0, out=num)
    return num


def mtf_mid(mid_value: float, black: float, white: float, target: float) -> float:
    """The midtone balance that maps `mid_value` to `target` inside black..white.

    Solved by bisection rather than by rearranging the formula: the algebra of MTF(y, m) = t has a singular
    case at t = 0.5 and two sign traps, and I got it wrong twice. MTF is monotonic in m, so a bisection is both
    shorter and provably right — it is checked by evaluating the curve it returns.
    """
    span = white - black
    if span <= 0:
        return 0.5
    y = (float(mid_value) - float(black)) / span
    y = float(min(max(y, 1e-6), 1.0 - 1e-6))
    t = float(min(max(target, 1e-6), 1.0 - 1e-6))
    # larger m darkens: MTF(y, m) falls as m rises, so bisect on m in [1e-6, 1-1e-6]
    lo, hi = 1e-6, 1.0 - 1e-6
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if float(mtf(y, mid)) > t:
            lo = mid                      # too bright: m needs to be larger
        else:
            hi = mid
    return float(min(max(0.5 * (lo + hi), 1e-6), 1.0 - 1e-6))


@dataclass
class Levels:
    """Where the black, midtone and white points sit, in the image's own units (0..1 for linear data)."""
    black: float = 0.0
    mid: float = 0.5          # the midtone balance, not a pixel value
    white: float = 1.0
    per_channel: bool = False  # True: one set of levels per channel (the "unlinked" case)

    def apply(self, a: np.ndarray) -> np.ndarray:
        a = np.asarray(a, np.float32)
        if a.ndim == 2:
            a = a[..., None]
        span = max(self.white - self.black, 1e-9)
        if self.per_channel and a.shape[2] >= 3:
            out = np.empty_like(a, np.float32)
            for c in range(a.shape[2]):
                x = np.subtract(a[..., c], self.black, dtype=np.float32)
                x /= span
                np.clip(x, 0.0, 1.0, out=x)
                out[..., c] = _mtf_unit(x, self.mid)
            return out
        x = np.subtract(a, self.black, dtype=np.float32)
        x /= span
        np.clip(x, 0.0, 1.0, out=x)
        return _mtf_unit(x, self.mid)

    def as_dict(self) -> dict:
        return {"black": round(float(self.black), 6), "mid": round(float(self.mid), 6),
                "white": round(float(self.white), 6), "per_channel": bool(self.per_channel)}


def histogram(a: np.ndarray, lo: float, hi: float, bins: int = 256, stride: int = 4,
              luminance: bool = True) -> dict:
    """Counts per channel over lo..hi, plus a luminance curve, for drawing.

    Sampling by `stride` keeps a 26-megapixel frame cheap to re-histogram while a slider is being dragged; the
    panel passes a sample it already holds and asks for one bin per pixel column instead.
    """
    a = np.asarray(a, np.float32)
    if a.ndim == 2:
        a = a[..., None]
    s = a[::stride, ::stride]
    if hi <= lo:
        hi = lo + 1e-6
    edges = np.linspace(float(lo), float(hi), bins + 1)
    out = {}
    rng = (float(lo), float(hi))
    for c in range(s.shape[2]):
        counts, _ = np.histogram(s[..., c], bins=bins, range=rng)
        out["rgb"[c] if s.shape[2] >= 3 else "l"] = counts
    if s.shape[2] >= 3 and luminance:
        lum = s[..., :3].mean(axis=2)
        counts, _ = np.histogram(lum, bins=bins, range=rng)
        out["l"] = counts
    out["edges"] = edges
    return out


def stats(a: np.ndarray) -> dict:
    """Per-channel min, max, mean and standard deviation, in the data's own units.

    The extremes are taken over the whole frame — they are the values a star's saturated core or a hot pixel
    sets, and a subsample would miss them. The mean and deviation come from every fourth row and column in
    float64, which is a couple of million pixels and exact enough for a readout.
    """
    a = np.asarray(a, np.float32)
    if a.ndim == 2:
        a = a[..., None]
    s = a[::4, ::4]
    out = {}
    for c in range(a.shape[2]):
        col = a[..., c]
        sample = s[..., c].ravel()
        out["rgb"[c] if a.shape[2] >= 3 else "l"] = {
            "min": float(col.min()),
            "max": float(col.max()),
            "avg": float(np.mean(sample, dtype=np.float64)),
            "std": float(np.std(sample.astype(np.float64))),
        }
    return out


def channel_means(a: np.ndarray, stride: int = 4) -> np.ndarray:
    """Each channel's mean in float64. The white balance is built from these, so the sum must not drift."""
    a = np.asarray(a, np.float32)
    if a.ndim == 2:
        a = a[..., None]
    s = a[::stride, ::stride]
    return np.array([float(np.mean(s[..., c], dtype=np.float64)) for c in range(s.shape[2])], np.float64)


def sky_mode(col: np.ndarray, bins: int = 512) -> float:
    """The value a channel's sky has most often, from a histogram of its darker part.

    The mode is what the sky actually is, which is more robust than a median here: a nebula or a rich star
    field fills part of the frame and drags a median off the true sky.
    """
    col = np.asarray(col, np.float32).ravel()
    lo = float(col.min())
    hi = float(np.percentile(col, 60.0))          # the sky lives in the dark part
    if not hi > lo:
        return lo
    counts, edges = np.histogram(col, bins=bins, range=(lo, hi))
    k = int(np.argmax(counts))
    return float(0.5 * (edges[k] + edges[k + 1]))


def auto_levels(a: np.ndarray, target_median: float = 0.20, minima=None) -> Levels:
    """Levels that put the sky just above black and the brightest part of the data at white.

    Read off ASIFitsView's own panels rather than guessed. For two different frames it put the slider markers
    at (1502, 2317) and (1466, 2281) on the 16-bit scale, and both satisfy

        black = mean of the three channel minima + 200
        white = black + 815

    so the window is a fixed width anchored on the darkest part of the frame — which is why the markers move
    from one frame to the next. The same two offsets are used here, as fractions of the 16-bit scale. The
    minima are taken over the whole frame: sampling every fourth pixel misses the single dark pixel that sets
    the marker, and the marker then lands hundreds of counts too high, where the curve is at its steepest.

    The midtone then places the sky's own level at `target_median`, so the sky lands dark but keeps its
    brightness relative to the object. `minima` lets a caller that already knows the full-frame channel minima
    pass them in (the panel measures the white-balanced frame, whose minima are not the raw frame's).
    """
    a = np.asarray(a, np.float32)
    if a.ndim == 2:
        a = a[..., None]
    n = max(1, a.shape[2])
    if minima is not None:
        floor = float(np.mean([float(m) for m in minima]))
    else:
        floor = float(np.mean([float(a[..., c].min()) for c in range(n)]))
    black = max(0.0, floor + 200.0 / ADU)
    white = black + 815.0 / ADU
    if white <= black:
        white = max(float(a.max()), black + 1e-6)
    s = a[::4, ::4]                                # the mode does not need every pixel
    sky = float(np.median([sky_mode(s[..., c]) for c in range(n)]))
    # a sky outside the window would give a degenerate curve: widen the window round it instead, keeping the
    # same width, so the frame still comes out with a stretch that can be adjusted from the panel
    if not black < sky < white:
        half = 0.5 * (white - black)
        centre = min(max(sky, half + 1e-6), 1.0 - half - 1e-6)
        black, white = centre - half, centre + half
    # The narrow window is what gives the dark sky, but on a frame with a high sky — a long sub at high gain,
    # a bright sky — it can end up below the object as well: the brightest half of a 294MC sub came out white,
    # the nebula in the middle of it included. When the frame has real data above the window, open the white
    # point out to the top of that data, so the picture is stretched rather than clipped.
    lum = s.mean(axis=2).ravel() if n >= 3 else s.ravel()
    if float(np.count_nonzero(lum > white)) > 0.01 * lum.size:
        # the trigger is the picture as a whole, but the new white point is taken per channel: an object that
        # is bright in one colour would still be clipped if only the luminance tail were measured
        tail_src = s.max(axis=2).ravel() if n >= 3 else s.ravel()
        tail = tail_src[tail_src < 0.999]          # a saturated star is meant to stay white
        if tail.size:
            # a little headroom above the tail: a white point sitting exactly on it would paint the brightest
            # part of the object white again
            white = max(white, min(1.0, 1.05 * float(np.percentile(tail, 99.9))))
        else:
            white = max(white, float(tail_src.max()))
    mid = mtf_mid(sky, black, white, target_median)
    return Levels(black=black, white=white, mid=float(min(max(mid, 0.02), 0.98)))
