"""The histogram panel: a zoomable per-channel plot with the levels dragged over it.

Modelled on ASIFitsView's panel. It draws the counts of the red, green and blue channels at every value,
filled so a channel can be followed by eye, with a real axis in 16-bit counts. The wheel zooms about the
cursor, a drag pans, the magnifier tool turns a drag into a zoom box, and a double click comes back to the
stretch window. Two markers under the plot set the black and white ends of the stretch, with a midtone handle
between them; dragging them restretches the picture, so the effect is visible while it happens.

The widget holds a *sample* of the frame (every third row and column) and recounts it for whatever range is
on screen: at any zoom the bars are as resolved as the pixels the plot has, which a histogram pre-binned over
the full 0..65535 range cannot be.
"""
from __future__ import annotations

import re

import numpy as np
from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PyQt5.QtWidgets import QWidget

from . import histogram as hist_mod

ADU = hist_mod.ADU
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 58, 14, 14, 48
HANDLE_R = 5
MIN_SPAN = 8.0 / ADU                  # do not let the wheel zoom past eight counts of the 16-bit scale

CH_COLORS = {"r": "#e85c5c", "g": "#5cd684", "b": "#5c94e8", "l": "#c8d2dc"}


def _qc(spec, fallback) -> QColor:
    """A QColor from a theme token: '#rrggbb', 'rgba(r,g,b,a)' or a QColor."""
    if isinstance(spec, QColor):
        return QColor(spec)
    s = str(spec or "").strip()
    m = re.match(r"rgba?\(([^)]+)\)", s)
    if m:
        parts = re.split(r"[,\s]+", m.group(1).strip())
        try:
            nums = [float(p) for p in parts if p]
            if len(nums) >= 3:
                a = int(round(float(nums[3]) * 255)) if len(nums) > 3 else 255
                return QColor(int(nums[0]), int(nums[1]), int(nums[2]), a)
        except ValueError:
            pass
    c = QColor(s)
    return c if c.isValid() else QColor(fallback)


def _fmt_count(n: float) -> str:
    """A count for the vertical axis: 1.2k, 3.4M."""
    n = float(n)
    if n >= 1e6:
        return "%.1fM" % (n / 1e6) if n < 1e7 else "%.0fM" % (n / 1e6)
    if n >= 1e3:
        return "%.1fk" % (n / 1e3) if n < 1e4 else "%.0fk" % (n / 1e3)
    return "%d" % int(round(n))


def _nice_step(span: float, target: int = 7) -> float:
    """A tick step of 1, 2, 2.5 or 5 times a power of ten, about `target` ticks across `span`."""
    span = max(float(span), 1e-9)
    raw = span / max(1, target)
    mag = 10.0 ** np.floor(np.log10(raw))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= m * mag:
            return m * mag
    return 10.0 * mag


