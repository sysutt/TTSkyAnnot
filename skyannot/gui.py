"""TTSkyAnnot window: quick viewer for FITS / XISF / TIFF / JPEG / PNG plus plate solving and annotation.

Viewer: automatic screen stretch for linear data (linked or per channel), wheel zoom, drag to pan, 1:1 and
fit, raw pixel values and RA/Dec under the cursor, FITS/XISF header table, Left/Right for the previous/next
image in the folder. Images that already carry a solution show their deep-sky objects at once (bundled
catalogues, no network).
Annotation: queue of images (drop or open several), solve + refine + write outputs, results drawn over the
image.
"""
from __future__ import annotations

import os
import sys
import traceback

import numpy as np
from PyQt5.QtCore import QPointF, QRectF, Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QDesktopServices, QFont, QIcon, QImage, QKeySequence, QPainter,
                         QPainterPath, QPalette, QPen, QPixmap)
from PyQt5.QtWidgets import (QAbstractItemView, QAction, QActionGroup, QApplication, QCheckBox, QComboBox,
                             QGridLayout,
                             QDialog, QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsPixmapItem,
                             QGraphicsRectItem,
                             QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                             QPlainTextEdit, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QToolBar,
                             QVBoxLayout, QWidget)

from . import __version__, catalogs, config, imageio, run, style, wcsio
from . import shapes as shapes_mod
from .i18n import set_language, t

ACCENT = "#55DDA0"
# annotation groups the user can switch on and off: (key, label)
GROUPS = (("galaxy", "星系"), ("nebula", "星云"), ("cluster", "星团"), ("dark", "暗星云"),
          ("other", "其他 NGC/IC 条目"), ("named", "亮星名"), ("grid", "坐标网格"))


# ───────────────────────────── workers ─────────────────────────────
class LoadWorker(QThread):
    done = pyqtSignal(object)            # dict

    def __init__(self, path, stretch, parent=None):
        super().__init__(parent)
        self.path, self.stretch = path, stretch

    def run(self):
        out = {"path": self.path}
        try:
            img = imageio.load(self.path)
            out["mode"] = imageio.auto_mode(img) if self.stretch == "auto" else self.stretch
            disp = imageio.display_stretch(img, out["mode"])
            a = np.ascontiguousarray((np.clip(disp, 0, 1) * 255 + 0.5).astype(np.uint8))
            h, w = a.shape[:2]
            out["qimage"] = QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy()
            out["img"] = img
            wcs = wcsio.read_existing(img)
            out["wcs"] = wcs
            if wcs is not None:
                try:
                    out["shapes"], out["named"], out["grid"] = run.local_overlay(img, wcs)
                except Exception:
                    out["shapes"], out["named"], out["grid"] = [], [], []
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, e)
        self.done.emit(out)


class AnnotateWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, float)     # queue index, fraction
    finished_one = pyqtSignal(int, object)

    def __init__(self, items, opt, parent=None):
        super().__init__(parent)
        self.items, self.opt = items, opt     # [(index, path)]

    def run(self):
        for idx, path in self.items:
            try:
                res = run.process(path, self.opt, log=self.log.emit,
                                  progress=lambda f, msg, i=idx: self.progress.emit(i, f))
            except Exception as e:                     # never let a worker die silently
                res = run.Result(image=path, error="%s\n%s" % (e, traceback.format_exc()))
            self.finished_one.emit(idx, res)


