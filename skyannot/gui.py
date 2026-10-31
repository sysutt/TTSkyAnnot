"""TTSkyAnnot window: quick viewer for FITS / XISF / TIFF / JPEG / PNG plus plate solving and annotation.

Viewer: automatic screen stretch for linear data (linked or per channel), wheel zoom, drag to pan, 1:1 and
fit, raw pixel values and RA/Dec under the cursor, FITS/XISF header table, Left/Right for the previous/next
image in the folder. Images that already carry a solution show their deep-sky objects at once (bundled
catalogues, no network).
Annotation: queue of images (drop or open several), solve + refine + write outputs, results drawn over the
image.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from dataclasses import replace

import numpy as np
from PyQt5.QtCore import (QEasingCurve, QEvent, QPointF, QProcess, QPropertyAnimation, QRectF, QSize,
                          QStringListModel, Qt,
                          QThread, QTimer, QUrl, pyqtProperty, pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QImage, QKeySequence, QPainter,
                         QPainterPath, QPalette, QPen, QPixmap, QPolygonF)
from PyQt5.QtWidgets import (QAbstractButton, QAbstractItemView, QAction, QApplication, QCheckBox, QComboBox,
                             QGraphicsLineItem,
                             QCompleter,
                             QDialog,
                             QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                             QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem,
                             QGraphicsItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QGridLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                             QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QProxyStyle, QPushButton,
                             QScrollArea, QSizeGrip,
                             QSizePolicy, QSpinBox, QStyle, QTableWidget, QTableWidgetItem, QToolBar, QToolButton,
                             QVBoxLayout, QWidget)

from . import (__version__, catalogs, catalogue, config, debayer, fileop, histview, imageio, piopen, reveal,
               run, style, tickwhale, updater, vizier, wcsio)
from . import histogram as hist_mod
from . import annotate, shapes as shapes_mod, skyregion
from .i18n import set_language, t

ACCENT = "#68E098"

# The palette follows the TTAstroRoom design tokens (see its UI spec, shared design language between the two
# tools): one green accent and nothing else competing with it, a near-black blue-grey system background, two
# greys for secondary text, hairline borders, small radii and a 4-px spacing scale. Surfaces are named by
# role rather than by lightness, which is what keeps the two themes — and every widget — consistent.
#
# The picture sits on a canvas that frames it — the darkest tone in the dark set, a quiet grey in the light one,
# so the chrome never outshines the image in either theme.
THEMES = {
    "dark": {
        "canvas": "#0a0d11",                      # behind the picture: darker than the page, so it frames it
        "shadow": "#0a0d11",                      # 3D shadow role for the parts Fusion still draws itself
        "bg": "#0a131a",                          # system background
        "surface": "#1e2224",                     # panels, bars, dialogs
        "surface2": "#2a333a",                    # inputs, chips, list bodies
        "surface3": "#1a232a",                    # inset containers
        "stripe": "#405159",                      # section bars and label columns
        "elev": "rgba(255,255,255,0.10)",         # a card laid on a dark page
        "elev_soft": "rgba(255,255,255,0.05)",    # zebra stripe
        "press": "rgba(255,255,255,0.20)",        # pressed / active overlay
        "border": "rgba(255,255,255,0.08)",       # hairline
        "border2": "rgba(255,255,255,0.14)",      # a step stronger, for inputs
        "fg1": "#ffffff",                         # primary text
        "fg2": "#bbc2ca",                         # secondary
        "fg3": "#8a9399",                         # tertiary
        "fg4": "#6b8eaa",                         # metadata, coordinates
        "fg5": "#597185",                         # hints, very secondary
        "accent": "#68E098",                      # the one accent
        "accent_press": "#4CAF50",
        "accent_tint": "rgba(104,224,152,0.12)",
        "accent_tint2": "rgba(104,224,152,0.22)",
        # Solid state colours for the stylesheet. Alpha fills repaint over themselves on hover (the widget does
        # not clear its background), so a repeatedly hovered pill keeps getting darker; these are the blends
        # worked out once, in hex.
        "hoverbg": "#2b353c",
        "pressbg": "#333f47",
        "selbg": "#263a31",
        "selbg2": "#2c4438",
        "field": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1f272c, stop:1 #2a343b)",
        "fieldfocus": "#2f3a43",
        "cardbg": "#232a2f",
        "cardhover": "#2b353c",
        "cardon": "#263139",
        "cardline": "#333f47",
        "cardlineon": "#3f505a",
        "cardbaroff": "#46545d",
        "cardfg_on": "#eef3f6",
        "cardfg_off": "#9aa6ad",
        "cardcount_on": "#b7c3ca",
        "cardcount_off": "#6c7c88",
        "on_accent": "#06210f",                   # text on an accent fill
        "warn": "#F1A66A",
        "danger": "#ff6b6b",
        "info": "#4A9EFF",
        "disabled": "#597185",
    },
    "light": {
        "canvas": "#E4E9EE",                      # behind the picture: a quiet grey, not the dark of the dark set
        "shadow": "#C2CBD4",
        "bg": "#EEF1F4",
        "surface": "#FFFFFF",
        "surface2": "#F1F4F7",
        "surface3": "#E6EBF0",
        "stripe": "#DDE3E9",                      # the label column on a white page
        "elev": "rgba(15,23,32,0.03)",
        "elev_soft": "rgba(15,23,32,0.02)",
        "press": "rgba(15,23,32,0.10)",
        "border": "rgba(15,23,32,0.10)",
        "border2": "rgba(15,23,32,0.16)",
        "fg1": "#161B22",
        "fg2": "#4E5966",
        "fg3": "#6E7885",                         # darkened from the design's #8A94A0: it read at 2.7:1
        "fg4": "#5C7183",
        "fg5": "#77828E",
        "accent": "#248049",                      # darkened until accent text clears 4.5:1 on the page
        "accent_press": "#1B6438",
        "accent_tint": "rgba(36,128,73,0.12)",
        "accent_tint2": "rgba(36,128,73,0.18)",
        "hoverbg": "#EDF1F5",
        "pressbg": "#E2E8ED",
        "selbg": "#D8E8DE",
        "selbg2": "#CDE3D6",
        "field": "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FBFCFD, stop:1 #EEF2F6)",
        "fieldfocus": "#FFFFFF",
        "cardbg": "#F5F7F9",
        "cardhover": "#EDF1F5",
        "cardon": "#E8F3EC",
        "cardline": "#DFE5EA",
        "cardlineon": "#C6D8CC",
        "cardbaroff": "#C2CBD4",
        "cardfg_on": "#161B22",
        "cardfg_off": "#6E7885",
        "cardcount_on": "#3E4A56",
        "cardcount_off": "#8A949E",
        "on_accent": "#ffffff",                   # white on the deeper green: 4.9:1
        "warn": "#B4682A",
        "danger": "#C94F4F",
        "info": "#2A6FBF",
        "disabled": "#A8B1BA",
    },
}

# spacing and radii, from the same 4-px scale
SP = {"1": 2, "2": 4, "3": 6, "4": 8, "5": 10, "6": 12, "7": 16, "8": 20, "9": 24}


def _theme(name: str) -> dict:
    return THEMES.get(name) or THEMES["dark"]


C = _theme("dark")                  # rebound by set_theme(); every style below is built from it
THEME = "dark"                      # the name C was built from, for the parts Qt's palette cannot reach


def set_theme(name: str) -> None:
    """Point the module-level palette at a theme. Call before building a window."""
    global C, THEME
    THEME = name if name in THEMES else "dark"
    C = _theme(THEME)
    _restyle()


# annotation groups the user can switch on and off, as the catalogues the objects come from: (key, label).
# The order is the precedence order — an object held by more than one catalogue (M31 is also NGC 224, NGC 7000
# is also Sharpless 2-117) is listed under the first enabled one only, so it is never double-labelled.
GROUPS = tuple((k, catalogue.LABELS[k]) for k in catalogue.KEYS)
KEYS_URL = "https://www.ttastro.com/ttskyannot/key"      # where a free online-solve key comes from
_SUPPORTED = frozenset(imageio.SUPPORTED)

# Styles that depend on the palette are built by _restyle(), which runs at import and whenever the theme
# changes. Everything below is a module-level name so the widgets can reach it.
BAR_QSS = TOGGLE_QSS = PANEL_QSS = BUTTON_QSS = SUBTITLE_QSS = None


def _css(pairs: dict) -> dict:
    """Fill a stylesheet template from the palette."""
    return dict(pairs)


def _qss(template: str, pairs: dict) -> str:
    """Substitute the palette and round Qt's whole-pixel widths: the tokens speak of a 0.5 px hairline, which
    Qt's style-sheet parser ignores, so those become 1 px. The hairline look comes from the low-alpha border
    colour rather than from the width."""
    return re.sub(r"\d+\.5px", "1px", template % _css(pairs))


def _restyle():
    """(Re)build every stylesheet from the current palette.

    The rules stay close to the TTAstroRoom component set: hairline borders instead of drawn outlines, small
    radii, flat fills, and state shown by a tint or an overlay rather than by a new colour.
    """
    global BAR_QSS, TOGGLE_QSS, PANEL_QSS, BUTTON_QSS, SUBTITLE_QSS
    BAR_QSS = _qss("""
    /* the bar of screening controls: one translucent sheet over the picture */
    #imageBar{background:%(surface)s;border:1px solid %(border2)s;border-radius:8px}
    #imageBar QToolButton{color:%(fg2)s;background:transparent;border:none;border-radius:4px;
                         font-size:15px;padding:0}
    #imageBar QToolButton:hover:enabled{background:%(hoverbg)s;color:%(fg1)s}
    #imageBar QToolButton:pressed:enabled{background:%(pressbg)s}
    #imageBar QToolButton:disabled{color:%(disabled)s}
    #imageBar QToolButton:checked{background:%(selbg)s;color:%(accent)s}
    QFrame#barSep{color:%(border2)s}
    """, C)
    # Toggles are chips rather than boxes with ticks: the state is the fill, which reads at a glance and keeps
    # a column of them looking like one set of controls. Qt applies :checked to a checkable QCheckBox, so
    # these stay ordinary check boxes with their signals and keyboard behaviour.
    TOGGLE_QSS = _qss("""
    QCheckBox{background:%(surface)s;border:1px solid %(border2)s;border-radius:6px;color:%(fg2)s;
              padding:4px 10px;spacing:0px}
    QCheckBox:hover{border-color:%(fg3)s;color:%(fg1)s}
    QCheckBox:checked{background:%(selbg)s;border:1px solid %(accent)s;color:%(accent)s;
                      font-weight:600}
    QCheckBox:checked:hover{background:%(selbg2)s}
    QCheckBox:disabled{color:%(disabled)s;border-color:%(border)s}
    /* no indicator at all: the fill is the state. Zero size alone still let the panel's indicator rule paint
       a stray dot before every label, so the subcontrol is neutralised completely here. */
    QCheckBox::indicator{width:0px;height:0px;border:none;background:transparent;image:none;margin:0px}
    """, C)
    # A section heading as a bar, the way the design uses a stripe between sections
    SUBTITLE_QSS = _qss("QLabel{background:%(stripe)s;color:%(fg1)s;font-weight:600;"
                        "padding:5px 10px;border-radius:3px}", C)
    PANEL_QSS = _qss("""
    QWidget{color:%(fg1)s}
    QLabel{background:transparent;color:%(fg2)s}
    QLabel#section{color:%(fg3)s;font-weight:600;font-size:11px;letter-spacing:1px;
                   padding-top:8px;padding-bottom:1px}
    QLabel#note{color:%(fg5)s}
    QLabel#value{color:%(fg1)s}
    /* inputs: dark surface, hairline border, accent only on focus */
    /* inputs: no outline at all, a soft gradient fill that reads as a shallow inset */
    QComboBox,QLineEdit,QSpinBox,QDoubleSpinBox{background:%(field)s;border:none;
        border-radius:6px;padding:5px 9px;color:%(fg1)s;selection-background-color:%(accent)s;
        selection-color:%(on_accent)s}
    QLineEdit:focus,QComboBox:focus{background:%(fieldfocus)s}
    QLineEdit[bad="true"]{border:1px solid %(danger)s}
    QComboBox::drop-down{border:none;width:16px}
    /* the spin buttons: plain 16-px columns; the arrows themselves are drawn by _ArrowStyle, because a
       stylesheet on the field hides Fusion's own little bitmaps and leaves two empty squares */
    QSpinBox::up-button,QDoubleSpinBox::up-button{width:16px;border:none;background:transparent}
    QSpinBox::down-button,QDoubleSpinBox::down-button{width:16px;border:none;background:transparent}
    QSpinBox::up-button:hover,QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover,QDoubleSpinBox::down-button:hover{background:%(hoverbg)s}
    QComboBox QAbstractItemView{background:%(surface)s;border:0.5px solid %(border2)s;color:%(fg1)s;
        selection-background-color:%(accent_tint2)s;selection-color:%(accent)s;outline:none}
    /* panels and their titles */
    QDockWidget{color:%(fg3)s;font-size:11px;letter-spacing:1px}
    QDockWidget::title{background:%(bg)s;padding:5px 8px;text-align:left}
    QMainWindow::separator{background:%(border)s;width:1px;height:1px}
    /* secondary buttons: flat fill, no outline */
    QPushButton{background:%(surface2)s;border:0.5px solid %(border)s;border-radius:4px;padding:6px 10px;
        color:%(fg1)s}
    QPushButton:hover{background:%(stripe)s}
    QPushButton:pressed{background:%(pressbg)s}
    QPushButton:disabled{background:%(surface)s;color:%(disabled)s;border-color:%(border)s}
    QPlainTextEdit{background:%(canvas)s;border:0.5px solid %(border)s;border-radius:4px;color:%(fg3)s;
        font-family:'JetBrains Mono','Consolas',monospace;font-size:11px}
    QProgressBar{background:%(surface3)s;border:none;border-radius:2px}
    QProgressBar::chunk{background:%(accent)s;border-radius:2px}
    /* thin, quiet scroll bars */
    QScrollBar:vertical{background:transparent;width:9px;margin:0}
    QScrollBar::handle:vertical{background:%(stripe)s;border-radius:4px;min-height:28px}
    QScrollBar::handle:vertical:hover{background:%(fg3)s}
    QScrollBar:horizontal{background:transparent;height:9px;margin:0}
    QScrollBar::handle:horizontal{background:%(stripe)s;border-radius:4px;min-width:28px}
    QScrollBar::handle:horizontal:hover{background:%(fg1)s}
    QScrollBar::handle:horizontal:pressed{background:%(fg1)s}
    QScrollBar::add-line,QScrollBar::sub-line{height:0;width:0}
    QScrollBar::add-page,QScrollBar::sub-page{background:transparent}
    QStatusBar{color:%(fg3)s}
    QStatusBar::item{border:none}
    QToolBar{background:%(bg)s;border:none;spacing:2px;padding:3px}
    QToolBar QToolButton{background:transparent;border:0.5px solid transparent;border-radius:4px;
        padding:4px 8px;color:%(fg2)s}
    QToolBar QToolButton:hover{background:%(hoverbg)s;color:%(fg1)s}
    QToolBar QToolButton:checked{background:%(selbg)s;border-color:%(accent)s;color:%(accent)s}
    QToolBar::separator{background:%(border)s;width:1px;margin:4px 5px}
    /* 全选 / 清空: a quiet accent link, coloured by the sheet so a live theme change reaches it */
    QToolButton#link{color:%(accent)s;border:none;background:transparent;padding:0 3px}
    QToolButton#link:hover{text-decoration:underline}
    QToolTip{background:%(surface)s;color:%(fg1)s;border:0.5px solid %(border2)s;padding:4px 6px}
    QMenu{background:%(surface)s;border:0.5px solid %(border2)s;color:%(fg1)s;padding:4px}
    QMenu::item{padding:5px 18px 5px 12px;border-radius:3px}
    QMenu::item:selected{background:%(selbg)s;color:%(accent)s}
    QMenu::separator{height:1px;background:%(border)s;margin:4px 6px}
    QCheckBox{color:%(fg2)s;spacing:6px}
    /* A real check box, as opposed to the pill toggles: Qt draws the indicator itself, and left unstyled it
       paints a dark box with a dark border that vanishes on these backgrounds. */
    QCheckBox::indicator{width:15px;height:15px;border:1px solid %(border2)s;border-radius:3px;
        background:%(surface2)s}
    QCheckBox::indicator:hover{border-color:%(accent)s}
    QCheckBox::indicator:checked{background:%(accent)s;border-color:%(accent)s}
    QCheckBox::indicator:disabled{background:%(surface3)s;border-color:%(border)s}
    QHeaderView::section{background:%(surface)s;border:none;border-right:0.5px solid %(border)s;
        border-bottom:0.5px solid %(border)s;padding:4px 8px;color:%(fg3)s}
    QTableWidget{background:%(surface)s;border:0.5px solid %(border)s;gridline-color:%(border)s;
        color:%(fg2)s;selection-background-color:%(accent_tint2)s;selection-color:%(accent)s}
    QMessageBox{background:%(surface)s}
    QDialog{background:%(surface)s}
    """, C)
    BUTTON_QSS = _qss("QPushButton{background:%(accent)s;color:%(on_accent)s;font-weight:600;padding:8px;"
                      "border:none;border-radius:4px}"
                      "QPushButton:hover{background:%(accent_press)s}"
                      "QPushButton:pressed{background:%(accent_press)s}"
                      "QPushButton:disabled{background:%(surface3)s;color:%(disabled)s}", C)


# The vertical air a section heading brings with it (see QLabel#section in PANEL_QSS above): blocks in the
# panel column that have no heading above them ask for the same amount through _build_panel's gap().
SECTION_GAP = 15


_restyle()


def _toggle(parent, text, checked, tooltip="", min_width=0):
    """A check box that looks like a pill: checked means filled with the accent colour."""
    c = QCheckBox(text, parent)
    c.setChecked(checked)
    c.setCursor(Qt.PointingHandCursor)
    if tooltip:
        c.setToolTip(tooltip)
    if min_width:
        c.setMinimumWidth(min_width)
    return c


class CatalogueCard(QAbstractButton):
    """A catalogue on the panel as a small card: an accent bar, its name, and how many objects it holds.

    The bar carries the catalogue's colour while the catalogue is switched on and goes grey when it is off;
    the card itself stays neutral (a quiet border, a slightly lighter hover) so a row of them reads calmly.
    Colours come from the theme in solid values: the palette's rgba() tints are strings QColor will not parse,
    and an invalid QColor paints black.
    """

    PAD = 12
    HEIGHT = 46

    def __init__(self, name, count, color, parent=None):
        super().__init__(parent)
        self.setText(name)                     # accessible name; the paint below draws it
        self._count = count
        self._color = QColor(color)
        self.setCheckable(True)
        self.setCursor(Qt.OpenHandCursor)      # the row is dragged sideways with the mouse
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(self.HEIGHT)
        self.toggled.connect(self.update)

    def enterEvent(self, ev):
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self.update()
        super().leaveEvent(ev)

    def sizeHint(self):
        fm = QFontMetrics(QFont("Segoe UI", 9, QFont.DemiBold))
        return QSize(max(fm.horizontalAdvance(self.text()), 56) + self.PAD * 2 + 10, self.HEIGHT)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self.isChecked()
        hover = self.underMouse()
        bg = C["cardon"] if on else (C["cardhover"] if hover else C["cardbg"])
        p.setBrush(QColor(bg))
        p.setPen(QPen(QColor(C["cardlineon"] if on else C["cardline"]), 1))
        p.drawRoundedRect(r, 8, 8)
        bar = QRectF(r.left() + 1.5, r.top() + 7, 3, r.height() - 14)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._color if on else C["cardbaroff"]))
        p.drawRoundedRect(bar, 1.5, 1.5)
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        p.setPen(QColor(C["cardfg_on"] if on else C["cardfg_off"]))
        p.drawText(QRectF(r.left() + self.PAD + 4, r.top() + 5, r.width() - self.PAD - 8, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, self.text())
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor(C["cardcount_on"] if on else C["cardcount_off"]))
        label = ("%s 个" % format(self._count, ",")) if self._count else t("在线")
        p.drawText(QRectF(r.left() + self.PAD + 4, r.top() + 22, r.width() - self.PAD - 8, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, label)


class SectionScroll(QScrollArea):
    """A column of sections whose preferred height is its content, so it shows fully at the default window
    size, but which shrinks (and scrolls) when the window is small."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pref = 300
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def set_content(self, content):
        self.setWidget(content)
        self.updateGeometry()

    def sizeHint(self):
        w = self.widget()
        if w is not None:
            self._pref = w.sizeHint().height() + 4      # the layout knows its height only once it is built
        return QSize(super().sizeHint().width(), self._pref)

    def minimumSizeHint(self):
        return QSize(super().minimumSizeHint().width(), 200)


class HScroll(QScrollArea):
    """A one-row scroll area: the wheel moves it sideways, a press on a card drags the row, and the bar is a
    thin overlay rather than a desktop-sized control."""

    def _qss(self):
        """A 6-pixel bar that stays quiet until the pointer is on it, then goes near-white.

        The hover colour must be brighter than the resting one: an earlier version dimmed it, and the bar
        looked like it vanished into the panel background while being dragged.
        """
        return ("QScrollArea{background:transparent;border:none}"
                "QScrollBar:horizontal{height:6px;background:transparent;margin:0}"
                "QScrollBar::handle:horizontal{background:%s;border-radius:3px;min-width:28px}"
                "QScrollBar::handle:horizontal:hover{background:%s}"
                "QScrollBar::handle:horizontal:pressed{background:%s}"
                "QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0}"
                "QScrollBar::add-page,QScrollBar::sub-page{background:transparent}"
                % (C["fg5"], C["fg1"], C["fg1"]))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(self._qss())
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._press = None
        self._dragging = False

    def watch(self, card):
        """Let a press on this card drag the whole row (a plain click still toggles it)."""
        card.installEventFilter(self)

    def wheelEvent(self, ev):
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - ev.angleDelta().y())
        ev.accept()

    def eventFilter(self, obj, ev):
        if not isinstance(obj, CatalogueCard):
            return super().eventFilter(obj, ev)
        kind = ev.type()
        bar = self.horizontalScrollBar()
        if kind == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            self._press = (ev.globalPos().x(), bar.value(), obj)
            self._dragging = False
            obj.setCursor(Qt.ClosedHandCursor)
            return False                        # the card still sees the press, so a click can toggle it
        if kind == QEvent.MouseMove and self._press is not None and (ev.buttons() & Qt.LeftButton):
            dx = ev.globalPos().x() - self._press[0]
            if abs(dx) > 4:                     # a few pixels of slack, so a shaky click is still a click
                self._dragging = True
                bar.setValue(self._press[1] - dx)
                return True
            return False
        if kind == QEvent.MouseButtonRelease:
            dragged = self._dragging
            self._press = None
            self._dragging = False
            obj.setCursor(Qt.OpenHandCursor)
            if dragged:
                obj.setDown(False)              # the button never saw the release: do not leave it pressed
                return True                     # and swallow it, so dragging does not toggle the card
            return False
        return super().eventFilter(obj, ev)


class Switch(QAbstractButton):
    """A two-state switch: a track that fills with the accent and a knob that slides to the other side.

    The colour scheme is one of two values, so a switch reads better than a drop-down: one click flips it, and
    the state is visible without opening anything.
    """

    def __init__(self, parent=None, width=44, height=24):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(width, height)
        self._knob = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self.toggled.connect(self._slide)

    def _slide(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self):
        return self._knob

    def set_knob(self, value):
        self._knob = float(value)
        self.update()

    knob = pyqtProperty(float, get_knob, set_knob)

    def sizeHint(self):
        return QSize(self.width(), self.height())

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        track = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        radius = track.height() / 2.0
        on = self.isChecked()
        p.setPen(QPen(QColor(C["border2"]), 1.0))
        p.setBrush(QColor(C["accent"] if on else C["surface2"]))
        p.drawRoundedRect(track, radius, radius)
        r = radius - 3.0
        left = track.left() + 3.0 + r
        right = track.right() - 3.0 - r
        cx = left + (right - left) * self._knob
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["on_accent"] if on else C["fg2"]))
        p.drawEllipse(QPointF(cx, track.center().y()), r, r)


def _paint_spin_arrows(widget):
    """Draw the up/down arrows on a spin box.

    A stylesheet that gives the field a border and a background makes Qt paint the sub-controls itself, and the
    arrows Fusion would draw are lost — two empty squares are all that is left. The buttons still exist and
    still take clicks; only the marks are missing, so they are painted here, on top, in the palette's own text
    colour (which means a live theme switch recolours them too).
    """
    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(C["fg2"] if widget.isEnabled() else C["disabled"]))
    w = 16.0
    r = 3.4
    cx = widget.width() - w / 2.0
    upper = widget.height() * 0.30
    lower = widget.height() * 0.70
    p.drawPolygon(QPolygonF((QPointF(cx - r, upper + r * 0.6), QPointF(cx + r, upper + r * 0.6),
                             QPointF(cx, upper - r * 0.8))))
    p.drawPolygon(QPolygonF((QPointF(cx - r, lower - r * 0.6), QPointF(cx + r, lower - r * 0.6),
                             QPointF(cx, lower + r * 0.8))))


class SpinBox(QSpinBox):
    """A QSpinBox that keeps its arrows under a stylesheet (see _paint_spin_arrows) and leaves the wheel to
    the page it sits on until the field has been clicked.

    The wheel guard is written out in each class instead of a mixin because a Python mixin in front of a sip
    class crashes PyQt5 here; calling the base directly is the safe shape.
    """

    def wheelEvent(self, ev):
        if self.hasFocus():
            QSpinBox.wheelEvent(self, ev)
        else:
            ev.ignore()                      # ignored events propagate to the scrolling parent

    def paintEvent(self, ev):
        super().paintEvent(ev)
        _paint_spin_arrows(self)


class DoubleSpinBox(QDoubleSpinBox):
    """A QDoubleSpinBox with the same two refinements as SpinBox."""

    def wheelEvent(self, ev):
        if self.hasFocus():
            QDoubleSpinBox.wheelEvent(self, ev)
        else:
            ev.ignore()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        _paint_spin_arrows(self)


class ComboBox(QComboBox):
    """A combo box that only changes on the wheel after it has been clicked."""

    def wheelEvent(self, ev):
        if self.hasFocus():
            QComboBox.wheelEvent(self, ev)
        else:
            ev.ignore()