class HistogramView(QWidget):
    """The histogram plus its handles. Emits `levels_changed` while a handle is dragged."""

    levels_changed = pyqtSignal()
    range_changed = pyqtSignal(float, float)
    hovered = pyqtSignal(float, object)          # the value under the cursor, and the counts there
    left = pyqtSignal()                          # the cursor left the plot

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.setMinimumWidth(360)
        self.setMouseTracking(True)
        self._sample = None
        self._counts = {}
        self._levels = hist_mod.Levels()
        self._lo, self._hi = 0.0, 1.0
        self._default = (0.0, 1.0)
        self._drag = ""
        self._pan = None                     # (x at press, lo at press, hi at press)
        self._band = None                    # (x0, x1) of the zoom box being drawn
        self._cursor = None
        self._zoom_tool = False
        self._colors = {
            "bg": QColor("#151b21"), "plot": QColor("#0a0d11"), "fg": QColor("#bbc2ca"),
            "dim": QColor("#8a9399"), "grid": QColor(255, 255, 255, 22),
            "border": QColor(255, 255, 255, 36), "accent": QColor("#68E098"),
        }
        self._font = QFont(self.font())
        self._font.setPointSizeF(8.4)
        self._font.setFamily("Consolas")
        self.setFocusPolicy(Qt.ClickFocus)

    # ---- theme and data -------------------------------------------------
    def set_theme(self, theme: dict) -> None:
        """Take the window's palette so the panel matches it."""
        if not theme:
            return
        self._colors = {
            "bg": _qc(theme.get("surface3"), "#151b21"),
            "plot": _qc(theme.get("canvas"), "#0a0d11"),
            "fg": _qc(theme.get("fg2"), "#bbc2ca"),
            "dim": _qc(theme.get("fg3"), "#8a9399"),
            "grid": _qc(theme.get("border"), "#ffffff16"),
            "border": _qc(theme.get("border2"), "#ffffff24"),
            "accent": _qc(theme.get("accent"), "#68E098"),
        }
        self.update()

    def set_sample(self, sample, levels: hist_mod.Levels = None, reset_range: bool = True) -> None:
        """A representative sample of the frame ((h, w, 3) or (h, w)), and the levels over it."""
        self._sample = None if sample is None else np.asarray(sample, np.float32)
        if levels is not None:
            self._levels = levels
        self._default = self._default_range()
        if reset_range or not (0.0 <= self._lo < self._hi <= 1.0):
            self._lo, self._hi = self._default
        self._recount()
        self.update()

    def set_data(self, counts: dict, lo: float, hi: float, levels: hist_mod.Levels) -> None:
        """A ready-made histogram over lo..hi (kept for callers that have counts, not pixels)."""
        self._sample = None
        self._levels = levels
        self._lo, self._hi = float(lo), float(hi)
        self._default = (float(lo), float(hi))
        self._counts = dict(counts)
        self.update()

    def levels(self) -> hist_mod.Levels:
        return self._levels

    def range(self):
        return self._lo, self._hi

    def default_range(self):
        return self._default

    def _default_range(self):
        """The stretch window with a margin: what the panel opens on, and what a double click returns to."""
        lv = self._levels
        span = max(lv.white - lv.black, 1e-9)
        if lv.black <= 1e-9 and lv.white >= 1.0 - 1e-9:
            return 0.0, 1.0
        lo = max(0.0, lv.black - 0.35 * span)
        hi = min(1.0, lv.white + 0.35 * span)
        return self._clamp_range(lo, hi)

    def _clamp_range(self, lo: float, hi: float):
        lo, hi = max(0.0, float(lo)), min(1.0, float(hi))
        if hi - lo < MIN_SPAN:
            mid = 0.5 * (lo + hi)
            lo = max(0.0, mid - MIN_SPAN / 2.0)
            hi = min(1.0, lo + MIN_SPAN)
            lo = max(0.0, hi - MIN_SPAN)
        return lo, hi

    def _recount(self) -> None:
        """One bin per pixel column of the plot, over the range on screen."""
        if self._sample is None or not self._sample.size:
            return
        n = max(32, int(self._plot().width()) + 1)
        self._counts = hist_mod.histogram(self._sample, self._lo, self._hi, bins=n, stride=1,
                                          luminance=False)

    # ---- geometry -------------------------------------------------------
    def _plot(self) -> QRectF:
        return QRectF(MARGIN_L, MARGIN_T,
                      max(1.0, self.width() - MARGIN_L - MARGIN_R),
                      max(1.0, self.height() - MARGIN_T - MARGIN_B))

    def _x_of(self, value: float) -> float:
        p = self._plot()
        span = max(self._hi - self._lo, 1e-12)
        return p.left() + (float(value) - self._lo) / span * p.width()

    def _value_at(self, x: float) -> float:
        p = self._plot()
        frac = (float(x) - p.left()) / max(p.width(), 1e-9)
        return self._lo + frac * (self._hi - self._lo)

    def _mid_value(self) -> float:
        """The pixel value the midtone handle sits on, for drawing and dragging."""
        span = max(self._levels.white - self._levels.black, 1e-9)
        return self._levels.black + _inverse_mtf(self._levels.mid) * span

    def _handle_at(self, x: float, y: float = None) -> str:
        """The handle under the cursor. The midtone marker sits at the top of the plot and is only picked up
        there, so a drag through the middle of the plot pans instead of shifting the midtone."""
        near = 9.0
        for name, value in (("black", self._levels.black), ("mid", self._mid_value()),
                            ("white", self._levels.white)):
            if name == "mid" and y is not None and y > self._plot().top() + 14:
                continue
            if abs(self._x_of(value) - float(x)) <= near:
                return name
        return ""

    # ---- interaction ----------------------------------------------------
    def set_zoom_tool(self, on: bool) -> None:
        self._zoom_tool = bool(on)
        self.setCursor(Qt.CrossCursor if self._zoom_tool else Qt.ArrowCursor)
        self.update()

    def zoom_tool(self) -> bool:
        return self._zoom_tool

    def zoom_to(self, lo: float, hi: float) -> None:
        lo, hi = self._clamp_range(lo, hi)
        if (round(lo, 9), round(hi, 9)) == (round(self._lo, 9), round(self._hi, 9)):
            return
        self._lo, self._hi = lo, hi
        self._recount()
        self.range_changed.emit(lo, hi)
        self.update()

    def reset_range(self) -> None:
        self.zoom_to(*self._default)

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        x = float(ev.x())
        plot = self._plot()
        hit = self._handle_at(x, float(ev.y()))
        if hit and ev.y() <= plot.bottom() + 16:
            self._drag = hit
            self._apply_drag(x)
            ev.accept()
            return
        if not plot.contains(QPointF(ev.pos())):
            return
        if self._zoom_tool:
            self._band = (x, x)
        else:
            self._pan = (x, self._lo, self._hi)
            self.setCursor(Qt.ClosedHandCursor)
        ev.accept()

    def mouseMoveEvent(self, ev):
        x = float(ev.x())
        if self._drag:
            self._apply_drag(x)
            ev.accept()
            return
        if self._band is not None:
            self._band = (self._band[0], x)
            self.update()
            ev.accept()
            return
        if self._pan is not None:
            x0, lo0, hi0 = self._pan
            span = hi0 - lo0
            shift = (x0 - x) / max(self._plot().width(), 1e-9) * span
            self.zoom_to(lo0 + shift, hi0 + shift)
            ev.accept()
            return
        self._cursor = self._value_at(x)
        counts = self._counts_at(self._cursor)
        self.hovered.emit(self._cursor, counts)
        self.setCursor(Qt.CrossCursor if (self._zoom_tool or self._handle_at(x, ev.y())) else Qt.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._band is not None:
            x0, x1 = sorted(self._band)
            self._band = None
            if abs(x1 - x0) >= 6:
                self.zoom_to(self._value_at(x0), self._value_at(x1))
            self.update()
        self._drag = ""
        self._pan = None
        self.setCursor(Qt.CrossCursor if self._zoom_tool else Qt.ArrowCursor)
        ev.accept()

    def mouseDoubleClickEvent(self, ev):
        if self._plot().contains(QPointF(ev.pos())):
            self.reset_range()
            ev.accept()

    def wheelEvent(self, ev):
        pos = ev.pos()
        if not self._plot().contains(QPointF(pos)):
            return
        v = self._value_at(pos.x())
        k = 1.0 / 1.3 if ev.angleDelta().y() > 0 else 1.3
        lo = v - (v - self._lo) * k
        hi = v + (self._hi - v) * k
        self.zoom_to(lo, hi)
        ev.accept()

    def leaveEvent(self, ev):
        self._cursor = None
        self.left.emit()
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._recount()

    def _apply_drag(self, x: float):
        value = float(np.clip(self._value_at(x), 0.0, 1.0))
        lv = self._levels
        span = max(lv.white - lv.black, 1e-9)
        if self._drag == "black":
            lv.black = min(value, lv.white - 1e-6)
        elif self._drag == "white":
            lv.white = max(value, lv.black + 1e-6)
        else:                                    # midtone: the balance that puts this value at the middle
            lv.mid = _mtf_for(float(np.clip((value - lv.black) / span, 1e-6, 1 - 1e-6)), 0.5)
        self.levels_changed.emit()
        self.update()

    def _counts_at(self, value: float) -> dict:
        if not self._counts:
            return {}
        n = len(self._counts.get("r", self._counts.get("l", [])))
        if not n:
            return {}
        i = int(np.clip((value - self._lo) / max(self._hi - self._lo, 1e-12) * n, 0, n - 1))
        return {k: int(v[i]) for k, v in self._counts.items() if k != "edges" and len(v) == n}

    # ---- drawing --------------------------------------------------------
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), self._colors["bg"])
        plot = self._plot()
        p.fillRect(plot, self._colors["plot"])
        if not self._counts:
            p.setPen(self._colors["dim"])
            p.setFont(self._font)
            p.drawText(plot, Qt.AlignCenter, "—")
            return
        peak = self._draw_axes(p, plot)
        self._draw_curves(p, plot, peak)
        self._draw_levels(p, plot)
        self._draw_hover(p, plot)
        if self._band is not None:
            x0, x1 = sorted(self._band)
            band = QRectF(x0, plot.top(), max(1.0, x1 - x0), plot.height())
            p.fillRect(band, QColor(self._colors["accent"].red(), self._colors["accent"].green(),
                                    self._colors["accent"].blue(), 40))
            p.setPen(QPen(self._colors["accent"], 1.0))
            p.drawRect(band)

    def _peak(self) -> float:
        return max([float(np.max(c)) for k, c in self._counts.items() if k != "edges"] or [1.0])

    def _draw_axes(self, p: QPainter, plot: QRectF) -> float:
        peak = max(self._peak(), 1.0) * 1.12
        p.setFont(self._font)
        fm = p.fontMetrics()
        # horizontal grid and the count labels
        step = _nice_step(peak, 4)
        v = 0.0
        while v <= peak + step * 0.01:
            y = plot.bottom() - (v / peak) * plot.height()
            if v > 0:
                p.setPen(QPen(self._colors["grid"], 1.0))
                p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(self._colors["dim"])
            p.drawText(QRectF(0, y - fm.height() / 2, MARGIN_L - 7, fm.height()),
                       Qt.AlignRight | Qt.AlignVCenter, _fmt_count(v))
            v += step
        # vertical grid and the values in 16-bit counts
        lo_adu, hi_adu = self._lo * ADU, self._hi * ADU
        step = _nice_step(hi_adu - lo_adu, 7)
        first = np.ceil(lo_adu / step) * step
        v = first
        while v <= hi_adu + 1e-6:
            x = self._x_of(v / ADU)
            p.setPen(QPen(self._colors["grid"], 1.0))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            label = "%d" % int(round(v))
            w = fm.width(label) + 6
            p.setPen(self._colors["dim"])
            p.drawText(QRectF(min(max(x - w / 2, 0.0), self.width() - w), plot.bottom() + 4, w, fm.height()),
                       Qt.AlignCenter, label)
            v += step
        p.setPen(QPen(self._colors["border"], 1.0))
        p.drawLine(QPointF(plot.left(), plot.top()), QPointF(plot.left(), plot.bottom()))
        p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        return peak

    def _draw_curves(self, p: QPainter, plot: QRectF, peak: float) -> None:
        keys = [k for k in ("b", "g", "r") if k in self._counts] or \
               [k for k in ("l",) if k in self._counts]
        for key in keys:
            counts = self._smooth(self._counts.get(key))
            if counts is None or not len(counts):
                continue
            n = len(counts)
            step = plot.width() / n
            base = QColor(CH_COLORS.get(key, "#c8d2dc"))
            path = QPainterPath()
            path.moveTo(plot.left(), plot.bottom())
            for i, c in enumerate(counts):
                x0 = plot.left() + i * step
                y = plot.bottom() - min(1.0, float(c) / peak) * plot.height()
                path.lineTo(x0, y)
                path.lineTo(x0 + step, y)
            path.lineTo(plot.right(), plot.bottom())
            path.closeSubpath()
            fill = QColor(base)
            fill.setAlpha(88)
            p.fillPath(path, fill)
            pen = QPen(base, 1.25)
            p.setPen(pen)
            p.drawPath(path)

    @staticmethod
    def _smooth(counts):
        """A three-bin moving average for drawing only.

        The bars are one bin per pixel column, so neighbouring bins are independent samples of the same
        distribution and differ by a few per cent; on a curve 6000 counts tall that reads as a saw edge.
        Averaging three bins keeps the shape and removes the saw. The counts under the cursor stay exact.
        """
        if counts is None or len(counts) < 5:
            return counts
        c = np.asarray(counts, np.float32)
        out = c.copy()
        out[1:-1] = (c[:-2] + c[1:-1] + c[2:]) / 3.0
        return out

    def _draw_levels(self, p: QPainter, plot: QRectF) -> None:
        lv = self._levels
        xb, xw = self._x_of(lv.black), self._x_of(lv.white)
        shade = QColor(0, 0, 0, 70)
        if xb > plot.left():
            p.fillRect(QRectF(plot.left(), plot.top(), xb - plot.left(), plot.height()), shade)
        if xw < plot.right():
            p.fillRect(QRectF(xw, plot.top(), plot.right() - xw, plot.height()), shade)
        for x, colour in ((xb, self._colors["fg"]), (xw, self._colors["fg"])):
            if plot.left() <= x <= plot.right():
                p.setPen(QPen(QColor(colour.red(), colour.green(), colour.blue(), 150), 1.0))
                p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        xm = self._x_of(self._mid_value())
        if plot.left() <= xm <= plot.right():
            p.setPen(QPen(QColor(self._colors["accent"].red(), self._colors["accent"].green(),
                                 self._colors["accent"].blue(), 90), 1.0))
            p.drawLine(QPointF(xm, plot.top() + 6), QPointF(xm, plot.bottom()))
            p.setPen(Qt.NoPen)
            p.setBrush(self._colors["accent"])
            p.drawPolygon(QPolygonF([QPointF(xm - 4, plot.top()), QPointF(xm + 4, plot.top()),
                                     QPointF(xm, plot.top() + 8)]))
        # the markers under the axis: a triangle per bound, its value beside it when there is room
        p.setFont(self._font)
        fm = p.fontMetrics()
        marks = []
        top = plot.bottom() + 20
        for x, value, outward in ((xb, lv.black, -1), (xw, lv.white, 1)):
            if not (plot.left() - 1 <= x <= plot.right() + 1):
                continue
            tri = QPolygonF([QPointF(x - HANDLE_R, top + HANDLE_R * 1.6), QPointF(x + HANDLE_R, top + HANDLE_R * 1.6),
                             QPointF(x, top)])
            p.setPen(Qt.NoPen)
            p.setBrush(self._colors["fg"] if value not in (0.0, 1.0) else self._colors["dim"])
            p.drawPolygon(tri)
            marks.append((x, value, outward))
        p.setPen(self._colors["fg"])
        widths = [fm.width("%d" % int(round(v * ADU))) for _x, v, _o in marks]
        if len(marks) == 2 and (marks[1][0] - marks[0][0]) > sum(widths) + 24:
            for (x, value, outward), w in zip(marks, widths):
                text = "%d" % int(round(value * ADU))
                if outward < 0:
                    rect = QRectF(x - w - 7, top - 1, w, fm.height())
                else:
                    rect = QRectF(x + 7, top - 1, w, fm.height())
                p.drawText(rect, Qt.AlignCenter, text)

    def _draw_hover(self, p: QPainter, plot: QRectF) -> None:
        if self._cursor is None or not (self._lo <= self._cursor <= self._hi):
            return
        x = self._x_of(self._cursor)
        p.setPen(QPen(QColor(255, 255, 255, 55), 1.0))
        p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))


def _inverse_mtf(m: float) -> float:
    """The normalised input that an MTF with balance m maps to 0.5."""
    m = float(min(max(m, 1e-6), 1 - 1e-6))
    return float(min(max((m * 0.5) / ((2.0 * m - 1.0) * 0.5 - m + 1.0), 0.0), 1.0))


def _mtf_for(y: float, target: float) -> float:
    y = float(min(max(y, 1e-6), 1 - 1e-6))
    t = float(min(max(target, 1e-6), 1 - 1e-6))
    return (t * (y - 1.0)) / (y * (2.0 * t - 1.0) - t)