# ───────────────────────────── image view ─────────────────────────────
class ImageView(QGraphicsView):
    moved = pyqtSignal(float, float)      # scene coordinates
    files_dropped = pyqtSignal(list)
    zoom_changed = pyqtSignal(float)

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
        self.setBackgroundBrush(QColor("#0B0E12"))
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._fit = True
        self.placeholder = self.scene().addText("")
        self.placeholder.setDefaultTextColor(QColor("#7A8794"))
        f = QFont()
        f.setPointSize(14)
        self.placeholder.setFont(f)

    def set_placeholder(self, text):
        self.placeholder.setPlainText(text)
        self.placeholder.setVisible(bool(text))
        if text:
            self.setSceneRect(self.placeholder.boundingRect())
            self.centerOn(self.placeholder)

    def set_image(self, qimage, keep_view=False):
        self.set_placeholder("")
        self.pix.setPixmap(QPixmap.fromImage(qimage))
        self.setSceneRect(QRectF(0, 0, qimage.width(), qimage.height()))
        self.overlay.setRect(0, 0, qimage.width(), qimage.height())
        # a small grey copy tells the label placement where the picture is bright
        k = min(1.0, 1200.0 / max(1, qimage.width(), qimage.height()))
        g = qimage.scaled(max(1, int(qimage.width() * k)), max(1, int(qimage.height() * k)),
                          Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_Grayscale8)
        ptr = g.bits()
        ptr.setsize(g.byteCount())
        arr = np.frombuffer(ptr, np.uint8).reshape(g.height(), g.bytesPerLine())[:, :g.width()]
        self._bright = style.Brightness(arr.astype(np.float32) / 255.0, k)
        if not keep_view:
            self.fit()

    def fit(self):
        if self.pix.pixmap().isNull():
            return
        self._fit = True
        self.fitInView(self.pix, Qt.KeepAspectRatio)
        self._relayout()
        self.zoom_changed.emit(self.transform().m11())

    def one_to_one(self):
        self._fit = False
        self.setTransform(self.transform().fromScale(1.0, 1.0))
        self._relayout()
        self.zoom_changed.emit(1.0)

    def wheelEvent(self, ev):
        k = 1.25 if ev.angleDelta().y() > 0 else 0.8
        s = self.transform().m11() * k
        if 0.02 < s < 40:
            self._fit = False
            self.scale(k, k)
            self._relayout()
            self.zoom_changed.emit(s)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._fit:
            self.fit()

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
        self._clear_items()

    def _clear_items(self):
        for it in self.overlay.childItems():
            self.scene().removeItem(it)

    def draw_overlay(self, shapes, named, grid=None, categories=None, show_named=True, shape_style="outline"):
        self._overlay_args = (shapes, named, grid, categories, show_named, shape_style)
        self._draw_overlay(*self._overlay_args)

    def _relayout(self):
        """Labels are laid out in screen pixels, so they are placed again when the zoom changes."""
        if getattr(self, "_overlay_args", None):
            self._draw_overlay(*self._overlay_args)

    def _draw_overlay(self, shapes, named, grid, categories, show_named, shape_style):
        self._clear_items()
        W, H = self.pix.pixmap().width(), self.pix.pixmap().height()
        s = self.transform().m11() or 1.0
        font = QFont("Segoe UI", 9, QFont.DemiBold)
        sfont = QFont("Segoe UI", 9)
        gfont = QFont("Segoe UI", 8)
        gcol = QColor(style.GRID_COLOR)
        lcol = QColor(gcol)
        lcol.setAlphaF(style.GRID_LINE_ALPHA)
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
        for e in shapes or []:
            if categories is not None and e.get("category") not in categories:
                continue
            col = QColor(style.color_for(e))
            pen = QPen(col, 1.6)
            pen.setCosmetic(True)
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
                prio = 0 if e["label"][:1] == "M" and e["label"][1:2].isdigit() else 1
                labels.append((prio, anchors, e["label"], col, font, 6 if e.get("kind") == "point" else 0))
        for r in (named or []) if show_named else []:
            col = QColor(style.STAR_COLOR)
            self._marker(r.x, r.y, 7, col)
            name = r.name + (" " + r.extra.get("common") if r.extra.get("common") else "")
            labels.append((2, [(r.x, r.y, None)], name, col, sfont, 7))
        # greedy placement in screen pixels: outside each outline (or around a point), inside the frame, clear
        # of labels already placed and preferably on a dark background; Messier objects choose first, star
        # names last
        placed = list(grid_boxes)
        for _p, anchors, text, col, f, rad in sorted(labels, key=lambda lb: lb[0]):
            it = self._label(0, 0, text, col, f)
            bw, bh = it.boundingRect().width(), it.boundingRect().height()
            gap = 5 + rad
            boxes = []
            for ax, ay, outward in anchors:
                sx, sy = ax * s, ay * s
                for ox, oy in style.label_offsets(outward, bw, bh, gap):
                    bx = min(max(sx + ox, 2), W * s - bw - 2)
                    by = min(max(sy + oy, 2), H * s - bh - 2)
                    boxes.append((ax, ay, bx, by))
            rects = [(bx / s, by / s, (bx + bw) / s, (by + bh) / s) for _ax, _ay, bx, by in boxes]  # image px
            best = style.choose_box(rects, placed, getattr(self, "_bright", None))
            ax, ay, bx, by = boxes[rects.index(best)]
            placed.append(best)
            it.parentItem().setPos(ax, ay)
            it.setPos(bx - ax * s, by - ay * s)

    def _marker(self, x, y, r, col):
        it = QGraphicsEllipseItem(-r, -r, 2 * r, 2 * r)
        pen = QPen(col, 1.4)
        pen.setCosmetic(True)
        it.setPen(pen)
        it.setPos(x, y)
        it.setFlag(it.ItemIgnoresTransformations)
        it.setParentItem(self.overlay)

    def _label(self, x, y, text, col, font, dx=4, dy=4):
        # An invisible anchor that ignores the view's zoom sits on the object; the text is its child, so the
        # (dx, dy) offset stays in screen pixels at any zoom (an item transform would be scaled with it).
        anchor = QGraphicsRectItem(0, 0, 0, 0)
        anchor.setPen(QPen(Qt.NoPen))
        anchor.setFlag(anchor.ItemIgnoresTransformations)
        anchor.setPos(x, y)
        it = QGraphicsSimpleTextItem(text, anchor)
        it.setFont(font)
        it.setBrush(QBrush(col))
        it.setPos(dx, dy)
        anchor.setParentItem(self.overlay)
        return it