def _list_qss(widget):
    """List styling with both selection states spelled out: as soon as a QSS rule touches ::item, Qt stops
    taking the highlighted-text colour from the palette, so without these the selected row keeps the canvas
    colour and cannot be read. The unfocused list gets a tint instead of a fill, which is also how the two
    lists stay distinguishable at a glance."""
    return ("QListWidget{background:%(surface2)s;border:1px solid %(border)s;border-radius:4px;"
            "padding:3px;outline:none}"
            "QListWidget::item{height:%(row)dpx;padding-left:8px;border-radius:3px;color:%(fg2)s}"
            "QListWidget::item:selected{background:%(accent)s;color:%(on_accent)s}"
            "QListWidget::item:selected:!active{background:%(selbg)s;color:%(accent)s}"
            "QListWidget::item:hover:!selected{background:%(hoverbg)s;color:%(fg1)s}"
            % dict(C, row=_list_row_height(widget)))


def _list_row_height(widget):
    """A comfortable row height for the two file lists: the font plus a few pixels of air. Taken from the
    font, so it stays right when the interface font scales up."""
    return widget.fontMetrics().height() + 9


# ───────────────────────────── workers ─────────────────────────────
class LoadWorker(QThread):
    done = pyqtSignal(object)            # dict

    def __init__(self, path, stretch, debayer=True, bayer_pattern="auto", parent=None, dso_cats=None):
        super().__init__(parent)
        self.path, self.stretch = path, stretch
        self.debayer = debayer
        self.bayer_pattern = bayer_pattern
        self.dso_cats = dso_cats            # enabled deep-sky catalogues, for a frame with a header solution
        self.target_object = None           # the online record the target name resolves to, if any
        self.tickwhale_base = ""             # the online database, for the region query

    def run(self):
        out = {"path": self.path}
        self._out = out
        try:
            img = imageio.load(self.path, want_debayer=self.debayer,
                               pattern="" if self.bayer_pattern == "auto" else self.bayer_pattern)
            out["mode"] = imageio.auto_mode(img) if self.stretch == "auto" else self.stretch
            out["color"] = img.is_color
            out["mosaic"] = img.is_bayer_mosaic
            rgb, balance, levels = imageio.display_u8(img, out["mode"])
            # keep what made this picture, so the histogram panel opens on the very state on screen
            img._balance = balance
            img._levels = levels
            a = rgb
            h, w = a.shape[:2]
            out["qimage"] = QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy()
            out["img"] = img
            wcs = wcsio.read_existing(img)
            out["wcs"] = wcs
            if wcs is not None:
                try:
                    out["shapes"], out["named"], out["grid"] = run.local_overlay(
                        img, wcs, cats=self.dso_cats, target=self.target_object)
                except Exception:
                    out["shapes"], out["named"], out["grid"] = [], [], []
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, e)
        self.done.emit(out)


class FolderScan(QThread):
    """Read the pixel size of every picture in a folder, off the GUI thread.

    The sizes decide which files are previews (see imageio.drop_previews). A folder can sit on a slow disk or a
    network share, so the first list is built with every file and the previews are taken out when this answers.
    """

    done = pyqtSignal(str, object, object)          # folder, names, kept

    def __init__(self, folder, names, parent=None):
        super().__init__(parent)
        self.folder, self.names = folder, list(names)

    def run(self):
        try:
            kept = imageio.drop_previews(self.folder, self.names)
        except Exception:                           # a broken file must not cost the whole list
            kept = list(self.names)
        self.done.emit(self.folder, self.names, kept)


class AnnotateWorker(QThread):
    """Solve and annotate a queue of images. Nothing is written here: after each image the main thread is
    asked whether to save, and this thread waits for that answer, so a batch run still shows its results one
    by one and only writes what was agreed to."""
    log = pyqtSignal(str)
    progress = pyqtSignal(int, float)     # queue index, fraction
    finished_one = pyqtSignal(int, object)
    save_prompt = pyqtSignal(object, object, str)   # (worker, result, path)

    def __init__(self, items, opt, parent=None):
        super().__init__(parent)
        self.items, self.opt = items, opt     # [(index, path)]
        self._answer = None
        self._reply = threading.Event()

    def answer(self, flags):
        """Called by the main thread with what to write (see MainWindow._save_prompt)."""
        self._answer = flags
        self._reply.set()

    def run(self):
        total = len(self.items)
        for n, (idx, path) in enumerate(self.items, start=1):
            # 解析日志带「队列序号/总数」：批量跑的时候能直接把一条日志对回左侧列表的第几张
            tag = "[%d/%d] " % (idx + 1, total)
            log = lambda m, _t=tag: self.log.emit(_t + m)
            try:
                img, res, disp = run.solve(path, self.opt, log=log,
                                           progress=lambda f, msg, i=idx: self.progress.emit(i, f))
                if res.ok:
                    self._reply.clear()
                    self._answer = None
                    self.save_prompt.emit(self, res, path)
                    self._reply.wait(300)          # the window always answers; the cap is a safety net
                    flags = self._answer or {}
                    if flags.get("_out_dir"):
                        self.opt.out_dir = flags["_out_dir"]      # the folder picked in the dialog
                    if flags.get("_remember"):                    # "use this from now on"
                        self.opt.write_txt = bool(flags.get("txt"))
                        self.opt.write_outlines = bool(flags.get("outlines"))
                        self.opt.write_preview = bool(flags.get("preview"))
                        self.opt.write_wcs = bool(flags.get("wcs"))
                    if not flags.get("_cancel") and any(
                            flags.get(k) for k in ("txt", "outlines", "preview", "wcs")):
                        run.write_outputs(img, res, self.opt, disp, log=self.log.emit)
            except Exception as e:                     # never let a worker die silently
                res = run.Result(image=path, error="%s\n%s" % (e, traceback.format_exc()))
            self.finished_one.emit(idx, res)


# ───────────────────────────── image view ─────────────────────────────
class Spinner(QWidget):
    """The loading mark: an accent-coloured arc turning over a faint track.

    Reading and stretching a 20-megapixel frame takes a moment, and without a mark the window looks frozen
    while it happens. It is drawn rather than shown as a static picture so it costs nothing to keep turning,
    and its own timer stops when it is hidden.
    """

    def __init__(self, parent=None, size=34):
        super().__init__(parent)
        self._angle = 0
        self._size = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)   # never in the way of a click or a drag
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._turn)
        self.hide()

    def _turn(self):
        self._angle = (self._angle + 24) % 360
        self.update()

    def showEvent(self, ev):
        self._timer.start()
        super().showEvent(ev)

    def hideEvent(self, ev):
        self._timer.stop()
        super().hideEvent(ev)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = max(2.0, self._size / 14.0)
        r = (self._size - w) / 2.0
        p.translate(self._size / 2.0, self._size / 2.0)
        track = QPen(QColor(C["fg5"]))
        track.setWidthF(w)
        track.setCapStyle(Qt.RoundCap)
        p.setPen(track)
        p.drawEllipse(QRectF(-r, -r, 2 * r, 2 * r))
        arc = QPen(QColor(C["accent"]))
        arc.setWidthF(w)
        arc.setCapStyle(Qt.RoundCap)
        p.setPen(arc)
        # a 90-degree arc at the current angle: drawArc measures in 1/16 degrees, clockwise from 3 o'clock
        p.drawArc(QRectF(-r, -r, 2 * r, 2 * r), int(-self._angle * 16), int(90 * 16))


def _display_u8(disp, own=False):
    """The 8-bit RGB the view paints, from a display array already in 0..1.

    `own` says the caller is done with `disp`: the scale and round-off are then written into that array instead
    of into two more 26-megapixel temporaries. Never pass own=True for an array the loaded image still holds
    (`img.data`), which is what the "none" stretch hands back.
    """
    a = np.asarray(disp, np.float32)
    shared = a is disp
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
        shared = False
    elif a.shape[2] == 1:
        a = np.repeat(a, 3, axis=2)
        shared = False
    if own and not shared and a.flags.writeable:
        np.multiply(a, 255.0, out=a)
        np.add(a, 0.5, out=a)
        return np.ascontiguousarray(a.astype(np.uint8))
    return np.ascontiguousarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))


def _box_downscale(qimage, tw, th):
    """Average a QImage down to tw x th, so every source pixel contributes instead of being skipped.

    Qt's own smooth scaling does not do this when the factor is large: it samples, and sampling a star field
    with a two-pixel structure against a 7:1 grid produces a lattice of stripes. A box filter cannot, because
    it is exactly the operation being approximated by the sampling.

    Pillow's BOX resampling is that average, written in C: averaging 26 megapixels down to a screen-sized copy
    takes about a third of a second, where the numpy `add.reduceat` version spent over a second building two
    float32 copies of the frame first.
    """
    if qimage.width() <= tw and qimage.height() <= th:
        return qimage
    src = qimage.convertToFormat(QImage.Format_RGB888)
    w, h = src.width(), src.height()
    tw, th = max(1, min(tw, w)), max(1, min(th, h))
    ptr = src.bits()
    ptr.setsize(src.byteCount())
    arr = np.frombuffer(ptr, np.uint8).reshape(h, src.bytesPerLine())[:, : w * 3]
    arr = np.ascontiguousarray(arr.reshape(h, w, 3))
    from PIL import Image
    small = np.asarray(Image.fromarray(arr).resize((tw, th), Image.BOX))
    out = np.ascontiguousarray(small)
    oh, ow = out.shape[:2]
    return QImage(out.data, ow, oh, 3 * ow, QImage.Format_RGB888).copy()


class _LabelText(QGraphicsSimpleTextItem):
    """A label drawn as a small plate: the name in its object's colour on a dark rounded chip.

    Plain coloured text disappears over a bright nebula core or a galaxy disc; the chip keeps every name
    readable, and its hairline carries the object's own shade so a crowded field still reads as many objects.
    """

    PAD = 3.0

    def __init__(self, text, color, parent=None):
        super().__init__(text, parent)
        self._line = QColor(color)
        self.setBrush(QBrush(self._line))

    def boundingRect(self):
        r = super().boundingRect()
        return r.adjusted(-self.PAD, -self.PAD * 0.55, self.PAD, self.PAD * 0.7)

    def paint(self, painter, option, widget=None):
        r = super().boundingRect()
        chip = QRectF(r.left() - self.PAD, r.top() - self.PAD * 0.55,
                      r.width() + 2 * self.PAD, r.height() + self.PAD * 1.25)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(self._line, 0.8)
        pen.setCosmetic(True)
        if style.theme().get("label_bg") == "cream":
            pen = QPen(QColor(58, 38, 30, 170), 0.8)
            painter.setPen(pen)
            painter.setBrush(QColor(236, 222, 190, 210))
        else:
            painter.setBrush(QColor(2, 4, 10, 168))
        painter.drawRoundedRect(chip, 3.5, 3.5)
        super().paint(painter, option, widget)