# ───────────────────────────── dialogs ─────────────────────────────
class HeaderDialog(QDialog):
    def __init__(self, img, parent=None):
        super().__init__(parent)
        self.setWindowTitle("%s · %s" % (t("文件头"), os.path.basename(img.path)))
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
        lay = QVBoxLayout(self)
        lay.addWidget(tab)


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("设置"))
        self.cfg = dict(cfg)
        f = QFormLayout(self)
        self.lang = QComboBox()
        for code, name in (("", t("跟随系统")), ("zh", "中文"), ("en", "English")):
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(cfg.get("language", ""))))
        f.addRow(t("界面语言"), self.lang)
        self.nova = QLineEdit(cfg.get("nova_api_key", ""))
        self.nova.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        f.addRow(t("nova.astrometry.net API key(可选,最后的兜底)"), self.nova)
        self.astap = QLineEdit(cfg.get("astap_path", ""))
        b = QPushButton(t("浏览…"))
        b.clicked.connect(lambda: self._browse(self.astap, file=True))
        row = QHBoxLayout()
        row.addWidget(self.astap)
        row.addWidget(b)
        f.addRow(t("ASTAP 路径(可选,离线解析)"), row)
        self.gmag = QDoubleSpinBox()
        self.gmag.setRange(8, 21)
        self.gmag.setDecimals(1)
        self.gmag.setValue(float(cfg.get("gaia_mag", 16)))
        f.addRow(t("Gaia 星等上限"), self.gmag)
        self.out = QLineEdit(cfg.get("out_dir", ""))
        b2 = QPushButton(t("浏览…"))
        b2.clicked.connect(lambda: self._browse(self.out, file=False))
        row2 = QHBoxLayout()
        row2.addWidget(self.out)
        row2.addWidget(b2)
        f.addRow(t("输出位置(留空 = 图片所在文件夹)"), row2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(t("确定"))
        bb.button(QDialogButtonBox.Cancel).setText(t("取消"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _browse(self, edit, file):
        p = (QFileDialog.getOpenFileName(self, "", edit.text())[0] if file
             else QFileDialog.getExistingDirectory(self, "", edit.text()))
        if p:
            edit.setText(p)

    def values(self):
        self.cfg.update(language=self.lang.currentData(), nova_api_key=self.nova.text().strip(),
                        astap_path=self.astap.text().strip(), gaia_mag=self.gmag.value(),
                        out_dir=self.out.text().strip())
        return self.cfg


# ───────────────────────────── main window ─────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self, cfg, files=None):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("%s %s" % (t("TTSkyAnnot · 星图查看与标注"), __version__))
        self.resize(1400, 900)
        self.img = None
        self.wcs = None
        self.current = None
        self.loader = None
        self.worker = None
        self.results = {}           # path -> Result
        self.overlay_data = {}      # path -> (shapes, named, grid)

        self.view = ImageView()
        self.view.set_placeholder(t("把图片拖到这里,或点「打开」"))
        self.view.moved.connect(self._on_move)
        self.view.files_dropped.connect(self.add_files)
        self.view.zoom_changed.connect(lambda s: self.lbl_zoom.setText("%.0f%%" % (s * 100)))
        self.setCentralWidget(self.view)
        self._build_toolbar()
        self._build_panel()
        self.lbl_pos = QLabel("")
        self.lbl_zoom = QLabel("")
        self.lbl_info = QLabel("")
        sb = self.statusBar()
        sb.addWidget(self.lbl_info, 1)
        sb.addPermanentWidget(self.lbl_pos)
        sb.addPermanentWidget(self.lbl_zoom)
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
        act(t("上一张"), lambda: self.step(-1), Qt.Key_Left)
        act(t("下一张"), lambda: self.step(1), Qt.Key_Right)
        tb.addSeparator()
        act(t("适应窗口"), self.view.fit, "F")
        act(t("原始大小"), self.view.one_to_one, "1")
        tb.addSeparator()
        tb.addWidget(QLabel(" %s " % t("拉伸")))
        self.cmb_stretch = QComboBox()
        for code, name in (("auto", t("自动")), ("linked", t("联动")), ("unlinked", t("分通道")), ("none", t("不拉伸"))):
            self.cmb_stretch.addItem(name, code)
        self.cmb_stretch.setCurrentIndex(max(0, self.cmb_stretch.findData(self.cfg.get("stretch", "auto"))))
        self.cmb_stretch.currentIndexChanged.connect(self._stretch_changed)
        tb.addWidget(self.cmb_stretch)
        tb.addSeparator()
        self.act_overlay = act(t("显示标注"), self._toggle_overlay, "A", checkable=True)
        self.act_overlay.setChecked(bool(self.cfg.get("show_overlay", True)))
        act(t("文件头"), self.show_header, "H")
        tb.addSeparator()
        act(t("设置"), self.settings)

    def _build_panel(self):
        dock = QDockWidget(t("标注"), self)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        w = QWidget()
        lay = QVBoxLayout(w)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.currentRowChanged.connect(self._row_changed)
        lay.addWidget(self.list, 2)
        self.ed_target = QLineEdit()
        self.ed_target.setPlaceholderText(t("目标名(可选)"))
        self.ed_target.setToolTip(t("例如 M31。没有文件头的 JPG/PNG 盲解失败时填它"))
        lay.addWidget(self.ed_target)
        lay.addWidget(QLabel(t("标注内容")))
        self.chk_groups = {}
        saved = self.cfg.get("groups")
        grid_lay = QGridLayout()
        grid_lay.setContentsMargins(0, 0, 0, 0)
        for i, (key, text) in enumerate(GROUPS):
            c = QCheckBox(t(text))
            c.setChecked(True if saved is None else key in saved)
            c.toggled.connect(self._groups_changed)
            self.chk_groups[key] = c
            grid_lay.addWidget(c, i // 2, i % 2)
        lay.addLayout(grid_lay)
        row = QHBoxLayout()
        row.addWidget(QLabel(t("天体形状")))
        self.cmb_shape = QComboBox()
        for text, key in (("轮廓线", "outline"), ("圆圈", "circle")):
            self.cmb_shape.addItem(t(text), key)
        self.cmb_shape.setCurrentIndex(max(0, self.cmb_shape.findData(self.cfg.get("shape_style", "outline"))))
        self.cmb_shape.setToolTip(t("轮廓线：有轮廓数据的星云按实际形状描边。圆圈：一律按星表尺寸画圆，长条形天体画成椭圆。"))
        self.cmb_shape.currentIndexChanged.connect(self._shape_changed)
        row.addWidget(self.cmb_shape, 1)
        lay.addLayout(row)
        lay.addWidget(QLabel(t("输出")))
        self.chk = {}
        for key, text in (("write_txt", "天体列表 TXT(PixInsight 格式)"), ("write_preview", "标注预览图"),
                          ("write_outlines", "轮廓坐标文件"), ("write_wcs", "解析结果写回图片")):
            c = QCheckBox(t(text))
            c.setChecked(bool(self.cfg.get(key, True)))
            if key == "write_wcs":
                c.setToolTip(t("写回会修改原文件的文件头(像素不动)。FITS/XISF 写进文件头,JPG/PNG/TIFF 另存同名 .wcs 文件。"))
            self.chk[key] = c
            lay.addWidget(c)
        self.btn_start = QPushButton(t("解析并标注"))
        self.btn_start.setStyleSheet("QPushButton{background:%s;color:#06140D;font-weight:600;padding:7px;"
                                     "border-radius:4px} QPushButton:disabled{background:#2A3A33;color:#6C7A73}" % ACCENT)
        self.btn_start.clicked.connect(self.start)
        lay.addWidget(self.btn_start)
        self.prog = QProgressBar()
        self.prog.setRange(0, 1000)
        self.prog.setTextVisible(False)
        self.prog.setFixedHeight(6)
        lay.addWidget(self.prog)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(4000)
        lay.addWidget(self.logbox, 3)
        self.btn_folder = QPushButton(t("打开输出文件夹"))
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_folder.setEnabled(False)
        lay.addWidget(self.btn_folder)
        dock.setWidget(w)
        dock.setMinimumWidth(320)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    # ---- files ----------------------------------------------------------
    def open_dialog(self):
        exts = " ".join("*" + e for e in imageio.SUPPORTED)
        paths, _ = QFileDialog.getOpenFileNames(self, t("打开"), self.cfg.get("last_dir", ""),
                                                "Images (%s);;All files (*)" % exts)
        if paths:
            self.add_files(paths)

    def add_files(self, paths):
        paths = [os.path.abspath(p) for p in paths if os.path.splitext(p)[1].lower() in imageio.SUPPORTED]
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
        self.list.setCurrentRow(first_row)

    def _row_changed(self, row):
        if row >= 0:
            self.show_image(self.list.item(row).data(Qt.UserRole))

    def step(self, d):
        """Previous / next supported image in the current image's folder."""
        if not self.current:
            return
        folder = os.path.dirname(self.current)
        files = sorted(f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in imageio.SUPPORTED
                       and not f.endswith(".annotated.jpg"))
        try:
            i = files.index(os.path.basename(self.current))
        except ValueError:
            return
        j = i + d
        if 0 <= j < len(files):
            self.add_files([os.path.join(folder, files[j])])

    def show_image(self, path, keep_view=False):
        self.current = path
        self.lbl_info.setText(t("正在读取…") + " " + os.path.basename(path))
        self._keep_view = keep_view
        ld = LoadWorker(path, self.cmb_stretch.currentData(), self)
        ld.done.connect(self._loaded)
        self.loader = ld
        ld.start()

    def _loaded(self, out):
        if out["path"] != self.current:
            return                                  # a newer request superseded this one
        if "error" in out:
            self.lbl_info.setText("%s: %s" % (t("读取失败"), out["error"]))
            return
        self.img = out["img"]
        res = self.results.get(out["path"])
        self.wcs = res.wcs if (res and res.ok) else out.get("wcs")
        if res and res.ok:
            self.overlay_data[out["path"]] = (res.shapes, res.named, res.grid)
        elif out.get("wcs") is not None:
            self.overlay_data[out["path"]] = (out.get("shapes"), out.get("named"), out.get("grid"))
        self.view.set_image(out["qimage"], keep_view=getattr(self, "_keep_view", False))
        self._refresh_overlay()
        im = self.img
        mode_name = {"linked": t("联动"), "unlinked": t("分通道"), "none": t("不拉伸")}.get(out.get("mode"), "")
        stretch_txt = ("%s: %s%s" % (t("拉伸"), t("自动") + " → " if self.cmb_stretch.currentData() == "auto" else "",
                                     mode_name)) if im.linear else ""
        self.lbl_info.setText("%s   %d × %d   %s%s   %s   %s" % (
            os.path.basename(im.path), im.width, im.height, im.fmt.upper(),
            "  (linear)" if im.linear else "", t("已解析") if self.wcs is not None else t("未解析"), stretch_txt))
        self.setWindowTitle("%s — TTSkyAnnot" % os.path.basename(im.path))

    def _stretch_changed(self):
        self.cfg["stretch"] = self.cmb_stretch.currentData()
        if self.current:
            self.show_image(self.current, keep_view=True)

    def _groups_on(self):
        return {k for k, c in self.chk_groups.items() if c.isChecked()}

    def _groups_changed(self):
        self.cfg["groups"] = sorted(self._groups_on())
        self._refresh_overlay()

    def _shape_changed(self):
        self.cfg["shape_style"] = self.cmb_shape.currentData()
        self._refresh_overlay()

    def _toggle_overlay(self):
        self.cfg["show_overlay"] = self.act_overlay.isChecked()
        self._refresh_overlay()

    def _refresh_overlay(self):
        data = self.overlay_data.get(self.current)
        if self.act_overlay.isChecked() and data:
            on = self._groups_on()
            self.view.draw_overlay(data[0], data[1], data[2] if "grid" in on else None,
                                   categories={k for k in on if k not in ("named", "grid")},
                                   show_named="named" in on, shape_style=self.cmb_shape.currentData())
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

    def settings(self):
        d = SettingsDialog(self.cfg, self)
        if d.exec_():
            old_lang = self.cfg.get("language", "")
            self.cfg = d.values()
            config.save(self.cfg)
            if self.cfg.get("language", "") != old_lang:
                QMessageBox.information(self, "TTSkyAnnot", "Restart TTSkyAnnot to switch the language. / 重启后生效。")

    # ---- annotation -----------------------------------------------------
    def start(self):
        if self.worker is not None and self.worker.isRunning():
            return
        rows = sorted({i.row() for i in self.list.selectedIndexes()}) or (
            [self.list.currentRow()] if self.list.currentRow() >= 0 else [])
        if not rows:
            return
        for k, c in self.chk.items():
            self.cfg[k] = c.isChecked()
        config.save(self.cfg)
        opt = run.Options(target=self.ed_target.text().strip(), astap_path=self.cfg.get("astap_path", ""),
                          nova_key=self.cfg.get("nova_api_key", ""), gaia_mag=float(self.cfg.get("gaia_mag", 16)),
                          write_txt=self.cfg["write_txt"], write_preview=self.cfg["write_preview"],
                          write_outlines=self.cfg["write_outlines"], write_wcs=self.cfg["write_wcs"],
                          out_dir=self.cfg.get("out_dir", ""),
                          stretch=self.cmb_stretch.currentData(),     # the preview looks like the viewer
                          categories=tuple(k for k in self._groups_on() if k not in ("named", "grid")),
                          named="named" in self._groups_on(), grid="grid" in self._groups_on(),
                          shape_style=self.cmb_shape.currentData())
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
        w.finished.connect(self._all_done)
        self.worker = w
        w.start()

    def _one_done(self, row, res):
        self._k_done += 1
        it = self.list.item(row)
        name = os.path.basename(it.data(Qt.UserRole))
        if res.ok:
            st = res.stats
            acc = st.get("median_px")
            it.setText("✓ " + name)
            it.setToolTip("%s\n%d %s%s" % (res.image, st.get("matched", 0), t("颗星配准"),
                                           ("  ·  %s %.2f %s" % (t("精度"), acc, t("像素"))) if acc is not None else ""))
            self.results[res.image] = res
            self.btn_folder.setEnabled(True)
            self._last_out = next(iter(res.outputs.values()), res.image)
            if res.image == self.current:
                self.show_image(res.image, keep_view=True)      # reload: header may now carry the solution
        else:
            it.setText("✗ " + name)
            it.setToolTip(res.error)

    def _all_done(self):
        self.btn_start.setEnabled(True)
        self.btn_start.setText(t("解析并标注"))
        self.prog.setValue(1000)

    def open_folder(self):
        p = getattr(self, "_last_out", None)
        if p:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))

    def closeEvent(self, ev):
        config.save(self.cfg)
        super().closeEvent(ev)


def _hms(ra):
    h = (ra / 15.0) % 24
    return "%02dh%02dm%05.2fs" % (int(h), int(h * 60) % 60, (h * 3600) % 60)


def _dms(dec):
    s = "-" if dec < 0 else "+"
    d = abs(dec)
    return "%s%02d°%02d′%04.1f″" % (s, int(d), int(d * 60) % 60, (d * 3600) % 60)


def _dark_palette(app):
    app.setStyle("Fusion")
    p = QPalette()
    base, panel, text = QColor("#12161B"), QColor("#1A2027"), QColor("#DCE3EA")
    p.setColor(QPalette.Window, panel)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, panel)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor("#232A33"))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.Highlight, QColor(ACCENT))
    p.setColor(QPalette.HighlightedText, QColor("#06140D"))
    p.setColor(QPalette.ToolTipBase, panel)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#6C7A86"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6C7A86"))
    app.setPalette(p)


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
    cfg = config.load()
    set_language(cfg.get("language", ""))
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication.instance() or QApplication(sys.argv)
    _dark_palette(app)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setWindowIcon(QIcon(os.path.join(catalogs.data_dir(), "app", "skyannot.png")))
    w = MainWindow(cfg, files)
    w.show()
    return app.exec_()