class EmptyHint(QGraphicsItem):
    """What an empty viewer shows: a dashed frame, a small picture glyph, and two lines of text.

    It is drawn rather than assembled from QGraphicsTextItems so the whole hint keeps one alignment and
    follows a live theme change with a repaint.
    """

    W, H = 480, 236

    def __init__(self):
        super().__init__()
        self._title = t("拖入图片，或点这里打开")
        self._sub = t("支持 FITS / XISF / TIFF / JPG / PNG")
        # 提示是界面元素，不是画面内容：滚轮缩放时它不该跟着放大缩小
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setVisible(False)

    def boundingRect(self):
        return QRectF(-self.W / 2.0, -self.H / 2.0, self.W, self.H)

    def paint(self, p, _opt, _widget=None):
        p.setRenderHint(QPainter.Antialiasing, True)
        top = -self.H / 2.0
        icon = QRectF(-34, top + 2, 68, 68)
        p.setPen(QPen(QColor(C["fg5"]), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(icon, 20, 20)
        p.setPen(QPen(QColor(C["fg4"]), 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(icon.left() + 24, icon.top() + 24), 5, 5)
        p.drawPolyline(QPointF(icon.left() + 12, icon.bottom() - 18),
                       QPointF(icon.left() + 30, icon.bottom() - 38),
                       QPointF(icon.left() + 44, icon.bottom() - 26),
                       QPointF(icon.left() + 52, icon.bottom() - 33),
                       QPointF(icon.right() - 10, icon.bottom() - 18))
        p.setBrush(QColor(C["fg5"]))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(icon.left() + 24, icon.top() + 24), 2.2, 2.2)
        f = QFont()
        f.setPointSize(12)          # 提示不随缩放走之后，字号要小一档才不显得压画面
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(C["fg2"]))
        p.drawText(QRectF(-self.W / 2.0, top + 92, self.W, 30), Qt.AlignHCenter | Qt.AlignTop, self._title)
        f2 = QFont()
        f2.setPointSize(9)
        p.setFont(f2)
        p.setPen(QColor(C["fg4"]))
        p.drawText(QRectF(-self.W / 2.0, top + 128, self.W, 24), Qt.AlignHCenter | Qt.AlignTop, self._sub)


class ImageView(QGraphicsView):
    moved = pyqtSignal(float, float)      # scene coordinates
    files_dropped = pyqtSignal(list)
    zoom_changed = pyqtSignal(float)
    open_requested = pyqtSignal()         # a click on an empty view asks for the file dialog
    step_requested = pyqtSignal(int)      # keyboard stepping: -1 / +1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self.pix)
        # everything drawn over the picture is a child of this frame-sized item, which clips it to the image
        self.overlay = QGraphicsRectItem(0, 0, 0, 0)
        self.overlay.setPen(QPen(Qt.NoPen))
        self.overlay.setFlag(QGraphicsRectItem.ItemClipsChildrenToShape)
        self.scene().addItem(self.overlay)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(C["canvas"]))
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._fit = True
        self._empty = True                     # no picture yet: a click opens the file dialog
        # where each label landed last time: {text: (anchor x, anchor y, dx, dy)} in image px and screen px,
        # so a zoom can put the same label back at the same place instead of shuffling every name
        self._label_prev = {}
        # the full-resolution picture, and the resolution currently in the pixmap (see _rebuild_pixmap)
        self._source = None
        self._pix_size = None
        self._pix_factor = 1.0
        # The view, its viewport and the bar each report Enter/Leave, and the pointer can be inside more than
        # one of them, so those events are counted rather than treated as a single flag.
        self._bar = None
        for w in (self, self.viewport()):
            w.installEventFilter(self)
        # the loading mark, laid over the picture while a frame is being read and stretched
        self._loading = False
        self.spinner = Spinner(self)
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._show_spinner)
        self._set_grab(False)                  # an empty view shows an arrow cursor and is clickable
        self.placeholder = EmptyHint()
        self.scene().addItem(self.placeholder)

    def eventFilter(self, obj, ev):
        """The wheel must not die on the bar: send it to the viewport, which is where the view scales."""
        bar = getattr(self, "_bar", None)
        if ev.type() == QEvent.Wheel and bar is not None and (obj is bar or bar.isAncestorOf(obj)):
            from PyQt5.QtGui import QWheelEvent
            self.viewport().setFocus()
            self.wheelEvent(QWheelEvent(ev.posF(), ev.globalPosF(), ev.pixelDelta(), ev.angleDelta(),
                                        ev.buttons(), ev.modifiers(), ev.phase(), ev.inverted()))
            return True
        return super().eventFilter(obj, ev)

    def set_image_bar(self, bar):
        """The bar of screening controls that floats along the bottom edge (built by MainWindow)."""
        self._bar = bar
        bar.setStyleSheet(BAR_QSS)
        bar.hide()
        for w in (bar,) + tuple(bar.findChildren(QToolButton)):
            w.installEventFilter(self)      # the wheel zooms the picture even over the bar
        self._place_nav()

    # loading mark ---------------------------------------------------------
    def set_loading(self, on):
        """A frame is being read. The mark is delayed a moment: on a small file the load is over before it
        would appear, and a mark that flashes on every step reads as a stutter rather than as progress. The
        picture already on screen stays visible underneath, so the mark never replaces what the user is
        looking at."""
        self._loading = bool(on)
        if on:
            self._load_timer.start(120)
        else:
            self._load_timer.stop()
            self.spinner.hide()

    def _show_spinner(self):
        if not getattr(self, "_loading", False):
            return
        self._place_spinner()
        self.spinner.show()
        self.spinner.raise_()

    def _place_spinner(self):
        """Centred on the viewport, which is where the picture actually is, rather than on the widget."""
        vp = self.viewport()
        self.spinner.move(max(0, vp.x() + (vp.width() - self.spinner.width()) // 2),
                          max(0, vp.y() + (vp.height() - self.spinner.height()) // 2))

    def _place_nav(self):
        """Keep the bar of screening controls on the bottom edge, centred, clear of the scroll bar."""
        bar = getattr(self, "_bar", None)
        if not self._empty and bar is not None and bar.isVisible():
            bottom = 14 + self.horizontalScrollBar().height()
            bar.adjustSize()
            bar.move(max(8, (self.width() - bar.width()) // 2),
                     max(8, self.height() - bar.height() - bottom))
            bar.raise_()
        if getattr(self, "spinner", None) is not None and self.spinner.isVisible():
            self._place_spinner()
            self.spinner.raise_()

    def _set_grab(self, on):
        self.setDragMode(QGraphicsView.ScrollHandDrag if on else QGraphicsView.NoDrag)
        if on:
            self.viewport().setCursor(Qt.OpenHandCursor)
        else:
            self.viewport().unsetCursor()

    def set_placeholder(self, on):
        """Show or hide the empty-state hint. The hint draws its own words, so this only takes a flag."""
        self.placeholder.setVisible(bool(on))
        if on:
            self.setSceneRect(self.placeholder.boundingRect())
            self.centerOn(0, 0)
        self._empty = bool(on)
        self._set_grab(not self._empty)
        self._place_nav()

    def _view_state(self):
        """Zoom and place of the current picture as (display size, scale, scene point at the viewport
        centre), or None while the picture is fitted. Switching to another frame of the same size puts the
        view back here, so the same detail can be compared from one frame to the next."""
        if self._fit or self.pix.pixmap().isNull() or self._source is None:
            return None
        # the picture's own size, not the size of the pixmap on screen: at a zoom below 1 that is a box-
        # averaged copy, and comparing it with the next frame's size would call every such frame different
        c = self.mapToScene(self.viewport().rect().center())
        return ((self._source.width(), self._source.height()), self.transform().m11(), (c.x(), c.y()))

    def _apply_view(self, state):
        """Restore a state from _view_state(). The point is a scene coordinate, so the same spot of the
        picture stays under the centre of the viewport whatever the two displays' shapes are."""
        _size, scale, (x, y) = state
        self._fit = False
        self.setTransform(self.transform().fromScale(scale, scale))
        # Put the point back under the viewport centre by setting the scroll bars ourselves. centerOn() uses
        # its own rounding of the centre (it halves the viewport width, where the state was read from
        # rect().center()), and that one-pixel disagreement walked the picture right and down with every
        # switch. Reading and writing through the same centre makes the round trip exact.
        c = self.viewport().rect().center()
        self.horizontalScrollBar().setValue(int(round(x * scale - c.x())))
        self.verticalScrollBar().setValue(int(round(y * scale - c.y())))
        self._relayout()
        self.zoom_changed.emit(scale)

    def clear_image(self):
        """Nothing left to show (every image in the folder is gone): back to the empty state."""
        self.pix.setPixmap(QPixmap())
        self.set_placeholder(True)

    def set_theme(self, theme):
        """Repaint the parts the window's stylesheet cannot reach: the canvas behind the picture and the drop
        hint. Called when the colour scheme changes while the window is open."""
        self.setBackgroundBrush(QColor(theme["canvas"]))
        self.placeholder.update()
        self.viewport().update()

    def _pixmap_for(self, qimage, factor):
        """qimage, or a box-averaged copy at `factor` of its size, plus that copy's scale factor.

        A 26-megapixel pixmap shown at 14% is squeezed by Qt on every paint; this makes the filtered copy up
        front instead, so the first paint costs nothing and no deferred rebuild has to freeze the window."""
        if factor >= 0.6 or qimage.width() < 16 or qimage.height() < 16:
            return qimage, 1.0
        tw = max(8, int(round(qimage.width() * factor)))
        th = max(8, int(round(qimage.height() * factor)))
        small = _box_downscale(qimage, tw, th)
        if small is None or small.isNull() or small.width() >= qimage.width():
            return qimage, 1.0
        return small, small.width() / float(qimage.width())

    def _show_source(self, qimage, factor):
        """Put qimage on screen as the current picture, honouring a zoom factor below 1 with a box average.

        Returns the image actually put up: the caller may want its pixels (the brightness map) and they are
        already at screen size.
        """
        small, used = self._pixmap_for(qimage, factor)
        self.pix.setPixmap(QPixmap.fromImage(small))
        self._pix_size, self._pix_factor = None, 1.0
        if used < 1.0:
            self.pix.setScale(1.0 / used)          # the item still covers the whole scene rectangle
            self._pix_size = (small.width(), small.height())
            self._pix_factor = used
        else:
            self.pix.setScale(1.0)
        return small

    def set_image(self, qimage, view=None):
        """view: a _view_state() to restore — only meaningful for a picture of the same pixel size, so the
        caller passes it only when the display size is unchanged (see MainWindow.show_image)."""
        self.set_placeholder(False)
        self._source = qimage
        w, h = qimage.width(), qimage.height()
        vw, vh = max(1, self.viewport().width() - 2), max(1, self.viewport().height() - 2)
        fit = min(1.0, vw / float(w), vh / float(h)) if w > 16 and h > 16 else 1.0
        # coming back to a zoomed view: build the on-screen copy at that zoom, not at fitted size
        factor = fit if view is None else min(1.0, max(float(view[1]), 1.0 / 64.0))
        small = self._show_source(qimage, factor)
        self.setSceneRect(QRectF(0, 0, w, h))
        self.overlay.setRect(0, 0, w, h)
        # a small grey copy tells the label placement where the picture is bright; taken from the screen-sized
        # copy just made rather than scaling 26 megapixels with Qt again
        if max(small.width(), small.height()) > 1200:
            k = 1200.0 / max(small.width(), small.height())
            small = _box_downscale(small, max(1, int(round(small.width() * k))),
                                   max(1, int(round(small.height() * k))))
        g = small.convertToFormat(QImage.Format_Grayscale8)
        ptr = g.bits()
        ptr.setsize(g.byteCount())
        arr = np.frombuffer(ptr, np.uint8).reshape(g.height(), g.bytesPerLine())[:, :g.width()]
        self._bright = style.Brightness(arr.astype(np.float32) / 255.0, g.width() / float(w))
        if view is not None:
            self._apply_view(view)
        else:
            self.fit()

    def show_array(self, disp, own=False):
        """Replace the picture with a display-ready array, keeping the zoom and scroll position.

        Used by the histogram panel: dragging a level must change what is on screen without the view jumping
        back to fitted. `own` says the array may be written over (see _display_u8).
        """
        self.show_rgb(_display_u8(disp, own=own))

    def show_rgb(self, rgb):
        """Replace the picture with an 8-bit RGB array, keeping the zoom and scroll position.

        The histogram panel's live redraw comes in here already converted: the stretch is a 16-bit table, so
        there is no float frame to make first.
        """
        rgb = np.ascontiguousarray(rgb)
        h, w = rgb.shape[:2]
        qimage = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        state = self._view_state()
        self._source = qimage
        factor = min(1.0, max(self.transform().m11(), 1.0 / 64.0))
        self._show_source(qimage, factor)
        self.setSceneRect(QRectF(0, 0, w, h))
        self.overlay.setRect(0, 0, w, h)
        if state is not None:
            self._apply_view(state)

    def fit(self):
        if self.pix.pixmap().isNull() and self._source is None:
            return
        self._fit = True
        self.fitInView(self.pix, Qt.KeepAspectRatio)
        self._relayout()
        self._schedule_pixmap()
        self.zoom_changed.emit(self.transform().m11())

    def one_to_one(self):
        self._fit = False
        self.setTransform(self.transform().fromScale(1.0, 1.0))
        self._relayout()
        self._schedule_pixmap()
        self.zoom_changed.emit(1.0)

    def _schedule_pixmap(self):
        """Re-pick the pixmap resolution soon, not on every event of a zoom gesture."""
        if not hasattr(self, "_pixmap_timer"):
            self._pixmap_timer = QTimer(self)
            self._pixmap_timer.setSingleShot(True)
            self._pixmap_timer.timeout.connect(self._rebuild_pixmap)
        self._pixmap_timer.start(120)

    def _rebuild_pixmap(self):
        """Choose a pixmap resolution that matches the zoom, so Qt never squeezes the frame by a large factor.

        Qt's smooth scaling samples the source for each destination pixel. That is fine when the two are close
        in size, but a 6248-pixel frame shown at 14% leaves it picking one source pixel out of every seven, and
        the picture's own two-pixel structure — the residue a Bayer reconstruction always leaves — beats
        against that sampling grid and shows as a lattice of stripes across the whole image. Averaging first
        (a true box filter, which sampling cannot emulate) and leaving Qt a factor of at most two removes it.

        Zooming in is never smoothed: at 1:1 or beyond every pixel must be shown as it is.
        """
        if getattr(self, "_source", None) is None or self._source.isNull():
            return
        W, H = self._source.width(), self._source.height()
        scale = float(self.transform().m11())
        factor = min(1.0, max(scale, 1.0 / 64.0))
        if factor >= 0.6 or W < 16 or H < 16:
            if getattr(self, "_pix_factor", 1.0) != 1.0 or getattr(self, "_pix_size", None):
                self._pix_factor = 1.0
                self._pix_size = None          # the next zoomed-out pass must build its copy again
                self.pix.setPixmap(QPixmap.fromImage(self._source))
                self.pix.setScale(1.0)
            return
        tw = max(8, int(round(W * factor)))
        th = max(8, int(round(H * factor)))
        # a few pixels either way is invisible; rebuilding on every wheel notch is not free
        if getattr(self, "_pix_size", None) and abs(tw - self._pix_size[0]) <= 3 \
                and abs(th - self._pix_size[1]) <= 3:
            return
        small = _box_downscale(self._source, tw, th)
        if small is None or small.isNull():
            return
        self._pix_size = (tw, th)
        self._pix_factor = tw / float(W)
        self.pix.setPixmap(QPixmap.fromImage(small))
        # keep the item occupying the same rectangle in scene coordinates, so the overlay stays aligned
        self.pix.setScale(W / float(tw))

    def wheelEvent(self, ev):
        k = 1.25 if ev.angleDelta().y() > 0 else 0.8
        s = self.transform().m11() * k
        if 0.02 < s < 40:
            self._fit = False
            self.scale(k, k)
            self._relayout()
            self._schedule_pixmap()
            self.zoom_changed.emit(s)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_nav()
        if self._fit:
            self.fit()

    def mousePressEvent(self, ev):
        # An empty view is a big "open" button; with a picture loaded the drag/pan handling stays untouched.
        # The hit test must run on the viewport: childAt() on the view itself reports its viewport.
        if self._empty and ev.button() == Qt.LeftButton and self.viewport().childAt(ev.pos()) is None:
            self.open_requested.emit()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        p = self.mapToScene(ev.pos())
        self.moved.emit(p.x(), p.y())
        super().mouseMoveEvent(ev)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, ev):
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)

    # overlay -------------------------------------------------------------
    def clear_overlay(self):
        self._overlay_args = None
        self._label_prev = {}
        self._clear_items()

    def _clear_items(self):
        for it in self.overlay.childItems():
            self.scene().removeItem(it)

    def clear_overlay(self):
        """Drop the overlay items: the rendered preview already carries the annotations."""
        self._clear_items()

    def draw_overlay(self, shapes, named, grid=None, categories=None, show_named=True, shape_style="outline"):
        """New data (a different frame, or a different set of groups): place the labels afresh."""
        self._overlay_args = (shapes, named, grid, categories, show_named, shape_style)
        self._label_prev = {}
        self._draw_overlay(*self._overlay_args)

    def _relayout(self):
        """Labels are laid out in screen pixels, so they are placed again when the zoom changes.

        The placement found last time is tried first: the text keeps the same screen offset from its object,
        so zooming does not make every name jump to a different side. Only a label that would now land on
        another one is placed again."""
        self._place_nav()                          # the bar follows the viewport
        if getattr(self, "_overlay_args", None):
            self._draw_overlay(*self._overlay_args)

    def _draw_overlay(self, shapes, named, grid, categories, show_named, shape_style):
        self._clear_items()
        # The frame's size in *scene* units — the pixels of the picture, not of the pixmap showing it. Zoomed
        # out, the pixmap is a downscaled copy (see _rebuild_pixmap), so its width is a fraction of the image's;
        # taking the bounds from it clamped every label into the top-left corner as soon as the view zoomed.
        W = int(round(self.overlay.rect().width())) or self.pix.pixmap().width()
        H = int(round(self.overlay.rect().height())) or self.pix.pixmap().height()
        s = self.transform().m11() or 1.0
        _fam = style.theme().get("font") or "sans"
        _qfam = {"mono": "Consolas", "serif": "Georgia", "script": "Segoe Script"}.get(_fam, "Segoe UI")
        font = QFont(_qfam, 9, QFont.DemiBold)
        sfont = QFont(_qfam, 9)
        gfont = QFont(_qfam, 8)
        _lw = 1.6 * float(style.theme().get("line_scale") or 1.0)
        gcol = QColor(style.grid_color())
        lcol = QColor(gcol)
        lcol.setAlphaF(style.grid_alpha())
        gpen = QPen(lcol, 1.0)
        gpen.setCosmetic(True)
        grid_boxes = []                                                 # image px; object labels avoid them
        for g in grid or []:
            for seg in g["polylines"]:
                path = QPainterPath(QPointF(*seg[0]))
                for x, y in seg[1:]:
                    path.lineTo(x, y)
                it = QGraphicsPathItem(path)
                it.setPen(gpen)
                it.setParentItem(self.overlay)
            if g.get("label_at"):
                x, y, edge = g["label_at"]
                it = self._label(x, y, g["label"], gcol, gfont)
                r = it.boundingRect()
                ox, oy = {"right": -r.width() - 4}.get(edge, 4), {"bottom": -r.height() - 2}.get(edge, 2)
                it.setPos(ox, oy)                                       # keep the label inside the frame
                grid_boxes.append((x + ox / s, y + oy / s, x + (ox + r.width()) / s, y + (oy + r.height()) / s))
        labels = []
        _shape_mode = style.theme().get("shape_mode") or "outline"
        for e in shapes or []:
            if categories is not None and not (e.get("category") in categories
                                               or e.get("type_group") in categories):
                continue
            col = QColor(style.color_for(e))
            pen = QPen(col, _lw)
            pen.setCosmetic(True)
            if _shape_mode == "x" and e.get("x") is not None:
                r = 9.0
                for (x0, y0, x1, y1) in ((e["x"] - r, e["y"] - r, e["x"] + r, e["y"] + r),
                                         (e["x"] - r, e["y"] + r, e["x"] + r, e["y"] - r)):
                    it = QGraphicsLineItem(x0, y0, x1, y1)
                    it.setPen(pen)
                    it.setParentItem(self.overlay)
                polys, closed = [], False
                anchors = [(e["x"], e["y"], None)] if e.get("label") else []
                text = _ascii_label(e.get("label") or e.get("name") or "")
                if anchors and text:
                    labels.append((0, anchors, text, col, font, 14, None))
                continue
            polys, closed = shapes_mod.display_polygons(e, shape_style)
            for poly in polys:
                if len(poly) < 2:
                    continue
                path = QPainterPath(QPointF(*poly[0]))
                for x, y in poly[1:]:
                    path.lineTo(x, y)
                if closed:
                    path.closeSubpath()
                it = QGraphicsPathItem(path)
                it.setPen(pen)
                it.setParentItem(self.overlay)
            if e.get("kind") == "point" and "x" in e:
                self._marker(e["x"], e["y"], 6, col)
            anchors = style.label_anchors(e, W, H, polys) if e.get("label") else []
            if anchors:
                text = _ascii_label(e.get("label") or e.get("name") or "")
                prio = 0 if text[:1] == "M" and text[1:2].isdigit() else 1
                # a big object's name goes in the middle of its own shape (a huge circle labelled at its rim
                # reads as a label for the sky beside it); small ones keep their label outside
                inside = style.inside_anchor(polys)
                labels.append((prio, anchors, text, col, font,
                               6 if e.get("kind") == "point" else 0, inside))
        for r in (named or []) if show_named else []:
            col = QColor(style.star_color())
            self._marker(r.x, r.y, 7, col)
            name = r.name + (" " + r.extra.get("common") if r.extra.get("common") else "")
            labels.append((2, [(r.x, r.y, None)], name, col, sfont, 7, None))
        # greedy placement in screen pixels: inside a big shape, otherwise outside an outline (or around a
        # point), inside the frame, clear of labels already placed and preferably on a dark background;
        # Messier objects choose first, star names last. A label with nowhere left to go is left out rather
        # than printed across another one.
        placed = list(grid_boxes)
        bright = getattr(self, "_bright", None)
        for _p, anchors, text, col, f, rad, inside in sorted(labels, key=lambda lb: lb[0]):
            it = self._label(0, 0, text, col, f, attach=False)
            bw, bh = it.boundingRect().width(), it.boundingRect().height()
            gap = 5 + rad
            best = None
            ax = ay = bx = by = 0.0
            # put this name back where it was at the previous zoom, if that still fits: a fixed screen offset
            # from the same anchor means the label does not wander while the picture is being zoomed
            prev = self._label_prev.get(text)
            if prev is not None:
                pax, pay, pdx, pdy = prev
                pbx, pby = pax * s + pdx, pay * s + pdy
                if 2 <= pbx <= W * s - bw - 2 and 2 <= pby <= H * s - bh - 2:
                    pbox = (pbx / s, pby / s, (pbx + bw) / s, (pby + bh) / s)
                    if style.choose_box([pbox], placed, None, may_skip=True) is not None:
                        best, ax, ay, bx, by = pbox, pax, pay, pbx, pby
            if best is None and inside is not None:
                cx, cy, iw, ih = inside
                if bw <= 0.6 * iw * s and bh <= 0.6 * ih * s:
                    bx = min(max(cx * s - bw / 2, 2), W * s - bw - 2)
                    by = min(max(cy * s - bh / 2, 2), H * s - bh - 2)
                    box = (bx / s, by / s, (bx + bw) / s, (by + bh) / s)
                    if style.choose_box([box], placed, None, may_skip=True) is not None \
                            and (bright is None or bright(box) < 0.55):
                        best, ax, ay = box, cx, cy
            if best is None:
                boxes = []
                for axx, ayy, outward in anchors:
                    sx, sy = axx * s, ayy * s
                    for ox, oy in style.label_offsets(outward, bw, bh, gap):
                        bx = min(max(sx + ox, 2), W * s - bw - 2)
                        by = min(max(sy + oy, 2), H * s - bh - 2)
                        boxes.append((axx, ayy, bx, by))
                rects = [(bx / s, by / s, (bx + bw) / s, (by + bh) / s)
                         for _ax, _ay, bx, by in boxes]                     # image px
                best = style.choose_box(rects, placed, bright, may_skip=True)
                if best is None:
                    continue                 # crowded patch: a missing name beats two names on top of each other
                # by identity, not by ==: clamped candidates can be equal rectangles at different anchors,
                # and pairing a box with the wrong anchor would make the label jump on the next zoom
                idx = next((i for i, r in enumerate(rects) if r is best), None)
                if idx is None:
                    continue
                ax, ay, bx, by = boxes[idx]
            placed.append(best)
            self._label_prev[text] = (ax, ay, bx - ax * s, by - ay * s)
            anchor = it.parentItem()
            anchor.setParentItem(self.overlay)
            anchor.setPos(ax, ay)
            it.setPos(bx - ax * s, by - ay * s)

    def _marker(self, x, y, r, col):
        it = QGraphicsEllipseItem(-r, -r, 2 * r, 2 * r)
        pen = QPen(col, 1.4)
        pen.setCosmetic(True)
        it.setPen(pen)
        it.setPos(x, y)
        it.setFlag(it.ItemIgnoresTransformations)
        it.setParentItem(self.overlay)

    def _label(self, x, y, text, col, font, dx=4, dy=4, attach=True):
        # An invisible anchor that ignores the view's zoom sits on the object; the text is its child, so the
        # (dx, dy) offset stays in screen pixels at any zoom (an item transform would be scaled with it).
        # attach=False leaves it out of the scene while the placement is measured, so a label that finds no
        # room never flashes at the frame's corner.
        anchor = QGraphicsRectItem(0, 0, 0, 0)
        anchor.setPen(QPen(Qt.NoPen))
        anchor.setFlag(anchor.ItemIgnoresTransformations)
        anchor.setPos(x, y)
        it = _LabelText(text, col, anchor)
        it.setFont(font)
        it.setPos(dx, dy)
        it._anchor = anchor                 # an unparented anchor is Python-owned: keep it alive while measuring
        if attach:
            anchor.setParentItem(self.overlay)
        return it


# ───────────────────────────── dialogs ─────────────────────────────
def _hist_mark_icon():
    """The bar-chart mark beside the histogram panel's title, drawn here so no icon file or glyph is needed."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    col = QColor(C["accent"])
    for x, h in ((4, 7), (10, 16), (16, 23), (22, 12)):
        p.fillRect(QRectF(x, 27 - h, 4, h), col)
    p.end()
    return QIcon(pm)


class FramedDialog(QDialog):
    """A dialog drawn as a card over the window: its own title row and one close button, no system frame.

    The system frame put a second ✕ (and a "?" on Windows) above whatever the dialog itself offered. These
    dialogs draw their own chrome instead: a rounded card with a hairline border, the title and a single ✕, a
    title row that can be dragged to move the dialog, and a resize grip in the bottom-right corner. Esc and
    Enter keep their usual meaning.

    Subclasses put their content in `self.body`; `btn_close` is the one close button.
    """

    def __init__(self, parent=None, title="", icon=None, resizable=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(self._card_sheet())
        self._grab_at = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame()
        self.frame.setObjectName("dlgFrame")
        outer.addWidget(self.frame)
        self._lay = QVBoxLayout(self.frame)
        self._lay.setContentsMargins(SP["6"], SP["6"], SP["6"], SP["5"])
        self._lay.setSpacing(SP["3"])

        head = QHBoxLayout()
        head.setSpacing(SP["3"])
        if icon is not None:
            mark = QLabel()
            mark.setPixmap(icon.pixmap(16, 16))
            mark.setFixedSize(16, 16)
            mark.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            head.addWidget(mark)
        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("color:%s;font-weight:600;font-size:13px" % C["fg1"])
        self.lbl_title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.lbl_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.addWidget(self.lbl_title, 1)
        self.btn_close = QToolButton()
        self.btn_close.setText("✕")
        self.btn_close.setFixedSize(26, 26)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setToolTip(t("关闭"))
        self.btn_close.setStyleSheet("QToolButton{background:transparent;border:none;color:%s;font-size:13px}"
                                     "QToolButton:hover{color:%s;background:%s;border-radius:13px}"
                                     % (C["fg3"], C["danger"], C["elev"]))
        head.addWidget(self.btn_close)
        self._lay.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(SP["3"])
        self._lay.addLayout(self.body, 1)

        if resizable:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addStretch(1)
            grip = QSizeGrip(self.frame)
            grip.setFixedSize(14, 14)
            row.addWidget(grip)
            self._lay.addLayout(row)
        self.btn_close.clicked.connect(self.reject)

    def _card_sheet(self):
        """The panel sheet plus the rules that turn this dialog into a card (rebuilt on a live theme change)."""
        return (PANEL_QSS
                + "#dlgFrame{background:%s;border:1px solid %s;border-radius:10px}"
                  "QDialog{background:transparent}" % (C["surface"], C["border2"]))

    def mousePressEvent(self, ev):
        """Dragging the title row moves the dialog: it has no frame of its own to grab by."""
        if ev.button() == Qt.LeftButton and ev.y() <= 52:
            self._grab_at = ev.globalPos() - self.frameGeometry().topLeft()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._grab_at is not None and (ev.buttons() & Qt.LeftButton):
            self.move(ev.globalPos() - self._grab_at)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._grab_at = None
        super().mouseReleaseEvent(ev)


class HeaderDialog(FramedDialog):
    def __init__(self, img, parent=None):
        super().__init__(parent, "%s · %s" % (t("文件头"), os.path.basename(img.path)))
        self.resize(760, 560)
        rows = []
        if img.fmt == "xisf":
            from .xisfio import XISF
            m = XISF(img.path).get_images_metadata()[0]
            for k, lst in (m.get("FITSKeywords") or {}).items():
                for e in lst:
                    rows.append((k, str(e.get("value", "")), str(e.get("comment", ""))))
            for k, p in (m.get("XISFProperties") or {}).items():
                v = p.get("value")
                if isinstance(v, np.ndarray):
                    v = "%s %s" % (p.get("type"), "x".join(str(s) for s in v.shape))
                rows.append((k, str(v)[:200], p.get("type", "")))
        elif img.fmt == "fits":
            from astropy.io import fits
            with fits.open(img.path, memmap=False) as h:
                hdu = next(x for x in h if getattr(x, "data", None) is not None and x.data.ndim >= 2)
                for c in hdu.header.cards:
                    rows.append((c.keyword, str(c.value), c.comment))
        else:
            rows = [(k, v, "") for k, v in img.keywords.items()]
        tab = QTableWidget(len(rows), 3)
        tab.setHorizontalHeaderLabels([t("关键字"), t("值"), t("说明")])
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                tab.setItem(i, j, QTableWidgetItem(v))
        tab.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        tab.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tab.resizeColumnsToContents()
        self.body.addWidget(tab)


class FixHeaderDialog(FramedDialog):
    """Correct the pointing and optics a FITS file records.

    A frame can arrive with a focal length that belongs to another telescope, and with it a pixel scale that
    makes every solver search the wrong field size (ASTAP then reports that the field is too small for its
    star database); a wide-field rig may record no pointing at all. Both values are what the solve runs on, so
    they are written into the file here. The panel's own hint follows from the file, so it is right afterwards
    without another word.
    """

    def __init__(self, img, parent=None):
        super().__init__(parent, "%s · %s" % (t("修正文件头"), os.path.basename(img.path)))
        self.img = img
        self.written = []
        self.resize(580, 330)
        kw = img.keywords
        hint = imageio.hints(img)
        f = QFormLayout()
        f.setContentsMargins(0, 0, 12, 0)      # keep the fields clear of the scroll bar
        f.setSpacing(SP["3"])
        self.focal = DoubleSpinBox()
        self.focal.setRange(1.0, 10000.0)
        self.focal.setDecimals(1)
        self.focal.setSuffix(" mm")
        self.focal.setValue(_header_float(kw.get("FOCALLEN"), 1.0) or 1.0)
        self.focal.setToolTip(t("镜头的实际焦距；解析器用它算像素尺度和视场"))
        f.addRow(t("焦距"), self.focal)
        self.pix = DoubleSpinBox()
        self.pix.setRange(0.5, 50.0)
        self.pix.setDecimals(3)
        self.pix.setSuffix(" µm")
        self.pix.setValue(_header_float(kw.get("XPIXSZ"), 0.0) or 1.0)
        self.pix.setToolTip(t("传感器像元尺寸（含 Bin）"))
        f.addRow(t("像素尺寸"), self.pix)
        self.ra = QLineEdit()
        self.ra.setPlaceholderText("04 03 20")
        self.dec = QLineEdit()
        self.dec.setPlaceholderText("+36 22 00")
        if hint.get("ra") is not None and hint.get("dec") is not None:
            self.ra.setText(fmt_ra(hint["ra"]))
            self.dec.setText(fmt_dec(hint["dec"]))
        coord = QHBoxLayout()
        coord.setContentsMargins(0, 0, 0, 0)
        coord.setSpacing(SP["3"])
        coord.addWidget(self.ra, 1)
        coord.addWidget(self.dec, 1)
        f.addRow(t("画面中心"), coord)
        self.body.addLayout(f)
        self.all_files = _toggle(self, t("应用到整个文件夹"), False,
                                 t("把同样的值写进这个文件夹里的每一张 FITS"))
        self.count_label = QLabel()
        self.count_label.setObjectName("note")
        self._folder_paths = _folder_fits(os.path.dirname(img.path))
        if len(self._folder_paths) <= 1:
            self.all_files.setEnabled(False)
            self.count_label.setText(t("（这个文件夹里只有这一张）"))
        else:
            self.count_label.setText(t("（共 %d 张 FITS）") % len(self._folder_paths))
        scope = QHBoxLayout()
        scope.setContentsMargins(0, 0, 0, 0)
        scope.setSpacing(SP["3"])
        scope.addWidget(self.all_files)
        scope.addWidget(self.count_label)
        scope.addStretch(1)
        self.body.addLayout(scope)
        self.info = QLabel()
        self.info.setObjectName("note")
        self.info.setWordWrap(True)
        self.body.addWidget(self.info)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton(t("取消"))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_write = QPushButton(t("写入文件"))
        self.btn_write.setStyleSheet(BUTTON_QSS)
        self.btn_write.setDefault(True)
        self.btn_write.clicked.connect(self.accept)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_write)
        self.body.addLayout(row)
        for w in (self.focal, self.pix):
            w.valueChanged.connect(self._update_info)
        for w in (self.ra, self.dec):
            w.textChanged.connect(self._update_info)
        self._update_info()

    def _update_info(self):
        focal, pix = self.focal.value(), self.pix.value()
        bad = False
        if focal > 0 and pix > 0:
            scale = 206.264806 * pix / focal
            self.info.setText("%s %.2f″/px · %s %.2f° × %.2f°" % (
                t("像素尺度"), scale, t("视场"),
                self.img.width * scale / 3600.0, self.img.height * scale / 3600.0))
        else:
            self.info.setText("")
        ra_text, dec_text = self.ra.text().strip(), self.dec.text().strip()
        if ra_text or dec_text:
            bad = parse_radec("%s %s" % (ra_text, dec_text)) is None
        for w in (self.ra, self.dec):
            if bool(w.property("bad")) != bad:
                w.setProperty("bad", bad)
                w.style().unpolish(w)
                w.style().polish(w)

    def target_paths(self):
        """The files this dialog will write: just the one on screen, or every FITS frame beside it."""
        if self.all_files.isChecked():
            return list(self._folder_paths)
        return [self.img.path]

    def accept(self):
        ra_text, dec_text = self.ra.text().strip(), self.dec.text().strip()
        radec = None
        if ra_text or dec_text:
            radec = parse_radec("%s %s" % (ra_text, dec_text))
            if radec is None:
                self.info.setText(t("坐标无法识别（时 分 秒 / 度 分 秒）：") + " " + ra_text + " " + dec_text)
                return
        self.done_paths, self.failed_paths = [], []
        paths = self.target_paths()
        if len(paths) > 1:
            self._write_many(paths, radec)
        else:
            try:
                self.written = imageio.fix_header(
                    self.img.path, focal_mm=self.focal.value(), pix_um=self.pix.value(),
                    ra=radec[0] if radec else None, dec=radec[1] if radec else None)
            except Exception as e:
                QMessageBox.warning(self, "TTSkyAnnot", "%s\n\n%s" % (t("无法写入文件头"), e))
                return
            self.done_paths = [self.img.path]
            self.img.keywords.update(imageio.header_keywords(self.img.path))
        super().accept()

    def _write_many(self, paths, radec):
        """Write the same values into every frame, showing how far along it is and allowing a stop."""
        prog = QProgressDialog(t("正在写入文件头"), t("取消"), 0, len(paths), self)
        prog.setWindowTitle("TTSkyAnnot")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(0)
        state = {"stop": False}

        def on_progress(i, _n, path):
            prog.setValue(i)
            prog.setLabelText("%s\n%s" % (t("正在写入文件头"), os.path.basename(path)))
            QApplication.processEvents()
            if prog.wasCanceled():
                state["stop"] = True

        try:
            done, failed = imageio.fix_header_batch(
                paths, focal_mm=self.focal.value(), pix_um=self.pix.value(),
                ra=radec[0] if radec else None, dec=radec[1] if radec else None,
                progress=on_progress, should_stop=lambda: state["stop"])
        finally:
            prog.close()
        self.done_paths, self.failed_paths = done, failed
        if self.img.path in done:
            self.img.keywords.update(imageio.header_keywords(self.img.path))
        if failed:
            QMessageBox.warning(
                self, "TTSkyAnnot", "%s\n\n%s" % (
                    t("有 %d 张写不进去（见日志）") % len(failed),
                    "\n".join("%s: %s" % (os.path.basename(p), e) for p, e in failed[:6])))



def _header_float(text, default=0.0):
    try:
        return float(str(text).strip().strip("'"))
    except (TypeError, ValueError):
        return default


def _folder_fits(folder):
    """The FITS frames in a folder, in the same order the file list shows them (previews left out)."""
    try:
        names = sorted(f for f in os.listdir(folder)
                       if os.path.splitext(f)[1].lower() in imageio.FITS_EXT
                       and not imageio.preview_name(f))
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names]


class SettingsDialog(FramedDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent, t("设置"))
        self.cfg = dict(cfg)
        self._theme0 = str(cfg.get("theme", "dark"))
        f = QFormLayout()
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(SP["3"])

        def section(text):
            """A caption between groups of settings; QLabel#section brings its own top padding."""
            space = QWidget()
            space.setFixedHeight(6)
            space.setAttribute(Qt.WA_TranslucentBackground)
            f.addRow(space)
            lb = QLabel(t(text))
            lb.setObjectName("section")
            f.addRow(lb)
            return lb

        # ── 界面 ─────────────────────────────────────────────────────────────────────────────
        section("界面")
        self.lang = ComboBox()
        for code, name in (("", t("跟随系统")), ("zh", "中文"), ("en", "English")):
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(cfg.get("language", ""))))
        f.addRow(t("界面语言"), self.lang)
        self.theme_switch = Switch()
        self.theme_switch.setChecked(cfg.get("theme", "dark") == "light")
        self.theme_switch.setToolTip(t("打开为浅色、关闭为深色；切换后立即生效"))
        self.theme_switch.setAccessibleName(t("界面配色"))
        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(0, 0, 0, 0)
        theme_row.setSpacing(SP["3"])
        theme_row.addWidget(self.theme_switch)
        self.lbl_theme = QLabel(t("浅色") if self.theme_switch.isChecked() else t("深色"))
        self.lbl_theme.setObjectName("value")
        theme_row.addWidget(self.lbl_theme)
        theme_row.addStretch(1)
        self.theme_switch.toggled.connect(self._theme_chosen)
        f.addRow(t("界面配色"), theme_row)
        self.chk_confirm_del = _toggle(self, t("删除图像前先确认"), bool(cfg.get("confirm_delete", True)))
        self.chk_confirm_del.setStyleSheet(TOGGLE_QSS)
        self.chk_confirm_del.setToolTip(t("关掉后按删除键就直接移入回收站（仍可撤销）"))
        row_del = QHBoxLayout()
        row_del.setContentsMargins(0, 0, 0, 0)
        row_del.addWidget(self.chk_confirm_del)
        row_del.addStretch(1)
        f.addRow(t("删除确认"), row_del)

        # ── 解析 ─────────────────────────────────────────────────────────────────────────────
        section("解析")
        self.chk_existing = _toggle(self, t("复用文件头里已有的解"), bool(cfg.get("use_existing", True)))
        self.chk_existing.setStyleSheet(TOGGLE_QSS)
        self.chk_existing.setToolTip(t("文件里已经带天体测量解时（ASIAIR/NINA/PixInsight 都会写），"
                                       "先和画面里的星点核对，对得上就直接用，省掉整轮解析。"))
        row_ex = QHBoxLayout()
        row_ex.setContentsMargins(0, 0, 0, 0)
        row_ex.addWidget(self.chk_existing)
        row_ex.addStretch(1)
        f.addRow(t("已有解"), row_ex)
        self.chk_debayer = _toggle(self, t("自动解拜耳单次彩色图像"), bool(cfg.get("debayer", True)))
        self.chk_debayer.setStyleSheet(TOGGLE_QSS)
        self.chk_debayer.setToolTip(t("单次彩色相机的原始帧是单通道的马赛克；开启后自动插值成彩色再显示。"
                                      "关掉可查看原始马赛克数据。"))
        row_db = QHBoxLayout()
        row_db.setContentsMargins(0, 0, 0, 0)
        row_db.addWidget(self.chk_debayer)
        row_db.addStretch(1)
        f.addRow(t("解拜耳"), row_db)
        self.apikey = QLineEdit(cfg.get("api_key", ""))
        self.apikey.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.apikey.setPlaceholderText(t("ttsky_…（点「领取密钥」免费获取）"))
        self.apikey.setToolTip(t("在线解析密钥（ttastro）。留空只能用很少的匿名次数；"
                                 "在 ttastro.com/ttskyannot/key 可以免费领一个，填进这里即可。"))
        b_key = QPushButton(t("领取密钥…"))
        b_key.setToolTip(t("在浏览器中打开领取页面（免费）"))
        b_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KEYS_URL)))
        row_key = QHBoxLayout()
        row_key.setContentsMargins(0, 0, 0, 0)
        row_key.addWidget(self.apikey)
        row_key.addWidget(b_key)
        f.addRow(t("在线解析密钥"), row_key)
        self.astrokey = QLineEdit(cfg.get("astrometry_key", ""))
        self.astrokey.setEchoMode(QLineEdit.Password)
        self.astrokey.setPlaceholderText(t("可留空；填写后优先使用你自己的 nova 额度"))
        f.addRow(t("astrometry.net 密钥"), self.astrokey)
        self.astap = QLineEdit(cfg.get("astap_path", ""))
        b = QPushButton(t("浏览…"))
        b.clicked.connect(lambda: self._browse(self.astap, file=True))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.astap)
        row.addWidget(b)
        f.addRow(t("ASTAP 路径"), row)

        # ── 星表与天体库 ─────────────────────────────────────────────────────────────────────
        section("星表")
        self.gmag = DoubleSpinBox()
        self.gmag.setRange(8, 21)
        self.gmag.setDecimals(1)
        self.gmag.setValue(float(cfg.get("gaia_mag", 16)))
        f.addRow(t("Gaia 星等上限"), self.gmag)
        # ── 输出 ─────────────────────────────────────────────────────────────────────────────
        section("输出")
        self.out = QLineEdit(cfg.get("out_dir", ""))
        b2 = QPushButton(t("浏览…"))
        b2.clicked.connect(lambda: self._browse(self.out, file=False))
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.addWidget(self.out)
        row2.addWidget(b2)
        f.addRow(t("输出位置"), row2)

        # ── 查看与打开 ──────────────────────────────────────────────────────────────────────
        section("查看与打开")
        self.slide = DoubleSpinBox()
        self.slide.setRange(0.3, 60.0)
        self.slide.setDecimals(1)
        self.slide.setSingleStep(0.5)
        self.slide.setSuffix(t(" 秒"))
        self.slide.setValue(float(cfg.get("slide_interval", 2.0)))
        f.addRow(t("自动播放间隔"), self.slide)
        self.pi = QLineEdit(cfg.get("pixinsight_path", ""))
        found_pi = piopen.find()
        self.pi.setPlaceholderText(t("留空 = 自动查找") + ("：" + found_pi if found_pi else ""))
        self.pi.setToolTip(t("留空即可：程序会从注册表和各硬盘的常见位置自动找到 PixInsight；"
                             "只有装在很特别的位置时才需要手动指定。"))
        b_pi = QPushButton(t("浏览…"))
        b_pi.clicked.connect(lambda: self._browse(self.pi, file=True))
        b_detect = QPushButton(t("自动检测"))
        b_detect.setToolTip(t("现在就让程序找一次，并把找到的路径填进来"))
        b_detect.clicked.connect(self._detect_pixinsight)
        row_pi = QHBoxLayout()
        row_pi.setContentsMargins(0, 0, 0, 0)
        row_pi.addWidget(self.pi)
        row_pi.addWidget(b_detect)
        row_pi.addWidget(b_pi)
        f.addRow(t("PixInsight 路径"), row_pi)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(t("确定"))
        bb.button(QDialogButtonBox.Cancel).setText(t("取消"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        # six groups are taller than a 768-pixel screen: scroll the form, keep the buttons in reach
        # The column paints the card colour itself: a translucent one left the form see-through, so the
        # picture behind the main window showed straight through the dialog.
        inner = QWidget()
        inner.setObjectName("settingsInner")
        inner.setStyleSheet("QWidget#settingsInner{background:%s}" % C["surface"])
        inner.setLayout(f)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        scroll.setMinimumHeight(560)
        scroll.setMaximumHeight(640)
        scroll.setMinimumWidth(540)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none}"
            "QScrollBar:vertical{width:8px;background:transparent;margin:2px 0 2px 0}"
            "QScrollBar::handle:vertical{background:%s;border-radius:4px;min-height:30px}"
            "QScrollBar::handle:vertical:hover{background:%s}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;width:0}"
            "QScrollBar::add-page,QScrollBar::sub-page{background:transparent}"
            % ("#3a4750" if THEME == "dark" else "#C9D2DA",
               "#55656f" if THEME == "dark" else "#B3BFC9"))
        scroll.viewport().setAutoFillBackground(False)
        self.body.addWidget(scroll, 1)
        self.body.addWidget(bb)

    def _detect_pixinsight(self):
        """Fill the field with what auto-detection finds, so nobody has to hunt for the executable."""
        path = piopen.find()
        if path:
            self.pi.setText(path)
            return
        QMessageBox.information(self, "TTSkyAnnot",
                                t("没有找到 PixInsight，请用「浏览…」手动选择它的可执行文件。"))

    def _browse(self, edit, file):
        p = (QFileDialog.getOpenFileName(self, "", edit.text())[0] if file
             else QFileDialog.getExistingDirectory(self, "", edit.text()))
        if p:
            edit.setText(p)

    def values(self):
        self.cfg.update(language=self.lang.currentData(), theme=self.theme_name(),
                        api_key=self.apikey.text().strip(),
                        astrometry_key=self.astrokey.text().strip(),
                        astap_path=self.astap.text().strip(), gaia_mag=self.gmag.value(),
                        out_dir=self.out.text().strip(), slide_interval=self.slide.value(),
                        pixinsight_path=self.pi.text().strip(),
                        confirm_delete=self.chk_confirm_del.isChecked(),
                        debayer=self.chk_debayer.isChecked(),
                        use_existing=self.chk_existing.isChecked())
        return self.cfg

    def theme_name(self):
        """The colour scheme the switch shows: "light" when it is on, "dark" when it is off."""
        return "light" if self.theme_switch.isChecked() else "dark"

    def _theme_chosen(self, on=False):
        """Write the colour scheme the moment the switch is flipped.

        The window itself is restyled when this dialog closes: re-applying a stylesheet to a *visible* modal
        card leaves its labels unpainted, so the switch is applied to the window right after, in settings().
        """
        theme = "light" if on else "dark"
        self.lbl_theme.setText(t("浅色") if on else t("深色"))
        if theme == self._theme0:
            return
        cfg = config.load()
        cfg["theme"] = theme
        if not config.save(cfg) and not getattr(self, "_save_warned", False):
            self._save_warned = True                 # say it once per dialog, not on every flip
            QMessageBox.warning(self, "TTSkyAnnot",
                                t("设置文件写入失败：%s\n配色等选项可能在下次启动时不生效。") % config._path())


class RegionLookup(QThread):
    """Ask the online database for the objects inside a solved field, off the GUI thread.

    A frame whose header already carries a solution never goes through solve(), so this is the only way its
    online catalogues can be annotated. It runs after the picture is on screen: a slow or dead database costs
    a moment of labels, not a moment of viewing.
    """

    found = pyqtSignal(str, object, list)         # path, cache key, records

    def __init__(self, path, key, ra, dec, radius, cats, base, parent=None):
        super().__init__(parent)
        self.path, self.key = path, key
        self.ra, self.dec, self.radius = ra, dec, radius
        self.cats, self.base = cats, base

    def run(self):
        try:
            recs = run.online_records_cone(self.ra, self.dec, self.radius, self.cats, self.base)
        except Exception:
            recs = []
        self.found.emit(self.path, self.key, recs)


class SaveDialog(FramedDialog):
    """Asked before anything is written: solving on its own produces no files, and this is where the user
    says what should land in the folder. Every output can be switched off here, so a solve can be looked at
    and thrown away."""

    def __init__(self, path, items, defaults=None, parent=None, start_dir=""):
        super().__init__(parent, t("保存解析结果"))
        self.setMinimumWidth(520)
        defaults = defaults or {}
        self._items = list(items) if items else [path]
        self._path = path
        lay = self.body
        lay.setContentsMargins(SP["3"], 0, SP["3"], 0)
        lay.setSpacing(SP["3"])

        head = QLabel(os.path.basename(path))
        head.setWordWrap(True)
        head.setStyleSheet("QLabel{font-size:15px;font-weight:600;color:%s}" % C["fg1"])
        lay.addWidget(head)
        lay.addSpacing(SP["2"])

        # what will be written: every target, so the choice below is concrete
        self.where = QLabel("\n".join(self._items))
        self.where.setObjectName("note")
        self.where.setWordWrap(True)
        lay.addWidget(self.where)

        # and where it goes: next to the picture by default, or a folder chosen here for this save
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SP["3"])
        row.addWidget(QLabel(t("保存到")))
        self.ed_dir = QLineEdit(start_dir or os.path.dirname(self._items[0]))
        self.ed_dir.setToolTip(t("默认写在图片旁边；可以改成别的文件夹，目录不存在时会自动创建"))
        self.ed_dir.textChanged.connect(self._dir_changed)
        row.addWidget(self.ed_dir, 1)
        btn_dir = QPushButton(t("浏览…"))
        btn_dir.setCursor(Qt.PointingHandCursor)
        btn_dir.clicked.connect(self._pick_dir)
        row.addWidget(btn_dir)
        lay.addLayout(row)
        lay.addSpacing(SP["5"])

        # A raster picture has no header to write into: the solution becomes a sidecar .wcs beside it. The
        # option says which of the two it is, and neither is ticked by writing into the picture until asked.
        sidecar = imageio.needs_sidecar(path)
        wcs_text = t("解析结果另存 .wcs") if sidecar else t("解析结果写回图片")
        wcs_tip = (t("JPG/PNG/TIFF 没有可写的天球头，解析结果会另存为同名 .wcs 文件，图片本身不动")
                   if sidecar else t("把天球解写进原文件的文件头（像素不动），仍属于修改原文件，默认不勾"))
        self.chk = {}
        for key, text in (("txt", "天体列表 TXT"), ("outlines", "轮廓坐标文件"),
                          ("preview", "标注图"), ("wcs", wcs_text)):
            c = _toggle(self, t(text), bool(defaults.get(key, key != "wcs")))
            c.setStyleSheet(TOGGLE_QSS)
            c.setToolTip(wcs_tip if key == "wcs" else (items[0] if items else ""))
            lay.addWidget(c, 0, Qt.AlignLeft)
            self.chk[key] = c

        lay.addSpacing(SP["5"])
        hint = QLabel(t("不勾就不会写任何文件；「写回图片」会修改原文件的文件头（像素不动）")
                      if not sidecar else
                      t("不勾就不会写任何文件；这里只生成 .wcs 文本文件，不会改动图片"))
        hint.setObjectName("note")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        lay.addSpacing(SP["4"])
        # a plain check box, not one of the pills above: this is a standing preference rather than another
        # thing to write, and it carries its own indicator styling because Qt draws the box itself
        self.remember = QCheckBox(t("以后都按这次的选择保存"))
        self.remember.setToolTip(t("批量处理时不再逐个询问"))
        self.remember.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.remember)

        lay.addSpacing(SP["5"])
        bb = QDialogButtonBox()
        self.btn_save = bb.addButton(t("保存"), QDialogButtonBox.AcceptRole)
        bb.addButton(t("不保存"), QDialogButtonBox.RejectRole)
        btn_cancel = bb.addButton(t("取消处理"), QDialogButtonBox.DestructiveRole)
        self.btn_save.setDefault(True)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setStyleSheet(BUTTON_QSS)          # the one filled button: the intended action
        for b in bb.buttons():
            b.setCursor(Qt.PointingHandCursor)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        btn_cancel.clicked.connect(self._cancel_run)
        self.cancelled = False
        lay.addWidget(bb)

    def _dir_changed(self, text):
        """Show the targets the way they would be written with the folder now in the field."""
        folder = text.strip() or os.path.dirname(self._items[0])
        self.where.setText("\n".join(os.path.join(folder, os.path.basename(p)) for p in self._items))

    def _pick_dir(self):
        folder = QFileDialog.getExistingDirectory(self, t("选择保存目录"),
                                                  self.ed_dir.text().strip() or os.path.dirname(self._path))
        if folder:
            self.ed_dir.setText(folder)

    def _cancel_run(self):
        self.cancelled = True
        self.reject()

    def flags(self):
        """What to write, plus the two out-of-band signals the worker understands."""
        out = {k: c.isChecked() for k, c in self.chk.items()}
        out["_remember"] = self.remember.isChecked()
        out["_cancel"] = self.cancelled
        out["_out_dir"] = self.ed_dir.text().strip()
        return out


class RemoteLookup(QThread):
    """Ask the tickwhale database for objects matching what is being typed.

    The bundled catalogues are OpenNGC plus Sharpless, which carry no Lynds nebulae and other deep
    catalogues; the server has them. Doing it on a worker keeps a slow or dead server from stalling typing,
    and a reply that is no longer about the text in the field is thrown away.
    """
    found = pyqtSignal(str, list)            # the text it was asked about, and the records

    def __init__(self, parent=None):
        super().__init__(parent)
        self._query = ""
        self._answer = None

    def ask(self, text, base):
        self._query = text
        self._answer = None
        self._base = base
        if self.isRunning():
            return                            # the running worker picks the newer text up when it finishes
        self.start()

    def run(self):
        while True:
            text, base = self._query, self._base
            recs = tickwhale.search(text, base) if base else []
            if self._query == text:           # nothing newer was typed while the server was answering
                self.found.emit(text, recs)
                return


class StatsTable(QWidget):
    """The statistics beside the plot: a colour chip for each channel, then Max / Min / Avg / Std.

    ASIFitsView lays the same numbers out the same way, in 16-bit counts. The chips are what makes the three
    columns readable at a glance: the eye matches a colour to a curve faster than it reads an "R".
    """

    ROWS = (("Max", "max"), ("Min", "min"), ("Avg", "avg"), ("Std", "std"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QGridLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setHorizontalSpacing(SP["5"])
        self._lay.setVerticalSpacing(SP["5"])
        self._values = {}
        self._keys = ()
        self._build(("r", "g", "b"))

    def _build(self, keys) -> None:
        """One column per channel that the frame actually has (red/green/blue, or luminance for mono)."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._values = {}
        self._keys = tuple(keys)
        title = QLabel(t("统计数据"))
        title.setObjectName("note")
        self._lay.addWidget(title, 0, 0)
        mono = "font-family:'Consolas','JetBrains Mono',monospace;font-size:12px"
        for i, key in enumerate(keys):
            chip = QLabel()
            chip.setFixedSize(15, 15)
            chip.setStyleSheet("background:%s;border-radius:4px" % histview.CH_COLORS.get(key, "#c8d2dc"))
            self._lay.addWidget(chip, 0, i + 1, Qt.AlignHCenter)
            for row, (_name, field) in enumerate(self.ROWS, start=1):
                lab = QLabel("—")
                lab.setObjectName("value")
                lab.setStyleSheet(mono)
                lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._lay.addWidget(lab, row, i + 1)
                self._values[(key, field)] = lab
        for row, (name, _field) in enumerate(self.ROWS, start=1):
            lab = QLabel(name)
            lab.setObjectName("note")
            self._lay.addWidget(lab, row, 0)
        for i in range(1, len(keys) + 1):
            self._lay.setColumnStretch(i, 1)
        self.setMinimumWidth(235 if len(keys) > 1 else 150)

    def set_stats(self, stats: dict) -> None:
        keys = tuple(k for k in ("r", "g", "b") if stats and k in stats)
        if not keys:
            keys = ("l",) if stats and "l" in stats else ()
        if keys and keys != self._keys:
            self._build(keys)
        for key in self._keys:
            s = stats.get(key) if stats else None
            for _name, field in self.ROWS:
                lab = self._values.get((key, field))
                if lab is not None:
                    lab.setText("%d" % int(round(float(s[field]) * hist_mod.ADU)) if s else "—")


class HistogramDialog(FramedDialog):
    """The histogram panel: the curve, the draggable levels, and the statistics beside them.

    Follows ASIFitsView's panel in what it offers — auto white balance, auto stretch, reset, and a magnifier
    that turns a drag across the plot into a zoom — because those are what one actually does while looking at
    a picture. It changes the display only; the file is never rewritten.

    `raw` is the frame as loaded and `balance` the white balance already applied to it (None or an identity
    Balance when there is none). The panel shows the balanced frame, which is the data the levels act on, so
    the black and white markers it draws are the ones the picture on screen was stretched with.
    """

    changed = pyqtSignal()               # the levels or the balance moved: the preview should follow

    def __init__(self, parent=None, raw=None, balance=None, levels=None, data=None, readout=None):
        super().__init__(parent, t("直方图"), icon=_hist_mark_icon())
        self.resize(920, 430)
        self._raw = np.asarray(raw if raw is not None else data, np.float32)
        self._balance = balance or imageio.Balance()
        self._balance0 = self._balance
        self._base_cache = None
        self._levels = levels or hist_mod.auto_levels(self._base_data())
        self._levels0 = self._levels
        self._readout = readout or {}

        lay = self.body
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SP["4"])

        top = QHBoxLayout()
        top.setSpacing(SP["3"])
        self.btn_awb = QPushButton(t("自动白平衡"))
        self.btn_auto = QPushButton(t("自动拉伸"))
        self.btn_reset = QPushButton(t("重置拉伸"))
        accent = ("QPushButton{background:%s;color:%s;border:none;border-radius:4px;padding:6px 12px;"
                  "font-weight:600}QPushButton:hover{background:%s}QPushButton:pressed{background:%s}"
                  "QPushButton:disabled{background:%s;color:%s}"
                  % (C["accent"], C["on_accent"], C["accent_press"], C["accent_press"],
                     C["surface3"], C["disabled"]))
        for b in (self.btn_awb, self.btn_auto):
            b.setStyleSheet(accent)
            b.setCursor(Qt.PointingHandCursor)
            top.addWidget(b)
        # a single-channel frame has nothing to balance against: leave the button out of reach rather than
        # letting it look usable
        self.btn_awb.setEnabled(self._raw.ndim >= 3 and self._raw.shape[2] >= 3)
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        top.addWidget(self.btn_reset)
        self.btn_zoom = QToolButton()
        self.btn_zoom.setCheckable(True)
        self.btn_zoom.setFixedSize(28, 28)
        self.btn_zoom.setCursor(Qt.PointingHandCursor)
        self.btn_zoom.setToolTip(t("放大镜：在直方图上拖一个框放大；滚轮缩放，拖动平移，双击复位"))
        self.btn_zoom.setStyleSheet("QToolButton{background:%s;border:1px solid %s;border-radius:14px}"
                                    "QToolButton:hover{border-color:%s}"
                                    "QToolButton:checked{background:%s;border-color:%s}"
                                    % (C["surface2"], C["border2"], C["fg3"], C["accent_tint2"], C["accent"]))
        self.btn_zoom.setIcon(self._zoom_icon())
        self.btn_zoom.setIconSize(QSize(16, 16))
        top.addWidget(self.btn_zoom)
        top.addStretch(1)
        lay.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(SP["5"])
        self.view = histview.HistogramView(self)
        self.view.set_theme(C)
        self.view.setToolTip(t("滚轮缩放 · 拖动平移 · 双击复位；拖动黑点／白点／中间调改变拉伸"))
        body.addWidget(self.view, 1)
        self.stats = StatsTable(self)
        body.addWidget(self.stats, 0)
        lay.addLayout(body, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(SP["4"])
        self.lbl_hover = QLabel("")
        self.lbl_hover.setObjectName("note")
        bottom.addWidget(self.lbl_hover, 1)
        self.lbl_vals = QLabel("")
        self.lbl_vals.setObjectName("note")
        bottom.addWidget(self.lbl_vals, 0)
        lay.addLayout(bottom)

        self.btn_awb.clicked.connect(self._auto_white_balance)
        self.btn_auto.clicked.connect(self._auto_stretch)
        self.btn_reset.clicked.connect(self._reset)
        self.btn_zoom.toggled.connect(self._zoom_toggled)
        self.view.levels_changed.connect(self._changed)
        self.view.hovered.connect(self._hovered)
        self.view.left.connect(self._hover_left)
        self.lbl_hover.setText(self._hint())
        self._refresh()

    HINT_TEXT = "滚轮缩放 · 拖动平移 · 双击复位；拖动黑点／白点／中间调改变拉伸"

    def _hint(self):
        return t(self.HINT_TEXT)

    # ---- data ----------------------------------------------------------
    def _base_data(self):
        """The balanced frame the levels act on — the same array the preview is built from.

        Kept once: balancing a 26-megapixel frame copies 300 MB, and the panel asks for it several times.
        """
        if self._base_cache is None:
            if self._balance is None or self._balance.is_identity:
                self._base_cache = self._raw
            else:
                self._base_cache = self._balance.apply(self._raw)
        return self._base_cache

    def _rebuild(self, reset_range: bool = True, levels: hist_mod.Levels = None):
        """Recompute everything that depends on the data: the plot's sample and the statistics."""
        if levels is not None:
            self._levels = levels
        base = self._base_data()
        self._sample = base[::3, ::3]
        self._stats = hist_mod.stats(base)
        self.view.set_sample(self._sample, self._levels, reset_range=reset_range)
        self.stats.set_stats(self._stats)
        self._changed()

    def levels(self):
        return self._levels

    def balance(self):
        return self._balance

    def _refresh(self):
        if self._raw is None or not self._raw.size:
            return
        self._rebuild(reset_range=True)

    def _hist_icon(self):
        """Kept for callers of the old name: the bar-chart mark beside the panel's title."""
        return _hist_mark_icon()

    def _zoom_icon(self):
        """A magnifier drawn here rather than borrowed from the theme font: the glyph is not in every font."""
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(C["accent"] if self.btn_zoom.isChecked() else C["fg2"]) \
            if hasattr(self, "btn_zoom") else QColor(C["fg2"])
        p.setPen(QPen(col, 2.0))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(5, 5, 15, 15))
        p.drawLine(QPointF(18.5, 18.5), QPointF(26, 26))
        p.end()
        return QIcon(pm)

    def _zoom_toggled(self, on):
        self.view.set_zoom_tool(on)
        self.btn_zoom.setIcon(self._zoom_icon())

    def _changed(self):
        lv = self._levels
        self.lbl_vals.setText("%s %d    %s %.3f    %s %d" % (
            t("黑点"), int(round(lv.black * hist_mod.ADU)), t("中间调"), lv.mid,
            t("白点"), int(round(lv.white * hist_mod.ADU))))
        self._emit_live()

    def _emit_live(self):
        """拖动时也把变化发给预览：70ms 节流，避免每个鼠标事件都重算整帧。"""
        timer = getattr(self, "_live_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(70)
            timer.timeout.connect(self.changed.emit)
            self._live_timer = timer
        timer.start()

    def _hovered(self, value, counts):
        parts = []
        for key, name in (("r", "R"), ("g", "G"), ("b", "B")):
            if key in counts:
                parts.append("%s %s" % (name, histview._fmt_count(counts[key])))
        self.lbl_hover.setText("%d  %s" % (int(round(value * hist_mod.ADU)), "   ".join(parts)))

    def _hover_left(self):
        self.lbl_hover.setText(self._hint())

    # ---- actions -------------------------------------------------------
    def _auto_stretch(self):
        if self._raw is None or not self._raw.size:
            return
        self._rebuild(levels=hist_mod.auto_levels(self._base_data()))
        self.changed.emit()

    def _auto_white_balance(self):
        """ASIFitsView's white balance on the frame as loaded, then its auto stretch."""
        if self._raw is None or self._raw.ndim < 3 or self._raw.shape[2] < 3:
            return
        self._balance = imageio.awb_balance(self._raw)
        self._base_cache = None
        base = self._base_data()
        self._rebuild(levels=hist_mod.auto_levels(base))
        self.changed.emit()

    def _reset(self):
        self._balance = self._balance0
        self._base_cache = None
        self._rebuild(levels=self._levels0)
        self.changed.emit()


class _UpdateCheck(QThread):
    """Reads the published feed off the GUI thread: a network share can be slow, or gone."""

    done = pyqtSignal(object, str)                 # feed or None, error text

    def __init__(self, base, parent=None):
        super().__init__(parent)
        self.base = base

    def run(self):
        try:
            self.done.emit(updater.fetch_feed(self.base), "")
        except Exception as e:
            self.done.emit(None, str(e))


class _UpdateDownload(QThread):
    """Downloads the new build off the GUI thread, reporting bytes and the result.

    An installed copy downloads the installer and hands over to it; a portable one downloads the zip and
    unpacks it for the swap batch. `done` carries the unpacked folder in the first case, the setup's path in
    the second.
    """

    progress = pyqtSignal(int, int)                # bytes done, bytes total (0 when unknown)
    done = pyqtSignal(str, str)                    # folder / installer path, or "", error text

    def __init__(self, base, feed, mode="zip", parent=None):
        super().__init__(parent)
        self.base, self.feed, self.mode = base, feed, mode
        self.stop = False

    def run(self):
        try:
            folder = tempfile.mkdtemp(prefix="ttskyannot-pkg-")
            if self.mode == "setup":
                path = updater.download_setup(
                    self.base, self.feed, folder,
                    progress=lambda done, total: self.progress.emit(done, total),
                    should_stop=lambda: self.stop)
                self.done.emit(path, "")
                return
            try:
                zip_path = updater.download(
                    self.base, self.feed, folder,
                    progress=lambda done, total: self.progress.emit(done, total),
                    should_stop=lambda: self.stop)
                unpacked = updater.unpack(zip_path)
            finally:
                # 下载目录（118 MB 的 zip）用完就删；解包目录要留给交换脚本，由下次启动的 sweep_temp 清理
                try:
                    import shutil as _sh, os as _os
                    if self.mode != "setup":
                        _sh.rmtree(folder, ignore_errors=True)
                    else:
                        for _n in _os.listdir(folder):
                            _os.remove(_os.path.join(folder, _n))
                        _os.rmdir(folder)
                except Exception:
                    pass
            self.done.emit(unpacked, "")
        except Exception as e:
            self.done.emit("", str(e))


class UpdateDialog(FramedDialog):
    """What the feed says, and the two answers that matter: later, or fetch it and restart into it."""

    def __init__(self, feed, base, parent=None):
        super().__init__(parent, t("软件更新"))
        self.feed, self.base = feed, base
        self.skipped = False
        self._worker = None
        # An installed copy is upgraded by the installer (Start Menu entry, uninstall record and shortcuts all
        # stay right); a portable folder is swapped by the batch. Ask the program folder, not a setting.
        self.mode = "setup" if (updater.is_installed() and updater.has_setup(feed)) else "zip"
        self.resize(580, 400)
        head = QLabel("%s %s  →  %s" % (t("当前版本"), __version__, feed.get("version", "")))
        head.setObjectName("value")
        self.body.addWidget(head)
        size = int((feed.get("setup_size") if self.mode == "setup" else feed.get("size")) or 0)
        if size:
            self.body.addWidget(QLabel("%s: %.1f MB" % (t("下载大小"), size / 1048576.0)))
        if self.mode == "setup":
            hint = QLabel(t("下载完成后程序会关闭，随后打开安装程序，按向导完成更新。"))
            hint.setObjectName("note")
            hint.setWordWrap(True)
            self.body.addWidget(hint)
        notes = QPlainTextEdit()
        notes.setPlainText(str(feed.get("notes") or "").strip())
        notes.setReadOnly(True)
        notes.setMinimumHeight(150)
        # 更新说明是可读文本，不是日志：字号按正文给，别用全局的最小等宽字号
        font = notes.font()
        font.setPointSize(max(11, font.pointSize() + 3))
        notes.setFont(font)
        notes.setStyleSheet("QPlainTextEdit{font-size:%dpt;line-height:150%%}" % max(11, font.pointSize()))
        self.body.addWidget(notes, 1)
        self.speed = QLabel()
        self.speed.setObjectName("note")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        self.speed.setVisible(False)
        self.body.addWidget(self.bar)
        self.body.addWidget(self.speed)
        self.skip_box = _toggle(self, t("跳过这个版本"), False,
                                t("下次启动不再提示这个版本；仍可用状态栏的「检查更新」手动检查"))
        self.body.addWidget(self.skip_box)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_later = QPushButton(t("稍后"))
        self.btn_later.clicked.connect(self.reject)
        self.btn_go = QPushButton(t("立即更新"))
        self.btn_go.setStyleSheet(BUTTON_QSS)
        self.btn_go.setDefault(True)
        self.btn_go.clicked.connect(self._start)
        row.addWidget(self.btn_later)
        row.addWidget(self.btn_go)
        self.body.addLayout(row)
        if not updater.app_dir():
            self.btn_go.setEnabled(False)
            self.btn_go.setToolTip(t("只有打包后的程序才能替换自己。"))

    def reject(self):
        self.skipped = self.skip_box.isChecked()
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop = True
            self._worker.wait(3000)
        super().reject()

    def _start(self):
        if not updater.app_dir():
            QMessageBox.information(self, "TTSkyAnnot", t("只有打包后的程序才能自动更新。"))
            return
        self.btn_go.setEnabled(False)
        self.btn_later.setEnabled(False)
        self.skip_box.setEnabled(False)
        self.bar.setVisible(True)
        self.speed.setVisible(True)
        self.bar.setRange(0, 0)
        config._diag("update: download start %r" % (self.feed.get("version"),))
        self._worker = _UpdateDownload(self.base, self.feed, self.mode, self)
        self._worker.progress.connect(self._progress)
        self._worker.done.connect(self._ready)
        self._worker.start()

    def _progress(self, done, total):
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000.0 * done / total))
            self.speed.setText("%.1f / %.1f MB" % (done / 1048576.0, total / 1048576.0))
        else:
            self.speed.setText("%.1f MB" % (done / 1048576.0))

    def _ready(self, folder, error):
        self.btn_later.setEnabled(True)
        config._diag("update: ready folder=%r error=%r" % (folder, error))
        if error:
            self.bar.setVisible(False)
            self.speed.setVisible(False)
            self.btn_go.setEnabled(True)
            QMessageBox.warning(self, "TTSkyAnnot", "%s\n\n%s" % (t("更新失败"), error))
            return
        try:
            if self.mode == "setup":
                updater.launch_setup(folder, os.getpid())
            else:
                updater.install_and_restart(folder, updater.app_dir(), os.getpid())
        except Exception as e:
            QMessageBox.warning(self, "TTSkyAnnot", "%s\n\n%s" % (t("无法开始更新"), e))
            return
        self.accept()
        if self.mode == "setup":
            # give the waiting script a moment to see this process go away, then close the window
            QTimer.singleShot(250, QApplication.quit)
        else:
            QApplication.quit()


# ───────────────────────────── main window ─────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self, cfg, files=None):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("%s %s" % (t("TTSkyAnnot · 星图标注"), __version__))
        # Fit the screen the window opens on: 1400x900 used to run under the taskbar on shorter displays.
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        width, height = 1400, 940
        if avail is not None:
            width = max(1000, min(width, avail.width() - 40))
            height = max(620, min(height, avail.height() - 40))
        self.resize(width, height)
        if avail is not None:
            self.move(avail.x() + max(0, (avail.width() - width) // 2),
                      avail.y() + max(0, (avail.height() - height) // 2))
        self.img = None
        self.wcs = None
        self.current = None
        self.loader = None
        self.worker = None
        self.results = {}           # path -> Result
        self.overlay_data = {}      # path -> (shapes, named, grid)
        self.overlay_online = {}    # path -> online records the region query returned
        self._region_cache = {}     # (ra, dec, radius) -> records, so a folder of frames asks once
        self._region_lookup = None
        self._region_next = None
        self._key_offer_shown = False   # the "where do I get a key" box is offered once per session
        self._folder = None         # folder the left-hand list is showing
        self._folder_dirty = False  # a file was removed or moved: rebuild the list even for the same folder
        self._previews = {}         # folder -> (names as listed, names worth listing: previews dropped)
        self._scan = None           # the FolderScan currently reading the folder's picture sizes
        self._scan_key = None
        self._file_guard = False    # suppress list signals while the list is rebuilt
        self._path = None           # file a right-click on a list row refers to
        self._target_radec = ()     # position of the object picked in the object-name field, if any
        self._target_obj = None     # the online record behind that name, so the object can be annotated
        self._unsaved = set()       # paths solved but not written to disk yet
        self._loaded_path = None    # file whose picture is on screen (re-reading it cannot keep the view)
        self._view_state = None     # zoom/position captured by show_image, applied by _loaded
        self._solved_reload = False # this reload follows a solve: the frame itself is not changing
        self._preview_pix = None    # set when the viewer shows a rendered theme preview instead of the photo
        self._slide_timer = QTimer(self)          # automatic playback: one frame per timeout
        self._slide_timer.setSingleShot(True)
        self._slide_timer.timeout.connect(self._slide_tick)
        self._undo_action = None                  # what Ctrl+Z would undo, cleared after a few seconds
        self._undo_message = ""
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.timeout.connect(self._expire_undo)
        self._advancing = False                   # guards the step() that follows a removal

        self.view = ImageView()
        self.view.set_placeholder(True)
        self.view.moved.connect(self._on_move)
        self.view.files_dropped.connect(self.add_files)
        self.view.zoom_changed.connect(lambda s: self.lbl_zoom.setText("%.0f%%" % (s * 100)))
        self.view.open_requested.connect(self.open_dialog)
        self.view.step_requested.connect(self.step)
        self.setCentralWidget(self.view)
        self._build_image_bar()
        self._build_list_panel()        # the toolbar picks up this dock's toggle action
        self._build_toolbar()
        self._build_panel()
        self.lbl_pos = QLabel("")
        self.lbl_zoom = QLabel("")
        self.lbl_rot = QLabel("")
        self.lbl_rot.setToolTip(t("画面「上」相对天球北的夹角（顺时针为正）；镜像的图会一并说明"))
        self.lbl_info = QLabel("")
        sb = self.statusBar()
        sb.addWidget(self.lbl_info, 1)
        sb.addPermanentWidget(self.lbl_rot)
        sb.addPermanentWidget(self.lbl_pos)
        sb.addPermanentWidget(self.lbl_zoom)
        self.btn_update = QToolButton()
        self.btn_update.setAutoRaise(True)
        self.btn_update.setText(t("检查更新"))
        self.btn_update.setToolTip(t("从服务器检查是否有新版本"))
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self.btn_update.clicked.connect(lambda: self.check_updates(manual=True))
        sb.addPermanentWidget(self.btn_update)
        self._update_feed = None
        self._update_check = None
        self._update_manual = False
        updater.cleanup_old(updater.app_dir())        # a swap leaves the previous install behind
        QTimer.singleShot(1500, self._auto_update_check)
        self._update_actions()                       # nothing is open yet: the toolbar says so
        if files:
            self.add_files(files)

    # ---- layout ---------------------------------------------------------
    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, key=None, checkable=False):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if key:
                a.setShortcut(QKeySequence(key))
            a.setCheckable(checkable)
            tb.addAction(a)
            return a
        act(t("打开"), self.open_dialog, QKeySequence.Open)
        # 上一张 / 下一张 / 播放 live on the bar of controls under the picture, not on the toolbar — but their
        # keys belong to the window, so they stay as actions with shortcuts and no button of their own.
        self._prev_action = QAction(t("上一张"), self)
        self._prev_action.setShortcut(QKeySequence(Qt.Key_Left))
        self._prev_action.triggered.connect(lambda: self.step(-1))
        self.addAction(self._prev_action)
        self._next_action = QAction(t("下一张"), self)
        self._next_action.setShortcut(QKeySequence(Qt.Key_Right))
        self._next_action.triggered.connect(lambda: self.step(1))
        self.addAction(self._next_action)
        self.act_play = QAction(t("播放"), self)
        self.act_play.setCheckable(True)
        self.act_play.setShortcut(QKeySequence(Qt.Key_Space))
        self.act_play.triggered.connect(self._toggle_slideshow)
        self.act_play.setEnabled(False)            # nothing to play before an image is open
        self.act_play.setToolTip(t("自动播放当前目录下的所有图片"))
        self.addAction(self.act_play)
        tb.addSeparator()
        self.act_fit = act(t("适应窗口"), self.view.fit, "F")
        self.act_original = act(t("原始大小"), self.view.one_to_one, "1")
        tb.addSeparator()
        tb.addWidget(QLabel(" %s " % t("拉伸")))
        self.cmb_stretch = ComboBox()
        for code, name in (("auto", t("自动")), ("awb", t("自动白平衡")), ("linked", t("联动")),
                           ("pedestal", t("去底色")), ("unlinked", t("分通道")), ("none", t("不拉伸"))):
            self.cmb_stretch.addItem(name, code)
        self.cmb_stretch.setCurrentIndex(max(0, self.cmb_stretch.findData(self.cfg.get("stretch", "auto"))))
        self.cmb_stretch.currentIndexChanged.connect(self._stretch_changed)
        tb.addWidget(self.cmb_stretch)
        tb.addSeparator()
        self.act_preview = act(t("效果预览"), self._toggle_preview_mode, "", checkable=True)
        self.act_preview.setToolTip(t("在当前图上按所选标注风格渲染成品效果（与导出的标注图一致）"))
        self.act_preview.setChecked(bool(self.cfg.get("preview_mode", True)))
        # 「显示标注」已移除：效果预览关闭即显示原图，叠加层不再使用
        self.act_overlay = act(t("显示标注"), self._toggle_overlay, "A", checkable=True)
        self.act_overlay.setVisible(False)
        self.act_overlay.setChecked(bool(self.cfg.get("show_overlay", True)))
        self.act_header = act(t("文件头"), self.show_header, "H")
        self.act_fix_header = act(t("修正文件头"), self.fix_header)
        self.act_histogram = act(t("直方图"), self.show_histogram, "L")
        self.act_undo = act(t("撤销删除或移动"), self.undo_last, QKeySequence.Undo)
        # The image bar's ✖ promises the Delete key in its tooltip; the key itself belongs to the window, not
        # to that button (which never takes focus), so it gets its own action — and no toolbar button of its own.
        self._del_action = QAction(t("删除当前图像（会先确认，删后可撤销）"), self)
        self._del_action.setShortcut(QKeySequence(Qt.Key_Delete))
        self._del_action.triggered.connect(self.delete_current)
        self.addAction(self._del_action)
        tb.addSeparator()
        act(t("设置"), self.settings)
        self.act_update = act(t("检查更新"), lambda: self.check_updates(manual=True))
        tb.addSeparator()
        tb.addAction(self._files_action)           # show/hide the left-hand folder list
        tb.addAction(self._bar_action)             # show/hide the bar of screening controls

    def _build_image_bar(self):
        """The bar of screening controls along the bottom edge of the picture: prev / play / next to look
        through a folder, then remove or move the frames that do not make the cut. It floats over the image,
        so the toolbar switch can hide it for a clean look at a picture."""
        bar = QFrame(self.view)
        bar.setObjectName("imageBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        def add(glyph, tip, slot, key=None, checkable=False):
            b = QToolButton(bar)
            b.setText(glyph)
            b.setToolTip("%s%s" % (t(tip), "  (%s)" % key if key else ""))
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(30, 30)
            b.setFocusPolicy(Qt.NoFocus)           # keys keep working in the viewer
            b.setCheckable(checkable)
            b.clicked.connect(slot)
            lay.addWidget(b)
            return b

        sep = lambda: lay.addWidget(self._bar_separator(bar))   # noqa: E731
        self.btn_prev = add("⏴", "上一张", lambda: self.step(-1), "←")
        self.btn_play = add("▶", "播放", self._toggle_slideshow, "空格", checkable=True)
        self.btn_next = add("⏵", "下一张", lambda: self.step(1), "→")
        sep()
        self.btn_del = add("✖", "删除当前图像（会先确认，删后可撤销）", self.delete_current, "Delete")
        self.btn_move = add("➜", "把当前图像移动到…", self.move_current)
        self.view.set_image_bar(bar)
        self.image_bar = bar

        a = QAction(t("图像操作栏"), self)
        a.setCheckable(True)
        a.setChecked(bool(self.cfg.get("show_image_bar", True)))
        a.setToolTip(t("显示或隐藏画面底部的操作栏（上一张/播放/下一张/删除/移动）"))
        a.toggled.connect(self._toggle_image_bar)
        self._bar_action = a

    def _bar_separator(self, parent):
        f = QFrame(parent)
        f.setFrameShape(QFrame.VLine)
        f.setObjectName("barSep")                  # styled by BAR_QSS, so a live theme change repaints it
        f.setFixedWidth(1)
        return f

    def _toggle_image_bar(self, on):
        self.cfg["show_image_bar"] = bool(on)
        self._update_bar()

    def _update_bar(self):
        """Keep the bottom bar in step with the folder: what it can act on, and the play button's state."""
        on = bool(self.cfg.get("show_image_bar", True))
        self._bar_action.setChecked(on)
        files, i = self._folder_files()
        has_image = self.img is not None
        if hasattr(self, "image_bar"):
            self.image_bar.setVisible(on and has_image and not self.view._empty)
            self.view._place_nav()
        if hasattr(self, "btn_prev"):
            for b in (self.btn_prev, self.btn_next):
                b.setEnabled(len(files) > 1)
            self.btn_del.setEnabled(has_image)
            self.btn_move.setEnabled(has_image)
            self.btn_play.setEnabled(has_image and i >= 0)


    def _build_list_panel(self):
        """Left-hand list of the images in the current folder. The dock can be floated, closed or re-shown
        from the toolbar (dock.toggleViewAction())."""
        dock = QDockWidget(t("文件夹"), self)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable |
                         QDockWidget.DockWidgetClosable)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(5)
        self.lbl_folder = QLabel("")
        self.lbl_folder.setWordWrap(True)
        self.lbl_folder.setToolTip(t("同目录下的图片"))
        lay.addWidget(self.lbl_folder)
        self.file_list = QListWidget()
        self.file_list.setToolTip(t("同目录下的图片"))
        self.file_list.setStyleSheet(_list_qss(self))
        self.file_list.currentRowChanged.connect(self._file_row_changed)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(
            lambda pos: self._list_menu(pos, self.file_list))
        lay.addWidget(self.file_list, 1)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("QFrame{color:%s}" % C["border"])
        lay.addWidget(line)
        # the history queue lives here now: the left column is the picture list plus the queue, and the right
        # column is about the annotation itself
        self.lbl_queue = QLabel(t("打开历史"))
        self.lbl_queue.setStyleSheet(SUBTITLE_QSS)
        self.lbl_queue.setToolTip(t("打开过的图片会按顺序记在这里，之后可以一起解析；在左栏点选或翻页看过的图"
                                    "也会自动加进来"))
        lay.addWidget(self.lbl_queue)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setStyleSheet(_list_qss(self))
        self.list.currentRowChanged.connect(self._row_changed)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(lambda pos: self._list_menu(pos, self.list))
        self.list.setMinimumHeight(2 * _list_row_height(self.list) + 8)
        lay.addWidget(self.list, 1)
        dock.setWidget(w)
        dock.setMinimumWidth(220)                  # the rows fit; the panel should not eat the picture area
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.dock_files = dock
        self._files_width = dock.sizeHint().width()
        self._files_anim = None
        self._files_user_closed = False            # the toggle was used: a folder refresh must not undo it
        # A plain toggleViewAction would pop the dock in and out; this action drives the drawer instead, and
        # follows the dock when the dock is closed by its own ✕.
        self._files_action = QAction(t("文件夹"), self)
        self._files_action.setCheckable(True)
        self._files_action.setText(" " + t("文件夹") + " ")
        self._files_action.setToolTip(t("显示或隐藏左侧的同目录图片列表"))
        self._files_action.triggered.connect(lambda: self._toggle_files())
        dock.visibilityChanged.connect(self._files_action.setChecked)
        self._files_action.setChecked(dock.isVisible())
        dock.hide()                                # nothing to show until a picture is open

    def _update_actions(self):
        """Grey out everything that needs a picture. A button that does nothing when pressed is a lie about
        what the window can do right now, so the toolbar follows the state of the viewer."""
        has = getattr(self, "img", None) is not None
        for name in ("act_fit", "act_original", "act_overlay", "act_header", "act_fix_header",
                     "act_histogram", "act_del_action", "_del_action", "_prev_action", "_next_action",
                     "_bar_action", "_files_action"):
            a = getattr(self, name, None)
            if a is not None:
                a.setEnabled(has)
        if getattr(self, "act_undo", None) is not None:
            self.act_undo.setEnabled(has and getattr(self, "_undo_action", None) is not None)
        if getattr(self, "act_play", None) is not None:
            self.act_play.setEnabled(has and self.file_list.count() > 0)
        if getattr(self, "btn_start", None) is not None:
            self.btn_start.setEnabled(has)

    def _toggle_files(self):
        """The toolbar button: slide the left list out, or in. The dock's own state decides, not the check
        mark: a checkable toolbar action can lose its state when the dock is shown or hidden from elsewhere."""
        show = not self.dock_files.isVisible()
        self._files_user_closed = not show
        self._files_action.setChecked(show)
        self._animate_files(show)

    def _animate_files(self, show):
        """A drawer, not a pop: the dock's width is animated, so the picture grows into the freed space.

        QMainWindow lays docks out from their width limits, so animating maximumWidth is enough; the minimum
        has to be relaxed for the duration or the 268-pixel floor would stop the animation at once.
        """
        dock = self.dock_files
        if dock is None:
            return
        if self._files_anim is not None:
            self._files_anim.stop()
        target = getattr(self, "_files_width", 288) or 288
        if show:
            start = dock.width() if dock.isVisible() else 0
            dock.setMinimumWidth(0)
            dock.setMaximumWidth(max(0, start))
            dock.show()
            end = target
        else:
            start = dock.width()
            self._files_width = start or target
            dock.setMinimumWidth(0)
            end = 0
        anim = QPropertyAnimation(dock, b"maximumWidth", self)
        anim.setDuration(170)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(max(0, start))
        anim.setEndValue(max(0, end))
        if show:
            anim.finished.connect(lambda: (dock.setMinimumWidth(268), dock.setMaximumWidth(16777215),
                                           self._files_action.setChecked(True)))
        else:
            anim.finished.connect(lambda: (dock.hide(), self._files_action.setChecked(False)))
        anim.finished.connect(lambda: setattr(self, "_files_anim", None))
        self._files_anim = anim
        anim.start()

    def _sync_files_dock(self):
        """Show the left list once there is something in it, hide it when the queue is empty."""
        has = self.file_list.count() > 0
        if has and not self.dock_files.isVisible() and not self._files_user_closed:
            self._animate_files(True)
        elif not has and self.dock_files.isVisible():
            self._animate_files(False)

    def _build_panel(self):
        """The annotation panel, in the order the work is done: the queue, the target hint, the star
        catalogues, what to draw, how to draw it, what to write, then the actions. Each group carries a quiet
        caption and the controls sit in one column, so the panel reads the same at any dock width."""
        dock = QDockWidget(t("标注"), self)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        w = QWidget()
        w.setObjectName("panelRoot")
        w.setStyleSheet(PANEL_QSS + "QWidget#panelRoot{background:%s}" % C["surface"])
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)

        # everything below is added in order to this column; the layout is placed once, here
        self.controls = QVBoxLayout()
        # 右侧留出滚动条的宽度，否则按钮会贴到滚动条下面、被裁掉一截
        self.controls.setContentsMargins(0, 0, 14, 0)
        self.controls.setSpacing(6)
        # The groups below can be taller than a small screen: they scroll inside the dock, while the queue,
        # the actions and the log keep their places.
        # Opaque, not translucent: a translucent column leaves the old pixels behind while the dock is being
        # dragged, which reads as a smear across the panel.
        inner = QWidget()
        inner.setObjectName("sectionInner")
        inner.setStyleSheet("QWidget#sectionInner{background:%s}" % C["surface"])
        inner.setLayout(self.controls)
        ctl_scroll = SectionScroll()
        ctl_scroll.set_content(inner)
        ctl_scroll.setStyleSheet("QScrollArea{background:%s;border:none}" % C["surface"])
        ctl_scroll.viewport().setAutoFillBackground(True)
        self.controls_scroll = ctl_scroll
        lay.addWidget(ctl_scroll)

        def section(text):
            """A group caption. QLabel#section brings its own top padding, so the rhythm is the caption's."""
            lb = QLabel(t(text))
            lb.setObjectName("section")
            self.controls.addWidget(lb)
            return lb

        def pill(text, tip, checked, handler):
            c = _toggle(w, t(text), checked)
            c.setStyleSheet(TOGGLE_QSS)
            if tip:
                c.setToolTip(t(tip))
            c.toggled.connect(handler)
            return c

        # ── 目标：解析提示 ────────────────────────────────────────────────────────────────────
        self.lbl_target = section("目标")
        forms = QFormLayout()
        forms.setContentsMargins(0, 0, 0, 0)
        forms.setSpacing(6)
        forms.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        forms.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        self.ed_target = QLineEdit()
        self.ed_target.setMinimumWidth(150)
        self.ed_target.setPlaceholderText(t("天体名(可选，可搜索)"))
        self.ed_target.setToolTip(t("输入 M57、NGC6720、Ring、6720 都能找到；选中后用它在天球上的准确位置"
                                    "做解析提示，比文件头里的指向更可靠"))
        self._make_target_completer()
        forms.addRow(t("天体名"), self.ed_target)
        self.ed_coord = QLineEdit()
        self.ed_coord.setMinimumWidth(150)
        self.ed_coord.setPlaceholderText("05 34 31.9  +22 00 52")
        self.ed_coord.setToolTip(t("直接输入画面中心的赤经/赤纬（时 分 秒、度 分 秒），优先于天体名用作解析提示；"
                                   "例：05 34 31.9 +22 00 52，也接受 05:34:31.9 +22:00:52"))
        self.ed_coord.editingFinished.connect(self._coord_entered)
        forms.addRow(t("中心坐标"), self.ed_coord)
        self.controls.addLayout(forms)

        # ── 解析星表 ──────────────────────────────────────────────────────────────────────────
        head = QHBoxLayout()                       # heading with the select all / none shortcut
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.lbl_cats = QLabel(t("解析星表"))
        self.lbl_cats.setObjectName("section")
        head.addWidget(self.lbl_cats)
        head.addStretch(1)
        for text, on in (("全选", True), ("清空", False)):
            link = QToolButton()
            link.setText(t(text))
            link.setCursor(Qt.PointingHandCursor)
            link.setFocusPolicy(Qt.NoFocus)
            link.setAutoRaise(True)
            link.setToolTip(t("全部解析") if on else t("一个都不解析"))
            link.setObjectName("link")             # PANEL_QSS colours it, so it follows a live theme change
            link.clicked.connect(lambda _=False, v=on: self._cats_all(v))
            head.addWidget(link)
        self.controls.addLayout(head)
        self.chk_cats = {}
        saved_cats = self.cfg.get("cats")
        saved_cats = list(vizier.CATALOGS) if saved_cats is None else list(saved_cats)
        cat_row = QHBoxLayout()
        cat_row.setContentsMargins(0, 0, 0, 0)
        cat_row.setSpacing(6)
        for key, spec in vizier.CATALOGS.items():
            c = pill(spec["label"], t("解析后写出 %s 星表（不勾就不查询、不写出）") % spec["block"],
                     key in saved_cats, self._cats_changed)
            self.chk_cats[key] = c
            cat_row.addWidget(c)
        cat_row.addStretch(1)
        self.controls.addLayout(cat_row)
        self.lbl_named = QLabel(t("亮星表（NamedStars）始终写出"))
        self.lbl_named.setObjectName("note")
        self.lbl_named.setWordWrap(True)
        self.controls.addWidget(self.lbl_named)
        self._cats_changed()                       # show the count from the start

        # ── 标注内容：画什么 ──────────────────────────────────────────────────────────────────
        saved = self.cfg.get("groups")              # None = never saved: everything starts on
        # the star names and the grid are not catalogues, so they are not part of the precedence list and are
        # switched separately. "grid" and "named" keep their historical config keys.
        #
        # An older config carries type keys ("galaxy", "nebula") and no catalogue key at all; taking that
        # literally would start the program with nothing switched on, so an unrecognised value means "all on".
        fresh = saved is None or not any(k in saved for k, _t in GROUPS)

        def is_on(key):
            return fresh or key in saved

        anno_head = QHBoxLayout()
        anno_head.setContentsMargins(0, 0, 0, 0)
        anno_head.setSpacing(5)
        self.lbl_anno = QLabel(t("标注内容"))
        self.lbl_anno.setObjectName("section")
        anno_head.addWidget(self.lbl_anno)
        anno_head.addStretch(1)
        self.chk_extra = {}
        for key, text in (("named", "亮星名"), ("grid", "坐标网格")):
            c = pill(text, t("和星表无关，单独开关；默认开启"), is_on(key), self._groups_changed)
            c.setStyleSheet(TOGGLE_QSS)
            self.chk_extra[key] = c
            anno_head.addWidget(c)
        self.controls.addLayout(anno_head)
        legend = QLabel("&nbsp;&nbsp;".join(
            "<span style='color:%s'>●</span> %s" % (style.CATEGORY_COLORS[k], t(name))
            for k, name in (("galaxy", "星系"), ("nebula", "星云"), ("cluster", "星团"), ("dark", "暗星云"))))
        legend.setTextFormat(Qt.RichText)
        legend.setObjectName("note")
        legend.setToolTip(t("颜色按天体类型：同一类型里的每个天体再取属于自己的深浅"))
        self.controls.addWidget(legend)

        self.chk_groups = {}
        counts = catalogue.counts()
        row_w = QWidget()
        row_w.setObjectName("cardRow")
        row_w.setStyleSheet("QWidget#cardRow{background:%s}" % C["surface"])
        row_h = QHBoxLayout(row_w)
        row_h.setContentsMargins(2, 3, 2, 5)        # the cards keep a little air off the row's edges
        row_h.setSpacing(6)
        for key, text in GROUPS:
            card = CatalogueCard(t(text), counts.get(key), catalogue.COLORS.get(key, C["accent"]), row_w)
            card.setChecked(is_on(key))
            card.setToolTip("%s\n%s %s" % (t(catalogue.NOTES[key]), t("星表里共有"),
                                           format(counts[key], ",") if counts.get(key) else t("在线库")))
            card.toggled.connect(self._groups_changed)
            self.chk_groups[key] = card
            row_h.addWidget(card)
        row_h.addStretch(1)
        scroller = HScroll()
        for card in self.chk_groups.values():
            scroller.watch(card)
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.NoFrame)
        scroller.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroller.setWidget(row_w)
        scroller.setFixedHeight(CatalogueCard.HEIGHT + 20)
        scroller.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        # The viewport names its colour instead of inheriting one from the palette: a scroll area's Base can
        # come out black on some Windows themes, which turned the row into a dark slab behind the cards.
        scroller.viewport().setStyleSheet("background:%s;" % C["surface"])
        self.catalogue_scroll = scroller
        self.controls.addWidget(scroller)


        # ── 显示：怎么画 ─────────────────────────────────────────────────────────────────────
        self.lbl_shape = section("显示")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(QLabel(t("天体形状")))
        self.cmb_shape = ComboBox()
        for text, key in (("轮廓线", "outline"), ("圆圈", "circle")):
            self.cmb_shape.addItem(t(text), key)
        self.cmb_shape.setCurrentIndex(max(0, self.cmb_shape.findData(self.cfg.get("shape_style", "outline"))))
        self.cmb_shape.setToolTip(t("轮廓线：有轮廓数据的星云按实际形状描边。圆圈：一律按星表尺寸画圆，长条形天体画成椭圆。"))
        self.cmb_shape.currentIndexChanged.connect(self._shape_changed)
        row.addWidget(self.cmb_shape, 1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(t("标注风格")))
        self.cmb_style = ComboBox()
        for key, th in style.THEMES.items():
            self.cmb_style.addItem(t(th["label"]), key)
        self.cmb_style.setCurrentIndex(max(0, self.cmb_style.findData(
            self.cfg.get("annotation_style", "science"))))
        self.cmb_style.setToolTip(t("标注风格：科学（信息最全）、极简、复古星图、霓虹；只影响叠加层与标注图"))
        self.cmb_style.currentIndexChanged.connect(self._style_changed)
        row2.addWidget(self.cmb_style, 1)
        lay.addLayout(row2)
        style.set_theme(self.cfg.get("annotation_style", "science"))
        row.addSpacing(6)
        # The colours of a one-shot colour sensor, for a frame whose header does not say which arrangement it
        # used. Only meaningful for a single-channel mosaic, so it stays disabled until one is on screen.
        self.lbl_bayer = QLabel(t("拜耳"))
        row.addWidget(self.lbl_bayer)
        self.cmb_bayer = ComboBox()
        self.cmb_bayer.addItem(t("自动"), "auto")         # the tooltip says it follows the header
        for key in debayer.PATTERNS:
            self.cmb_bayer.addItem(t(debayer.PATTERN_LABELS[key]), key)
        self.cmb_bayer.setCurrentIndex(
            max(0, self.cmb_bayer.findData(self.cfg.get("bayer_pattern", "auto"))))
        self.cmb_bayer.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_bayer.setMinimumContentsLength(7)   # the long item list must not widen the dock
        self.cmb_bayer.setToolTip(t("彩色相机的未解拜耳帧如果文件头没写拜耳排列，就在这里指定。\n"
                                    "只对单通道的彩色马赛克帧生效；已经解拜耳或本来就是单色的帧不受影响。"))
        self.cmb_bayer.currentIndexChanged.connect(self._bayer_changed)
        row.addWidget(self.cmb_bayer, 1)
        self.controls.addLayout(row)

        # ── 输出：写什么 ─────────────────────────────────────────────────────────────────────
        self.lbl_out = section("输出")
        self.chk = {}
        out_grid = QGridLayout()                   # two across: four switches in four rows made the panel tall
        out_grid.setContentsMargins(0, 0, 0, 0)
        out_grid.setHorizontalSpacing(6)
        out_grid.setVerticalSpacing(5)
        for i, (key, text, tip) in enumerate((
                ("write_txt", "天体列表 TXT", "PixInsight 格式的天体列表，存在图片旁边"),
                ("write_preview", "标注图", "带标注的预览图，另存为 *_annotated.jpg"),
                ("write_outlines", "轮廓坐标文件", ""),
                ("write_wcs", "解析结果写回图片",
                 "写回会修改原文件的文件头(像素不动)。FITS/XISF 写进文件头,JPG/PNG/TIFF 另存同名 .wcs 文件。"))):
            c = pill(text, tip, bool(self.cfg.get(key, key != "write_wcs")), self._output_changed)
            self.chk[key] = c
            out_grid.addWidget(c, i // 2, i % 2, Qt.AlignLeft)
        self.controls.addLayout(out_grid)

        # ── 动作 ─────────────────────────────────────────────────────────────────────────────
        lay.addSpacing(10)                         # a little air before the actions
        self.btn_start = QPushButton(t("解析并标注"))
        self.btn_start.setStyleSheet(BUTTON_QSS)
        self.btn_start.clicked.connect(self.start)
        lay.addWidget(self.btn_start)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_save = QPushButton(t("保存当前结果"))
        self.btn_save.setToolTip(t("把这张图的解析结果写到磁盘（解析本身不写任何文件）"))
        self.btn_save.clicked.connect(self.save_current)
        self.btn_save.setEnabled(False)
        self.btn_pi = QPushButton(t("在 PixInsight 中打开"))
        self.btn_pi.setToolTip(t("用 PixInsight 打开当前显示的原图（XISF/FITS 保留文件头，不做任何改动）"))
        self.btn_pi.clicked.connect(self.open_in_pixinsight)
        self.btn_pi.setEnabled(False)              # nothing to open before an image is shown
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_pi)
        lay.addLayout(row)
        self.btn_preview = QPushButton(t("只存标注图"))
        self.btn_preview.setToolTip(t("按当前标注风格重新生成标注图，其它文件不动"))
        self.btn_preview.clicked.connect(self.save_preview_only)
        self.btn_preview.setEnabled(False)
        lay.addWidget(self.btn_preview)

        self.prog = QProgressBar()
        self.prog.setRange(0, 1000)
        self.prog.setTextVisible(False)
        self.prog.setFixedHeight(6)
        lay.addWidget(self.prog)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(4000)
        self.logbox.setMinimumHeight(40)           # the sections above are tall; the log still gets a few lines
        lay.addWidget(self.logbox, 3)
        self.btn_folder = QPushButton(t("打开输出文件夹"))
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_folder.setEnabled(False)
        lay.addWidget(self.btn_folder)
        dock.setWidget(w)
        dock.setMinimumWidth(296)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.dock_annotate = dock

    # ---- files ----------------------------------------------------------
    def open_dialog(self):
        exts = " ".join("*" + e for e in imageio.SUPPORTED)
        paths, _ = QFileDialog.getOpenFileNames(self, t("打开"), self.cfg.get("last_dir", ""),
                                                "Images (%s);;All files (*)" % exts)
        if paths:
            self.add_files(paths)

    def add_files(self, paths, select=True):
        """Add files to the annotation queue. select=False only queues them: the caller (the left-hand list,
        the ‹ / › buttons) does the showing itself."""
        paths = [os.path.abspath(p) for p in paths if os.path.splitext(p)[1].lower() in _SUPPORTED]
        if not paths:
            return
        existing = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        first_row = None
        for p in paths:
            if p in existing:
                row = existing.index(p)
            else:
                it = QListWidgetItem(os.path.basename(p))
                it.setData(Qt.UserRole, p)
                it.setToolTip(p)
                self.list.addItem(it)
                row = self.list.count() - 1
            if first_row is None:
                first_row = row
        self.cfg["last_dir"] = os.path.dirname(paths[0])
        self._files_user_closed = False
        self._renumber_files()
        if select:
            self.list.setCurrentRow(first_row)

    def _renumber_files(self):
        """Prefix each row with its position, so the user can see how many files are queued and which one
        is on screen ("7 / 214"). The path stays in UserRole; the text is display only."""
        n = self.list.count()
        for i in range(n):
            it = self.list.item(i)
            if it is None:
                continue
            path = it.data(Qt.UserRole) or ""
            it.setText("%*d / %d   %s" % (len(str(n)), i + 1, n, os.path.basename(path)))

    def _row_changed(self, row):
        if row >= 0:
            self.show_image(self.list.item(row).data(Qt.UserRole))

    # ---- right-click menu on a listed file -------------------------------
    def _list_menu(self, pos, widget):
        """Right-click a row of either list: locate the file in Explorer or copy its full path."""
        it = widget.itemAt(pos)
        if it is None:
            return                                           # a click below the last row: no target
        path = it.data(Qt.UserRole) or it.text()
        menu = QMenu(self)
        act_show = menu.addAction(t("在文件资源管理器中显示"))
        act_copy = menu.addAction(t("复制文件完整路径"))
        menu.addSeparator()
        act_dir = menu.addAction(t("复制所在文件夹路径"))
        chosen = menu.exec_(widget.viewport().mapToGlobal(pos))
        if chosen is act_show:
            # via the shell API rather than `explorer /select,...`: that relies on explorer.exe re-parsing an
            # argument full of commas, spaces and backslashes, and opens Documents when the parse fails
            if not reveal.reveal(path):
                self.logbox.appendPlainText("%s %s" % (t("无法在文件资源管理器中定位"), path))
        elif chosen is act_copy:
            QApplication.clipboard().setText(os.path.normpath(path))
        elif chosen is act_dir:
            QApplication.clipboard().setText(os.path.dirname(os.path.normpath(path)))

    # ---- the image's folder (left-hand list and the ‹ / › buttons) -------
    def _folder_files(self):
        """(image files in the current image's folder, index of the current one) — annotated previews and the
        small previews written beside each frame are skipped so they do not show up as pictures of their own.

        The previews the ASIAIR names `..._thn.jpg` are left out here, by name, before anything is shown; the
        background scan then drops any other preview by comparing picture sizes."""
        if not self.current:
            return [], -1
        folder = os.path.dirname(self.current)
        try:
            names = sorted(f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in _SUPPORTED
                           and not f.endswith(".annotated.jpg") and not imageio.preview_name(f))
        except OSError:
            return [], -1
        shown = self._without_previews(folder, names)
        try:
            return shown, shown.index(os.path.basename(self.current))
        except ValueError:
            return shown, -1

    def _without_previews(self, folder, names):
        """The names to list: the frames, without the low-resolution previews beside them.

        The first answer for a folder comes from a background scan, because reading the size of every file can
        take a couple of seconds on a slow or networked disk; until it arrives every file is listed."""
        hit = self._previews.get(folder)
        if hit and hit[0] == tuple(names):
            return list(hit[1])
        key = (folder, tuple(names))
        if self._scan is None or self._scan_key != key:
            if self._scan is not None and self._scan.isRunning():
                return list(names)                 # one scan at a time; this folder waits for the next pass
            self._scan_key = key
            self._scan = FolderScan(folder, names, self)
            self._scan.done.connect(self._previews_found)
            self._scan.start()
        return list(names)

    def _previews_found(self, folder, names, kept):
        """A folder's sizes are known: remember them and rebuild the list from whatever is on screen.

        The rebuild matters even when the scan was for another folder: a scan blocks new ones while it runs, so
        the folder the user switched to in the meantime gets its own scan started by this pass."""
        self._previews[folder] = (tuple(names), list(kept))
        self._folder_dirty = True                  # the rows changed: refresh_folder must rebuild them
        self.refresh_folder()

    def refresh_folder(self):
        """Fill the left-hand list from the current image's folder and leave the rest of the window alone."""
        files, i = self._folder_files()
        folder = os.path.dirname(self.current) if self.current else ""
        if folder != self._folder or self._folder_dirty:
            self._folder, self._folder_dirty = folder, False
            self._file_guard = True                    # setCurrentRow below must not load the image again
            try:
                self.file_list.clear()
                for n in files:
                    it = QListWidgetItem(n)
                    it.setData(Qt.UserRole, os.path.join(folder, n))
                    it.setToolTip(os.path.join(folder, n))
                    self.file_list.addItem(it)
                self.file_list.setCurrentRow(i if i >= 0 else -1)
            finally:
                self._file_guard = False
        elif self.file_list.currentRow() != i:
            self._file_guard = True
            try:
                self.file_list.setCurrentRow(i)
            finally:
                self._file_guard = False
        self.lbl_folder.setText(os.path.basename(folder) if folder
                                else t("把图片拖到这里,或点「打开」"))
        self.lbl_folder.setToolTip(folder or "")
        if hasattr(self, "act_play"):
            self.act_play.setEnabled(i >= 0 and self.img is not None)   # playback needs a folder
        if hasattr(self, "btn_pi"):
            self.btn_pi.setEnabled(self.img is not None)
        self._update_save_button()
        self._sync_files_dock()
        self._update_actions()
        self._update_bar()

    def _file_row_changed(self, row):
        """A click in the left-hand list shows that image (it also joins the annotation queue)."""
        if self._file_guard or row < 0:
            return
        p = self.file_list.item(row).data(Qt.UserRole)
        self.add_files([p], select=False)          # already on screen: do not reload through the queue
        self.show_image(p)

    # ---- automatic playback ---------------------------------------------
    def _toggle_slideshow(self, on):
        """The ▶ buttons (toolbar and image bar): step through the folder on its own until switched off.
        Either button can drive it, so both are kept in step here."""
        self.act_play.setText(t("暂停") if on else t("播放"))
        self.act_play.setToolTip(t("每隔几秒自动切换到下一张") if on else t("自动播放当前目录下的所有图片"))
        self.act_play.setChecked(bool(on))
        if hasattr(self, "btn_play"):               # the two buttons are one function
            self.btn_play.setChecked(bool(on))
            self.btn_play.setText("⏸" if on else "▶")
            self.btn_play.setToolTip(t("暂停") if on else t("播放"))
        self._slide_timer.stop()
        if on:
            self._slide_show_next()

    def _stop_slideshow(self):
        """Leaving the window: switch playback off without touching the timer's running state."""
        if self.act_play.isChecked():
            self.act_play.setChecked(False)        # triggers _toggle_slideshow(False)
        self._slide_timer.stop()

    def _slide_show_next(self):
        """Arm the next frame. Anything can interrupt playback: closing the window, switching to an image in
        another folder, or clicking ▶ again. The frame is only armed while the picture already on screen has
        finished loading, so the pause stays a real pause on slow frames."""
        if not self.act_play.isChecked() or self.worker is not None and self.worker.isRunning():
            return
        _files, i = self._folder_files()
        if i < 0:
            return
        self._slide_timer.start(max(300, int(float(self.cfg.get("slide_interval", 2.0)) * 1000)))

    def _slide_tick(self):
        if self.act_play.isChecked():
            self.step(1)

    def step(self, d):
        """Previous / next image in the current image's folder (wraps around at the ends, so neither button
        is ever a dead end)."""
        if self._advancing:
            return
        files, i = self._folder_files()
        if i < 0 or len(files) < 2:
            return
        j = (i + d) % len(files)
        p = os.path.join(self._folder or os.path.dirname(self.current), files[j])
        self.add_files([p], select=False)
        self.show_image(p)

    def show_image(self, path):
        """Start loading an image. The zoom and position of whatever is on screen are captured first and put
        back by _loaded when the new picture has the same display size, so frames can be compared around the
        same detail; a different size or shape falls back to fitting the frame."""
        if path != self.current and os.path.dirname(path) != (self._folder or ""):
            self._folder = None                        # a different folder: rebuild the list
            self._folder_dirty = True
        self.current = path
        self.lbl_info.setText(t("正在读取…") + " " + os.path.basename(path))
        self.view.set_loading(True)
        # re-reading the same frame (after solving, or a solved one refreshed) cannot keep the view: the
        # displayed copy may have just been built from the same pixels but the scroll position is stale
        same = path == self._loaded_path or self._solved_reload
        self._solved_reload = False
        self._view_state = None if same else self.view._view_state()
        self.loader = ld = LoadWorker(path, self.cmb_stretch.currentData(),
                                      bool(self.cfg.get("debayer", True)),
                                      self.cfg.get("bayer_pattern", "auto"), self,
                                      dso_cats=self._groups_on())
        ld.target_object = self._target_obj
        ld.tickwhale_base = self.cfg.get("tickwhale_base", "")
        ld.done.connect(self._loaded)
        ld.start()

    def _loaded(self, out):
        if out["path"] != self.current:
            return                                  # a newer request superseded this one
        self.view.set_loading(False)                # this frame is on screen (or failed): see below
        if "error" in out:
            self.lbl_info.setText("%s: %s" % (t("读取失败"), out["error"]))
            self.refresh_folder()                   # keep the highlight and the ‹ / › buttons in step
            self._slide_show_next()                 # a broken frame must not end playback
            return
        self.img = out["img"]
        res = self.results.get(out["path"])
        self.wcs = res.wcs if (res and res.ok) else out.get("wcs")
        if res and res.ok:
            self.overlay_data[out["path"]] = (res.shapes, res.named, res.grid)
            self.overlay_online[out["path"]] = list(getattr(res, "online", None) or [])
        elif out.get("wcs") is not None:
            self.overlay_data[out["path"]] = (out.get("shapes"), out.get("named"), out.get("grid"))
            self.overlay_online[out["path"]] = list(out.get("online") or [])
        # keep zoom and position across frames of the same size (see ImageView._view_state)
        state = self._view_state
        if state is not None and state[0] != (out["qimage"].width(), out["qimage"].height()):
            state = None
        self._view_state = None
        self._loaded_path = out["path"]
        try:
            res_now = self.results.get(out["path"])
            rot = (res_now.stats.get("rotation") if res_now is not None else None)
            if rot is not None:
                self.lbl_rot.setText("%s %.1f°" % (t("旋转"), float(rot)))
            elif self.wcs is not None and self.img is not None:
                self.lbl_rot.setText("%s %.1f°" % (t("北向"), _north_angle(self.wcs, self.img.width, self.img.height)))
            else:
                self.lbl_rot.setText("")
        except Exception:
            self.lbl_rot.setText("")
        self._request_online(out)
        self.view.set_image(out["qimage"], view=state)
        self._refresh_overlay()
        self._update_bayer_control()
        im = self.img
        mode_name = {"linked": t("联动"), "unlinked": t("分通道"), "none": t("不拉伸"),
                     "awb": t("自动白平衡"), "pedestal": t("去底色")}.get(out.get("mode"), "")
        stretch_txt = ("%s: %s%s" % (t("拉伸"), t("自动") + " → " if self.cmb_stretch.currentData() == "auto" else "",
                                     mode_name)) if im.linear else ""
        # say what kind of colour the frame is, and that a mosaic was interpolated on the way in
        if im.debayered:
            colour_txt = "%s %s → %s" % (t("彩色"), im.debayered, t("已解拜耳"))
        elif im.is_color:
            colour_txt = t("彩色")
        else:
            colour_txt = t("单色")
        self.lbl_info.setText("%s   %d × %d   %s   %s%s   %s   %s" % (
            os.path.basename(im.path), im.width, im.height, im.fmt.upper(), colour_txt,
            ("  (linear)" if im.linear else
             ("  " + t("已拉伸") if not imageio.plain_raster(im) else "")),
            t("已解析") if self.wcs is not None else t("未解析"),
            stretch_txt))
        self.setWindowTitle("%s — TTSkyAnnot" % os.path.basename(im.path))
        self.refresh_folder()                      # left-hand list and the ‹ / › buttons follow the folder
        if self._undo_message:                     # a removal is still undoable: say so instead
            self.lbl_info.setText(self._undo_hint())
        self._slide_show_next()                    # playback waits for the frame to be ready

    def _stretch_changed(self):
        self.cfg["stretch"] = self.cmb_stretch.currentData()
        if self.current:
            self._solved_reload = True                 # the frame itself is unchanged: keep it fitted
            self.show_image(self.current)

    def _groups_on(self):
        """The enabled deep-sky catalogues, in precedence order."""
        return tuple(k for k, _t in GROUPS if self.chk_groups[k].isChecked())

    def _extras_on(self):
        return {k for k, c in self.chk_extra.items() if c.isChecked()}

    def _groups_changed(self):
        """Save what is switched on: the precedence list in order, then the two non-catalogue switches."""
        self.cfg["groups"] = list(self._groups_on()) + sorted(self._extras_on())
        self._refresh_overlay()

    def _cats_on(self):
        return tuple(k for k in vizier.CATALOGS if self.chk_cats[k].isChecked())

    def _cats_changed(self):
        """Which star catalogues take part: what is switched off is not even queried."""
        self.cfg["cats"] = list(self._cats_on())
        self.lbl_cats.setText("%s (%d/%d)" % (t("解析星表"), len(self.cfg["cats"]), len(vizier.CATALOGS)))

    def _output_changed(self):
        """Remember which files to offer to write; the actual choice is confirmed per image."""
        for key in ("write_txt", "write_preview", "write_outlines", "write_wcs"):
            self.cfg[key] = self.chk[key].isChecked()
        self._update_save_button()

    def _cats_all(self, on):
        for c in self.chk_cats.values():
            c.blockSignals(True)
            c.setChecked(on)
            c.blockSignals(False)
        self._cats_changed()

    def _shape_changed(self):
        self.cfg["shape_style"] = self.cmb_shape.currentData()
        if getattr(self, "act_preview", None) is not None and self.act_preview.isChecked():
            self._render_view_preview()
        else:
            self._refresh_overlay()

    def _bayer_changed(self):
        """Remember the choice and reload: the pattern changes how the frame is interpolated, so it has to be
        read again rather than just redrawn."""
        self.cfg["bayer_pattern"] = self.cmb_bayer.currentData()
        if self.current and self.img is not None and self.img.can_choose_bayer:
            self.show_image(self.current)

    def _update_bayer_control(self):
        """Only single-channel data can have its arrangement chosen; anything else is left alone, and the
        control says why rather than sitting there looking usable."""
        if not hasattr(self, "cmb_bayer"):
            return
        usable = self.img is None or self.img.can_choose_bayer
        self.cmb_bayer.setEnabled(usable)
        self.lbl_bayer.setEnabled(usable)
        if usable and self.img is not None and not self.img.is_bayer_mosaic:
            hint = t("这张是单通道图像、文件头也没写拜耳排列。如果它其实是彩色相机的未解拜耳帧，"
                     "在这里指定排列即可解出彩色。")
        elif usable:
            hint = t("彩色相机的未解拜耳帧如果文件头没写拜耳排列，就在这里指定。\n"
                     "只对单通道的彩色马赛克帧生效；已经解拜耳或本来就是单色的帧不受影响。")
        else:
            hint = t("当前图像不是未解拜耳的彩色马赛克帧，这个选项对它无效。")
        self.cmb_bayer.setToolTip(hint)

    def _toggle_overlay(self):
        self.cfg["show_overlay"] = self.act_overlay.isChecked()
        self._refresh_overlay()
        if self.act_overlay.isChecked() and self.img is not None \
                and not self.overlay_data.get(self.current):
            # annotations are drawn from the solution: without one there is nothing to draw, and a silently
            # empty picture reads as a broken switch rather than as "this frame has not been solved yet"
            self.statusBar().showMessage(
                t("这张图还没有解析：标注来自解析结果，先用「解析并标注」把它解出来"), 8000)

    def _request_online(self, out):
        """Ask for the online objects of this frame's field, unless a result or the cache already has them."""
        path, wcs = out["path"], out.get("wcs")
        if wcs is None:
            return
        res = self.results.get(path)
        if res is not None and res.ok:
            return                                 # the solve already filled res.online
        cats = self._groups_on()
        if not any(k in catalogue.ONLINE for k in cats):
            return
        base = (self.cfg.get("tickwhale_base") or "").strip()
        if not base or self.img is None:
            return
        try:
            ra0, de0, rad, _area = annotate.field_geometry(wcs, self.img.width, self.img.height)
        except Exception:
            return
        key = (round(ra0, 2), round(de0, 2), round(rad, 2))
        if key in self._region_cache:
            self.overlay_online[path] = list(self._region_cache[key])
            if path == self.current:
                self._refresh_overlay()
            return
        if self.overlay_online.get(path):
            return                                 # already asked for this very frame
        job = (path, key, ra0, de0, rad, cats, base)
        if self._region_lookup is not None and self._region_lookup.isRunning():
            self._region_next = job                # the running query finishes first; this one follows
            return
        self._start_region(job)

    def _start_region(self, job):
        path, key, ra0, de0, rad, cats, base = job
        self._region_lookup = RegionLookup(path, key, ra0, de0, rad, cats, base, self)
        self._region_lookup.found.connect(self._region_found)
        self._region_lookup.start()

    def _region_found(self, path, key, recs):
        """Store what the online database returned and put the labels on the picture that asked for them."""
        self._region_cache[key] = list(recs)
        self.overlay_online[path] = list(recs)
        if path == self.current:
            self._refresh_overlay()
        nxt, self._region_next = self._region_next, None
        if nxt is not None:
            self._start_region(nxt)

    def _toggle_preview_mode(self):
        self.cfg["preview_mode"] = self.act_preview.isChecked()
        config.save(self.cfg)
        if self.act_preview.isChecked():
            self._render_view_preview()
        else:
            self._preview_pix = None
            if self.current:
                self.show_image(self.current)

    def _inherit_stretch(self, img):
        """第 1 张图新载入时没有自定义拉伸；把它继承自屏幕上那张，标注图才会和预览一致。"""
        cur = self.img
        if cur is None or img is None:
            return
        try:
            if getattr(cur, "path", None) != getattr(img, "path", None):
                return
            if getattr(cur, "_levels", None) is not None:
                img._levels = cur._levels
            if getattr(cur, "_balance", None) is not None:
                img._balance = cur._balance
        except Exception:
            pass

    def _display_for_preview(self, img, opt):
        """The picture the annotated preview should be built from.

        When the histogram panel has left custom levels (or a white balance) on this frame, they win:
        the annotation must look like what the user sees, not like the default stretch.
        """
        lv = getattr(img, "_levels", None)
        bal = getattr(img, "_balance", None)
        if lv is not None and bal is not None:
            try:
                base = np.asarray(img.data, np.float32)
                if not bal.is_identity:
                    base = bal.apply(base)
                disp = lv.apply(base)
                if disp.ndim == 2:
                    disp = np.repeat(disp[..., None], 3, axis=2)
                return disp
            except Exception:
                pass
        return imageio.display_stretch(img, opt.stretch)

    def _render_view_preview(self):
        """Render the annotated picture at a modest size and show it: the viewer then matches the export."""
        res = self.results.get(self.current)
        if not res or not res.ok:
            self.act_preview.setChecked(False)
            return
        try:
            img = imageio.load(res.image, want_debayer=self.cfg.get("debayer", True), pattern="")
            self._inherit_stretch(img)
            opt = replace(self._last_opt or run.Options(), write_preview=True,
                          annotation_style=self.cfg.get("annotation_style", "science"),
                          shape_style=self.cmb_shape.currentData(),
                          grid="grid" in self._extras_on(), named="named" in self._extras_on(),
                          categories=self._groups_on(), dso_cats=self._groups_on())
            p = os.path.join(tempfile.gettempdir(), "ttskyannot_preview.jpg")
            run.write_preview(img, res, opt, self._display_for_preview(img, opt), p, max_edge=1600)
            pm = QPixmap(p)
            if not pm.isNull():
                self._preview_pix = p
                self.view.set_image(pm.toImage())
                self.view.clear_overlay()
                self.logbox.appendPlainText(t("效果预览已更新"))
        except Exception as e:
            self.logbox.appendPlainText("%s %s" % (t("效果预览失败："), e))
            self.act_preview.setChecked(False)

    def _style_changed(self, *a):
        key = self.cmb_style.currentData() or "science"
        style.set_theme(key)
        self.cfg["annotation_style"] = key
        config.save(self.cfg)
        if getattr(self, "act_preview", None) is not None and self.act_preview.isChecked():
            self._render_view_preview()
        else:
            self._refresh_overlay()
        self._update_save_button()          # the preview-only button follows "is there a solved frame"
        res = self.results.get(self.current)
        if res and res.ok:
            self.logbox.appendPlainText(t("标注风格已切换：点「只存标注图」可按新风格重存，不必导出全部文件。"))

    def _schedule_preview_refresh(self):
        """Rebuild the annotated preview shortly after a settings change.

        The rendered preview is a separate picture, so toggling a catalogue, a star list or the shape style
        has to regenerate it; a 250 ms delay keeps a quick series of clicks from rendering it several times.
        """
        timer = getattr(self, "_preview_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(250)
            timer.timeout.connect(self._render_view_preview_now)
            self._preview_timer = timer
        timer.start()

    def _render_view_preview_now(self):
        if getattr(self, "act_preview", None) is None or not self.act_preview.isChecked():
            return
        try:
            self._render_view_preview()
        except Exception as e:
            self.logbox.appendPlainText("效果预览失败: %s: %s" % (type(e).__name__, e))

    def _refresh_overlay(self):
        # 「效果预览」关掉就显示原图：不再叠加任何标注（这是用户明确要求的行为）
        if getattr(self, "act_preview", None) is None or not self.act_preview.isChecked():
            self.view.clear_overlay()
            return
        if getattr(self, "act_preview", None) is not None and self.act_preview.isChecked():
            self.view.clear_overlay()      # the rendered preview already has the annotations
            self._schedule_preview_refresh()
            return
        data = self.overlay_data.get(self.current)
        if self.act_overlay.isChecked() and data:
            on, extra = self._groups_on(), self._extras_on()
            # Switching a catalogue changes the label of a dual object (M31 under Messier, NGC224 under NGC),
            # and the shapes carry that label, so they are rebuilt from the frame's own solution. The shapes
            # stored with the result are replaced too, so a preview saved from here follows the same choice.
            if self.img is not None and getattr(self, "wcs", None) is not None:
                try:
                    P = annotate.Projector(self.wcs, self.img.width, self.img.height)
                    fresh = shapes_mod.collect(P, cats=on)
                    res_now = self.results.get(self.current)
                    online_here = list(getattr(res_now, "online", None) or []) or list(
                        self.overlay_online.get(self.current) or [])
                    for rec in online_here:
                        okey = catalogue.catalogue_of(rec)
                        if okey in on:
                            extra_shape = shapes_mod.online_shape(P, rec, okey)
                            if extra_shape is not None:
                                fresh.append(extra_shape)
                    if self._target_obj:
                        tkey = catalogue.catalogue_of(self._target_obj)
                        extra_shape = shapes_mod.online_shape(P, self._target_obj, tkey) if tkey else None
                        if extra_shape is not None:
                            fresh.append(extra_shape)
                    res = self.results.get(self.current)
                    if res is not None and getattr(res, "sky", None):
                        # 星野图：重建的轮廓也要按天空掩膜过一遍，否则换一个星表就把地景上的标注又画回来
                        fresh = [s for s in fresh
                                 if skyregion.allows(res.sky, s.get("x"), s.get("y"))]
                    data = (fresh, data[1], data[2])
                    self.overlay_data[self.current] = data
                    if res is not None:
                        res.shapes = fresh
                except Exception:
                    pass
            self.view.draw_overlay(data[0], data[1], data[2] if "grid" in extra else None,
                                   categories=set(on), show_named="named" in extra,
                                   shape_style=self.cmb_shape.currentData())
        else:
            self.view.clear_overlay()

    def _on_move(self, x, y):
        im = self.img
        if im is None:
            return
        i, j = int(np.floor(x)), int(np.floor(y))
        if not (0 <= i < im.width and 0 <= j < im.height):
            self.lbl_pos.setText("")
            return
        v = im.data[j, i]
        vals = " ".join("%.4f" % c for c in v)
        txt = "x %d  y %d   %s" % (i, j, vals)
        if self.wcs is not None:
            try:
                ra, dec = self.wcs.all_pix2world([[x - 0.5, y - 0.5]], 0)[0]
                txt += "   RA %s  Dec %s" % (_hms(ra), _dms(dec))
            except Exception:
                pass
        self.lbl_pos.setText(txt)

    def show_header(self):
        if self.img is not None:
            HeaderDialog(self.img, self).exec_()

    def fix_header(self):
        """Correct the optics and pointing recorded in the frame's header — what every solver searches on."""
        if self.img is None:
            return
        if self.img.fmt != "fits":
            QMessageBox.information(self, "TTSkyAnnot", t("只有 FITS 文件能这样修改文件头。"))
            return
        d = FixHeaderDialog(self.img, self)
        if not d.exec_():
            return
        if d.all_files.isChecked():
            msg = t("已修正 %d 张的文件头") % len(d.done_paths)
            if d.failed_paths:
                msg += "，" + t("失败 %d 张") % len(d.failed_paths)
        else:
            msg = "%s: %s" % (t("已修正文件头"), ", ".join(d.written))
        self.lbl_info.setText(msg)
        self.logbox.appendPlainText(msg)
        for p, e in d.failed_paths:
            self.logbox.appendPlainText("  %s: %s" % (p, e))

    def show_histogram(self):
        """The histogram panel, docked under the preview so it never covers the picture.

        It used to be a modal dialog: dragging a level was invisible until it closed, and the panel sat on
        top of the frame it was meant to adjust. Docked, it stays open beside the picture and the levels
        update the view as they move.
        """
        if self.img is None:
            return
        raw = np.asarray(self.img.data, np.float32)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dlg = HistogramDialog(self, raw=raw, balance=getattr(self.img, "_balance", None),
                                  levels=self._levels_for(self.img))
        finally:
            QApplication.restoreOverrideCursor()
        # 面板化：去掉对话框自己的窗口框架，放进底部停靠区
        if getattr(self, "_hist_dock", None) is None:
            dock = QDockWidget(t("直方图"), self)
            dock.setObjectName("histDock")
            dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
                             | QDockWidget.DockWidgetClosable)
            self.addDockWidget(Qt.BottomDockWidgetArea, dock)
            dock.visibilityChanged.connect(lambda vis: self._close_histogram() if not vis else None)
            self._hist_dock = dock
        dlg.setWindowFlags(Qt.Widget)
        dlg.setModal(False)
        self._hist_dock.setWidget(dlg)
        self._hist_dock.show()
        self._hist_dock.raise_()
        self._hist_before = (dlg.levels().as_dict(), dlg.balance().as_dict())
        dlg.changed.connect(lambda: self._apply_histogram(dlg, fast=True))
        self._hist_panel = dlg

    def _close_histogram(self):
        """Dock closed: one full-resolution render of whatever the panel left behind.

        Guarded against re-entry: the dock's visibilityChanged also fires while the window itself is being
        shown or hidden, and a redraw at that moment must not disturb a solve that is in progress.
        """
        if getattr(self, "_closing_hist", False):
            return
        dlg = getattr(self, "_hist_panel", None)
        if dlg is None:
            return
        self._closing_hist = True
        try:
            self._apply_histogram(dlg, fast=False)
            self._hist_panel = None
        finally:
            self._closing_hist = False

    def _apply_histogram(self, dlg, fast=False):
        """Take the panel's state onto the picture: the balance first, then the levels over it.

        fast=True is the drag-time path: it works on a downscaled copy so the picture follows the sliders
        while they move; the dialog's close event re-renders at full size.
        """
        if self.img is None:
            return
        state = (dlg.levels().as_dict(), dlg.balance().as_dict())
        if state == getattr(self, "_hist_before", None):
            return                       # opened and closed without moving anything: keep the picture as it is
        self._hist_before = state
        self._set_levels(self.img, dlg.levels())
        self.img._balance = dlg.balance()
        if fast:
            self._refresh_preview(fast=True)
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._refresh_preview()
        finally:
            QApplication.restoreOverrideCursor()

    def _levels_for(self, img):
        """The levels that reproduce what the picture currently shows, so the panel opens on them."""
        lv = getattr(img, "_levels", None)
        if lv is not None:
            return lv
        base = np.asarray(img.data, np.float32)
        bal = getattr(img, "_balance", None)
        if bal is not None and not bal.is_identity:
            base = bal.apply(base[::4, ::4])
        return hist_mod.auto_levels(base)

    def _set_levels(self, img, levels):
        img._levels = levels
        self.cfg["levels"] = levels.as_dict()

    def _refresh_preview(self, fast=False):
        """Redraw the picture with the current levels, leaving the file alone.

        fast=True uses a downscaled copy of the frame (built once, cached) so dragging a level stays
        interactive on a 26-megapixel file.
        """
        if self.img is None:
            return
        bal = getattr(self.img, "_balance", None) or imageio.Balance()
        lv = getattr(self.img, "_levels", None)
        data = self.img.data
        if fast:
            cache = getattr(self, "_drag_cache", None)
            if cache is None or cache[0] is not data:
                k = max(1, int(max(data.shape[:2]) / 1400))
                cache = (data, np.ascontiguousarray(data[::k, ::k]))
                self._drag_cache = cache
            data = cache[1]
        if lv is not None and not lv.per_channel:
            # the panel moves one curve: 16-bit table, no 300 MB float frame in between
            self.view.show_rgb(imageio.apply_u8(data, bal, lv))
            return
        base = np.asarray(self.img.data, np.float32)
        if bal is not None and not bal.is_identity:
            base = bal.apply(base)
        disp = lv.apply(base) if lv is not None else imageio.display_stretch(self.img, self.cmb_stretch.currentData())
        self.view.show_array(disp, own=disp is not self.img.data)

    def _apply_theme(self, name):
        """Switch the colour scheme in place — no restart.

        Every theme-dependent rule lives in one application-wide stylesheet, so re-applying it restyles the
        window; the handful of widgets that carry a sheet of their own are matched against the one they were
        built with and given its counterpart. The views the sheet cannot reach (the canvas behind the picture,
        the drop hint) are repainted by hand.
        """
        before = {"toggle": TOGGLE_QSS, "button": BUTTON_QSS, "bar": BAR_QSS,
                  "subtitle": SUBTITLE_QSS, "panel": PANEL_QSS}
        _dark_palette(QApplication.instance(), name)
        after = {"toggle": TOGGLE_QSS, "button": BUTTON_QSS, "bar": BAR_QSS,
                 "subtitle": SUBTITLE_QSS, "panel": PANEL_QSS}
        for w in self.findChildren(QWidget):
            sheet = w.styleSheet()
            if not sheet:
                continue
            for key, old in before.items():
                if sheet == old:
                    w.setStyleSheet(after[key])
                    break
        self.list.setStyleSheet(_list_qss(self.list))
        self.file_list.setStyleSheet(_list_qss(self.file_list))
        for scroller in self.findChildren(HScroll):
            scroller.setStyleSheet(scroller._qss())
        for dlg in self.findChildren(FramedDialog):        # an open dialog carries the panel sheet plus its card
            dlg.setStyleSheet(dlg._card_sheet())
        self.view.set_theme(C)
        _paint_title_bar(self, THEME == "dark")            # the window frame is drawn by Windows, not by us
        self._refresh_overlay()

    def settings(self):
        d = SettingsDialog(self.cfg, self)
        accepted = bool(d.exec_())
        old_lang = self.cfg.get("language", "")
        old_theme = self.cfg.get("theme", "dark")
        if accepted:
            self.cfg = d.values()
        else:
            # closed with ✕ or Esc: nothing else is applied, but a colour scheme that was picked stays — it
            # is a preference, not an edit, and losing it here is what made "选了浅色却还是深色" happen
            picked = d.theme_name()
            if picked and picked != old_theme:
                self.cfg["theme"] = picked
        config._diag("settings: accepted=%s picked=%r old=%r new=%r"
                     % (accepted, d.theme_name(), old_theme, self.cfg.get("theme")))
        if self.cfg.get("theme", "dark") != THEME:
            self._apply_theme(self.cfg.get("theme", "dark"))     # a picked scheme was already applied live
        if not config.save(self.cfg):
            QMessageBox.warning(self, "TTSkyAnnot",
                                t("设置文件写入失败：%s\n配色等选项可能在下次启动时不生效。") % config._path())
        if accepted and self.cfg.get("language", "") != old_lang:
            self._offer_restart()

    def _offer_restart(self):
        """The interface language is baked into the window when it is built, so a language change needs a new
        window. The colour scheme does not: it is applied in place."""
        if QMessageBox.question(self, "TTSkyAnnot", t("界面语言已保存，需要重启后生效。现在重启吗？"),
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return
        args = [os.path.abspath(self.current)] if self.current else []
        if getattr(sys, "frozen", False):                 # the packaged app is its own executable
            ok = QProcess.startDetached(sys.executable, args)
        else:                                             # running from source: python <entry> [file]
            entry = os.path.abspath(sys.argv[0] or "")
            if os.path.basename(entry) in ("__main__.py", "-c", ""):
                ok = QProcess.startDetached(sys.executable, ["-m", "skyannot"] + args)
            else:
                ok = QProcess.startDetached(sys.executable, [entry] + args)
        if not ok:
            config._diag("restart failed: %r %r" % (sys.executable, args))
            QMessageBox.information(self, "TTSkyAnnot", t("无法自动重启，请手动重新打开程序。"))
            return
        config._diag("restarting: %r %r" % (sys.executable, args))
        self.close()

    # ---- updates --------------------------------------------------------
    def _auto_update_check(self):
        """Ask the configured feed once the window has settled; never hold up the start."""
        if not updater.app_dir():
            return                     # a source run has no install to replace, and the tests must stay offline
        if (self.cfg.get("update_url") or "").strip():
            self.check_updates(manual=False)

    def check_updates(self, manual=True):
        """Read the update feed; a new version opens the dialog that can fetch and install it."""
        base = (self.cfg.get("update_url") or "").strip()
        if not base:
            if manual:
                QMessageBox.information(self, "TTSkyAnnot",
                                        t("还没有设置更新源。请在「设置 → 更新源」里填 version.json 所在的地址或文件夹。"))
            return
        if self._update_check is not None and self._update_check.isRunning():
            return
        self._update_manual = manual
        self.btn_update.setEnabled(False)
        self.btn_update.setText(t("检查中…"))
        self._update_check = _UpdateCheck(base, self)
        self._update_check.done.connect(self._update_checked)
        self._update_check.start()

    def _update_checked(self, feed, error):
        self.btn_update.setEnabled(True)
        manual = self._update_manual
        if error:
            self.btn_update.setText(t("检查更新"))
            if manual:
                QMessageBox.warning(self, "TTSkyAnnot", "%s\n\n%s" % (t("检查更新失败"), error))
            else:
                self.logbox.appendPlainText("%s %s" % (t("检查更新失败"), error))
            return
        version = str((feed or {}).get("version") or "")
        config._diag("update: check version=%r error=%r" % (version, error))
        if updater.is_newer(version):
            skipped = str(self.cfg.get("skipped_version") or "")
            self._update_feed = feed
            self.btn_update.setText("↑ %s" % version)
            self.btn_update.setToolTip("%s %s" % (t("新版本"), version))
            if manual or skipped != version:
                d = UpdateDialog(feed, (self.cfg.get("update_url") or "").strip(), self)
                d.exec_()
                if d.skipped:
                    self.cfg["skipped_version"] = version
                    config.save(self.cfg)
            else:
                self.logbox.appendPlainText("%s %s（%s）" % (t("有新版本"), version, t("已跳过")))
            return
        self.btn_update.setText(t("已是最新"))
        QTimer.singleShot(4000, lambda: self.btn_update.setText(t("检查更新")))
        if manual:
            QMessageBox.information(self, "TTSkyAnnot", "%s（%s）" % (t("已是最新版本"), __version__))

    # ---- annotation -----------------------------------------------------
    def start(self):
        if self.worker is not None and self.worker.isRunning():
            return
        # checked before anything else: with no catalogue ticked there is nothing to solve for, and the
        # setting is worth complaining about even when no image happens to be selected
        self.cfg["cats"] = list(self._cats_on())
        if not self.cfg["cats"]:
            QMessageBox.warning(self, "TTSkyAnnot", t("至少要选一个星表"))
            return
        rows = sorted({i.row() for i in self.list.selectedIndexes()}) or (
            [self.list.currentRow()] if self.list.currentRow() >= 0 else [])
        if not rows:
            return
        for k, c in self.chk.items():
            self.cfg[k] = c.isChecked()
        config.save(self.cfg)
        # a position typed into the centre-coordinate field wins: it is the most direct statement of where the
        # frame is centred, and both solvers use it as the search point
        coord = self.coord_radec()
        if coord:
            self.logbox.appendPlainText("%s RA %s Dec %s" % (t("解析提示：画面中心"), _hms(coord[0]), _dms(coord[1])))
        elif self.ed_coord.text().strip():
            # something is written there but it is not a position: say so instead of quietly ignoring it
            self.ed_coord.setProperty("bad", True)
            self.ed_coord.style().unpolish(self.ed_coord)
            self.ed_coord.style().polish(self.ed_coord)
            self.logbox.appendPlainText("%s %s" % (t("坐标无法识别（时 分 秒 / 度 分 秒）："),
                                                   self.ed_coord.text().strip()))
        coord = coord or self._target_radec
        opt = run.Options(target=self.ed_target.text().strip(), target_radec=coord,
                          target_object=self._target_obj,
                          tickwhale_base=self.cfg.get("tickwhale_base", ""),
                          astap_path=self.cfg.get("astap_path", ""),
                          api_key=self.cfg.get("api_key", ""),
                          astrometry_key=self.cfg.get("astrometry_key", ""),
                          gaia_mag=float(self.cfg.get("gaia_mag", 16)),
                          remote_fallback=bool(self.cfg.get("remote_fallback", True)),
                          annotation_style=self.cfg.get("annotation_style", "science"),
                          solve_image_url=self.cfg.get("solve_image_url", ""),
                          solve_result_url=self.cfg.get("solve_result_url", ""),
                          cats=tuple(self.cfg["cats"]),
                          write_txt=self.cfg["write_txt"], write_preview=self.cfg["write_preview"],
                          write_outlines=self.cfg["write_outlines"], write_wcs=self.cfg["write_wcs"],
                          out_dir=self.cfg.get("out_dir", ""),
                          stretch=self.cmb_stretch.currentData(),     # the preview looks like the viewer
                          categories=self._groups_on(),
                          dso_cats=self._groups_on(),
                          debayer=bool(self.cfg.get("debayer", True)),
                          use_existing=bool(self.cfg.get("use_existing", True)),
                          bayer_pattern=self.cfg.get("bayer_pattern", "auto"),
                          named="named" in self._extras_on(), grid="grid" in self._extras_on(),
                          shape_style=self.cmb_shape.currentData())
        self._last_opt = opt                         # save_current() writes with the same settings
        items = [(r, self.list.item(r).data(Qt.UserRole)) for r in rows]
        for r, _ in items:
            self.list.item(r).setText("⏳ " + os.path.basename(self.list.item(r).data(Qt.UserRole)))
        self.btn_start.setEnabled(False)
        self.btn_start.setText(t("处理中…"))
        self.prog.setValue(0)
        self._n_items, self._k_done = len(items), 0
        w = AnnotateWorker(items, opt, self)
        w.log.connect(self.logbox.appendPlainText)
        w.progress.connect(lambda i, f: self.prog.setValue(int(1000 * (self._k_done + f) / self._n_items)))
        w.finished_one.connect(self._one_done)
        w.save_prompt.connect(self._save_prompt)
        w.finished.connect(self._all_done)
        self.worker = w
        w.start()

    def _save_prompt(self, worker, res, path):
        """The worker has finished an image and is waiting: ask before anything is written. Everything is
        offered here, so a solve can be inspected and then thrown away without touching the folder."""
        d = SaveDialog(path, run.output_paths(path, worker.opt),
                       {"txt": worker.opt.write_txt, "outlines": worker.opt.write_outlines,
                        "preview": worker.opt.write_preview, "wcs": worker.opt.write_wcs}, self,
                       start_dir=worker.opt.out_dir)
        accepted = d.exec_() == QDialog.Accepted
        flags = d.flags() if accepted else {k: False for k in ("txt", "outlines", "preview", "wcs")}
        if d.remember.isChecked() and accepted:
            # "use this from now on": the chosen outputs become the settings, so batch runs stop asking
            self.cfg.update(write_txt=flags["txt"], write_outlines=flags["outlines"],
                            write_preview=flags["preview"], write_wcs=flags["wcs"])
            for key, attr in (("txt", "write_txt"), ("outlines", "write_outlines"),
                              ("preview", "write_preview"), ("wcs", "write_wcs")):
                c = self.chk.get(attr)
                if c is not None:
                    c.setChecked(flags[key])
            config.save(self.cfg)
        if accepted and flags.get("_remember"):
            flags["_remember"] = True
        worker.answer(flags)
        if accepted and any(flags.get(k) for k in ("txt", "outlines", "preview", "wcs")):
            res.saved = True
        elif res.ok:
            self._unsaved.add(res.image)                   # still on screen, still not written
        self._update_save_button()

    def _one_done(self, row, res):
        self._k_done += 1
        it = self.list.item(row)
        if it is None or it.data(Qt.UserRole) != res.image:
            # the queue changed while the run was going (rows added or removed): the row number is stale, so
            # find the entry by its path instead — and if it is gone too, there is nothing to tick
            it = next((self.list.item(i) for i in range(self.list.count())
                       if self.list.item(i).data(Qt.UserRole) == res.image), None)
        if it is None:
            return
        name = os.path.basename(res.image)
        if res.ok:
            st = res.stats
            acc = st.get("median_px")
            it.setText("✓ " + name)
            it.setToolTip("%s\n%d %s%s" % (res.image, st.get("matched", 0), t("颗星配准"),
                                           ("  ·  %s %.2f %s" % (t("精度"), acc, t("像素"))) if acc is not None else ""))
            self.results[res.image] = res
            self.btn_folder.setEnabled(True)
            self._last_out = next(iter(res.outputs.values()), res.image)
            self._update_save_button()
            if res.image == self.current:
                # the solved centre goes into the coordinate box, so the next run of this frame starts
                # from a known position instead of solving blind again
                cra, cdec = st.get("center_ra"), st.get("center_dec")
                if cra is not None and cdec is not None:
                    self.ed_coord.setText(_coord_text(cra, cdec))
                # show the annotations at once: the reload below re-reads the file and can race the
                # worker's result, which left a solved frame looking unsolved
                if res.wcs is not None:
                    self.wcs = res.wcs
                    self.overlay_data[res.image] = (res.shapes, res.named, res.grid)
                    # 解析成功：自动切到标注成品图，用户不必再点一次开关
                    if getattr(self, "act_preview", None) is not None and not self.act_preview.isChecked():
                        self.act_preview.setChecked(True)
                    self._refresh_overlay()
                    if getattr(self, "act_preview", None) is not None and self.act_preview.isChecked():
                        try:
                            self._render_view_preview()
                        except Exception as e:      # 预览失败不能影响标注显示，也不影响解析流程
                            self.logbox.appendPlainText("效果预览失败: %s: %s" % (type(e).__name__, e))
                    try:
                        rot = st.get("rotation")
                        if rot is not None:
                            self.lbl_rot.setText("%s %.1f°" % (t("旋转"), float(rot)))
                        elif self.img is not None:
                            self.lbl_rot.setText("%s %.1f°" % (t("北向"),
                                                               _north_angle(self.wcs, self.img.width, self.img.height)))
                    except Exception:
                        pass
                    txt = self.lbl_info.text()
                    if t("未解析") in txt:
                        self.lbl_info.setText(txt.replace(t("未解析"), t("已解析"), 1))
        else:
            it.setText("✗ " + name)
            it.setToolTip(res.error)
            self.logbox.appendPlainText("%s%s" % (t("解析失败："), res.error))
            if not self.ed_target.text().strip() and not self.ed_coord.text().strip():
                # a blind solve on a processed wide field is the hardest case there is; a hint fixes it at once
                self.logbox.appendPlainText(
                    t("可以在「天体名」里填目标（如 M33），或在「中心坐标」里填画面中心，解析器会快很多。"))
            if not self._key_offer_shown and any(w in (res.error or "") for w in ("密钥", "额度")):
                # the anonymous allowance is small on purpose: this is the moment to say where a key comes from
                self._key_offer_shown = True
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Information)
                box.setWindowTitle(t("在线解析密钥"))
                box.setText(t("在线解析需要一个免费密钥"))
                box.setInformativeText("%s\n\n%s" % (
                    t("匿名额度很小（每天 20 次）。用一个免费密钥可以解析到每小时 150 次、每天 600 次。"),
                    t("密钥在网页上自助领取，填回「设置 → 在线解析密钥」即可。")))
                open_btn = box.addButton(t("打开领取页面"), QMessageBox.AcceptRole)
                box.addButton(t("稍后"), QMessageBox.RejectRole)
                box.exec_()
                if box.clickedButton() is open_btn:
                    QDesktopServices.openUrl(QUrl(KEYS_URL))

    def _all_done(self):
        self.btn_start.setEnabled(True)
        self.btn_start.setText(t("解析并标注"))
        self.prog.setValue(1000)

    def open_folder(self):
        p = getattr(self, "_last_out", None)
        if p:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))

    def _update_save_button(self):
        """The save button is about the picture on screen: it is there when its result is not on disk yet."""
        if not hasattr(self, "btn_save"):
            return
        n = len(self._unsaved)
        self.btn_save.setEnabled(bool(self.current) and self.current in self._unsaved)
        if hasattr(self, "btn_preview"):
            res = self.results.get(self.current)
            self.btn_preview.setEnabled(bool(res and res.ok))
        self.btn_save.setText(t("保存当前结果") if n <= 1 else "%s (%d)" % (t("保存结果"), n))
        self.btn_save.setToolTip(t("把这张图的解析结果写到磁盘（解析本身不写任何文件）")
                                 if not n else "%s：%d %s" % (t("还没保存的结果"), n, t("张")))

    def save_preview_only(self):
        """Re-render the annotated picture with the current style: the lists and outlines are left alone."""
        res = self.results.get(self.current)
        if not res or not res.ok:
            return
        # 预览图始终跟着当前打开的文件走（除非在设置里指定了全局输出目录），
        # 不要沿用上一次保存对话框选择过的目录
        out_dir = (self.cfg.get("out_dir") or "").strip() or None
        path = run.preview_path(res.image, self.cfg.get("annotation_style", "science"), out_dir)
        try:
            img = imageio.load(res.image, want_debayer=self.cfg.get("debayer", True), pattern="")
            self._inherit_stretch(img)
            opt = replace(self._last_opt or run.Options(), write_preview=True,
                          annotation_style=self.cfg.get("annotation_style", "science"),
                          shape_style=self.cmb_shape.currentData(),
                          grid="grid" in self._extras_on(), named="named" in self._extras_on(),
                          categories=self._groups_on(), dso_cats=self._groups_on())
            p = run.write_preview(img, res, opt, self._display_for_preview(img, opt), path)
        except Exception as e:
            QMessageBox.warning(self, "TTSkyAnnot", "%s\n\n%s" % (t("保存失败"), e))
            return
        self.logbox.appendPlainText("%s %s" % (t("标注图已更新："), p))
        self._last_out = p

    def save_current(self):
        """Write the results of the picture on screen: the way back for a solve that was not saved."""
        path = self.current
        res = self.results.get(path)
        if not res or not res.ok or path not in self._unsaved:
            return
        d = SaveDialog(path, run.output_paths(path, self._last_opt or run.Options()),
                       {"txt": self.cfg["write_txt"], "outlines": self.cfg["write_outlines"],
                        "preview": self.cfg["write_preview"], "wcs": self.cfg["write_wcs"]}, self,
                       start_dir=(self._last_opt.out_dir if self._last_opt else ""))
        if d.exec_() != QDialog.Accepted:
            return
        flags = d.flags()
        opt = replace(self._last_opt, write_txt=flags["txt"], write_outlines=flags["outlines"],
                      write_preview=flags["preview"], write_wcs=flags["wcs"],
                      out_dir=flags.get("_out_dir", ""))
        try:
            img = imageio.load(path, want_debayer=opt.debayer,
                               pattern="" if opt.bayer_pattern == "auto" else opt.bayer_pattern)
            disp = imageio.display_stretch(img, opt.stretch)
            try:
                import numpy as _np
                a = _np.asarray(disp)
                self.logbox.appendPlainText("保存预览：%d × %d，左上角像素 %s" % (
                    a.shape[1], a.shape[0], _np.round(a[0, 0], 2).tolist()))
            except Exception:
                pass
            run.write_outputs(img, res, opt, disp, log=self.logbox.appendPlainText)
        except Exception as e:
            QMessageBox.warning(self, "TTSkyAnnot", "%s\n\n%s" % (t("保存失败"), e))
            return
        self._unsaved.discard(path)
        self._last_out = next(iter(res.outputs.values()), path)
        self.btn_folder.setEnabled(True)
        self._update_save_button()
        for k, p in res.outputs.items():
            self.logbox.appendPlainText("  %-9s %s" % (k, p))

    def _make_target_completer(self):
        """Complete the object-name field from the bundled deep-sky catalogues.

        Every object is offered under every name it has (designation, Messier number, catalogue number, common
        name), so "ring", "m57", "ngc6720" and plain "6720" all lead to the Ring Nebula. Picking one records
        its position, which is then used as the solve hint: a mount's own coordinates say where it was told to
        point, which is not always where the frame ended up.
        """
        entries = catalogs.searchable()
        self._target_dso = {}
        self._target_by_key = {}                       # every name an object has -> the object
        labels = []
        for keys, d in entries:
            for alias in str(keys).split("|"):      # the names of one object, joined by "|"
                if alias:
                    self._target_by_key.setdefault(alias, d)
            key = catalogs.label_for(d)
            if key not in self._target_dso:            # keep the first (brightest) of duplicate labels
                self._target_dso[key] = d
                labels.append(key)
        model = QStringListModel(labels, self)
        comp = QCompleter(model, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)           # searching inside the names, not only at their start
        comp.setMaxVisibleItems(16)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        self.ed_target.setCompleter(comp)
        self._target_model = model
        self._target_local = set(labels)               # names that came from the bundled catalogues
        self._remote = {}
        self._remote_lookup = RemoteLookup(self)
        self._remote_lookup.found.connect(self._remote_found)
        self._remote_timer = QTimer(self)
        self._remote_timer.setSingleShot(True)
        self._remote_timer.setInterval(220)            # give the typing a chance to settle
        self._remote_timer.timeout.connect(self._ask_remote)
        comp.activated.connect(self._target_chosen)    # picking from the list
        self.ed_target.textEdited.connect(self._target_typed)

    def _remote_found(self, text, recs):
        """Add what the server knows to the completer, keeping the bundled entries first.

        The answer usually arrives after the user stopped typing, when the completion popup has already closed:
        the list is shown again here, and the text that is in the field is resolved to the object's position on
        the spot. Otherwise "LBN777" would be only a label to the solver, and an online-only catalogue name is
        exactly what the bundled data cannot place.
        """
        if text != self.ed_target.text().strip():
            return                                     # the field has moved on; this answer is stale
        added = []
        for rec in recs:
            label = tickwhale.label_for(rec)
            self._remote[label] = rec
            if label not in self._target_dso:
                self._target_dso[label] = rec
                added.append(label)
            for alias in (label, rec.get("name"), rec.get("common")):
                if alias:
                    self._target_by_key.setdefault(str(alias).replace(" ", "").lower(), rec)
        if added:
            self._target_model.setStringList(sorted(self._target_dso))
            self.logbox.appendPlainText("%s %d %s: %s" % (
                t("在线星表补充"), len(added), t("个天体"), ", ".join(added[:4])))
        resolved = self._resolve_typed()
        if resolved:
            ra, dec = self._target_radec
            self.logbox.appendPlainText("%s %s: RA %.5f Dec %.5f  (%s)" % (
                t("使用天体"), text, ra, dec, t("在线星表")))
        if added and self.ed_target.hasFocus() and self.ed_target.text().strip():
            self.ed_target.completer().complete()      # show the entries that just arrived

    def _ask_remote(self):
        """Ask the configured server about the current text, when one is configured."""
        base = (self.cfg.get("tickwhale_base") or "").strip()
        text = self.ed_target.text().strip()
        if base and len(text) >= 2 and text.lower() not in self._target_local:
            self._remote_lookup.ask(text, base)

    def _target_chosen(self, text):
        d = self._target_dso.get(text)
        if d is None:
            return
        if isinstance(d, dict):                        # came from the online catalogue
            self._target_radec = (d["ra"], d["dec"])
            self._target_obj = d
            name, online = tickwhale.label_for(d), "  (%s)" % t("在线星表")
        else:
            self._target_radec = (d.ra, d.dec)
            self._target_obj = None
            name, online = catalogs.label_for(d), ""
        self.logbox.appendPlainText("%s %s: RA %.5f Dec %.5f%s" % (
            t("使用天体"), name, self._target_radec[0], self._target_radec[1], online))

    def _target_typed(self, text):
        """Free text is a label, not a position, until it names a known object.

        Typing "M33" is enough: it is looked up against every name each object carries (designation, Messier
        number, common name), so the position becomes the solve hint without having to pick from the list."""
        self._resolve_typed()
        self._remote_timer.start()

    def _resolve_typed(self) -> bool:
        """Set the solve hint from the name in the field when it matches a known object; say whether it did.

        The lookup covers the bundled catalogues and whatever the online catalogue has already answered with,
        matched without spaces and without case, so "lbn 777" finds LBN777.
        """
        text = self.ed_target.text().strip()
        key = text.replace(" ", "").lower()
        d = self._target_by_key.get(key) or self._target_dso.get(text)
        if isinstance(d, dict):                        # came from the online catalogue
            self._target_radec = (float(d["ra"]), float(d["dec"]))
            self._target_obj = d
            return True
        if d is not None:
            self._target_radec = (float(d.ra), float(d.dec))
            self._target_obj = None                    # a bundled object is annotated by its catalogue
            return True
        self._target_radec = ()
        self._target_obj = None
        return False

    # ---- the centre-coordinate field ------------------------------------
    def _coord_entered(self):
        """Normalise the typed position into hours/degrees, minutes and seconds; flag it when it is not one."""
        text = self.ed_coord.text().strip()
        radec = parse_radec(text) if text else None
        bad = bool(text) and radec is None
        if bool(self.ed_coord.property("bad")) != bad:
            self.ed_coord.setProperty("bad", bad)
            self.ed_coord.style().unpolish(self.ed_coord)
            self.ed_coord.style().polish(self.ed_coord)
        if radec is None:
            if text:
                self.logbox.appendPlainText("%s %s" % (t("坐标无法识别（时 分 秒 / 度 分 秒）："), text))
            return
        formatted = "%s  %s" % (fmt_ra(radec[0]), fmt_dec(radec[1]))
        if formatted != self.ed_coord.text():
            self.ed_coord.setText(formatted)
        self.logbox.appendPlainText("%s RA %s Dec %s" % (t("使用画面中心坐标"), _hms(radec[0]), _dms(radec[1])))

    def coord_radec(self):
        """The position typed into the centre-coordinate field, in degrees, or () when it is empty or unreadable."""
        text = self.ed_coord.text().strip()
        return parse_radec(text) or () if text else ()

    def open_in_pixinsight(self):
        """Hand the picture on screen to PixInsight, the way Explorer's "Open with" does."""
        if not self.current:
            return
        try:
            piopen.open_image(self.current, self.cfg.get("pixinsight_path", ""))
        except Exception as e:
            QMessageBox.warning(self, "TTSkyAnnot", "%s\n\n%s" % (t("无法打开 PixInsight"), e))

    # ---- screening: throw away or file away a frame ---------------------
    def delete_current(self):
        """Send the frame on screen to the Recycle Bin, asking first.

        The confirmation shows the file name rather than a generic "are you sure": the usual mistake is
        deleting the frame next to the one intended, and only the name makes that visible. Someone screening a
        folder in bulk can switch the question off in the settings.
        """
        if not self.current:
            return
        if self.cfg.get("confirm_delete", True) and not self._confirm_delete(self.current):
            return
        self.delete_current_now()

    def _confirm_delete(self, path):
        """Ask before throwing a frame away. True = go ahead."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(t("删除图像"))
        box.setText(t("要删除这一张吗？"))
        box.setInformativeText(os.path.basename(path))
        box.setDetailedText(path)
        yes = box.addButton(t("移入回收站"), QMessageBox.DestructiveRole)
        no = box.addButton(t("取消"), QMessageBox.RejectRole)
        box.setDefaultButton(no)                       # Enter keeps the file, not deletes it
        box.setEscapeButton(no)
        no.setFocus()
        box.exec_()
        return box.clickedButton() is yes

    def delete_current_now(self):
        """Send the frame on screen to the Recycle Bin, with no further questions."""
        if not self.current:
            return
        path = os.path.abspath(self.current)
        # where the frame sat in the list, read before it is gone: after the removal the same index is the
        # frame that followed it, which is the one to look at next
        files, pos = self._folder_files()
        gone, self.current = self.current, None        # stop playback and the loader before touching the file
        if self.act_play.isChecked():
            self.act_play.setChecked(False)
        try:
            recoverable = fileop.delete(path)
        except Exception as e:
            self.current = gone
            QMessageBox.warning(self, "TTSkyAnnot",
                                "%s\n\n%s\n%s" % (t("无法删除这个文件"), path, e))
            return
        if recoverable:
            self._offer_undo("✖ " + t("已移入回收站") + ": " + os.path.basename(path),
                             lambda: self._undo_delete(path))
            self._forget(path)
            self._advance_after_removal(path, pos)
        else:
            # no Recycle Bin on this drive: the file is gone for good, so do not offer an undo. The next
            # frame is loaded first and the message written afterwards, so it survives that reload.
            self._forget(path)
            self._advance_after_removal(path, pos)
            self._undo_action, self._undo_message = None, ""
            self._undo_timer.stop()
            self.lbl_info.setText("✖ " + t("已删除（回收站不可用，无法撤销）") + ": " + os.path.basename(path))
        self.logbox.appendPlainText("%s %s" % (
            t("已移入回收站") if recoverable else t("已永久删除"), path))

    def _undo_delete(self, path):
        src = fileop.trash_path(path)
        if not src:
            self.lbl_info.setText(t("回收站里已经找不到它了"))
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.move(src, path)
        except Exception as e:
            QMessageBox.warning(self, "TTSkyAnnot", "%s\n\n%s" % (t("撤销失败"), e))
            return
        self.add_files([path], select=False)
        self._folder = None                            # the folder has this image again: rebuild the list
        self.show_image(path)
        self.logbox.appendPlainText("%s %s" % (t("已撤销删除"), path))

    def move_current(self):
        """File the frame on screen away into another folder."""
        if not self.current:
            return
        start = self.cfg.get("last_move_dir") or self.cfg.get("last_dir", "")
        folder = QFileDialog.getExistingDirectory(self, t("移动到文件夹"), start)
        if not folder:
            return
        folder = os.path.abspath(folder)
        if folder == os.path.dirname(os.path.abspath(self.current)):
            return                                     # already there
        self.cfg["last_move_dir"] = folder
        path = os.path.abspath(self.current)
        files, pos = self._folder_files()              # its slot, read while the frame is still in the list
        try:
            landed = fileop.move(path, folder)
        except Exception as e:
            QMessageBox.warning(self, "TTSkyAnnot", "%s\n\n%s\n%s" % (t("无法移动这个文件"), path, e))
            return
        self._offer_undo("➜ " + t("已移动") + ": " + landed, lambda: self._undo_move(landed, path))
        self._forget(path)
        self._advance_after_removal(path, pos)
        self.logbox.appendPlainText("%s %s" % (t("已移动"), landed))

    def _undo_move(self, landed, original):
        try:
            os.makedirs(os.path.dirname(original), exist_ok=True)
            os.replace(landed, original)
        except Exception as e:
            QMessageBox.warning(self, "TTSkyAnnot", "%s\n\n%s" % (t("撤销失败"), e))
            return
        self.add_files([original], select=False)
        self._folder = None
        self.show_image(original)
        self.logbox.appendPlainText("%s %s" % (t("已撤销移动"), original))

    def _forget(self, path):
        """Drop everything we remember about a file that is no longer where it was."""
        self.results.pop(path, None)
        self.overlay_data.pop(path, None)
        for row in range(self.list.count() - 1, -1, -1):
            if self.list.item(row).data(Qt.UserRole) == path:
                self.list.takeItem(row)
        self._renumber_files()
        if self._loaded_path == path:
            self._loaded_path = None

    def _advance_after_removal(self, path, pos=-1):
        """Keep screening flowing: show the frame that followed the removed one (or the new last frame when
        the last one was removed). `pos` is the slot the removed frame had, read before it went away — after
        the removal the same index is the next frame. Loaded directly rather than through step(), so a
        playback tick cannot slip in between."""
        folder = os.path.dirname(os.path.abspath(path))
        self.current = path                            # the list follows the file on screen: find its folder
        self._folder = None                            # the folder contents changed: rebuild the list
        self._folder_dirty = True
        files, _i = self._folder_files()
        if not files:
            self.clear_image_view()
            return
        j = pos if 0 <= pos < len(files) else len(files) - 1
        p = os.path.join(folder, files[j])
        self.add_files([p], select=False)
        self.show_image(p)

    def clear_image_view(self):
        self.img = None
        self.wcs = None
        self.current = None
        self.view.clear_image()
        self.lbl_info.setText(t("这个文件夹里没有图像了"))
        self.setWindowTitle("TTSkyAnnot")
        self.refresh_folder()

    def _offer_undo(self, message, action):
        """Put an undo in the status bar for a few seconds (one slot: a second removal replaces it). The
        message is kept because the next frame finishing to load rewrites the status bar."""
        self._undo_action = action
        self._undo_message = message
        self.lbl_info.setText(self._undo_hint())
        self._undo_timer.start(10000)
        self._update_actions()

    def _undo_hint(self):
        return "%s   ·   %s" % (self._undo_message, t("按 Ctrl+Z 撤销"))

    def _expire_undo(self):
        self._undo_action = None
        self._undo_message = ""
        self._update_actions()

    def undo_last(self):
        action, self._undo_action = self._undo_action, None
        self._undo_timer.stop()
        self._update_actions()
        if action:
            action()


    def closeEvent(self, ev):
        self._stop_slideshow()
        # a folder scan reads headers from disk: let it finish rather than destroying a running QThread
        if getattr(self, "_scan", None) is not None and self._scan.isRunning():
            self._scan.wait(4000)
        config.save(self.cfg)
        super().closeEvent(ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        # the window's own title bar is drawn by Windows, not by Qt: tell it which theme the app is in
        _paint_title_bar(self, THEME == "dark")
        # QMainWindow shows every dock when the window appears, including the one hidden before it: an empty
        # folder list should stay out of the way until there is something in it.
        if getattr(self, "dock_files", None) is not None and not self._files_user_closed:
            if self.file_list.count() == 0:
                self.dock_files.hide()
            self._files_action.setChecked(self.dock_files.isVisible())


def _paint_title_bar(win, dark: bool = True) -> None:
    """Ask Windows to draw the window's title bar in the app's theme.

    The title bar is outside the client area, so neither the palette nor the stylesheet reaches it: a dark
    window kept the system's light caption above its dark toolbar. DWM has an attribute for exactly this
    (20 on Windows 10 2004 and later, 19 on the first builds that had it); elsewhere this does nothing.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        hwnd = int(win.winId())
        value = ctypes.c_int(1 if dark else 0)
        # 20 is the attribute on Windows 10 2004 and later, 19 on the builds before that — and some of those
        # answer 20 with success while ignoring it, which is how a dark window kept a light caption. Set both.
        for attribute in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                       ctypes.sizeof(value))
        # ask for a new frame now: after a live theme change DWM can keep drawing the old caption until
        # something forces it to compose the frame again
        SWP_FRAMECHANGED, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER = 0x0020, 0x0002, 0x0001, 0x0004
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                          SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
    except Exception:
        pass


def _ascii_label(text):
    """The overlay fonts carry no Chinese glyphs; keep the catalogue id and the Latin name only."""
    import re
    s = re.sub(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", " ", str(text or ""))
    return re.sub(r"\s+", " ", s).strip(" -·|")


def _north_angle(wcs, w, h):
    """Angle of celestial north from the image's "up" edge, clockwise, in degrees (0..360)."""
    import math
    x, y = w / 2.0, h / 2.0
    a0, d0 = wcs.pixel_to_world_values(x, y)
    a1, d1 = wcs.pixel_to_world_values(x, max(0.0, y - 100.0))
    dra = (a1 - a0) * math.cos(math.radians(d0))
    return (math.degrees(math.atan2(dra, d1 - d0)) + 360.0) % 360.0


def _coord_text(ra, dec):
    """The centre in the same shape the coordinate box shows: 05 34 31.9  +22 00 52."""
    h = (ra / 15.0) % 24
    d = abs(dec)
    return "%02d %02d %04.1f  %s%02d %02d %02d" % (int(h), int(h * 60) % 60, (h * 3600) % 60,
                                                  "-" if dec < 0 else "+", int(d), int(d * 60) % 60,
                                                  int(d * 3600) % 60)


def _hms(ra):
    h = (ra / 15.0) % 24
    return "%02dh%02dm%05.2fs" % (int(h), int(h * 60) % 60, (h * 3600) % 60)


def _dms(dec):
    s = "-" if dec < 0 else "+"
    d = abs(dec)
    return "%s%02d°%02d′%04.1f″" % (s, int(d), int(d * 60) % 60, (d * 3600) % 60)


def fmt_ra(ra_deg):
    """RA in degrees as hours, minutes, seconds: "05 34 31.9"."""
    h = (float(ra_deg) / 15.0) % 24.0
    hh = int(h)
    mm = int((h - hh) * 60.0)
    ss = ((h - hh) * 60.0 - mm) * 60.0
    if ss < 0.0:                                     # a rounding step below zero would print as "-0.0"
        ss = 0.0
    ss = round(ss, 1)
    if ss >= 60.0:
        ss -= 60.0
        mm += 1
    if mm >= 60:
        mm -= 60
        hh = (hh + 1) % 24
    return "%02d %02d %04.1f" % (hh, mm, ss)


def fmt_dec(dec_deg):
    """Dec in degrees as degrees, minutes, seconds: "+22 00 52"."""
    dec = float(dec_deg)
    sign = "-" if dec < 0 else "+"
    d = abs(dec)
    dd = int(d)
    mm = int((d - dd) * 60.0)
    ss = int(round(((d - dd) * 60.0 - mm) * 60.0))
    if ss >= 60:
        ss -= 60
        mm += 1
    if mm >= 60:
        mm -= 60
        dd += 1
    return "%s%02d %02d %02d" % (sign, dd, mm, ss)


def parse_radec(text):
    """RA/Dec in degrees from what was typed, or None when the text is not a position.

    Sexagesimal is what the panel shows and what the sites quote: "05 34 31.9 +22 00 52",
    "05:34:31.9 +22:00:52", "05h34m31.9s +22d00m52s" and the degree/minute/second spellings all work. Plain
    degrees are accepted as well ("83.633 +22.014"), where a single RA number above 24 cannot be hours. The
    declination needs its sign when hours and degrees are written without separators, which is why the panel
    always puts one in.
    """
    raw = (text or "").strip().replace("−", "-").replace("–", "-")
    if not raw:
        return None
    s = re.sub(r"(?<=\d)[hmsd](?![A-Za-z])", " ", raw, flags=re.I)
    for ch in "°′″'\"":
        s = s.replace(ch, " ")
    for ch in ":,;":
        s = s.replace(ch, " ")
    if re.search(r"[A-Za-z]", s):                    # letters left over: a name, not a position
        return None
    tokens = s.split()
    if not tokens or len(tokens) < 2:
        return None
    # merge a token that is only a sign into the number after it
    merged = []
    i = 0
    while i < len(tokens):
        if tokens[i] in ("+", "-") and i + 1 < len(tokens):
            merged.append(tokens[i] + tokens[i + 1])
            i += 2
        else:
            merged.append(tokens[i])
            i += 1
    try:
        values = [float(t) for t in merged]
    except ValueError:
        return None
    if any(not math.isfinite(v) for v in values):
        return None
    signs = [i for i, t in enumerate(merged) if t.startswith(("+", "-"))]
    if signs and signs[0] > 0:
        split = signs[0]
    else:
        # no sign: only an even count splits unambiguously (2 = degrees each, 4 = 2+2, 6 = 3+3); a stray
        # odd count would have to guess which side gets the extra number, and a wrong hint is worse than none
        n = len(values)
        if n not in (2, 4, 6):
            return None
        split = n // 2
    ra_parts, dec_parts = values[:split], values[split:]
    if not ra_parts or not dec_parts or len(ra_parts) > 3 or len(dec_parts) > 3:
        return None
    for parts in (ra_parts, dec_parts):
        if any(abs(p) >= 60 for p in parts[1:]):
            return None
    ra = ra_parts[0] + (ra_parts[1] / 60.0 if len(ra_parts) > 1 else 0.0) \
        + (ra_parts[2] / 3600.0 if len(ra_parts) > 2 else 0.0)
    if len(ra_parts) == 1 and ra > 24.0:             # a single number that large cannot be hours
        ra_deg = ra
    else:
        ra_deg = ra * 15.0
    dec = abs(dec_parts[0]) + (dec_parts[1] / 60.0 if len(dec_parts) > 1 else 0.0) \
        + (dec_parts[2] / 3600.0 if len(dec_parts) > 2 else 0.0)
    dec_deg = -dec if "-" in merged[split] else dec
    ra_deg %= 360.0
    if not (-90.0 <= dec_deg <= 90.0):
        return None
    return (ra_deg, dec_deg)


def _dark_palette(app, theme="dark"):
    """Apply a theme to the whole application: Fusion draws the parts QSS does not reach, and the palette
    keeps dialogs, tooltips and menus in the same tones as the rest."""
    set_theme(theme)
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor(C["bg"]))
    p.setColor(QPalette.WindowText, QColor(C["fg1"]))
    p.setColor(QPalette.Base, QColor(C["surface"]))
    p.setColor(QPalette.AlternateBase, QColor(C["surface2"]))
    p.setColor(QPalette.Text, QColor(C["fg1"]))
    p.setColor(QPalette.PlaceholderText, QColor(C["fg5"]))
    p.setColor(QPalette.Button, QColor(C["surface2"]))
    p.setColor(QPalette.ButtonText, QColor(C["fg1"]))
    p.setColor(QPalette.BrightText, QColor(C["accent"]))
    p.setColor(QPalette.Link, QColor(C["accent"]))
    p.setColor(QPalette.Highlight, QColor(C["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(C["on_accent"]))
    p.setColor(QPalette.ToolTipBase, QColor(C["surface"]))
    p.setColor(QPalette.ToolTipText, QColor(C["fg1"]))
    p.setColor(QPalette.Mid, QColor(C["stripe"]))
    p.setColor(QPalette.Dark, QColor(C.get("shadow", C["canvas"])))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor(C["disabled"]))
    app.setPalette(p)
    app.setStyleSheet(PANEL_QSS)          # the application-wide rules follow the theme too


def _install_excepthook():
    """The window build has no console: unexpected errors go to %APPDATA%\\TTSkyAnnot\\crash.log and a dialog."""
    log_path = os.path.join(os.path.dirname(config._path()), "crash.log")

    def hook(tp, val, tb):
        text = "".join(traceback.format_exception(tp, val, tb))
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n=== %s ===\n%s" % (__version__, text))
        except OSError:
            pass
        try:
            QMessageBox.critical(None, "TTSkyAnnot", "%s\n\n%s" % (val, log_path))
        except Exception:
            pass
    sys.excepthook = hook


def main(files=None):
    _install_excepthook()
    # 注册「打开方式」：让资源管理器里右键图片能看到本程序，并直接带文件启动
    try:
        from . import shellex
        shellex.register()
    except Exception:
        pass
    # 清掉上次更新残留（%TEMP% 里的 zip 与解包目录，每个约 400 MB）
    try:
        from . import updater
        updater.sweep_temp()
        updater.cleanup_old(updater.app_dir())
    except Exception:
        pass
    cfg = config.load()
    set_language(cfg.get("language", ""))
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication.instance() or QApplication(sys.argv)
    _dark_palette(app, cfg.get("theme", "dark"))
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setWindowIcon(QIcon(os.path.join(catalogs.data_dir(), "app", "skyannot.png")))
    w = MainWindow(cfg, files)
    w.show()
    return app.exec_()

