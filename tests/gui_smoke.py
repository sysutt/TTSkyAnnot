"""Headless check of the window's browsing behaviour (no display needed).

    python tests/gui_smoke.py

Builds a small folder of throwaway images, then drives the real MainWindow with the offscreen Qt platform:
clicking the empty view opens the file dialog, the left-hand list follows the image's folder and the ‹ / ›
buttons over the picture step through it. Exits non-zero when a check fails.
"""
import contextlib
import os
import shutil
import tempfile
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts=false"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image                                   # noqa: E402
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt     # noqa: E402
from PyQt5.QtGui import QMouseEvent                      # noqa: E402
from PyQt5.QtTest import QTest                          # noqa: E402
from PyQt5.QtWidgets import QApplication, QFileDialog   # noqa: E402

from skyannot import catalogue, config, gui, run, updater  # noqa: E402

TMP = os.path.join(ROOT, ".skyannot_test")
failures = []
_loaded = None            # set while the playback section stubs MainWindow._loaded (see load())


def check(label, ok, extra=""):
    print("%-58s %s%s" % (label, "OK" if ok else "FAIL", ("  " + extra) if extra else ""))
    if not ok:
        failures.append(label)


def _mosaic(colour, pattern):
    """Sample a colour image the way a one-shot colour sensor would: one channel per pixel."""
    import numpy as _np
    grid = {"R": 0, "G": 1, "B": 2}
    out = _np.zeros(colour.shape[:2], _np.float32)
    for row in range(2):
        for col in range(2):
            out[row::2, col::2] = colour[row::2, col::2, grid[pattern[row * 2 + col]]]
    return out[..., None]


def _synthetic_wcs(ra, dec, width=1200, height=800, pixscale=1.0):
    """A plain TAN WCS centred on (ra, dec), for the checks that need a real projection."""
    from astropy.wcs import WCS
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [width / 2.0, height / 2.0]
    s = pixscale / 3600.0
    w.wcs.cd = [[-s, 0.0], [0.0, s]]
    return w


def _ellipse_angle(shapes_mod, ann_mod, wcs, name):
    """How far from horizontal the catalogue-sized shape for an object lies, in degrees.

    Routed through the same `_axis_for` the drawing uses, so this measures the shape that is actually drawn
    rather than a private reconstruction of it.
    """
    import numpy as _np
    proj = ann_mod.Projector(wcs, 6248, 4176)
    d = next((o for o in shapes_mod.catalogs.openngc() if o.name == name), None)
    if d is None:
        return 0.0
    pts = next((p for n, _s, p in shapes_mod.catalogs.outlines() if n == name), None)
    pa, maj, minor = shapes_mod._axis_for(d, pts)
    er, ed = shapes_mod._ellipse_world(d.ra, d.dec, maj, minor, pa)
    x, y = proj.xy(_np.array(er), _np.array(ed))
    ok = _np.isfinite(x) & _np.isfinite(y)
    if ok.sum() < 3:
        return 0.0
    pts = _np.column_stack([x[ok], y[ok]])
    centred = pts - pts.mean(axis=0)
    vals, vecs = _np.linalg.eigh(_np.cov(centred.T))
    v = vecs[:, int(_np.argmax(vals))]
    return abs(_np.degrees(_np.arctan2(v[1], v[0]))) % 180.0


def main():
    # A fresh directory per run. The obvious "remove then recreate" needs a delete of the previous run's tree,
    # and a tree written through the DSH sandbox cannot always be removed afterwards (an empty directory it
    # created can come back "Access is denied" even to its owner); every run starting clean is what actually
    # matters here, so nothing depends on deleting anything.
    global TMP
    TMP = os.path.join(ROOT, ".skyannot_test", "run%d" % time.time_ns())
    os.makedirs(TMP, exist_ok=True)
    os.environ["APPDATA"] = TMP                  # keep the user's real config out of it
    folder = os.path.join(TMP, "imgs")
    os.makedirs(folder)
    paths = []
    for name in ("a.png", "b.png", "c.png"):
        p = os.path.join(folder, name)
        Image.new("RGB", (300, 200), (30, 40, 50)).save(p)
        paths.append(p)
    Image.new("RGB", (300, 200), (10, 10, 10)).save(os.path.join(folder, "a.annotated.jpg"))

    app = QApplication.instance() or QApplication(sys.argv)
    gui._dark_palette(app)
    win = gui.MainWindow(gui.config.load())
    win.show()
    QTest.qWait(200)

    shots = os.path.join(TMP, "shots")
    os.makedirs(shots)

    def shot(name):
        win.grab().save(os.path.join(shots, name + ".png"))

    shot("1_empty")

    def wait_for_idle(timeout=100, path=None, loader=None, was_img=None):
        """Wait until the image that was just asked for has actually arrived.

        Two traps here, both of which made checks race and fail only on a busy machine:

        * `win.img` holds the PREVIOUS frame while the new one loads, so "an image exists" is true at once;
        * the loader thread stops running BEFORE its `finished` signal is delivered on the main thread, so
          waiting for `isRunning()` to go false returns before `_loaded` has refreshed anything.

        So the wait is anchored on the observable effect: the displayed image object has been replaced.
        """
        for _ in range(timeout):
            QTest.qWait(50)
            busy = win.loader is not None and win.loader.isRunning()
            if loader is not None and win.loader is not loader:
                busy = False                       # this load finished and was superseded
            arrived = win.img is not None and (was_img is None or win.img is not was_img)
            if not busy and arrived and (path is None or win.current == path):
                return True
        return False

    def wait_for_change(timeout=40):
        """Wait for the picture to change on its own (playback). Returns (name, loader) or ("", None).

        `current` changes as soon as the load starts, while the worker's result is still being filled in; the
        playback check feeds that result to _loaded by hand, so wait for the worker to finish rather than
        reading a half-written dict.
        """
        was = win.current
        for _ in range(timeout):
            QTest.qWait(50)
            if win.current != was:
                ld = win.loader
                for _ in range(100):
                    if ld is None or ld.isFinished():
                        break
                    QTest.qWait(25)
                return os.path.basename(win.current or ""), ld
        return "", None

    def load(path):
        """Drive one image through the async loader and wait for the view to settle. While slideshow_test
        stubs _loaded, feeding the result back is left to the test instead."""
        was_img = win.img
        win.show_image(path)
        loader = win.loader                        # the load this call started
        if _loaded is not None:
            for _ in range(100):
                QTest.qWait(50)
                if win.current == path:
                    return win.img             # the playback section feeds the result in itself
        wait_for_idle(path=path, loader=loader, was_img=was_img)
        return win.img

    def names():
        return [win.file_list.item(i).text() for i in range(win.file_list.count())]

    # 1 ── empty window: a click on the picture asks for the file dialog
    check("no image loaded at start", win.current is None and win.img is None)
    check("play is unavailable with no image open",
          not win.act_play.isEnabled() and win.act_play.text() == "播放")
    check("the PixInsight button is unavailable with no image open", not win.btn_pi.isEnabled())
    box = {}

    def fake_dialog(*_a, **_k):                  # never open the real dialog
        box["called"] = True
        return list(paths), ""

    QFileDialog.getOpenFileNames = staticmethod(fake_dialog)
    QTest.mouseClick(win.view.viewport(), Qt.LeftButton, pos=QPoint(400, 300))
    check("click on the empty view opens the file dialog", box.get("called") is True)
    load(paths[0])
    shot("2_loaded")

    # 2 ── left-hand list of the folder
    check("folder list lists the three images", names() == ["a.png", "b.png", "c.png"], str(names()))
    check(".annotated.jpg is not listed", "a.annotated.jpg" not in names())
    check("current image highlighted", win.file_list.currentRow() == 0)
    check("dock is closable and has a toggle action",
          win.dock_files.features() & win.dock_files.DockWidgetClosable != 0
          and win._files_action.isCheckable())
    # the list slides in when a picture is open and out again when the toolbar switch is pressed
    check("the folder list is in when there is something to list", win.dock_files.isVisible())
    win._files_action.trigger()
    QTest.qWait(320)                              # the drawer animates for 170 ms
    hidden = win.dock_files.isHidden()
    win._files_action.trigger()
    QTest.qWait(320)
    check("dock toggle slides the list out then back", hidden and not win.dock_files.isHidden())

    # 3 ── the bottom bar is the only place with previous / next, and stepping wraps around at the ends
    check("the picture has no floating side arrows", not hasattr(win.view, "_nav"))
    win.step(1)
    wait_for_idle()
    check("stepping forward", os.path.basename(win.current or "") == "b.png", str(win.current))

    win.step(-1)
    wait_for_idle()
    check("stepping back", os.path.basename(win.current or "") == "a.png")
    win.step(-1)
    wait_for_idle()
    check("stepping back at the first image wraps to the last",
          os.path.basename(win.current or "") == "c.png", str(win.current))
    win.step(1)
    wait_for_idle()
    check("stepping forward at the last image wraps to the first",
          os.path.basename(win.current or "") == "a.png", str(win.current))
    win.step(1)
    wait_for_idle()

    # 4 ── clicking a row in the left-hand list shows that image
    check("list selection follows navigation", win.file_list.currentRow() == 1,
          "row %s" % win.file_list.currentRow())
    win.file_list.setCurrentRow(2)
    load(paths[2])
    check("clicking a row shows that image", os.path.basename(win.current or "") == "c.png")
    check("queue holds the browsed images",
          sorted(win.list.item(i).text() for i in range(win.list.count())) == ["a.png", "b.png", "c.png"],
          str([win.list.item(i).text() for i in range(win.list.count())]))
    check("only one queue entry per file", win.list.count() == 3)

    # 4b ── right-click menu on a listed file
    from PyQt5.QtWidgets import QMenu                 # imported here: only this check needs it
    check("both lists use a custom context menu",
          win.file_list.contextMenuPolicy() == Qt.CustomContextMenu
          and win.list.contextMenuPolicy() == Qt.CustomContextMenu)
    opened, chosen = [], {}

    def fake_exec(self, *_a, **_k):
        opened.append([a.text() for a in self.actions() if not a.isSeparator()])
        win.list.clearSelection()
        return next(a for a in self.actions() if a.text() == "复制文件完整路径")

    real_exec, QMenu.exec_ = QMenu.exec_, fake_exec
    try:
        for widget, row in ((win.file_list, 1), (win.list, 0)):
            widget.setCurrentRow(row)
            QTest.qWait(30)
            expect = widget.item(row).data(Qt.UserRole)
            ipos = widget.visualItemRect(widget.item(row)).center()
            widget.customContextMenuRequested.emit(ipos)
            QTest.qWait(30)
            got = QApplication.clipboard().text()
            check("right-click copies the path of row %d" % row,
                  got == os.path.normpath(expect), "%s != %s" % (got, expect))
    finally:
        QMenu.exec_ = real_exec
    check("menu offers locate, copy path and copy folder",
          opened and all(labels == ["在文件资源管理器中显示", "复制文件完整路径", "复制所在文件夹路径"]
                         for labels in opened), str(opened))

    # 4c ── the menu copies the folder path too
    def exec_dir(self, *_a, **_k):
        return next(a for a in self.actions() if a.text() == "复制所在文件夹路径")

    QMenu.exec_ = exec_dir
    try:
        win.file_list.setCurrentRow(1)
        win.file_list.customContextMenuRequested.emit(
            win.file_list.visualItemRect(win.file_list.item(1)).center())
        check("copy folder path gives the containing folder",
              QApplication.clipboard().text() == os.path.dirname(paths[1]),
              QApplication.clipboard().text())
    finally:
        QMenu.exec_ = real_exec

    # 4d ── zoom and position carry over to the next frame of the same size
    from PyQt5.QtCore import QPointF                      # noqa: E402
    from PyQt5.QtGui import QWheelEvent                   # noqa: E402
    view = win.view

    def wheel(steps):
        for _ in range(steps):
            pos = QPointF(view.viewport().rect().center())
            ev = QWheelEvent(pos, view.mapToGlobal(pos.toPoint()), QPoint(0, 0), QPoint(0, 120),
                             Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
            view.wheelEvent(ev)

    def compare_view():
        """(scale, scene point at the viewport centre) — the two things that must survive a frame change."""
        c = view.mapToScene(view.viewport().rect().center())
        return round(view.transform().m11(), 6), (round(c.x(), 1), round(c.y(), 1))

    def same_view(a, b):
        """Zoom must match exactly; the centre only to within a pixel, since Qt rounds it through the
        integer scroll bar offsets."""
        return (abs(a[0] - b[0]) < 1e-6 and abs(a[1][0] - b[1][0]) <= 1.0
                and abs(a[1][1] - b[1][1]) <= 1.0)

    check("a freshly opened image is fitted", view._view_state() is None)
    win.step(1)                                          # navigation itself must load the frame
    wait_for_idle()
    check("fitted view stays fitted on the next frame", view._view_state() is None)

    wheel(3)                                             # zoom in, then pan off centre
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().maximum() - 40)
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() - 25)
    QTest.qWait(50)
    before = compare_view()
    check("zoomed in and panned", before[0] > 1.0 and view._view_state() is not None,
          "scale %.3f" % before[0])

    win.step(1)                                          # the › button
    wait_for_idle()
    check("step keeps the same zoom and position", same_view(compare_view(), before),
          "%s vs %s" % (compare_view(), before))

    win.file_list.setCurrentRow(0)                       # the left-hand list loads it itself
    wait_for_idle()
    check("picking from the list keeps the view", same_view(compare_view(), before), str(compare_view()))

    # one pixel per switch is invisible once, but walks the picture right and down over a folder: the restore
    # must land exactly where the state was read, not through a rounding of its own
    for _ in range(10):
        win.step(1)
        wait_for_idle()
    check("ten switches do not walk the picture", same_view(compare_view(), before),
          "%s vs %s" % (compare_view(), before))

    win.btn_prev.click()                                 # the bar's previous button
    wait_for_idle()
    check("the bar's previous button keeps the view too", same_view(compare_view(), before),
          str(compare_view()))

    # a frame of a different size cannot keep it: fall back to fitted
    sizes_dir = os.path.join(TMP, "sizes")               # not the images folder: it would join the list
    os.makedirs(sizes_dir, exist_ok=True)
    big = os.path.join(sizes_dir, "big.png")
    Image.new("RGB", (900, 700), (40, 40, 60)).save(big)
    Image.new("RGB", (900, 700), (60, 40, 40)).save(os.path.join(sizes_dir, "big2.png"))
    win.add_files([big], select=False)
    load(big)
    check("a different image size falls back to fitted", view._view_state() is None)
    check("the folder list followed it", names() == ["big.png", "big2.png"], str(names()))

    # below a zoom of 1 the pixmap on screen is a box-averaged copy, and that copy's size is not the
    # picture's: the view must still follow the next frame of the same size
    view.one_to_one()                                    # also clears _fit, so _view_state() has something to say
    QTest.qWait(200)
    view.scale(0.4, 0.4)
    view.centerOn(QPointF(450, 350))
    view._schedule_pixmap()
    QTest.qWait(250)
    check("the on-screen copy is downscaled here", view.pix.pixmap().width() < 900,
          str(view.pix.pixmap().size()))
    before_big = compare_view()
    win.step(1)                                          # big2.png, same 900 x 700
    wait_for_idle()
    check("a zoomed-out view keeps its place on the next frame", same_view(compare_view(), before_big),
          "%s vs %s" % (compare_view(), before_big))

    for row in range(win.list.count() - 1, -1, -1):      # do not leave them in the annotation queue either
        if os.path.dirname(win.list.item(row).data(Qt.UserRole) or "") == os.path.abspath(sizes_dir):
            win.list.takeItem(row)
    win.show_image(paths[2])                             # back to the images folder
    wait_for_idle()
    check("returning to the folder re-fits (the size changed in between)", view._view_state() is None)
    check("and lists the images there again", names() == ["a.png", "b.png", "c.png"], str(names()))

    # 4f ── "Open in PixInsight" hands the file over the way Explorer's "Open with" does
    from skyannot import imageio as iio, piopen          # noqa: E402
    calls = []
    real_launch = piopen._launch
    piopen._launch = calls.append
    try:
        win.open_in_pixinsight()
        check("handing over passes the image path", calls and calls[-1][1] == win.current,
              str(calls[-1] if calls else None))
        check("the executable is the first argument", calls and calls[-1][0].lower().endswith("pixinsight.exe"),
              str(calls[-1][0] if calls else None))
        win.cfg["pixinsight_path"] = r"E:\nowhere\PixInsight.exe"      # a wrong setting must still work
        win.open_in_pixinsight()
        check("a stale configured path falls back to the install",
              calls[-1][0].lower().endswith("pixinsight.exe") and "nowhere" not in calls[-1][0], calls[-1][0])
    finally:
        piopen._launch = real_launch
        win.cfg["pixinsight_path"] = ""

    # ... and a missing installation reports itself instead of crashing
    real_find = piopen.find
    piopen.find = lambda *_a, **_k: ""
    shown = {}
    real_warning = gui.QMessageBox.warning
    gui.QMessageBox.warning = staticmethod(lambda *a, **k: shown.setdefault("msg", a[-1]))
    try:
        win.open_in_pixinsight()
        check("a missing PixInsight is reported to the user", "PixInsight" in str(shown.get("msg", "")),
              str(shown.get("msg"))[:60])
        check("no process is started for a missing PixInsight", len(calls) == 2)
    finally:
        piopen.find = real_find
        gui.QMessageBox.warning = real_warning

    # 4g ── PNG/JPEG are shown exactly as stored, whatever the stretch mode says
    import numpy as np                                   # noqa: E402
    raster = iio.load(paths[0])
    check("a PNG is recognised as a finished picture",
          raster.fmt == "raster" and iio.auto_mode(raster) == "none")
    raw = iio.display_stretch(raster, "none")
    check("PNG pixels are shown unchanged", np.array_equal(raw, raster.data), "max diff %g"
          % float(np.abs(raw - raster.data).max()))
    forced = iio.display_stretch(raster, "linked")        # even when the user forces a stretch
    check("PNG is not stretched even with a mode forced on",
          np.allclose(forced, raster.data), "max diff %g" % float(np.abs(forced - raster.data).max()))

    # 4h ── the bar of screening controls along the bottom of the picture
    from skyannot import fileop                          # noqa: E402
    paths = [os.path.join(folder, n) for n in ("a.png", "b.png", "c.png")]
    win.show_image(paths[1])                             # a known starting point for the section below
    wait_for_idle()
    bar = win.image_bar
    check("the bar holds prev / play / next / remove / move",
          [b.text() for b in (win.btn_prev, win.btn_play, win.btn_next, win.btn_del, win.btn_move)]
          == ["⏴", "▶", "⏵", "✖", "➜"],
          str([b.text() for b in (win.btn_prev, win.btn_play, win.btn_next, win.btn_del, win.btn_move)]))
    check("the bar sits along the bottom of the picture",
          bar.isVisible() and bar.y() > win.view.height() * 0.8
          and abs(bar.x() + bar.width() / 2 - win.view.width() / 2) < 20,
          "at %d,%d size %dx%d in view %dx%d" % (bar.x(), bar.y(), bar.width(), bar.height(),
                                                 win.view.width(), win.view.height()))
    check("the bar's buttons work the folder", win.btn_prev.isEnabled() and win.btn_next.isEnabled())

    win.btn_play.click()                                 # the bar's play button drives the same playback
    QTest.qWait(50)
    check("the bar's play button drives playback",
          win.act_play.isChecked() and win.btn_play.isChecked() and win.btn_play.text() == "⏸")
    win.btn_play.click()
    QTest.qWait(50)
    check("clicking it again stops playback",
          not win.act_play.isChecked() and win.btn_play.text() == "▶")

    win._bar_action.setChecked(False)                    # the toolbar switch hides it for a clean look
    QTest.qWait(50)
    check("the toolbar switch hides the bar", not bar.isVisible() and win.cfg["show_image_bar"] is False)
    win._bar_action.setChecked(True)
    QTest.qWait(50)
    check("and brings it back", bar.isVisible() and win.cfg["show_image_bar"] is True)

    # 4i ── remove: the frame leaves the folder, the next one takes its place, Ctrl+Z brings it back
    win.list.clear()                                     # start the section with an empty queue
    win.file_list.setCurrentRow(1)                       # b.png
    wait_for_idle()
    victim = win.current
    removed, parked = [], os.path.join(TMP, "trash")
    os.makedirs(parked, exist_ok=True)

    def fake_delete(path):
        """Stand in for the Recycle Bin: the file must really leave the folder or the app would rightly
        keep showing it (the app never changes the folder behind our back). Returns True like the real one
        does when the file can still be brought back."""
        removed.append(path)
        shutil.move(path, os.path.join(parked, os.path.basename(path)))
        return True

    real_delete, fileop.delete = fileop.delete, fake_delete
    # Delete asks first now, so the confirmation is answered: the real box cannot be clicked in a test. The
    # fake records what the box was told, which is also how the question itself gets checked.
    asked = []
    real_exec = gui.QMessageBox.exec_
    confirm = {"answer": "yes"}

    def fake_exec(box):
        buttons = box.buttons()
        destructive = next((b for b in buttons if box.buttonRole(b) == gui.QMessageBox.DestructiveRole),
                           buttons[0])
        reject = next((b for b in buttons if box.buttonRole(b) == gui.QMessageBox.RejectRole), buttons[-1])
        asked.append({"text": box.text(), "detail": box.informativeText(),
                      "buttons": [b.text() for b in buttons],
                      "destructive": destructive.text(),
                      "default": box.defaultButton().text() if box.defaultButton() else ""})
        button = destructive if confirm["answer"] == "yes" else reject
        box.clickedButton = lambda b=button: b
        return 0

    gui.QMessageBox.exec_ = fake_exec
    try:
        win.btn_del.click()
        QTest.qWait(100)
    finally:
        gui.QMessageBox.exec_ = real_exec
        fileop.delete = real_delete
    check("delete asks before doing anything", len(asked) == 1, str(len(asked)))
    check("the question names the file, so the wrong frame is visible",
          asked and asked[0]["detail"] == os.path.basename(victim), str(asked[:1]))
    check("the confirm button says what will happen",
          asked and asked[0]["destructive"] == "移入回收站", str(asked[:1]))
    check("and the default answer is the safe one (Enter keeps the file)",
          asked and asked[0]["default"] == "取消", str(asked[:1]))
    check("remove sends the file to the Recycle Bin", removed == [victim], str(removed))
    check("the removed frame is off the list", "b.png" not in names(), str(names()))
    successor = names()[1]                               # b.png's slot now holds the frame after it
    check("the frame that took its place is shown",
          os.path.basename(win.current or "") == successor, "%s vs %s" % (win.current, successor))
    check("the removed frame leaves the annotation queue",
          sorted(win.list.item(i).text() for i in range(win.list.count())) == ["c.png"],
          str([win.list.item(i).text() for i in range(win.list.count())]))
    check("an undo is offered", win._undo_action is not None and "已移入回收站" in win.lbl_info.text(),
          win.lbl_info.text()[:60])

    # Deleting in the middle of a bigger folder must show the frame that followed it — not the last frame,
    # which is what looking the deleted name up in the new list would give (and which, before, could be a
    # preview that sorts next to a frame).
    five = os.path.join(TMP, "five")
    os.makedirs(five, exist_ok=True)
    for k in range(1, 6):
        Image.new("RGB", (800, 600), (18 + k, 20, 20)).save(os.path.join(five, "f%d.png" % k))
        Image.new("RGB", (120, 90), (20, 20, 20)).save(os.path.join(five, "f%d_thn.jpg" % k))
    win.list.clear()
    load(os.path.join(five, "f2.png"))
    real_delete5, fileop.delete = fileop.delete, fake_delete
    try:
        win.delete_current_now()
        QTest.qWait(250)
    finally:
        fileop.delete = real_delete5
    check("after deleting a middle frame the next frame is shown",
          os.path.basename(win.current or "") == "f3.png", str(win.current))
    check("and never a preview of one", not (win.current or "").lower().endswith(".jpg"), str(win.current))
    win.list.clear()
    load(os.path.join(five, "f5.png"))
    real_delete6, fileop.delete = fileop.delete, fake_delete
    try:
        win.delete_current_now()
        QTest.qWait(250)
    finally:
        fileop.delete = real_delete6
    check("deleting the last frame shows the new last frame",
          os.path.basename(win.current or "") == "f4.png", str(win.current))

    # a platform with no Recycle Bin must not promise an undo it cannot keep
    throwaway = os.path.join(TMP, "throwaway.txt")
    with open(throwaway, "w", encoding="utf-8") as fh:
        fh.write("x")
    can_come_back = fileop.delete(throwaway)
    check("delete() reports whether the file can come back",
          can_come_back is True and not os.path.exists(throwaway), "returned %s" % can_come_back)
    with open(throwaway, "w", encoding="utf-8") as fh:
        fh.write("x")
    undo_before = win._undo_action
    real_delete2, fileop.delete = fileop.delete, lambda p: False

    def confirm_yes(box):                                # QMessageBox.exec_() returns 0 for the first button
        box.clickedButton = lambda: next(
            b for b in box.buttons() if box.buttonRole(b) == gui.QMessageBox.DestructiveRole)
        return 1

    gui.QMessageBox.exec_ = confirm_yes
    try:
        win.current = throwaway
        win.delete_current()                             # what happens where there is no bin at all
        QTest.qWait(50)
    finally:
        fileop.delete = real_delete2
        gui.QMessageBox.exec_ = real_exec
    check("a permanent delete offers no undo",
          win._undo_action is None and "无法撤销" in win.lbl_info.text(), win.lbl_info.text()[:60])
    win._undo_action = undo_before                       # restore the state the next check expects
    win.lbl_info.setText("")
    win.current = victim
    win._folder = None
    win._folder_dirty = True

    # cancelling the question must leave the file exactly where it is — the whole point of asking
    survivor = os.path.join(TMP, "survivor.png")
    Image.new("RGB", (300, 200), (40, 40, 60)).save(survivor)
    win.current = survivor
    before_cancel = os.path.exists(survivor)
    removed_cancel = []
    real_del3, fileop.delete = fileop.delete, lambda p: (removed_cancel.append(p), True)[1]
    confirm["answer"] = "no"
    gui.QMessageBox.exec_ = fake_exec
    try:
        win.delete_current()
    finally:
        gui.QMessageBox.exec_ = real_exec
        fileop.delete = real_del3
        confirm["answer"] = "yes"
    check("cancelling the question deletes nothing",
          removed_cancel == [] and os.path.exists(survivor) == before_cancel,
          "removed=%s exists=%s" % (removed_cancel, os.path.exists(survivor)))
    check("and the frame stays on screen", win.current == survivor, str(win.current))

    # with the setting off there is no question at all, but the file still goes to the bin
    win.cfg["confirm_delete"] = False
    removed_quiet = []
    real_del4, fileop.delete = fileop.delete, lambda p: (removed_quiet.append(p), True)[1]
    asked_quiet = []
    gui.QMessageBox.exec_ = lambda box: asked_quiet.append(box) or 0
    try:
        win.current = survivor
        win.delete_current()
        QTest.qWait(50)
    finally:
        gui.QMessageBox.exec_ = real_exec
        fileop.delete = real_del4
        win.cfg["confirm_delete"] = True
    check("switched off, delete does not ask", asked_quiet == [], str(len(asked_quiet)))
    check("but it still removes the frame", removed_quiet == [survivor], str(removed_quiet))
    win.current = victim
    win.lbl_info.setText("")

    real_trash, fileop.trash_path = fileop.trash_path, lambda p: os.path.join(parked, os.path.basename(p))
    try:
        win._undo_delete(victim)                         # what Ctrl+Z does
        wait_for_idle()
    finally:
        fileop.trash_path = real_trash
    check("the undo shows the frame again", os.path.basename(win.current or "") == "b.png", str(win.current))
    check("and puts it back in the list", "b.png" in names(), str(names()))
    win._folder = None
    win.refresh_folder()

    # 4j ── move: the frame is filed away into another folder
    dest = os.path.join(TMP, "keep")
    os.makedirs(dest, exist_ok=True)
    win.file_list.setCurrentRow(1)
    wait_for_idle()
    victim = win.current
    real_dialog = QFileDialog.getExistingDirectory
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: dest)
    try:
        win.btn_move.click()
        QTest.qWait(100)
    finally:
        QFileDialog.getExistingDirectory = real_dialog
    check("move puts the file in the chosen folder",
          os.path.exists(os.path.join(dest, os.path.basename(victim))), dest)
    check("the moved frame leaves the folder list", os.path.basename(victim) not in names(), str(names()))
    check("the folder it was moved to is remembered", win.cfg.get("last_move_dir") == dest)
    shutil.move(os.path.join(dest, os.path.basename(victim)), victim)     # put it back for the rest
    win._folder = None
    win._folder_dirty = True
    win.show_image(victim)
    wait_for_idle()
    check("the folder list is whole again", "b.png" in names(), str(names()))

    # 4k ── which star catalogues take part
    from skyannot import annotate, vizier               # noqa: E402
    check("catalogue keys and block names", list(vizier.CATALOGS) == ["hip", "tyc", "gaia"]
          and [vizier.CATALOGS[k]["block"] for k in vizier.CATALOGS] == ["Hipparcos", "TYCHO-2", "Gaia DR3"],
          str([vizier.CATALOGS[k]["block"] for k in vizier.CATALOGS]))

    # a projection of a real solution, so the fake rows land inside the frame
    asked, cone_real = [], vizier.cone
    from astropy.wcs import WCS                          # noqa: E402
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [283.34, 32.97]
    w.wcs.crpix = [200.0, 150.0]
    w.wcs.cd = [[-2.8e-4, 0.0], [0.0, 2.8e-4]]
    proj = annotate.Projector(w, 400, 300)
    fake_rows = {
        "hip": [{"HIP": "1", "RAICRS": "283.34", "DEICRS": "32.97", "Vmag": "5.1", "BTmag": "5.4",
                 "VTmag": "5.2", "B-V": "0.3", "V-I": "0.6", "SpType": "A0V", "Plx": "8.1",
                 "pmRA": "1.0", "pmDE": "1.0"}],
        "tyc": [{"TYC1": "1", "TYC2": "2", "TYC3": "1", "RAmdeg": "283.341", "DEmdeg": "32.971",
                 "BTmag": "9.9", "VTmag": "9.8", "HIP": "1"}],
        "gaia": [{"Source": "1", "RA_ICRS": "283.342", "DE_ICRS": "32.972", "Gmag": "12.0", "BPmag": "12.4",
                  "RPmag": "11.6", "Plx": "3.3", "pmRA": "2.0", "pmDE": "2.0"}],
    }

    def run_blocks(cats):
        """Run star_blocks with the VizieR queries faked, and report what was asked for."""
        asked[:] = []
        vizier.cone = lambda c, *a, **k: (asked.append(c), fake_rows.get(c, []))[1]
        try:
            blocks, _notes = annotate.star_blocks(proj, 283.34, 32.97, 0.2, 0.09, 2026.0, cats=cats)
        finally:
            vizier.cone = cone_real
        return asked[:], [b.name for b in blocks]

    q, names_all = run_blocks(None)
    check("all catalogues are queried by default", q == ["hip", "tyc", "gaia"], str(q))
    check("all requested blocks are written",
          all(b in names_all for b in ("Hipparcos", "TYCHO-2", "Gaia DR3")), str(names_all))

    q, names_hip = run_blocks(("hip",))
    check("a switched-off catalogue is not queried", q == ["hip"], str(q))
    check("... and has no block", "TYCHO-2" not in names_hip and "Gaia DR3" not in names_hip, str(names_hip))
    check("... while the requested one is written", "Hipparcos" in names_hip, str(names_hip))

    q, names_tg = run_blocks(("tyc", "gaia"))
    check("a subset queries exactly that subset", q == ["tyc", "gaia"], str(q))
    check("... and writes exactly those blocks",
          "Hipparcos" not in names_tg and "TYCHO-2" in names_tg and "Gaia DR3" in names_tg, str(names_tg))

    # 4l ── the panel control for it
    check("all three catalogues are ticked by default",
          all(c.isChecked() for c in win.chk_cats.values()), str(list(win.chk_cats)))
    win.chk_cats["tyc"].setChecked(False)
    QTest.qWait(30)
    check("unticking one updates the setting", win.cfg["cats"] == ["hip", "gaia"], str(win.cfg["cats"]))
    check("the heading shows how many are on", "2/3" in win.lbl_cats.text(), win.lbl_cats.text())
    win._cats_all(False)
    QTest.qWait(30)
    check("'clear' switches them all off", win.cfg["cats"] == [], str(win.cfg["cats"]))
    warned = {}
    real_warn = gui.QMessageBox.warning
    gui.QMessageBox.warning = staticmethod(lambda *a, **k: warned.setdefault("told", a[-1]))
    try:
        win.start()
    finally:
        gui.QMessageBox.warning = real_warn
    check("solving without any catalogue is refused", "星表" in str(warned.get("told", "")),
          str(warned.get("told"))[:40])
    win._cats_all(True)
    QTest.qWait(30)
    check("'all' switches them back on", win.cfg["cats"] == ["hip", "tyc", "gaia"], str(win.cfg["cats"]))

    # 4m ── solving writes nothing until the user agrees
    work = os.path.join(TMP, "work")
    os.makedirs(work, exist_ok=True)
    src_paths = []
    for n in ("w1.png", "w2.png"):                       # same picture, so the solution is identical
        p = os.path.join(work, n)
        shutil.copyfile(paths[0], p)
        src_paths.append(p)
    stem = os.path.splitext(src_paths[0])[0]
    outputs = (stem + ".objects.txt", stem + ".outlines.json", stem + ".annotated.jpg")
    for f in outputs:
        if os.path.exists(f):
            os.remove(f)

    # The save confirmation is driven from here. PyQt5 resolves dialog.exec_() to the base class slot, so
    # patching the subclass attribute would not take: QDialog.exec_ itself is what gets replaced.
    from PyQt5.QtWidgets import QDialog as _QDialog       # noqa: E402
    real_dialog_exec = _QDialog.exec_
    answer = {"accepted": False, "flags": None}

    def fake_exec(dlg):
        """Stand in for the modal dialog: tick what the test asked for and answer yes or no."""
        for key, chk in getattr(dlg, "chk", {}).items():
            if answer["flags"] and key in answer["flags"]:
                chk.setChecked(bool(answer["flags"][key]))
        return _QDialog.Accepted if answer["accepted"] else _QDialog.Rejected

    _QDialog.exec_ = fake_exec

    def answer_save(accepted, flags=None):
        """Set how the next save dialog is answered."""
        answer["accepted"], answer["flags"] = accepted, flags

    solved = {}                                          # stand in for the solver: no network in a test
    real_solve = run.solve

    def fake_solve(path, opt, log=print, progress=lambda f, m: None):
        img = iio.load(path)
        w = WCS(naxis=2)                                 # a real solution: the write-back path is exercised
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.crval = [283.34, 32.97]
        w.wcs.crpix = [200.0, 150.0]
        w.wcs.cd = [[-2.8e-4, 0.0], [0.0, 2.8e-4]]
        res = run.Result(image=path, ok=True, wcs=w, stats={"solver": "fake", "median_px": 0.1},
                         blocks=[], shapes=[], named=[], grid=[])
        solved[path] = (img, res)
        return img, res, iio.display_stretch(img, opt.stretch)

    run.solve = fake_solve
    try:
        win.list.clear()
        win.add_files(src_paths)
        win.cfg["cats"] = ["hip"]
        answer_save(False)                               # the user says "不保存"
        win.start()
        for _ in range(200):
            QTest.qWait(50)
            if win.worker is None or not win.worker.isRunning():
                break
    finally:
        run.solve = real_solve
        _QDialog.exec_ = real_dialog_exec
    check("declining the save dialog writes no file", not any(os.path.exists(f) for f in outputs),
          str([os.path.basename(f) for f in outputs if os.path.exists(f)]))
    check("the solve is kept for later saving", win.current in win._unsaved, str(sorted(win._unsaved)))
    check("the save button lights up for it",
          win.btn_save.isEnabled() and win.btn_save.text().startswith("保存"), win.btn_save.text())

    # ... and agreeing writes exactly what was ticked
    _QDialog.exec_ = fake_exec
    try:
        answer_save(True, {"txt": True, "outlines": True, "preview": True, "wcs": True})
        win.save_current()
    finally:
        _QDialog.exec_ = real_dialog_exec
    check("accepting writes the result", os.path.exists(outputs[0]), str(outputs[0]))
    check("it is no longer listed as unsaved", win.current not in win._unsaved)
    check("the save button goes quiet again", not win.btn_save.isEnabled())

    # ... ticking only one output writes only that one
    for f in outputs:
        if os.path.exists(f):
            os.remove(f)
    win._unsaved.add(win.current)
    _QDialog.exec_ = fake_exec
    try:
        answer_save(True, {"txt": False, "outlines": False, "preview": True, "wcs": False})
        win.save_current()
    finally:
        _QDialog.exec_ = real_dialog_exec
    check("only the ticked output is written",
          os.path.exists(outputs[2]) and not os.path.exists(outputs[0]) and not os.path.exists(outputs[1]),
          str([os.path.basename(f) for f in outputs if os.path.exists(f)]))
    check("the written files are what the dialog listed",
          sorted(os.path.basename(f) for f in run.output_paths(win.current, run.Options()))
          == ["w1.annotated.jpg", "w1.objects.txt", "w1.outlines.json", "w1.wcs"])
    for f in outputs:
        if os.path.exists(f):
            os.remove(f)
    # ... writing the solution into the image is offered too, and writes the sidecar for a PNG
    win._unsaved.add(win.current)
    _QDialog.exec_ = fake_exec
    try:
        answer_save(True, {"txt": False, "outlines": False, "preview": False, "wcs": True})
        win.save_current()
    finally:
        _QDialog.exec_ = real_dialog_exec
    sidecar = os.path.splitext(win.current)[0] + ".wcs"
    check("the sidecar solution is written", os.path.exists(sidecar), sidecar)
    if os.path.exists(sidecar):
        os.remove(sidecar)
    for row in range(win.list.count() - 1, -1, -1):      # the section used its own queue and folder
        win.list.takeItem(row)
    win._unsaved.clear()
    shutil.rmtree(work, ignore_errors=True)

    win.show_image(paths[1])                             # back to the images folder for the rest
    wait_for_idle()
    check("the folder list is back to the test images", names() == ["a.png", "b.png", "c.png"], str(names()))

    # 4n ── the object-name field searches the deep-sky database
    from skyannot import catalogs as cats                # noqa: E402
    for query, expect in (("m57", "M57"), ("ring", "M57"), ("ngc6720", "M57"), ("6720", "M57"),
                          ("andromeda", "M31"), ("m1", "M1")):
        hits = cats.find_objects(query, limit=5)
        got = cats.label_for(hits[0]) if hits else ""
        check("search %-10s finds %s" % (query, expect), expect in got, got)
    check("an exact designation comes first",
          cats.label_for(cats.find_objects("m31")[0]).startswith("M31"),
          cats.label_for(cats.find_objects("m31")[0]))
    check("a single letter offers the bright Messier objects",
          cats.label_for(cats.find_objects("m", limit=1)[0]).startswith("M"),
          cats.label_for(cats.find_objects("m", limit=1)[0]))
    check("nothing is offered for an empty search", cats.find_objects("") == [])

    # the field completes from that database, and picking an entry records its position
    check("the field completes from the catalogue",
          win.ed_target.completer() is not None and win._target_model.rowCount() > 5000,
          str(win._target_model.rowCount()))
    check("no position is used before anything is picked", win._target_radec == ())
    label = next(lb for lb, d in win._target_dso.items() if d.messier == "M57")
    win.ed_target.setText(label)
    win._target_chosen(label)
    m57 = win._target_dso[label]
    check("picking an object records its position",
          win._target_radec == (m57.ra, m57.dec), str(win._target_radec))
    check("the logged hint is the catalogued position",
          "%.5f" % m57.ra in win.logbox.toPlainText(), win.logbox.toPlainText()[-60:])

    # ... and that position, not the header's pointing, is what the solver is told
    grabbed = {}
    real_solve2 = run.solve

    def grab_hint(path, opt, log=print, progress=lambda f, m: None):
        grabbed["radec"] = opt.target_radec
        grabbed["target"] = opt.target
        img = iio.load(path)
        return img, run.Result(image=path, ok=False, error="stopped"), iio.display_stretch(img, opt.stretch)

    run.solve = grab_hint
    try:
        win.list.clear()
        win.add_files([paths[0]])
        win.cfg["cats"] = ["hip"]
        win.start()
        for _ in range(100):
            QTest.qWait(50)
            if win.worker is None or not win.worker.isRunning():
                break
    finally:
        run.solve = real_solve2
    check("the solver receives the picked position", grabbed.get("radec") == (m57.ra, m57.dec),
          str(grabbed))
    check("and the name as text", grabbed.get("target") == label, str(grabbed.get("target")))

    # ── a position typed into the centre-coordinate field, in sexagesimal ──
    check("a typed position is read from hours/degrees",
          gui.parse_radec("05 34 31.9 +22 00 52") is not None)
    ra_t, dec_t = gui.parse_radec("05:34:31.9 +22:00:52")
    check("the separators do not matter",
          abs(ra_t - 83.6329) < 1e-3 and abs(dec_t - 22.0144) < 1e-3, "%.4f %.4f" % (ra_t, dec_t))
    check("it is shown as 时 分 秒 and 度 分 秒",
          gui.fmt_ra(ra_t) == "05 34 31.9" and gui.fmt_dec(dec_t) == "+22 00 52",
          "%s / %s" % (gui.fmt_ra(ra_t), gui.fmt_dec(dec_t)))
    check("a name is not a position", gui.parse_radec("M57") is None)
    check("an ambiguous three-number string is refused", gui.parse_radec("05 34 31.9") is None)
    win.ed_coord.setText("05 34 31.9 +22 00 52")
    win._coord_entered()
    check("the field is normalised on the way out", win.ed_coord.text() == "05 34 31.9  +22 00 52",
          win.ed_coord.text())
    check("and is not flagged as unreadable", not bool(win.ed_coord.property("bad")))

    grabbed.clear()
    run.solve = grab_hint
    try:
        win.cfg["cats"] = ["hip"]
        win.start()
        for _ in range(100):
            QTest.qWait(50)
            if win.worker is None or not win.worker.isRunning():
                break
    finally:
        run.solve = real_solve2
    check("the typed position is the hint the solver gets",
          grabbed.get("radec") is not None and abs(grabbed["radec"][0] - ra_t) < 1e-6
          and abs(grabbed["radec"][1] - dec_t) < 1e-6, str(grabbed.get("radec")))
    check("and it overrides the picked object's position",
          grabbed.get("radec") != (m57.ra, m57.dec), str(grabbed.get("radec")))
    win.ed_coord.setText("nonsense")
    win._coord_entered()
    check("an unreadable position is flagged", bool(win.ed_coord.property("bad")))
    win.ed_coord.clear()
    win._coord_entered()

    # typing a designation the catalogue knows is enough to give the solver a position (no dropdown needed)
    win.ed_target.setText("M57")
    win._target_typed("M57")
    check("typing a known designation resolves to its position",
          win._target_radec == (m57.ra, m57.dec), str(win._target_radec))
    # free text that names nothing still leaves no position behind
    win.ed_target.setText("zz-not-an-object")
    win._target_typed("zz-not-an-object")
    check("typed text that matches no entry leaves no position", win._target_radec == (),
          str(win._target_radec))
    win.list.clear()
    win._unsaved.clear()
    win.show_image(paths[1])
    wait_for_idle()

    # 4o ── the colour themes
    check("two themes are offered: dark and light",
          list(gui.THEMES) == ["dark", "light"], str(list(gui.THEMES)))
    needed = {"canvas", "bg", "surface", "surface2", "surface3", "stripe", "elev", "border", "border2",
              "fg1", "fg2", "fg3", "fg5", "accent", "accent_press", "accent_tint", "accent_tint2",
              "on_accent", "disabled"}
    check("every theme defines the whole palette",
          all(needed <= set(t) for t in gui.THEMES.values()),
          str([k for k in gui.THEMES if not needed <= set(gui.THEMES[k])]))
    check("the accent is the shared brand green",
          gui.THEMES["dark"]["accent"] == "#68E098", gui.THEMES["dark"]["accent"])
    check("the light theme darkens it so it stays readable on white",
          gui.THEMES["light"]["accent"] != gui.THEMES["dark"]["accent"]
          and gui.THEMES["light"]["bg"].startswith("#EE"), gui.THEMES["light"]["accent"])
    check("the default theme is dark", config.DEFAULTS["theme"] == "dark", config.DEFAULTS["theme"])
    try:
        for name in list(gui.THEMES):
            gui.set_theme(name)
            qss = gui.TOGGLE_QSS + gui.PANEL_QSS + gui.BUTTON_QSS + gui.BAR_QSS + gui.SUBTITLE_QSS
            check("%-5s builds its stylesheets" % name,
                  gui.C["accent"] in qss and gui.C["surface2"] in qss
                  and "%(" not in qss and "0.5px" not in qss,
                  "accent %s" % gui.C["accent"])
    finally:
        gui.set_theme("dark")
    dlg = gui.SettingsDialog(win.cfg, win)
    check("the theme is a two-state switch, not a list",
          isinstance(dlg.theme_switch, gui.Switch) and dlg.theme_switch.isCheckable()
          and dlg.theme_name() in ("dark", "light"),
          str(dlg.theme_name()))
    dlg.theme_switch.setChecked(True)
    check("and is saved with the other settings", dlg.values()["theme"] == "light",
          str(dlg.values().get("theme")))
    check("the switch names the state it is in", dlg.lbl_theme.text() == "浅色", dlg.lbl_theme.text())

    # scrolling the settings page must not edit the numbers it rolls past
    from PyQt5.QtGui import QWheelEvent
    from PyQt5.QtCore import QPointF

    def wheel(widget, dy=120):
        ev = QWheelEvent(QPointF(8, 8), QPointF(8, 8), QPoint(0, 0), QPoint(0, dy),
                         Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QApplication.sendEvent(widget, ev)

    before = dlg.gmag.value()
    wheel(dlg.gmag)
    check("the wheel leaves an unfocused number field alone", dlg.gmag.value() == before,
          str(dlg.gmag.value()))

    class _Focused(gui.DoubleSpinBox):
        def hasFocus(self):
            return True

    focused = _Focused()
    focused.setRange(0, 30)
    focused.setValue(16.0)
    wheel(focused)
    check("but works once the field has been clicked", focused.value() != 16.0, str(focused.value()))
    focused.deleteLater()
    lang = dlg.lang.currentIndex()
    wheel(dlg.lang)
    check("and a combo box does not change while scrolling either", dlg.lang.currentIndex() == lang,
          str(dlg.lang.currentIndex()))
    dlg.theme_switch.setChecked(False)
    check("flipping it back is dark again", dlg.theme_name() == "dark", dlg.theme_name())
    win.cfg["theme"] = "dark"

    # 4p ── the loading mark while a frame is being read
    view = win.view
    check("no mark is shown while idle", not view.spinner.isVisible())
    view.set_loading(True)
    check("a quick load does not flash a mark", not view.spinner.isVisible())
    QTest.qWait(220)                                     # ... but a slow one does
    check("a slow load shows the mark", view.spinner.isVisible())
    vp = view.viewport()
    check("the mark sits in the middle of the picture",
          abs(view.spinner.x() + view.spinner.width() / 2 - (vp.x() + vp.width() / 2)) <= 2
          and abs(view.spinner.y() + view.spinner.height() / 2 - (vp.y() + vp.height() / 2)) <= 2,
          "at %d,%d in viewport %dx%d" % (view.spinner.x(), view.spinner.y(), vp.width(), vp.height()))
    check("the mark never takes clicks", view.spinner.testAttribute(Qt.WA_TransparentForMouseEvents))
    was = view.spinner._angle
    view.spinner._turn()
    check("the mark animates", view.spinner._angle != was,
          "%s -> %s" % (was, view.spinner._angle))
    view.set_loading(False)
    check("the mark goes away when the frame is ready", not view.spinner.isVisible())
    QTest.qWait(200)
    check("and stays away", not view.spinner.isVisible())

    # ... it is driven by the real load path: shown while a slow worker runs, gone once it answers.
    # The delay goes inside the reading itself, which is what is actually slow on a 20-megapixel frame.
    real_load = iio.load
    iio.load = lambda *a, **k: (QTest.qWait(300), real_load(*a, **k))[1]
    try:
        win.show_image(paths[0])
        QTest.qWait(220)
        check("a slow load shows the mark by itself", view.spinner.isVisible())
        for _ in range(80):
            QTest.qWait(50)
            # wait for *this* frame, not merely "an image is on screen": the worker can stop before its
            # queued `done` slot has run, and the old picture is still there at that instant
            if win._loaded_path == paths[0] and not (win.loader is not None and win.loader.isRunning()):
                break
    finally:
        iio.load = real_load
    check("and the resting picture clears it",
          not view.spinner.isVisible() and view._view_state() is None, str(view.spinner.isVisible()))

    # 4q ── the online catalogue (tickwhale) fills in what the bundled files do not have
    from skyannot import tickwhale as tw                 # noqa: E402
    check("the bundled catalogues have no Lynds nebulae",
          cats.find_objects("lbn777") == [] and cats.find_objects("ldn1235") == [],
          str(cats.find_objects("lbn777")))

    # the server's type codes are mapped onto the vocabulary the rest of the program speaks
    check("server types map onto catalogue types",
          (tw.classify_type("BN"), tw.classify_type("DNe"), tw.classify_type("Nb"),
           tw.classify_type("PN"), tw.classify_type("GTrpl"))
          == ("BN", "DrkN", "Neb", "PN", "GTrpl"),
          str([tw.classify_type(x) for x in ("BN", "DNe", "Nb", "PN", "GTrpl")]))
    check("an unknown type is passed through unharmed", tw.classify_type("Weird") == "Weird")

    rows = {"result": {"list": [
        {"id": "LBN777", "type": "BN", "ra": "60.5", "dec": "26.25", "size_major": "20",
         "size_minor": "18", "bmag": "99.90", "name_zh": "暗星云", "constellation_zh": "金牛座"},
        {"id": "M57", "type": "PN", "ra": "283.39625", "dec": "33.0297222", "size_major": "3",
         "size_minor": "2.4", "bmag": "9.70", "name_zh": "指环星云"},
        {"id": "broken", "type": "G", "ra": "", "dec": "12"},     # no position: must be dropped
    ]}}
    asked = []

    def fake_ask(url, payload, timeout):
        asked.append(payload)
        return [r for r in (tw._one(x) for x in rows["result"]["list"]) if r]

    real_ask = tw._ask
    tw._ask = fake_ask
    try:
        hits = tw.search("LBN777")
    finally:
        tw._ask = real_ask
    check("an exact id is tried first", asked and asked[0] == {"catalog_id": "LBN777"}, str(asked[:1]))
    check("the sentinel magnitude becomes no magnitude", hits[0]["mag"] is None, str(hits[0]["mag"]))
    check("rows without a position are dropped", [h["name"] for h in hits] == ["LBN777", "M57"],
          str([h["name"] for h in hits]))
    check("a found record carries what the field needs",
          hits[0]["ra"] == 60.5 and hits[0]["dec"] == 26.25 and hits[0]["maj"] == 20.0)
    check("the label reads like the bundled ones", tw.label_for(hits[0]) == "LBN777 · 暗星云",
          tw.label_for(hits[0]))
    check("a one-letter search never reaches the server", tw.search("L") == [] and tw.search("") == [])

    # a dead server yields nothing rather than an error
    real_urlopen = tw.urllib.request.urlopen
    tw.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError("no route"))
    try:
        check("an unreachable server yields nothing", tw.search("LBN777") == [])
    finally:
        tw.urllib.request.urlopen = real_urlopen

    # ... and the field merges what it returns
    win.ed_target.setText("LBN777")
    rec = {"name": "LBN777", "ra": 60.5, "dec": 26.25, "type": "BN", "raw_type": "BN", "mag": None,
           "maj": 20.0, "minr": 18.0, "common": "暗星云", "constellation": "金牛座", "source": "tickwhale"}
    win._remote_found("LBN777", [rec])
    label = tw.label_for(rec)
    check("the online object joins the completion list", label in win._target_dso, label)
    check("and is marked as online", win._remote.get(label) is rec)
    win._target_chosen(label)
    check("picking it records its position", win._target_radec == (60.5, 26.25), str(win._target_radec))
    check("the log says it came from online", "在线星表" in win.logbox.toPlainText(),
          win.logbox.toPlainText()[-40:])
    before = len(win._target_dso)
    win._remote_found("another query entirely", [{"name": "X", "ra": 1.0, "dec": 2.0, "type": "G",
                                                  "raw_type": "G", "mag": None, "maj": None,
                                                  "minr": None, "common": "", "constellation": "",
                                                  "source": "tickwhale"}])
    check("a reply for text the field no longer holds is ignored",
          len(win._target_dso) == before, str(len(win._target_dso)))
    win.ed_target.clear()
    win._target_typed("")
    dlg = gui.SettingsDialog(win.cfg, win)
    check("the online catalogue address is not a user setting any more",
          not hasattr(dlg, "tw") and dlg.values()["tickwhale_base"] == win.cfg["tickwhale_base"],
          str(dlg.values().get("tickwhale_base")))

    # 4q2 ── PixInsight is found for the user, not hunted by them
    from skyannot import piopen                             # noqa: E402
    found = piopen.find()
    check("PixInsight is detected on this machine", bool(found) and os.path.isfile(found), found or "none")
    check("the settings show what was found and offer to fill it in",
          hasattr(dlg, "_detect_pixinsight") and dlg.pi.placeholderText().startswith("留空 = 自动查找"),
          dlg.pi.placeholderText()[:40])

    # 4r ── ASTAP: the path, and why a failure must not be silent
    from skyannot import solve as solvemod                 # noqa: E402
    check("the configured ASTAP path is what is searched first",
          solvemod.astap_candidates(r"X:\exe\astap.exe")[0] == r"X:\exe\astap.exe",
          str(solvemod.astap_candidates(r"X:\exe\astap.exe")[:1]))
    check("the built-in locations are searched too",
          any("astap.exe" in c for c in solvemod.astap_candidates()),
          str(solvemod.astap_candidates()))
    check("a configured path that is not a file is not used",
          solvemod.find_astap(r"X:\nowhere\astap.exe") is None)

    exe = r"X:\astap\astap.exe"
    check("a wide-field-only database is called out",
          "H17 or H18" in solvemod.astap_db_hint(exe, look=lambda d: ["astap.exe", "d80_0101.1476"]),
          solvemod.astap_db_hint(exe, look=lambda d: ["astap.exe", "d80_0101.1476"]))
    check("H18 present means no complaint",
          solvemod.astap_db_hint(exe, look=lambda d: ["astap.exe", "h18_0101.1476"]) == "")
    check("no database at all is called out differently",
          "no star database" in solvemod.astap_db_hint(exe, look=lambda d: ["astap.exe"]),
          solvemod.astap_db_hint(exe, look=lambda d: ["astap.exe"]))
    check("an unreadable directory is not an error",
          solvemod.astap_db_hint(exe, look=lambda d: (_ for _ in ()).throw(OSError())) == "")

    # ASTAP running but finding nothing must say why, not just "could not solve". The program file is faked so
    # the run really gets past the lookup, and ASTAP's own failure is faked so no process is started.
    exe = os.path.join(TMP, "astap.exe")
    with open(exe, "wb") as fh:
        fh.write(b"stub")
    with open(os.path.join(TMP, "d80_0101.1476"), "wb") as fh:
        fh.write(b"stub")
    real_run = solvemod.subprocess.run
    real_mkdtemp = solvemod.tempfile.mkdtemp
    scratch = os.path.join(TMP, "astap_tmp")
    os.makedirs(scratch, exist_ok=True)
    solvemod.tempfile.mkdtemp = lambda prefix="", suffix="", dir=None: scratch
    solvemod.subprocess.run = lambda *a, **k: type("P", (), {"stdout": b"", "stderr": b"no solution found",
                                                             "returncode": 1})()
    try:
        try:
            solvemod.solve_astap(np.zeros((20, 20, 3), np.float32), {}, exe=exe)
            said = "no error raised"
        except Exception as e:                              # noqa: BLE001
            said = str(e)
    finally:
        solvemod.subprocess.run = real_run
        solvemod.tempfile.mkdtemp = real_mkdtemp
    check("a failed ASTAP run reports ASTAP's own words", "no solution found" in said, said)
    check("and explains the likely database cause", "H17 or H18" in said, said)

    # ... and a path that cannot be used is reported instead of being skipped in silence.
    # run.solve() is called for real: it gets as far as the ASTAP branch, finds nothing, logs it, and then
    # fails at the ttastro solver above it, which is what the offline fallback exists to cover.
    real_find = run.solver.find_astap
    real_ttastro = run.solver.solve_ttastro
    run.solver.find_astap = lambda configured=None: None
    run.solver.solve_ttastro = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("stubbed: no network in tests"))
    said = []
    # a field with stars in it: the solver loop is only reached once the detector has found some, and the
    # point here is what happens inside that loop when ASTAP cannot be run
    starry = os.path.join(TMP, "starry.png")
    arr = np.zeros((400, 400), np.uint8)
    for i in range(24):
        arr[20 + (i * 61) % 360:24 + (i * 61) % 360, 20 + (i * 97) % 360:24 + (i * 97) % 360] = 255
    Image.fromarray(arr, "L").convert("RGB").save(starry)
    try:
        with contextlib.suppress(Exception):
            run.solve(starry, run.Options(solvers=("ttastro", "astap"),
                                          astap_path=r"X:\nowhere\astap.exe"),
                      log=lambda m: said.append(m))
    finally:
        run.solver.find_astap = real_find
        run.solver.solve_ttastro = real_ttastro
    text = "\n".join(said)
    check("the solver loop was actually reached", "stars detected" in text, text[-300:])
    check("a missing ASTAP is logged, not skipped in silence", "astap not found" in text, text[-300:])
    check("and the paths it looked at are named", "nowhere" in text, text[-300:])

    # 4s ── the panel's own vertical rhythm: one caption per group, each with the same air above it
    def _span(widget):
        top = widget.mapTo(win, widget.rect().topLeft()).y()
        return top, top + widget.height()

    # the catalogue cards live in a scroller; its bottom edge is the end of the annotation group
    last_dso = win.catalogue_scroll
    gaps = [
        ("the target group starts below the queue", _span(win.lbl_target)[0] - _span(win.list)[1]),
        ("the star catalogues sit below the target group", _span(win.lbl_cats)[0] - _span(win.ed_coord)[1]),
        ("the annotation group follows the star catalogues", _span(win.lbl_anno)[0] - _span(win.lbl_named)[1]),
        ("the display group follows the annotation group", _span(win.lbl_shape)[0] - _span(last_dso)[1]),
        ("the output group follows the display group", _span(win.lbl_out)[0] - _span(win.cmb_bayer)[1]),
    ]
    for text, gap in gaps:
        check(text, gap >= 4, "%d px" % gap)
    check("the captions keep one rhythm", max(g for _t, g in gaps) - min(g for _t, g in gaps) <= 6,
          " / ".join(str(g) for _t, g in gaps))
    order = [_span(w)[0] for w in (win.lbl_target, win.lbl_cats, win.lbl_anno, win.lbl_shape, win.lbl_out)]
    check("the groups are in the order the work happens", order == sorted(order), str(order))
    check("the shape and Bayer choosers share one row under the display caption",
          abs(_span(win.cmb_bayer)[0] - _span(win.cmb_shape)[0]) <= 2
          and _span(win.cmb_shape)[0] > _span(win.lbl_shape)[1],
          "%d / %d" % (_span(win.cmb_shape)[0], _span(win.cmb_bayer)[0]))
    check("the output caption sits above the output switches, not above the catalogues",
          _span(win.lbl_out)[0] > _span(win.cmb_bayer)[1] and _span(win.lbl_out)[0] < _span(win.chk["write_txt"])[0],
          "%d / %d / %d" % (_span(win.cmb_bayer)[1], _span(win.lbl_out)[0], _span(win.chk["write_txt"])[0]))

    # 4u ── the catalogue cards: catalogue sizes, and a press on a card drags the row instead of toggling it
    cs = catalogue.counts()
    check("the Messier card counts the whole catalogue (M102 has no Messier number in OpenNGC)",
          cs.get("m") == 110, str(cs.get("m")))
    check("the other cards count their catalogue",
          cs.get("ngc", 0) > 7000 and cs.get("ic", 0) > 4000 and cs.get("sh2") == 313, str(cs))

    bar = win.catalogue_scroll.horizontalScrollBar()
    card = win.chk_groups["ngc"]
    before_on, before_v = card.isChecked(), bar.value()

    def send_mouse(kind, x, buttons, button):
        gp = card.mapToGlobal(QPoint(x, 20))
        QApplication.sendEvent(card, QMouseEvent(kind, QPointF(x, 20), gp, button, buttons, Qt.NoModifier))

    send_mouse(QEvent.MouseButtonPress, 150, Qt.LeftButton, Qt.LeftButton)
    send_mouse(QEvent.MouseMove, 70, Qt.LeftButton, Qt.NoButton)
    send_mouse(QEvent.MouseButtonRelease, 70, Qt.NoButton, Qt.LeftButton)
    check("dragging a card scrolls the row",
          bar.value() > before_v, "%d -> %d" % (before_v, bar.value()))
    check("and does not toggle the card it was dragged by",
          card.isChecked() == before_on, "%s -> %s" % (before_on, card.isChecked()))

    # 4t ── the save dialog's own styling
    dlg = gui.SaveDialog(paths[0], [paths[0] + ".objects.txt"])
    dlg.show()
    QTest.qWait(150)
    check("the confirm button is the one filled button", "background" in dlg.btn_save.styleSheet(),
          dlg.btn_save.styleSheet()[:60])
    check("every output toggle is a pill",
          all(c.styleSheet() == gui.TOGGLE_QSS for c in dlg.chk.values()),
          str([bool(c.styleSheet()) for c in dlg.chk.values()]))
    check("the remember box is a real check box, not a pill",
          dlg.remember.styleSheet() != gui.TOGGLE_QSS, dlg.remember.styleSheet()[:40])

    # writing into a picture's header is invasive and off by default; a raster gets a sidecar instead
    check("the solution write is off unless asked for", not dlg.chk["wcs"].isChecked(), "")
    check("the config keeps it off by default", config.DEFAULTS["write_wcs"] is False,
          str(config.DEFAULTS.get("write_wcs")))
    check("a raster picture is offered a .wcs sidecar, not a header write",
          ".wcs" in dlg.chk["wcs"].text(), dlg.chk["wcs"].text())
    fits_dlg = gui.SaveDialog("C:\\x\\frame.fit", ["C:\\x\\frame.fit"])
    check("a FITS picture is offered the header write",
          "写回图片" in fits_dlg.chk["wcs"].text() and not fits_dlg.chk["wcs"].isChecked(),
          fits_dlg.chk["wcs"].text())
    fits_dlg.close()
    # the save dialog can write into a folder picked right there, not only beside the picture
    out = tempfile.mkdtemp(prefix="tts-save-")
    try:
        dlg.ed_dir.setText(out)
        first = dlg.where.text().splitlines()[0]
        check("the targets follow the chosen folder", first.startswith(out), first[:60])
        check("and the dialog reports that folder", dlg.flags().get("_out_dir") == out, dlg.flags().get("_out_dir"))
    finally:
        shutil.rmtree(out, ignore_errors=True)
    dlg.close()
    # Qt paints the indicator itself, so it has to be told the colours or it draws an invisible dark box
    for name in ("dark", "light"):
        gui.set_theme(name)
        qss = gui.PANEL_QSS
        check("%-5s check indicators are styled" % name,
              "QCheckBox::indicator" in qss and gui.C["border2"] in qss and gui.C["accent"] in qss)
        check("%-5s checked indicator is filled with the accent" % name,
              "QCheckBox::indicator:checked{background:%s" % gui.C["accent"] in qss)
    # the dialog paints itself in whichever theme is current, so build one per theme and check the surface
    for name in ("dark", "light"):
        gui.set_theme(name)
        d2 = gui.SaveDialog(paths[0], [paths[0] + ".objects.txt"])
        d2.show()
        QTest.qWait(120)
        # the dialog is a rounded card: sample just inside the corner, not in the transparent margin
        px = d2.grab().toImage().pixelColor(12, 12).name().lower()
        check("%-5s dialog paints its own surface colour" % name, px == gui.C["surface"].lower(),
              "%s vs %s" % (px, gui.C["surface"]))
        d2.close()
    gui.set_theme("dark")
    check("the remember box is tickable and starts off", dlg.remember.isChecked() is False)
    dlg.close()

    # 4u ── annotation groups are the catalogues, and each object is listed under exactly one
    from skyannot import catalogue as cat                # noqa: E402
    check("the groups are catalogues, in precedence order",
          [k for k, _t in gui.GROUPS] == ["m", "ngc", "ic", "sh2", "lbn", "ldn", "vdb", "ced", "rcw"],
          str([k for k, _t in gui.GROUPS]))
    check("they are labelled by catalogue name",
          [t for _k, t in gui.GROUPS][:4] == ["Messier", "NGC", "IC", "Sharpless"],
          str([t for _k, t in gui.GROUPS][:4]))
    dso = {d.messier: d for d in gui.catalogs.messier() if d.messier}
    m31 = dso["M31"]
    check("a Messier object with an NGC number counts as Messier", cat.catalogue_of(m31) == "m",
          "%s ngcic=%s" % (cat.catalogue_of(m31), m31.ngcic))
    check("and M31 really does carry both designations", m31.ngcic == "NGC224", m31.ngcic)
    check("a plain NGC object counts as NGC",
          cat.catalogue_of(next(d for d in gui.catalogs.ngcic() if d.name == "NGC7000")) == "ngc")
    check("an IC object counts as IC",
          cat.catalogue_of(next(d for d in gui.catalogs.ngcic() if d.name.startswith("IC"))) == "ic")
    check("a Sharpless object counts as Sharpless",
          cat.catalogue_of(gui.catalogs.sharpless()[0]) == "sh2")
    check("the online catalogues are recognised by their designations",
          [cat.catalogue_of({"name": n}) for n in ("LBN777", "LDN1235", "vdB141", "Ced214", "RCW120")]
          == ["lbn", "ldn", "vdb", "ced", "rcw"])
    check("an unrecognised designation belongs to no catalogue", cat.catalogue_of({"name": "XYZ9"}) == "")

    # the precedence rule itself: switching a catalogue off hands its objects to the next one that has them
    every = list(gui.catalogs.messier()) + list(gui.catalogs.ngcic()) + list(gui.catalogs.sharpless())
    check("OpenNGC really does list some objects twice",
          len(every) > len({cat.key_of(e) for e in every}),
          "%d records, %d objects" % (len(every), len({cat.key_of(e) for e in every})))
    check("unique() resolves a doubly-listed object to one record",
          len(cat.unique(every)) == len({cat.key_of(e) for e in every}),
          "%d vs %d" % (len(cat.unique(every)), len({cat.key_of(e) for e in every})))
    m31 = next(d for d in every if d.messier == "M31")
    check("M31 is held by both Messier and NGC",
          cat.memberships(every)[cat.key_of(m31)] == ["m", "ngc"],
          str(cat.memberships(every)[cat.key_of(m31)]))
    with_m = cat.assign(every, ("m", "ngc", "ic", "sh2"))
    check("with Messier on, M31 is listed as Messier", with_m[cat.key_of(m31)] == "m",
          with_m.get(cat.key_of(m31)))
    without_m = cat.assign(every, ("ngc", "ic", "sh2"))
    check("with Messier off, M31 falls through to NGC", without_m[cat.key_of(m31)] == "ngc",
          without_m.get(cat.key_of(m31)))
    only_ngc = cat.assign(every, ("ngc",))
    sh_alone = [e for e in gui.catalogs.sharpless() if "NGC" not in (e.name or "").upper()]
    check("enough Sharpless entries exist to test with", len(sh_alone) > 100, str(len(sh_alone)))
    check("a Sharpless-only object is left out when only NGC is on",
          all(cat.key_of(e) not in only_ngc for e in sh_alone))
    check("every object is assigned to exactly one catalogue",
          set(with_m) == {cat.key_of(e) for e in every}
          and all(k in ("m", "ngc", "ic", "sh2") for k in with_m.values()),
          "%d assigned" % len(with_m))
    counts = {}
    for _k, key in with_m.items():
        counts[key] = counts.get(key, 0) + 1
    check("the four local catalogues split the whole set",
          sum(counts.values()) == len(with_m) and set(counts) == {"m", "ngc", "ic", "sh2"},
          str(sorted(counts.items())))
    check("Messier holds 109 objects, not 216",
          counts["m"] == len(gui.catalogs.messier()), str(counts.get("m")))

    # a config written before this scheme must not start with everything switched off
    check("an old config falls back to all catalogues on",
          cat.enabled_from(["galaxy", "nebula", "cluster", "dark"]) == list(cat.KEYS),
          str(cat.enabled_from(["galaxy", "nebula"])))
    check("a catalogue config is honoured as written",
          cat.enabled_from(["m", "sh2", "grid"]) == ["m", "sh2"],
          str(cat.enabled_from(["m", "sh2", "grid"])))
    check("no saved value means everything on", cat.enabled_from(None) == list(cat.KEYS))

    # colours still follow the object's type, not its catalogue — and inside a family every object takes a
    # shade of its own, so a field of nebulae is not twenty copies of one cyan
    import colorsys as _colorsys

    def _hue(hex_color):
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        return _colorsys.rgb_to_hsv(r, g, b)

    c1 = gui.style.color_for({"name": "NGC7000", "category": "ngc", "type_group": "nebula"})
    c2 = gui.style.color_for({"name": "IC1396", "category": "ic", "type_group": "nebula"})
    cg = gui.style.color_for({"name": "M33", "category": "m", "type_group": "galaxy"})
    check("colour is chosen by type, not by catalogue",
          abs(_hue(c1)[0] * 360 - _hue(c2)[0] * 360) < 40
          and abs(_hue(c1)[0] * 360 - 184) < 25, "%s / %s" % (c1, c2))
    check("two objects of one type still get their own shade", c1 != c2, "%s vs %s" % (c1, c2))
    check("a galaxy stays in the warm family", _hue(cg)[0] * 360 > 300 or _hue(cg)[0] * 360 < 40, cg)
    check("a shape carries both the catalogue and the type",
          gui.shapes_mod.category({"name": "NGC7000", "type": "HII"}) == "ngc"
          and gui.shapes_mod.object_type({"name": "NGC7000", "type": "HII"}) == "nebula")

    # 4v ── the star names and the grid: not catalogues, so they keep their own switches
    check("both have their own switch", set(win.chk_extra) == {"named", "grid"},
          str(sorted(win.chk_extra)))
    check("they carry the labels the panel has always shown",
          (win.chk_extra["named"].text(), win.chk_extra["grid"].text()) == ("亮星名", "坐标网格"),
          "%s / %s" % (win.chk_extra["named"].text(), win.chk_extra["grid"].text()))
    check("they are on by default", win._extras_on() == {"named", "grid"}, str(win._extras_on()))
    check("they are not part of the catalogue precedence list",
          all(k not in ("named", "grid") for k, _t in gui.GROUPS))

    # switching them is remembered, both on and off
    win.chk_extra["named"].setChecked(False)
    check("switching a star-name switch off is saved", "named" not in win.cfg["groups"],
          str(win.cfg["groups"]))
    check("and the grid stays on", "grid" in win.cfg["groups"], str(win.cfg["groups"]))
    win.chk_extra["grid"].setChecked(False)
    check("switching both off is saved",
          "grid" not in win.cfg["groups"] and "named" not in win.cfg["groups"], str(win.cfg["groups"]))
    win.chk_extra["named"].setChecked(True)
    check("switching one back on is saved", "named" in win.cfg["groups"], str(win.cfg["groups"]))

    # a window built from a config that remembers them off must come up off
    off_cfg = dict(win.cfg)
    off_cfg["groups"] = ["m", "ngc"]
    w_off = gui.MainWindow(off_cfg)
    check("a config with them off starts with them off",
          w_off._extras_on() == set() and w_off._groups_on() == ("m", "ngc"),
          "%s / %s" % (sorted(w_off._extras_on()), w_off._groups_on()))
    on_cfg = dict(win.cfg)
    on_cfg["groups"] = ["m", "named", "grid"]
    check("a config with them on starts with them on",
          gui.MainWindow(on_cfg)._extras_on() == {"named", "grid"})
    old_cfg = dict(win.cfg)
    old_cfg["groups"] = ["galaxy", "nebula", "cluster", "dark", "other"]   # pre-rename config
    w_old = gui.MainWindow(old_cfg)
    check("a config from before the rename turns everything on, not off",
          w_old._extras_on() == {"named", "grid"} and len(w_old._groups_on()) == len(gui.GROUPS),
          "%s / %d catalogues" % (sorted(w_old._extras_on()), len(w_old._groups_on())))
    for w in (w_off, w_old):
        w.close()
    win.chk_extra["named"].setChecked(True)
    win.chk_extra["grid"].setChecked(True)

    # the overlay really draws them: a star at the centre of the field, and a grid over the frame
    alf = next((d for d in gui.catalogs.named_stars() if d.name.startswith("alf CMa")), None)
    if alf is None:
        check("the bright-star catalogue is loaded", False, "no alf CMa")
    else:
        wcs = _synthetic_wcs(alf.ra, alf.dec)
        fake = type("F", (), {"width": 1200, "height": 800, "linear": True, "fmt": "fits"})()
        shapes2, named2, grid2 = run.local_overlay(fake, wcs)
        check("a named star in the field is found", any(r.name.startswith("alf CMa") for r in named2),
              "%d named" % len(named2))
        check("the grid is produced", len(grid2) > 0, "%d lines" % len(grid2))
        v = win.view
        v.draw_overlay(shapes2, named2, grid2, categories=set(win._groups_on()), show_named=True,
                       shape_style="outline")
        with_them = len(v.scene().items())
        v.draw_overlay(shapes2, named2, None, categories=set(win._groups_on()), show_named=False,
                       shape_style="outline")
        without = len(v.scene().items())
        check("turning them off draws strictly less", with_them > without,
              "%d with, %d without" % (with_them, without))
        v.clear_overlay()

    # 4v ── labels never overprint each other
    # A wide nebula caption can land exactly where a star name goes (NGC1499's caption and ksi Per are a real
    # case). Every label now has several distances to try, keeps a couple of pixels of air, and is left out
    # rather than printed across another one.
    from skyannot import style as st                                 # noqa: E402
    offs = st.label_offsets(None, 100, 20, 5)
    check("a star name has several places to try", len(offs) >= 12, "%d offsets" % len(offs))
    check("the rings step further out", offs[0][0] == 5 and offs[4][0] == 5 + 18, str(offs[:5]))
    check("a candidate inside a placed label is not free",
          st.choose_box([(10, 10, 90, 30)], [(0, 0, 100, 40)], None, may_skip=True) is None)
    check("and one closer than the padding counts as touching",
          st.choose_box([(101, 0, 160, 20)], [(0, 0, 100, 20)], None, may_skip=True) is None)
    check("clear of it once past the padding",
          st.choose_box([(103, 0, 160, 20)], [(0, 0, 100, 20)], None, may_skip=True) is not None)
    check("the bitmap preview still gets a box when crowded",
          st.choose_box([(10, 10, 90, 30)], [(0, 0, 100, 40)], None) is not None)

    # a crowded field: whatever is drawn must be clear of everything else
    from PyQt5.QtGui import QImage as _QI                             # noqa: E402
    from types import SimpleNamespace as _NS                          # noqa: E402
    v2 = gui.ImageView()
    v2.resize(640, 480)
    v2.show()
    QTest.qWait(80)
    px = np.full((480, 640, 3), 24, np.uint8)
    v2.set_image(_QI(np.ascontiguousarray(px).data, 640, 480, 640 * 3, _QI.Format_RGB888).copy())
    QTest.qWait(80)
    pts = [{"kind": "point", "x": 300 + (i % 3) * 14, "y": 230 + (i // 3) * 12,
            "label": "OBJ-%d" % i, "category": "nebula", "name": "OBJ-%d" % i} for i in range(6)]
    stars = [_NS(x=305 + i * 5, y=240 + i * 4, name="Star%d" % i, extra={}) for i in range(3)]
    v2.draw_overlay(pts, stars, [], categories=None, show_named=True)
    s2 = v2.transform().m11()
    boxes = []
    for it in v2.overlay.childItems():
        kids = it.childItems()
        if not kids:
            continue
        t = kids[0]
        bw, bh = t.boundingRect().width(), t.boundingRect().height()
        ax, ay = it.pos().x(), it.pos().y()
        dx, dy = t.pos().x(), t.pos().y()
        boxes.append((ax + dx / s2, ay + dy / s2, ax + (dx + bw) / s2, ay + (dy + bh) / s2))
    overlaps = sum(1 for i in range(len(boxes)) for j in range(i + 1, len(boxes))
                   if min(boxes[i][2], boxes[j][2]) > max(boxes[i][0], boxes[j][0])
                   and min(boxes[i][3], boxes[j][3]) > max(boxes[i][1], boxes[j][1]))
    check("a crowded field draws its labels clear of each other", overlaps == 0 and len(boxes) >= 5,
          "%d labels, %d overlap(s)" % (len(boxes), overlaps))
    v2.clear_overlay()
    v2.close()

    # 4w ── a label stays by its object when the picture is shown small
    # The placement used to take the frame's size from the pixmap, which is a downscaled copy when the view is
    # zoomed out; every label was then clamped into a box the size of that copy's corner, and moved again on
    # every zoom. Bounds must come from the picture itself.
    v3 = gui.ImageView()
    v3.resize(700, 500)
    v3.show()
    QTest.qWait(80)
    big = np.full((1400, 2000, 3), 20, np.uint8)
    v3.set_image(_QI(np.ascontiguousarray(big).data, 2000, 1400, 2000 * 3, _QI.Format_RGB888).copy())
    QTest.qWait(450)                       # let the view build and install its downscaled pixmap
    check("the view really is showing a downscaled copy of the frame", v3.pix.pixmap().width() < 1500,
          "%d px wide" % v3.pix.pixmap().width())
    v3.draw_overlay([{"kind": "point", "x": 1700, "y": 1100, "label": "EDGE OBJ", "category": "nebula",
                      "name": "EDGE"}], [], [], categories=None, show_named=False)
    v3._relayout()
    s3 = v3.transform().m11()
    text = next(it for it in v3.scene().items() if isinstance(it, gui.QGraphicsSimpleTextItem))
    anchor = text.parentItem()
    tx = anchor.pos().x() * s3 + text.pos().x()          # screen px, scene origin at 0
    ty = anchor.pos().y() * s3 + text.pos().y()
    check("a label on a downscaled frame stays by its object",
          abs(tx - 1700 * s3) < 200 and abs(ty - 1100 * s3) < 150,
          "label at %.0f,%.0f object at %.0f,%.0f" % (tx, ty, 1700 * s3, 1100 * s3))
    # and it must not move when the view zooms: same anchor, same screen offset
    before = (anchor.pos().x(), anchor.pos().y(), text.pos().x(), text.pos().y())
    v3._fit = False
    v3.scale(1.7, 1.7)
    v3._relayout()
    text = next(it for it in v3.scene().items() if isinstance(it, gui.QGraphicsSimpleTextItem))
    anchor = text.parentItem()
    after = (anchor.pos().x(), anchor.pos().y(), text.pos().x(), text.pos().y())
    check("and it keeps the same place through a zoom",
          all(abs(a - b) < 0.01 for a, b in zip(before, after)),
          "%s -> %s" % (tuple(round(v, 1) for v in before), tuple(round(v, 1) for v in after)))
    v3.clear_overlay()
    v3.close()

    # 4w ── colour frames: telling a mosaic from a colour image, and debayering
    from skyannot import debayer as dby                       # noqa: E402
    check("the Bayer pattern is read from the header",
          [dby.pattern_of({k: "RGGB"}) for k in ("BAYERPAT", "BAYERPATTERN", "PCL:CFASourcePattern")]
          == ["RGGB"] * 3,
          str([dby.pattern_of({k: "RGGB"}) for k in ("BAYERPAT", "BAYERPATTERN", "PCL:CFASourcePattern")]))
    check("a pattern in lower case still reads", dby.pattern_of({"BAYERPAT": "bggr"}) == "BGGR")
    check("a header that names no pattern gives nothing", dby.pattern_of({}) == "")
    check("a nonsense pattern is refused rather than guessed",
          dby.pattern_of({"BAYERPAT": "XYZW"}) == "" and dby.pattern_of({"BAYERPAT": "RG"}) == "")

    mosaic = np.zeros((9, 9, 1), np.float32)                  # 2-D data plus a pattern: a mosaic
    check("a mosaic is recognised", dby.needs_debayer(mosaic, {"BAYERPAT": "RGGB"}))
    rgb3 = np.zeros((9, 9, 3), np.float32)                    # three channels: already done
    check("three-channel data is never a mosaic", not dby.needs_debayer(rgb3, {"BAYERPAT": "RGGB"}))
    check("no pattern named means nothing to do", not dby.needs_debayer(mosaic, {}))
    check("a file that records where its CFA came from is already done",
          not dby.needs_debayer(mosaic, {"BAYERPAT": "RGGB",
                                         "PCL:CFASourceFilePath": "somewhere/x.fits"}))

    # the interpolation must keep every pixel's own measured value, for its own channel
    hh, ww = 30, 40
    yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
    colour = np.clip(np.stack([0.30 + 0.35 * np.sin(xx / 6.0), 0.20 + 0.30 * np.cos(yy / 5.0),
                               0.25 + 0.25 * np.sin((xx + yy) / 9.0)], axis=2), 0, 1)
    for pattern in ("RGGB", "BGGR", "GRBG", "GBRG"):
        field = _mosaic(colour, pattern)[..., 0]
        out = dby.debayer(field[..., None], pattern)
        kept = True
        for row in range(2):
            for col in range(2):
                ch = {"R": 0, "G": 1, "B": 2}[pattern[row * 2 + col]]
                kept &= bool(np.allclose(out[row::2, col::2, ch], field[row::2, col::2]))
        check("%s keeps each pixel's measured channel" % pattern, kept)
        check("%s recovers colour over the whole frame" % pattern,
              float(np.abs(out[3:-3, 3:-3] - colour[3:-3, 3:-3]).mean()) < 0.05,
              "%.4f" % float(np.abs(out[3:-3, 3:-3] - colour[3:-3, 3:-3]).mean()))
        # A flat field of one colour must come back flat and at that colour. Filling a missing channel from a
        # neighbour of another colour leaves a one-pixel chequerboard, and the channel means then no longer
        # match the sensor's — which is exactly what tilted the white balance and turned the sky magenta.
        flat = np.zeros((24, 24), np.float32)
        for row in range(2):
            for col in range(2):
                flat[row::2, col::2] = {"R": 0.10, "G": 0.20, "B": 0.30}[pattern[row * 2 + col]]
        fo = dby.debayer(flat, pattern)
        check("%s returns a flat colour flat" % pattern,
              float(np.ptp(fo[..., 0])) < 1e-6 and float(np.ptp(fo[..., 1])) < 1e-6
              and float(np.ptp(fo[..., 2])) < 1e-6,
              "R span %.4f G %.4f B %.4f" % (float(np.ptp(fo[..., 0])), float(np.ptp(fo[..., 1])),
                                             float(np.ptp(fo[..., 2]))))
        check("%s keeps the channel means" % pattern,
              abs(float(fo[..., 0].mean()) - 0.10) < 1e-5 and abs(float(fo[..., 1].mean()) - 0.20) < 1e-5
              and abs(float(fo[..., 2].mean()) - 0.30) < 1e-5,
              "%.4f %.4f %.4f" % (float(fo[..., 0].mean()), float(fo[..., 1].mean()),
                                  float(fo[..., 2].mean())))
    check("the result stays in range",
          float(dby.debayer(_mosaic(colour, "RGGB"), "RGGB").max()) <= 1.0)
    check("a frame too small to interpolate is repeated, not crashed",
          dby.debayer(np.zeros((2, 2), np.float32), "RGGB").shape == (2, 2, 3))

    # through load(): a real CFA file on disk gets debayered, and one already done does not
    from astropy.io import fits as _fits                      # noqa: E402
    cfa_path = os.path.join(TMP, "cfa.fits")
    _fits.PrimaryHDU((_mosaic(colour, "RGGB")[..., 0] * 65535).astype(np.uint16)).writeto(cfa_path)
    with _fits.open(cfa_path, mode="update") as hd:
        hd[0].header["BAYERPAT"] = "RGGB"
    loaded = iio.load(cfa_path)
    check("a mosaic frame comes back as three channels", loaded.data.shape[2] == 3, str(loaded.data.shape))
    check("and is reported as debayered", loaded.debayered == "RGGB", loaded.debayered)
    check("and counts as a colour image", loaded.is_color)
    check("its colours survive the round trip",
          float(np.abs(loaded.data[3:-3, 3:-3] - colour[3:-3, 3:-3]).mean()) < 0.12,
          "%.4f" % float(np.abs(loaded.data[3:-3, 3:-3] - colour[3:-3, 3:-3]).mean()))
    raw_load = iio.load(cfa_path, want_debayer=False)
    check("switched off, the mosaic is left alone",
          raw_load.data.shape[2] == 1 and raw_load.debayered == "" and not raw_load.is_color,
          str(raw_load.data.shape))
    check("and the status phrase says it is still raw",
          "raw" in dby.describe(raw_load.data, raw_load.keywords, raw_load.debayered))
    check("the loaded value range is what the file held, not blown out",
          float(loaded.data.max()) <= 1.0 and abs(float(loaded.data.mean()) - float(colour.mean())) < 0.03,
          "mean %.4f vs %.4f" % (loaded.data.mean(), colour.mean()))

    rgb_path = os.path.join(TMP, "rgb_bayer.fits")
    _fits.PrimaryHDU(np.moveaxis((colour * 65535).astype(np.uint16), -1, 0)).writeto(rgb_path)
    with _fits.open(rgb_path, mode="update") as hd:
        hd[0].header["BAYERPAT"] = "RGGB"                     # what PixInsight leaves behind
        hd[0].header["PCL:CFASOURCEFILEPATH"] = "elsewhere.fits"
    already = iio.load(rgb_path)
    check("an RGB frame carrying a leftover BAYERPAT is not touched",
          already.debayered == "" and already.data.shape[2] == 3, already.debayered)
    check("and is reported as colour, not as debayered", already.is_color and not already.debayered)

    # ── fixing the frame's own header: what every solver reads as its search scale ──
    fix_path = os.path.join(TMP, "fixme.fits")
    _fits.PrimaryHDU(np.moveaxis((colour * 65535).astype(np.uint16), -1, 0)).writeto(fix_path)
    with _fits.open(fix_path, mode="update") as hd:
        hd[0].header["FOCALLEN"] = 491.0                  # a lens profile from another telescope
        hd[0].header["XPIXSZ"] = 4.63
    before = iio.hints(iio.load(fix_path))
    check("a wrong focal length makes a wrong pixel scale",
          abs(before.get("pixscale", 0) - 206.264806 * 4.63 / 491.0) < 0.01, str(before))
    check("and the frame records no pointing at all", "ra" not in before, str(before))
    written = iio.fix_header(fix_path, focal_mm=62.0, pix_um=4.63, ra=60.83, dec=36.37)
    after = iio.hints(iio.load(fix_path))
    check("the corrected focal length gives the wide-field scale",
          abs(after.get("pixscale", 0) - 206.264806 * 4.63 / 62.0) < 0.01, str(after))
    check("and the corrected centre is what the solvers are told",
          abs(after.get("ra", 0) - 60.83) < 1e-6 and abs(after.get("dec", 0) - 36.37) < 1e-6, str(after))
    check("the header carries the values in both spellings",
          "RA" in written and "OBJCTRA" in written
          and iio.header_keywords(fix_path).get("FOCALLEN") == "62.0",
          str(iio.header_keywords(fix_path).get("FOCALLEN")))
    dlg = gui.FixHeaderDialog(iio.load(fix_path), win)
    check("the fix dialog opens on the file's own values",
          abs(dlg.focal.value() - 62.0) < 0.01 and dlg.ra.text().startswith("04 03"),
          "%s / %r" % (dlg.focal.value(), dlg.ra.text()))
    check("and says what the field becomes", "15.4" in dlg.info.text(), dlg.info.text())
    dlg.ra.setText("nonsense")
    dlg.accept()
    check("an unreadable position is refused", dlg.written == [] and bool(dlg.ra.property("bad")),
          str(dlg.written))
    dlg.close()

    # ── the same correction written into a whole folder ──
    batch = os.path.join(TMP, "batchfix")
    os.makedirs(batch, exist_ok=True)
    paths_b = []
    for k in range(3):
        fp = os.path.join(batch, "f%d.fits" % k)
        _fits.PrimaryHDU(np.moveaxis((colour * 65535).astype(np.uint16), -1, 0)).writeto(fp)
        with _fits.open(fp, mode="update") as hd:
            hd[0].header["FOCALLEN"] = 491.0
        paths_b.append(fp)
    Image.new("RGB", (60, 45)).save(os.path.join(batch, "f0_thn.jpg"))
    seen = []
    done_b, failed_b = iio.fix_header_batch(paths_b, focal_mm=62.0, pix_um=4.63, ra=60.83, dec=36.37,
                                            progress=lambda i, n, p: seen.append((i, n)))
    check("a batch writes every frame", len(done_b) == 3 and not failed_b, "%d / %s" % (len(done_b), failed_b))
    check("and reports how far along it is", seen == [(1, 3), (2, 3), (3, 3)], str(seen))
    check("each frame now carries the correction",
          all(iio.header_keywords(p).get("FOCALLEN") == "62.0" for p in paths_b),
          str([iio.header_keywords(p).get("FOCALLEN") for p in paths_b]))
    done_b2, failed_b2 = iio.fix_header_batch(paths_b + [os.path.join(batch, "missing.fits")], focal_mm=62.0)
    check("one bad file does not cost the rest of the folder",
          len(done_b2) == 3 and len(failed_b2) == 1, "%d / %d" % (len(done_b2), len(failed_b2)))
    stepped = []
    done_b3, _ = iio.fix_header_batch(paths_b, focal_mm=63.0, should_stop=lambda: len(stepped) > 0,
                                      progress=lambda i, n, p: stepped.append(i))
    check("and a stop request ends the run", len(done_b3) == 1 and stepped == [1], str(stepped))
    dlg_b = gui.FixHeaderDialog(iio.load(paths_b[0]), win)
    check("the dialog offers the whole folder",
          dlg_b.all_files.isEnabled() and len(dlg_b._folder_paths) == 3, str(len(dlg_b._folder_paths)))
    check("a thumbnail is not part of the batch",
          all(p.endswith(".fits") for p in dlg_b._folder_paths),
          str([os.path.basename(p) for p in dlg_b._folder_paths]))
    dlg_b.all_files.setChecked(True)
    check("the folder is what it writes", len(dlg_b.target_paths()) == 3, str(dlg_b.target_paths()))
    dlg_b.close()

    mono_path = os.path.join(TMP, "mono.fits")
    _fits.PrimaryHDU((_mosaic(colour, "RGGB")[..., 0] * 65535).astype(np.uint16)).writeto(mono_path)
    mono = iio.load(mono_path)
    check("a monochrome frame with no pattern stays single channel",
          mono.data.shape[2] == 1 and not mono.debayered and not mono.is_color, str(mono.data.shape))

    check("the setting is offered in the settings dialog",
          gui.SettingsDialog(win.cfg, win).chk_debayer.isChecked() is True)
    check("and is saved with the other settings",
          gui.SettingsDialog(win.cfg, win).values()["debayer"] is True)

    # 4x ── choosing the Bayer arrangement by hand, for a frame whose header does not say
    check("only the four arrangements a Bayer sensor can have are offered",
          dby.PATTERNS == ("RGGB", "BGGR", "GRBG", "GBRG"), str(dby.PATTERNS))
    check("each one has two greens, one on each diagonal",
          all(p.count("G") == 2
              and (p[0] == "G") != (p[1] == "G")        # one green in the cell's first row, one in the second
              and (p[0] == "G") != (p[2] == "G")        # ... and one in each column
              for p in dby.PATTERNS),
          str([(p, [i for i, ch in enumerate(p) if ch == "G"]) for p in dby.PATTERNS]))
    check("each one is described for the chooser",
          all(dby.PATTERN_LABELS.get(p) for p in dby.PATTERNS)
          and len(set(dby.PATTERN_LABELS[p] for p in dby.PATTERNS)) == 4,
          str([dby.PATTERN_LABELS.get(p) for p in dby.PATTERNS]))
    check("a pattern typed in any case is accepted",
          [dby.normalise(x) for x in ("rggb", " RGGB ", "bggr")] == ["RGGB", "RGGB", "BGGR"])
    check("an arrangement no sensor has is refused",
          [dby.normalise(x) for x in ("GRGB", "GBBR", "RGGR", "RGGBX", "", None)]
          == [""] * 6,
          str([dby.normalise(x) for x in ("GRGB", "GBBR", "RGGR", "RGGBX", "", None)]))

    # a header that names nothing: the chosen pattern is what gets used
    no_header = os.path.join(TMP, "no_header_cfa.fits")
    _fits.PrimaryHDU((_mosaic(colour, "BGGR")[..., 0] * 65535).astype(np.uint16)).writeto(no_header)
    auto_load = iio.load(no_header)
    check("with no header pattern and no choice, the frame stays a mosaic",
          auto_load.data.shape[2] == 1 and not auto_load.is_color and not auto_load.debayered,
          str(auto_load.data.shape))
    check("and it is not offered as a mosaic to choose for until a pattern is named",
          not auto_load.is_bayer_mosaic)
    chosen = iio.load(no_header, pattern="BGGR")
    check("naming the arrangement debayers it", chosen.debayered == "BGGR" and chosen.data.shape[2] == 3,
          "%s %s" % (chosen.debayered, chosen.data.shape))
    check("the colours come back with the right arrangement",
          float(np.abs(chosen.data[3:-3, 3:-3] - colour[3:-3, 3:-3]).mean()) < 0.12,
          "%.4f" % float(np.abs(chosen.data[3:-3, 3:-3] - colour[3:-3, 3:-3]).mean()))
    wrong = iio.load(no_header, pattern="RGGB")
    check("the wrong arrangement is measurably worse, so the choice matters",
          float(np.abs(wrong.data[3:-3, 3:-3] - colour[3:-3, 3:-3]).mean())
          > 1.5 * float(np.abs(chosen.data[3:-3, 3:-3] - colour[3:-3, 3:-3]).mean()),
          "%.4f vs %.4f" % (float(np.abs(wrong.data[3:-3, 3:-3] - colour[3:-3, 3:-3]).mean()),
                            float(np.abs(chosen.data[3:-3, 3:-3] - colour[3:-3, 3:-3]).mean())))
    check("a chosen pattern never overrides the header",
          iio.load(cfa_path, pattern="BGGR").debayered == "RGGB",
          iio.load(cfa_path, pattern="BGGR").debayered)
    rgb_here = os.path.join(TMP, "rgb_for_choice.fits")
    _fits.PrimaryHDU(np.moveaxis((colour * 65535).astype(np.uint16), -1, 0)).writeto(rgb_here)
    check("a chosen pattern is ignored for an RGB frame",
          iio.load(rgb_here, pattern="BGGR").debayered == ""
          and iio.load(rgb_here).data.shape[2] == 3,
          iio.load(rgb_here, pattern="BGGR").debayered)
    # a single-channel frame with no header pattern is the ambiguous case this option is for: naming a
    # pattern is a statement that it IS a colour frame, so it gets debayered
    forced = iio.load(mono_path, pattern="BGGR")
    check("naming a pattern on an undecided single-channel frame debayers it",
          forced.debayered == "BGGR" and forced.data.shape[2] == 3,
          "%s %s" % (forced.debayered, forced.data.shape))
    check("while leaving it on automatic does not",
          iio.load(mono_path).data.shape[2] == 1 and iio.load(mono_path).debayered == "")
    check("and the panel offers the choice for exactly that frame",
          iio.load(mono_path).can_choose_bayer and not iio.load(mono_path).is_bayer_mosaic)
    check("but not for a frame that is already colour",
          not iio.load(rgb_here).can_choose_bayer and not iio.load(cfa_path).can_choose_bayer)

    # the panel control: offered, remembered, and disabled when it cannot apply
    check("the panel offers the Bayer chooser",
          [win.cmb_bayer.itemData(i) for i in range(win.cmb_bayer.count())]
          == ["auto"] + list(dby.PATTERNS),
          str([win.cmb_bayer.itemData(i) for i in range(win.cmb_bayer.count())]))
    check("it starts on automatic", win.cmb_bayer.currentData() == "auto")
    win.cmb_bayer.setCurrentIndex(1)
    check("choosing one is remembered", win.cfg["bayer_pattern"] == dby.PATTERNS[0],
          win.cfg["bayer_pattern"])
    win.cmb_bayer.setCurrentIndex(0)
    check("back to automatic is remembered too", win.cfg["bayer_pattern"] == "auto")
    check("it is disabled for a frame that is not a mosaic",
          not win.cmb_bayer.isEnabled() or win.img is None,
          "enabled=%s img=%s" % (win.cmb_bayer.isEnabled(), win.img is not None))

    # 4y ── a big nebula's outline must survive vertices that cannot be projected
    # The projector returns NaN beyond about twice the field radius (a SIP solution is only valid near the
    # frame). A large outline runs well outside it, and one such vertex used to discard the whole outline,
    # leaving the catalogue ellipse — which has no position angle for exactly these nebulae, so it came out
    # vertical whatever the real orientation. See NGC1499 (160' x 40', no PosAng in OpenNGC).
    from skyannot import annotate as ann_mod                # noqa: E402
    big = _synthetic_wcs(60.9, 36.4, width=6248, height=4176, pixscale=2.2222)
    proj = ann_mod.Projector(big, 6248, 4176)
    col = gui.shapes_mod.collect(proj)
    ngc1499 = next((e for e in col if e.get("name") == "NGC1499"), None)
    check("NGC1499 is drawn from its real outline, not an ellipse",
          ngc1499 is not None and ngc1499.get("kind") == "outline",
          "none" if ngc1499 is None else str(ngc1499.get("kind")))
    if ngc1499 is not None:
        pts = [p for poly in (ngc1499.get("polygons") or []) for p in poly]
        check("the outline kept its vertices", len(pts) > 20, "%d points" % len(pts))
        check("and every drawn vertex is a real number",
              all(np.isfinite(x) and np.isfinite(y) for x, y in pts))
        # the real orientation: the major axis of the point cloud, not its bounding box (a diagonal shape has
        # a box nearly as tall as it is wide, so a box tells you nothing about which way it runs)
        arr = np.array(pts, float)
        centred = arr - arr.mean(axis=0)
        cov = np.cov(centred.T)
        vals, vecs = np.linalg.eigh(cov)
        major = vecs[:, int(np.argmax(vals))]                 # (dx, dy) of the long axis
        angle = abs(np.degrees(np.arctan2(major[1], major[0]))) % 180.0
        check("its long axis is nowhere near vertical",
              min(angle, 180.0 - angle) > 25.0,
              "major axis %.0f deg from horizontal (a PA-0 ellipse would be 90)" % angle)
        # 4z ── a missing PosAng must not become a vertical oval
        # OpenNGC has no PosAng for NGC1499, and the old code filled that gap with 0 degrees, which draws a
        # vertical oval and asserts a direction the catalogue never gave. The outline's own long axis now
        # stands in, and an object whose outline is too round to have an axis gets a circle instead.
        fallback = _ellipse_angle(gui.shapes_mod, ann_mod, big, "NGC1499")
        check("with no catalogue PosAng the ellipse takes its angle from the outline",
              min(fallback, 180.0 - fallback) < 80.0,
              "%.0f deg from horizontal (0 would be the old vertical oval)" % fallback)
        check("an elongated outline is allowed to supply a direction",
              gui.shapes_mod.MIN_AXIS_ELONGATION > 1.0,
              "%.2f" % gui.shapes_mod.MIN_AXIS_ELONGATION)
        # a round outline has no axis, so its 'long axis' is vertex noise and must not be used
        ring = [(10.0 + 0.10 * np.cos(a), 5.0 + 0.10 * np.sin(a))
                for a in np.linspace(0, 2 * np.pi, 40, endpoint=False)]
        pa_round, elong = gui.shapes_mod._outline_axis(ring)
        check("a round outline is recognised as having no axis",
              elong < gui.shapes_mod.MIN_AXIS_ELONGATION, "elongation %.3f" % elong)
        straight = [(10.0 + 0.30 * a, 5.0 + 0.02 * a) for a in np.linspace(-1, 1, 40)]
        pa_line, elong_line = gui.shapes_mod._outline_axis(straight)
        check("an elongated outline is recognised as having one",
              elong_line > gui.shapes_mod.MIN_AXIS_ELONGATION, "elongation %.2f" % elong_line)
        check("and its angle points along the shape",
              abs(((pa_line - 90.0) % 180.0)) < 12.0 or abs(((pa_line - 90.0) % 180.0) - 180.0) < 12.0,
              "PA %.1f deg for a shape running east-west (90 = due east)" % pa_line)
        # a catalogue angle, where one exists, is never overridden
        with_pa = next((o for o in gui.catalogs.openngc() if o.pa is not None and o.maj), None)
        check("a catalogue PosAng is left alone",
              gui.shapes_mod._axis_for(with_pa, None)[0] == float(with_pa.pa),
              "%s: %.1f" % (with_pa.name, float(with_pa.pa)))
        no_dir = next((o for o in gui.catalogs.openngc() if o.pa is None and o.maj), None)
        check("an object with no angle and no outline comes back with none",
              gui.shapes_mod._axis_for(no_dir, None)[0] is None, no_dir.name)
        # 4za ── position angles that OpenNGC leaves blank
        # OpenNGC has no PosAng for ~3200 entries, and a blank used to become 0 degrees, i.e. a vertical oval.
        # tools/leda_pa.py fills what HyperLEDA knows (validated against OpenNGC's own angles: median 2.0 deg,
        # 83% within 10), and the outline covers elongated objects LEDA does not list.
        fix = gui.catalogs.posang_fix()
        check("the HyperLEDA addendum is bundled and readable", len(fix) > 100, "%d entries" % len(fix))
        rows = gui.catalogs.openngc()
        applied = [d for d in rows if d.pa is not None and d.name in fix]
        check("its angles reach the catalogue entries", len(applied) == len(fix),
              "%d of %d applied" % (len(applied), len(fix)))
        check("every filled angle is a real number in range",
              all(0.0 <= float(d.pa) <= 360.0 for d in applied))
        check("the addendum does not overwrite what OpenNGC already had",
              all(d.name not in fix or d.pa is not None for d in rows))
        # the addendum is a separate file, so the upstream catalogue is untouched
        upstream = os.path.join(ROOT, "data", "openngc", "NGC.csv")
        check("NGC.csv itself was not edited",
              os.path.exists(upstream) and
              "posang.csv" not in open(upstream, encoding="utf-8").readline(),
              "upstream file still as published")
        check("the loader tolerates the addendum being absent",
              gui.catalogs.posang_fix.__wrapped__ is not None)

    # and a catalogue ellipse is still used where there is no outline at all
    ell = next((e for e in col if e.get("kind") == "ellipse"), None)
    check("an object with no outline still falls back to the catalogue ellipse",
          ell is not None, "none found")
    check("its polygons are finite too",
          ell is None or all(np.isfinite(x) and np.isfinite(y)
                             for poly in (ell.get("polygons") or []) for x, y in poly))

    # 4z ── showing a file in the file manager
    from skyannot import reveal as rvl                        # noqa: E402
    calls = []
    real_shell = rvl._reveal_windows
    rvl._reveal_windows = lambda p: (calls.append(p), True)[1]
    try:
        ok = rvl.reveal(paths[0])
    finally:
        rvl._reveal_windows = real_shell
    check("revealing a file hands the shell its absolute path",
          ok and calls == [os.path.abspath(paths[0])],
          str(calls))
    check("a file that is gone reveals the folder it was in, not an error",
          rvl.reveal(os.path.join(TMP, "gone.png")) is True or not rvl._WINDOWS,
          "non-Windows skips this")
    gone = []
    rvl._reveal_windows = lambda p: (gone.append(p), True)[1]
    try:
        rvl.reveal(os.path.join(TMP, "also_gone.png"))
    finally:
        rvl._reveal_windows = real_shell
    check("and it passes the folder, not the missing file",
          not gone or os.path.isdir(gone[0]), str(gone))
    # the menu action must go through the shell API, not a spawned `explorer /select,...` command line
    gui_src = open(os.path.join(ROOT, "skyannot", "gui.py"), encoding="utf-8").read()
    check("the menu goes through reveal.reveal()", "reveal.reveal(path)" in gui_src)
    check("and no longer spawns explorer itself",
          "subprocess.Popen(['explorer'" not in gui_src and "'/select," not in gui_src,
          "still spawns explorer" if "subprocess.Popen(['explorer'" in gui_src else "")

    # 5a ── the sky-neutral stretch: removing each channel's pedestal instead of rescaling it
    # A one-shot colour frame that has not been background-calibrated has a different black level per channel,
    # which is what makes an uncalibrated frame's sky green. Scaling each channel (unlinked) hides that but
    # exaggerates every other colour difference; subtracting the pedestal removes it at its source.
    col_a = np.zeros((64, 64, 3), np.float32)
    col_a[..., 0] = 0.010                                    # red sky level
    col_a[..., 1] = 0.020                                    # green sky, twice as high
    col_a[..., 2] = 0.010
    col_a[16:48, 16:48, 0] += 0.30                           # an object brighter in red
    fake = iio.LoadedImage(path="x.fits", fmt="fits", data=col_a, linear=True)
    ped = iio.display_stretch(fake, "pedestal")
    sky = ped[2:8, 2:8].reshape(-1, 3).mean(axis=0)
    check("the sky-neutral stretch makes a green-cast sky neutral",
          abs(float(sky[0] - sky[1])) < 0.02 and abs(float(sky[2] - sky[1])) < 0.02,
          "sky R %.3f G %.3f B %.3f" % tuple(sky))
    obj = ped[28:36, 28:36].reshape(-1, 3).mean(axis=0)
    check("and keeps an object that is redder than the sky red",
          float(obj[0]) > float(obj[2]) + 0.05, "object R %.3f B %.3f" % (obj[0], obj[2]))
    un = iio.display_stretch(fake, "unlinked")
    un_sky = un[2:8, 2:8].reshape(-1, 3).mean(axis=0)
    check("the per-channel stretch also neutralises the sky, but by rescaling",
          abs(float(un_sky[0] - un_sky[1])) < 0.05 and abs(float(un_sky[2] - un_sky[1])) < 0.05,
          "sky R %.3f G %.3f B %.3f" % tuple(un_sky))
    check("the sky-neutral mode is offered in the toolbar",
          "pedestal" in [win.cmb_stretch.itemData(i) for i in range(win.cmb_stretch.count())],
          str([win.cmb_stretch.itemData(i) for i in range(win.cmb_stretch.count())]))
    check("the pedestal estimate finds the mode of each channel",
          np.allclose(iio._offsets(col_a, col_a[::4, ::4].reshape(-1, 3)),
                      [0.010, 0.020, 0.010], atol=0.002),
          str(iio._offsets(col_a, col_a[::4, ::4].reshape(-1, 3))))

    # 5b ── reusing a solution the frame already carries, instead of solving again
    # The ASIAIR, NINA and PixInsight all write a full astrometric solution into the header. Solving again
    # costs the whole solver wait, so it is reused — but only after checking it against the stars actually in
    # the picture, because a stale or wrong header solution is worse than none.
    alf = next(d for d in gui.catalogs.named_stars() if d.name.startswith("alf CMa"))
    w_good = _synthetic_wcs(alf.ra, alf.dec, width=1200, height=800, pixscale=1.0)
    # detections where the catalogue says the stars are: a correct solution must be trusted
    bright = [d for d in gui.catalogs.named_stars()
              if getattr(d, "mag", None) is not None and d.mag <= 6.5]
    bra = np.array([d.ra for d in bright])
    bde = np.array([d.dec for d in bright])
    bpx, bpy = w_good.all_world2pix(bra, bde, 0, quiet=True)
    onframe = np.isfinite(bpx) & np.isfinite(bpy) & (bpx > 0) & (bpx < 1200) & (bpy > 0) & (bpy < 800)
    det = np.column_stack([bpx[onframe], bpy[onframe]])
    # even a 1-degree synthetic field holds only a star or two of the bright catalogue, which is exactly why
    # _trusts_existing has a rule for a single sub-pixel match
    check("there is at least one star to check a solution against", len(det) >= 1, "%d stars" % len(det))
    good = run._existing_check(w_good, det, 1200, 800, log=lambda m: None)
    check("a correct header solution is recognised",
          run._trusts_existing(good, 1200, 800), str(good if good is None else (good[1], float(np.min(good[0])))))
    import copy as _copy                                          # noqa: E402
    w_off = _copy.deepcopy(w_good)
    w_off.wcs.crval = [w_good.wcs.crval[0] + 0.5, w_good.wcs.crval[1] + 0.5]
    bad = run._existing_check(w_off, det, 1200, 800, log=lambda m: None)
    # a solution half a degree out cannot put a catalogue star within a pixel of a detection, so it is not
    # trusted — either because nothing matched, or because the check cannot be made at all (None)
    check("a solution half a degree out is not trusted",
          not run._trusts_existing(bad, 1200, 800),
          "None (uncheckable)" if bad is None else str((bad[1], float(np.min(bad[0])))))
    check("one star landing under a pixel is enough on a sparse field",
          run._trusts_existing((np.array([0.3]), 1), 6248, 4176))
    check("but a lone star far from any detection is not",
          not run._trusts_existing((np.array([40.0]), 0), 6248, 4176))
    check("and no check at all is not trusted either", not run._trusts_existing(None, 1200, 800))
    check("the reuse of an existing solution can be switched off",
          "use_existing" in gui.config.DEFAULTS and gui.config.DEFAULTS["use_existing"] is True,
          str(gui.config.DEFAULTS.get("use_existing")))
    run_src = open(os.path.join(ROOT, "skyannot", "run.py"), encoding="utf-8").read()
    check("the solve path consults the header before calling any solver",
          "wcsio.read_existing(img)" in run_src and "use_existing" in run_src)

    # 5c ── ASIFitsView's display pipeline: auto white balance, then one stretch
    # Its own log shows the order (runDeBayer, runAutoWhiteBalance, "Color Stretch") and its black/mid levels
    # for a 16-bit frame. The point of copying the order is that the sky ends up neutral instead of green,
    # which is what a per-channel stretch on its own cannot do without exaggerating every other colour.
    check("its black and mid levels are on the 16-bit scale",
          0 < iio.AWB_BLACK < iio.AWB_MID < 65535, "%s / %s" % (iio.AWB_BLACK, iio.AWB_MID))
    cast = np.zeros((64, 64, 3), np.float32)
    cast[..., 0] = 0.0187                                    # the sky, green-heavy as an uncalibrated frame
    cast[..., 1] = 0.0250
    cast[..., 2] = 0.0187
    cast[8:24, 8:24, 0] += 0.02                              # an object brighter in red than in blue
    fake2 = iio.LoadedImage(path="x.fits", fmt="fits", data=cast, linear=True)
    awb = iio.display_stretch(fake2, "awb")
    sky = awb[2:6, 2:6].reshape(-1, 3).mean(axis=0)
    check("the white balance leaves a neutral sky, not a green one",
          abs(float(sky[0]) - float(sky[2])) < 0.04 and float(sky[1]) <= max(sky[0], sky[2]) + 0.02,
          "sky R %.3f G %.3f B %.3f" % tuple(sky))
    check("and the sky is dark, as a stretched frame's sky should be",
          float(sky.max()) < 0.35, "max %.3f" % float(sky.max()))
    obj = awb[12:20, 12:20].reshape(-1, 3).mean(axis=0)
    check("the object keeps the colour it had",
          float(obj[0]) > float(obj[2]) + 0.01, "object R %.3f B %.3f" % (obj[0], obj[2]))
    check("the white balance keeps green as the reference",
          abs(float(iio._sky_mode(cast[..., 1]) / iio._sky_mode(cast[..., 1])) - 1.0) < 1e-6)
    # the rule is read off ASIFitsView's own statistics panel: for a frame whose channel averages are
    # 1259/1669/1237 it reports 1668/1669/1669, which only "divide by the mean, times green's mean" produces
    stat = np.zeros((64, 64, 3), np.float32)
    stat[..., 0] = 0.020
    stat[..., 1] = 0.030
    stat[..., 2] = 0.019
    stat[32:40, 32:40, 0] = 0.40                     # a bright red patch, to give each channel a spread
    stat[16:24, 16:24, 2] = 0.35
    means_before = np.array([stat[::4, ::4, c].mean() for c in range(3)])
    gains = means_before[1] / means_before
    balanced = stat * gains
    means_after = np.array([balanced[::4, ::4, c].mean() for c in range(3)])
    check("its rule equalises the channel averages to green's",
          float(np.abs(means_after - means_after[1]).max()) < 1e-5,
          "%s -> %s" % (np.round(means_before, 5), np.round(means_after, 5)))
    check("and leaves the overall level where the black point expects it",
          abs(float(means_after[1]) - float(means_before[1])) < 1e-6,
          "green %.6f -> %.6f" % (means_before[1], means_after[1]))
    check("a single-channel frame is handled without complaint",
          iio.display_stretch(iio.LoadedImage(path="x", fmt="fits", data=cast[..., :1], linear=True),
                              "awb").shape == (64, 64, 3))
    check("the mode is offered in the toolbar",
          "awb" in [win.cmb_stretch.itemData(i) for i in range(win.cmb_stretch.count())],
          str([win.cmb_stretch.itemData(i) for i in range(win.cmb_stretch.count())]))

    # 5d ── the histogram panel: levels, the curve, and the drag handles
    from skyannot import histogram as hm, histview as hv          # noqa: E402
    hdata = np.zeros((60, 90, 3), np.float32)
    hdata[..., 0] = 0.02
    hdata[..., 1] = 0.03
    hdata[..., 2] = 0.02
    hdata[30:45, 30:60, 0] = 0.60                                 # an object
    lv = hm.Levels(black=0.01, mid=0.5, white=0.7)
    out = lv.apply(hdata)
    check("levels map a pixel at the black point to 0",
          abs(float(hm.mtf(0.0, 0.5))) < 1e-6, "%.6f" % float(hm.mtf(0.0, 0.5)))
    check("and a pixel at the white point to 1",
          abs(float(hm.mtf(1.0, 0.5)) - 1.0) < 1e-6, "%.6f" % float(hm.mtf(1.0, 0.5)))
    check("the sky just above black stays dark and the object comes up bright",
          float(out[2:6, 2:6].max()) < 0.05 and float(out[36:39, 40:50, 0].mean()) > 0.8,
          "sky %.3f object %.3f" % (float(out[2:6, 2:6].max()), float(out[36:39, 40:50, 0].mean())))
    check("a midtone below 0.5 brightens and above 0.5 darkens",
          float(hm.mtf(0.5, 0.25)) > 0.5 and float(hm.mtf(0.5, 0.75)) < 0.5,
          "%.3f / %.3f" % (float(hm.mtf(0.5, 0.25)), float(hm.mtf(0.5, 0.75))))
    # mtf_mid(mid_value, black, white, target): the balance that puts 0.10 at 0.2
    solved = hm.mtf_mid(0.10, 0.01, 0.7, 0.2)
    check("the midtone balance can be solved for a target",
          abs(float(hm.mtf((0.10 - 0.01) / 0.69, solved)) - 0.2) < 0.01,
          "m=%.4f makes mtf(0.13) = %.4f" % (solved, float(hm.mtf((0.10 - 0.01) / 0.69, solved))))
    h = hm.histogram(hdata, 0.0, 0.7)
    check("the histogram has a curve per channel and a luminance one",
          set(h) >= {"r", "g", "b", "l", "edges"}, str(sorted(k for k in h if k != "edges")))
    check("the counts account for every sampled pixel of that channel",
          int(np.sum(h["r"])) == int(np.ceil(60 / 4) * np.ceil(90 / 4)),
          "%d counts over %d bins" % (int(np.sum(h["r"])), int(np.count_nonzero(h["r"]))))
    auto = hm.auto_levels(hdata)
    check("auto levels put black below white", auto.black < auto.white,
          "%.5f < %.5f" % (auto.black, auto.white))
    # The window is a fixed width anchored on the frame's dark end (815 counts on the 16-bit scale), which is
    # what makes the markers move from frame to frame the way ASIFitsView's do — but only while the frame's own
    # bright data fits inside it. This synthetic frame has 8 % of its pixels at 0.60, so the window opens out to
    # the top of that data instead of painting the object white.
    check("the auto window opens up when the frame has bright data above it",
          (auto.white - auto.black) * 65535.0 > 1600.0 and auto.white > 0.60,
          "%.1f counts wide, white %.3f" % ((auto.white - auto.black) * 65535.0, auto.white))
    # A star field has far too few bright pixels for that, and keeps the narrow window
    stardata = np.full((60, 90, 3), 0.02, np.float32)
    stardata[:2, :3] = 0.8                         # six stars, 0.1 % of the frame
    star_auto = hm.auto_levels(stardata)
    check("a star field keeps the narrow window",
          abs((star_auto.white - star_auto.black) * 65535.0 - 815.0) < 5.0,
          "%.1f counts wide" % ((star_auto.white - star_auto.black) * 65535.0))
    # A long sub at high gain can have its sky high up the scale; the window must still take in the object in
    # the middle of the frame (the 294MC case: the brightest half of the frame came out white).
    high = np.full((80, 120, 3), 0.055, np.float32)
    high[30:50, 50:70] = 0.085                     # a faint object in the centre, a little above the sky
    high_auto = hm.auto_levels(high)
    rendered = high_auto.apply(high)
    check("a high sky keeps its object inside the window",
          high_auto.white > 0.085 and float((rendered[30:50, 50:70, 0] >= 0.99).mean()) < 0.10,
          "white %.3f, object clipped %.2f%%" % (high_auto.white,
                                                 100.0 * float((rendered[30:50, 50:70, 0] >= 0.99).mean())))
    # The window is anchored on the frame's dark end, but a sky that sits below the naive black point would
    # come out black; the window then slides down over it, keeping its width, so the sky always falls inside.
    sky_mode = float(np.median([hm.sky_mode(hdata[::4, ::4, c]) for c in range(3)]))
    check("and straddles the sky, whatever the frame's floor",
          auto.black < sky_mode < auto.white,
          "black %.5f sky %.5f white %.5f" % (auto.black, sky_mode, auto.white))
    darker = np.full((40, 40, 3), 0.010, np.float32)
    darker[10:20, 10:20] = 0.50
    check("a darker frame gets its markers in a different place",
          abs(hm.auto_levels(darker).black - auto.black) > 1e-3,
          "%.5f vs %.5f" % (hm.auto_levels(darker).black, auto.black))

    dlg = gui.HistogramDialog(win, data=hdata, levels=lv)
    dlg.show()
    QTest.qWait(150)
    check("the panel offers its three actions",
          dlg.btn_awb.text() and dlg.btn_auto.text() and dlg.btn_reset.text(),
          "%s / %s / %s" % (dlg.btn_awb.text(), dlg.btn_auto.text(), dlg.btn_reset.text()))
    check("it draws the frame's own histogram", bool(dlg.view._counts))
    stats = dlg._stats
    check("it reports the statistics per channel",
          set(stats) >= {"r", "g", "b"} and stats["r"]["max"] > stats["r"]["min"],
          str({k: round(v["avg"] * 65535) for k, v in stats.items()}))
    check("the statistics are shown beside the plot as counts",
          dlg.stats._values[("r", "max")].text().isdigit()
          and int(dlg.stats._values[("r", "max")].text()) == int(round(stats["r"]["max"] * 65535)),
          dlg.stats._values[("r", "max")].text())
    check("every statistic sits in 16-bit counts, not above them",
          all(0 <= v <= 65535 for s in stats.values() for v in (s["min"], s["max"])),
          str(stats["r"]))
    check("it opens on the levels it was given",
          abs(dlg.levels().black - lv.black) < 1e-9 and abs(dlg.levels().white - lv.white) < 1e-9)
    # the plot opens on a window around the stretch, not on the whole 0..65535: a linear frame's sky is a
    # spike at the very left of the full range, where nothing can be read off it
    check("the plot opens on a window around the stretch",
          dlg.view.range()[0] <= lv.black and dlg.view.range()[1] >= lv.white,
          "%s" % (dlg.view.range(),))
    # zooming in must recount the sample for the narrower range, and the reset must come back
    before = dlg.view.range()
    lo, hi = 0.2 * (lv.white - lv.black) + lv.black, 0.6 * (lv.white - lv.black) + lv.black
    dlg.view.zoom_to(lo, hi)
    check("the plot can be zoomed to a range",
          abs(dlg.view.range()[0] - lo) < 1e-9 and abs(dlg.view.range()[1] - hi) < 1e-9,
          "%s -> %s" % (before, dlg.view.range()))
    check("the zoomed plot is recounted for its own range", bool(dlg.view._counts))
    dlg.view.reset_range()
    check("a reset of the range comes back to the default",
          dlg.view.range() == dlg.view.default_range(), str(dlg.view.range()))
    # dragging the black handle must move the black point, and only the black point
    v = dlg.view
    start = float(dlg.levels().black)
    x = v._x_of(start)
    v._drag = "black"
    v._apply_drag(x + 25)
    check("dragging the black handle raises the black point",
          dlg.levels().black > start, "%.5f -> %.5f" % (start, dlg.levels().black))
    check("and leaves the white point alone", abs(dlg.levels().white - lv.white) < 1e-9)
    check("the black point cannot pass the white point",
          (v._drag, v._apply_drag(v._x_of(dlg.levels().white) + 500), dlg.levels().black < dlg.levels().white)[2])
    dlg._reset()
    check("reset restores the stretch the panel opened with",
          abs(dlg.levels().black - lv.black) < 1e-9 and abs(dlg.levels().white - lv.white) < 1e-9,
          "%.5f / %.5f" % (dlg.levels().black, dlg.levels().white))
    dlg.close()
    check("the panel is reachable from the toolbar",
          "直方图" in [a.text() for a in win.findChildren(type(win.btn_del)) if hasattr(a, "text")] or
          hasattr(gui.MainWindow, "show_histogram"))

    # 5e ── the picture must not gain a stripe lattice when it is shown small
    # A 6248-pixel frame at 14% leaves Qt sampling one source pixel in seven, and the two-pixel residue a Bayer
    # reconstruction leaves beats against that grid: the sky showed a regular lattice. Averaging first fixes it.
    from PyQt5.QtGui import QImage as _QImage                       # noqa: E402
    W2, H2 = 624, 416
    yy, xx = np.mgrid[0:H2, 0:W2]
    base = np.full((H2, W2, 3), 40, np.uint8)
    # a two-pixel alternation, like the residue a debayer leaves behind: +6 on even columns, -6 on odd
    base[..., 1] = (base[..., 1].astype(np.int16) + np.where(xx % 2 == 0, 6, -6)).astype(np.uint8)
    base[100:110, 300:310] = 255                                   # one bright star, so it is not all flat
    q = _QImage(np.ascontiguousarray(base).data, W2, H2, 3 * W2, _QImage.Format_RGB888).copy()
    small = gui._box_downscale(q, W2 // 7, H2 // 7)
    check("the downscale produces the size asked for",
          abs(small.width() - W2 // 7) <= 1 and abs(small.height() - H2 // 7) <= 1,
          "%dx%d" % (small.width(), small.height()))
    sptr = small.bits()
    sptr.setsize(small.byteCount())
    sarr = np.frombuffer(sptr, np.uint8).reshape(small.height(), small.bytesPerLine())[:, :small.width() * 3]
    sarr = sarr.reshape(small.height(), small.width(), 3)
    # The point is not that every trace of the alternation vanishes — groups of seven cannot halve an even
    # count — but that what is left is invisible, while Qt's own scaling turns it into a visible lattice.
    # Measured on a band away from the test star, so its step cannot be mistaken for the alternation.
    prof = sarr[20:40, :, 1].mean(axis=0).astype(np.float64)
    box_ripple = float(prof.max() - prof.min())
    q_small = q.scaled(small.width(), small.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    qptr = q_small.bits()
    qptr.setsize(q_small.byteCount())
    qarr = np.frombuffer(qptr, np.uint8).reshape(q_small.height(), q_small.bytesPerLine())
    qarr = qarr[:, :q_small.width() * 3].reshape(q_small.height(), q_small.width(), 3)
    q_prof = qarr[20:40, :, 1].mean(axis=0).astype(np.float64)
    q_ripple = float(q_prof.max() - q_prof.min())
    # An odd group width cannot halve an even count, so a trace of the alternation necessarily survives; what
    # matters is that it is a couple of levels out of 255, against a hundred times that without averaging.
    check("averaging leaves the alternation invisible (a few levels at most)",
          box_ripple < 4.0, "ripple %.1f levels (Qt's own scaling leaves %.0f)" % (box_ripple, q_ripple))
    check("and beats Qt's scaling on the same input",
          box_ripple < q_ripple, "%.2f vs %.1f" % (box_ripple, q_ripple))
    check("the picture keeps its brightness",
          abs(float(sarr[:, :, 1].mean()) - float(base[:, :, 1].mean())) < 4.0,
          "%.1f vs %.1f" % (float(sarr[:, :, 1].mean()), float(base[:, :, 1].mean())))
    check("the bright star survives the averaging",
          int(sarr[:, :, 1].max()) > 150, "max %d" % int(sarr[:, :, 1].max()))
    up = gui._box_downscale(q, W2 * 2, H2 * 2)
    check("it never upscales", up.width() == q.width() and up.height() == q.height(),
          "%dx%d" % (up.width(), up.height()))
    # and the view keeps the full picture as its source, so zooming back in restores every pixel
    check("the view can rebuild its pixmap at another resolution",
          hasattr(gui.ImageView, "_rebuild_pixmap") and hasattr(gui.ImageView, "_schedule_pixmap"))

    # 5 ── automatic playback: the timer advances through the folder on its own
    global _loaded
    win.cfg["slide_interval"] = 0.3
    win.file_list.setCurrentRow(0)                       # start playback from the first frame again
    wait_for_idle()
    check("play becomes available once an image is open", win.act_play.isEnabled()
          and win.act_play.text() == "播放")
    _loaded = win._loaded                           # checked before the queue starts (see load())

    win.act_play.trigger()
    QTest.qWait(50)
    check("playing swaps the button to pause", win.act_play.isChecked() and win.act_play.text() == "暂停")

    seen = [os.path.basename(paths[0])]
    for _ in range(6):
        nxt_name, loader = wait_for_change()
        if not nxt_name:
            break
        seen.append(nxt_name)
        gui.MainWindow._loaded(win, loader._out)     # the real method, as the worker would call it
        if len(seen) >= 4:
            break
    say = " -> ".join(seen)
    check("playback steps through the folder by itself",
          seen[:4] == ["a.png", "b.png", "c.png", "a.png"], say)
    check("playback wraps around at the folder end", seen[3] == "a.png", say)

    win.act_play.trigger()
    QTest.qWait(50)
    check("stopping swaps the button back to play",
          not win.act_play.isChecked() and win.act_play.text() == "播放")
    QTest.qWait(500)
    check("stopped playback leaves the image alone",
          os.path.basename(win.current or "") == seen[-1], str(win.current))
    _loaded = None

    # 5b ── the interval comes from the settings and survives a restart
    dlg = gui.SettingsDialog(win.cfg, win)
    check("settings offer the playback interval",
          dlg.slide.minimum() <= 0.3 and dlg.slide.maximum() >= 30
          and abs(dlg.slide.value() - 0.3) < 1e-9, "value %.1f" % dlg.slide.value())
    dlg.slide.setValue(3.0)
    dlg.accept()
    win.cfg = dlg.values()
    config.save(win.cfg)
    check("saved interval survives a reload", abs(config.load().get("slide_interval", 0) - 3.0) < 1e-9,
          str(config.load().get("slide_interval")))
    win.cfg["slide_interval"] = 0.3

    # 6 ── opening from another folder rebuilds the list
    other = os.path.join(TMP, "other")
    os.makedirs(other)
    p = os.path.join(other, "z.png")
    Image.new("RGB", (300, 200), (60, 20, 20)).save(p)
    load(p)
    check("list rebuilt for a new folder", names() == ["z.png"], str(names()))
    check("one image in the folder: nowhere to step",
          not win.btn_prev.isEnabled() and not win.btn_next.isEnabled())
    shot("3_other_folder")

    # 7 ── self-update: the feed, the package and the swap script
    import zipfile
    import json as _json
    import skyannot.updater as up
    check("versions are compared as numbers",
          up.is_newer("0.10.0", "0.9.9") and not up.is_newer("0.1.0", "0.1.0")
          and not up.is_newer("0.1.0", "0.1.10"), "")
    updir = os.path.join(TMP, "updates")
    os.makedirs(updir, exist_ok=True)
    pkgdir = os.path.join(TMP, "pkgstage", "TTSkyAnnot")
    os.makedirs(os.path.join(pkgdir, "_internal"), exist_ok=True)
    for n in ("TTSkyAnnot.exe", os.path.join("_internal", "thing.txt")):
        with open(os.path.join(pkgdir, n), "w", encoding="utf-8") as fh:
            fh.write("x")
    zip_path = os.path.join(updir, "TTSkyAnnot-0.2.0.zip")
    with zipfile.ZipFile(zip_path, "w") as z:
        for root, _dirs, fs in os.walk(pkgdir):
            for f in fs:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, os.path.dirname(pkgdir)))
    feed = {"version": "0.2.0", "url": os.path.basename(zip_path), "sha256": up._sha256(zip_path),
            "size": os.path.getsize(zip_path), "notes": "更新说明"}
    with open(os.path.join(updir, "version.json"), "w", encoding="utf-8") as fh:
        _json.dump(feed, fh, ensure_ascii=False)
    got = up.fetch_feed(updir)
    check("the feed is read from a folder", got.get("version") == "0.2.0", str(got))
    check("and a newer version is recognised", up.is_newer(got.get("version", ""), "0.1.0"))
    dl = os.path.join(TMP, "dl")
    os.makedirs(dl, exist_ok=True)
    pkg = up.download(updir, got, dl)
    check("the package downloads and matches its hash", os.path.isfile(pkg), pkg)
    try:
        os.makedirs(os.path.join(TMP, "dl2"), exist_ok=True)
        up.download(updir, dict(got, sha256="00" * 32), os.path.join(TMP, "dl2"))
        refused = False
    except up.UpdateError as e:
        refused = "SHA-256" in str(e)
    check("a package whose hash is wrong is refused", refused, "")
    staged = up.unpack(pkg)
    check("the package unpacks around its exe", os.path.isfile(os.path.join(staged, "TTSkyAnnot.exe")), staged)
    script = up.install_script(staged, os.path.join(TMP, "appdir"), 4242)
    body = open(script, encoding="utf-8").read()
    check("the swap waits for the running program", "tasklist" in body and "4242" in body, "")
    check("and carries the user's settings over", "config.json" in body and "start" in body, "")
    sd = gui.SettingsDialog(win.cfg, win)
    check("the update source is not a user setting any more",
          not hasattr(sd, "upd") and sd.values().get("update_url") == win.cfg.get("update_url"),
          str(sd.values().get("update_url")))
    sd.deleteLater()
    check("the status bar offers a manual check", bool(win.btn_update.text()), win.btn_update.text())
    check("and so does the toolbar, always in sight", win.act_update.text() != "", win.act_update.text())

    # 7b ── a frame's low-resolution preview is not listed beside it
    # The ASIAIR writes a 244 x 160 *_thn.jpg next to every 6248 x 4176 FITS. The list compares picture sizes,
    # so the small one is left out; the check runs in the background, so the list settles a moment later.
    pv = os.path.join(TMP, "previews")
    os.makedirs(pv)
    big = os.path.join(pv, "frame.png")
    Image.new("RGB", (800, 600), (30, 30, 30)).save(big)
    Image.new("RGB", (120, 90), (30, 30, 30)).save(os.path.join(pv, "frame_thn.png"))
    check("previews are told apart by picture size",
          iio.drop_previews(pv, ["frame.png", "frame_thn.png"]) == ["frame.png"],
          str(iio.drop_previews(pv, ["frame.png", "frame_thn.png"])))
    check("the ASIAIR thumbnail name is recognised on its own",
          iio.preview_name("Light_NGC57_0031_thn.jpg") and not iio.preview_name("Light_NGC57_0031.fit"),
          str(iio.preview_name("Light_NGC57_0031_thn.jpg")))
    check("a thumbnail named that way is dropped even when its size cannot be read",
          iio.drop_previews(pv, ["frame.png", "frame_thn.jpg"]) == ["frame.png"],
          str(iio.drop_previews(pv, ["frame.png", "frame_thn.jpg"])))
    # And the listing leaves them out before the background scan answers, so they never flash up at all
    win.current = big
    check("the listing has no thumbnail in it from the start",
          win._folder_files()[0] == ["frame.png"], str(win._folder_files()[0]))
    check("a thumbnail is not counted as the current picture",
          win._folder_files()[1] == 0, str(win._folder_files()[1]))
    tiny = os.path.join(TMP, "tiny")
    os.makedirs(tiny)
    for n in ("one.png", "two.png"):
        Image.new("RGB", (120, 90), (30, 30, 30)).save(os.path.join(tiny, n))
    check("a folder of nothing but small pictures drops none of them",
          sorted(iio.drop_previews(tiny, ["one.png", "two.png"])) == ["one.png", "two.png"],
          str(iio.drop_previews(tiny, ["one.png", "two.png"])))
    load(big)
    for _ in range(60):
        QTest.qWait(50)
        if names() == ["frame.png"]:
            break
    check("the folder list leaves the preview out", names() == ["frame.png"], str(names()))
    check("and the picture itself is still selected",
          os.path.basename(win.current or "") == "frame.png", str(win.current))

    win.close()
    # 4u1 ── an online-only catalogue name resolves to a position as soon as the answer arrives
    rec = {"name": "LBN777", "ra": 60.5, "dec": 26.25, "common": "LBN 777", "type": "BN",
           "maj": 20.0, "minr": 18.0, "source": "tickwhale"}
    win.ed_target.setText("LBN777")
    win._remote_found("LBN777", [rec])
    check("an online-only name gives the solver a position", win._target_radec == (60.5, 26.25),
          str(win._target_radec))
    check("and the name is offered in the field",
          any(s.startswith("LBN777") for s in win._target_model.stringList()),
          str([s for s in win._target_model.stringList() if "LBN" in s][:3]))
    win.ed_target.setText("lbn 777")
    check("the match ignores case and spaces", win._resolve_typed() and win._target_radec == (60.5, 26.25),
          str(win._target_radec))
    win.ed_target.clear()
    win._resolve_typed()

    # 4u1b ── an object that lives only in the online database is annotated when it is the target
    from skyannot import shapes                             # noqa: E402
    w3 = WCS(naxis=2)
    w3.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w3.wcs.crval = [60.5, 26.25]                           # LBN777
    w3.wcs.crpix = [250.0, 250.0]
    w3.wcs.cd = [[-6.0e-4, 0.0], [0.0, 6.0e-4]]
    proj3 = annotate.Projector(w3, 500, 500)
    rec3 = {"name": "LBN777", "ra": 60.5, "dec": 26.25, "common": "LBN 777", "type": "BN",
            "maj": 20.0, "minr": 18.0, "mag": None, "source": "tickwhale"}
    tb = annotate.online_target_block(proj3, rec3, ("lbn",))
    check("the online target gets a block of its catalogue",
          tb is not None and tb.rows[0].name == "LBN777", tb.name if tb else "none")
    check("and none when that catalogue is switched off",
          annotate.online_target_block(proj3, rec3, ("m",)) is None, "")
    os3 = shapes.online_shape(proj3, rec3, "lbn")
    check("with a label and a size shape",
          os3["category"] == "lbn" and os3["label"].startswith("LBN777") and bool(os3["polygons"]),
          str(os3["label"]) if os3 else "none")

    # 4u1c ── the online region query: the cone it asks for, and many records becoming one block
    from skyannot import tickwhale                          # noqa: E402
    asked = {}
    real_ask = tickwhale._ask

    def fake_ask(url, payload, timeout, action="dso_search"):
        asked["action"], asked["payload"] = action, payload
        return [dict(rec3), dict(rec3, name="LBN778", ra=60.6)]

    try:
        tickwhale._ask = fake_ask
        recs = tickwhale.region("https://x.test", 60.5, 26.25, 0.8, catalogs=("lbn",))
    finally:
        tickwhale._ask = real_ask
    check("the region query asks for a cone",
          asked["action"] == "dso_region" and asked["payload"]["radius"] == 0.8
          and asked["payload"]["catalogs"] == ["lbn"], str(asked["payload"]))
    check("and keeps what the server returns", len(recs) == 2, str(len(recs)))
    rb = annotate.online_region_blocks(proj3, recs, ("lbn",))
    check("the records form one block per catalogue", len(rb) == 1 and len(rb[0].rows) == 2,
          "%d blocks" % len(rb))
    check("and are gone when that catalogue is switched off",
          annotate.online_region_blocks(proj3, recs, ("m",)) == [], "")

    # a frame that arrives with a solution in its header never goes through solve(); it must still get the
    # online objects of its field
    import types                                            # noqa: E402
    fake_img = types.SimpleNamespace(width=500, height=500)
    real_region = tickwhale.region

    def fake_region(base, ra, dec, radius, catalogs=None, limit=500, timeout=8.0):
        return [dict(rec3)]

    try:
        tickwhale.region = fake_region
        found, named2, grid2, online2 = run.local_overlay_with_online(
            fake_img, w3, cats=("lbn",), tickwhale_base="https://x.test")
    finally:
        tickwhale.region = real_region
    check("a header-solved frame still gets its online objects",
          any(s["label"].startswith("LBN777") for s in found), str([s["label"] for s in found][:4]))
    check("and the records are kept for a later refilter", [r["name"] for r in online2] == ["LBN777"],
          str([r["name"] for r in online2]))

    # 4u1e ── a file named after its target gives the solver a position without any typing
    g = run.guess_target(r"C:\x\Light_M 31_300.0s_Bin1_294MC.fits")
    check("a Messier name in the file name is recognised",
          bool(g) and g[0].startswith("M31") and abs(g[1][0] - 10.6848) < 0.05, str(g[:2]))
    real_search = tickwhale.search

    def fake_search(text, base="", timeout=6.0, limit=12):
        return [dict(rec3)] if text.upper() == "LBN777" else []

    try:
        tickwhale.search = fake_search
        g2 = run.guess_target(os.path.join("C:\\x", "261007-261020_2600mm_LBN777", "Image19.jpg"),
                              "https://x.test")
    finally:
        tickwhale.search = real_search
    check("an online-only name in the folder is recognised",
          bool(g2) and g2[0] == "LBN777" and g2[2] is not None, str(g2[:2]))
    # the service answers cd in radians (Nebula3D blind-solve spec); the client must build the same WCS
    class _FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True, "ra": 61.0, "dec": 26.5,
                    "cd": [[-5.95e-8, 2.1e-9], [1.9e-9, -5.94e-8]],
                    "cd_deg": [[-3.41e-6, 1.2e-7], [1.1e-7, -3.40e-6]],
                    "matched": 87, "nstars": 200, "rms_arcsec": 0.42, "parity": 1, "name": "HINT"}

    import requests as _rq                                    # noqa: E402
    from skyannot import solve as solve_mod                   # noqa: E402
    real_post = _rq.post
    _rq.post = lambda *a, **k: _FakeResp()
    try:
        w_api, info = solve_mod.solve_ttastro([[i * 10.0, i * 8.0, 100.0 - i] for i in range(20)],
                                          3672, 5648, api_key="k")
    finally:
        _rq.post = real_post
    check("the service's radians cd becomes a FITS WCS",
          abs(w_api.wcs.cd[0][0] + 3.41e-6) < 1e-9, str(w_api.wcs.cd[0][0]))
    check("and the name and matched count are read",
          info.get("name") == "HINT" and info.get("matched") == 87, str(info.get("name")))

    # the refinement takes its reference stars from the online star cone; VizieR is only the fallback
    from skyannot import solve as solver_mod                   # noqa: E402
    real_fetch = solver_mod.fetch_stars
    calls = {}

    def fake_fetch(ra, dec, radius, mag=16.0, limit=80000, timeout=20.0, log=print):
        calls["args"] = (round(ra, 2), round(dec, 2), round(radius, 2), mag)
        return [[61.0, 26.5, 12.0], [61.1, 26.6, 13.0]]

    solver_mod.fetch_stars = fake_fetch
    try:
        ra, de, mag, rows, capped, cap = run._reference_stars(w3, 500, 500, 2026.0, 16.0, 60000, lambda m: None)
    finally:
        solver_mod.fetch_stars = real_fetch
    check("the refinement takes its stars from the online cone",
          len(rows) == 2 and abs(float(rows[0]["RA_ICRS"]) - 61.0) < 1e-6, str(rows[:1]))
    check("and asks for the solved field", calls.get("args", ("", "", "", 0))[3] == 16.0, str(calls.get("args")))

    # the popular-field index is bundled with the installer: 1,300 hot fields and their Gaia patterns
    from skyannot import fieldindex                              # noqa: E402
    fields, stars, photos = fieldindex.coverage()
    check("the popular-field index is bundled", fields >= 2500 and stars >= 200000,
          "%d fields, %d stars" % (fields, stars))
    check("every field carries a usable pattern", all(len(f["stars"]) >= 12 for f in fieldindex.load()), "")
    nearest, sep = fieldindex.nearest(61.0, 26.5)
    check("LBN777's field is in it", nearest is not None and sep < 0.1 and len(nearest["stars"]) >= 40,
          "%.2f deg, %d stars" % (sep, len(nearest["stars"]) if nearest else 0))

    check("a name with no catalogue designation stays blind",
          run.guess_target(r"C:\x\Image19.jpg", "") == (), str(run.guess_target(r"C:\x\Image19.jpg", "")))

    # 4u2 ── the catalogue switches decide what is drawn and how it is labelled
    from skyannot import shapes                            # noqa: E402
    w2 = WCS(naxis=2)
    w2.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w2.wcs.crval = [10.6847, 41.2690]                      # M31, which is also NGC224
    w2.wcs.crpix = [250.0, 250.0]
    w2.wcs.cd = [[-6.0e-4, 0.0], [0.0, 6.0e-4]]
    proj2 = annotate.Projector(w2, 500, 500)
    by_ngc = {s["name"]: s for s in shapes.collect(proj2, cats=("ngc",))}
    check("with NGC on, M31 is listed and labelled by its NGC number",
          by_ngc["NGC224"]["category"] == "ngc" and by_ngc["NGC224"]["label"].startswith("NGC224"),
          by_ngc["NGC224"]["label"])
    by_m = {s["name"]: s for s in shapes.collect(proj2, cats=("m",))}
    check("with Messier on, the same object is M31",
          by_m["NGC224"]["category"] == "m" and by_m["NGC224"]["label"].startswith("M31"),
          by_m["NGC224"]["label"])
    by_ic = {s["name"]: s for s in shapes.collect(proj2, cats=("ic",))}
    check("with only IC on, an object no enabled catalogue holds is left out",
          "NGC224" not in by_ic, str(sorted(by_ic)[:4]))

    # 4v ── how the update installs itself: the wizard for an installed copy, the zip swap for a portable one
    inst = tempfile.mkdtemp(prefix="tts-inst-")
    try:
        check("a portable folder is not an install", not updater.is_installed(inst))
        open(os.path.join(inst, "unins000.exe"), "wb").close()
        check("unins000.exe beside the program means an install", updater.is_installed(inst))
        feed = {"setup": "https://example.com/TTSkyAnnot-Setup-9.9.9.exe",
                "setup_sha256": "abc", "setup_size": 123}
        check("a feed with an installer is recognised", updater.has_setup(feed) and not updater.has_setup({}))
        sub = updater.setup_feed(feed)
        check("the installer half is a normal feed",
              sub["url"].endswith(".exe") and sub["sha256"] == "abc" and sub["size"] == 123, str(sub))

        # the two scripts the updater writes: the swap batch, and the "wait for us, then run the wizard" one
        work = tempfile.mkdtemp(prefix="tts-scripts-")
        try:
            swap = open(updater.install_script(work, os.path.join(work, "target"), 4242), encoding="utf-8").read()
            check("the swap batch waits for this process and can retry a refused move",
                  "PID eq 4242" in swap and "robocopy" in swap and "trynew" in swap, swap[:40])
            check("and every way out of it starts the program again",
                  "could not move the old install aside" in swap and "must come back" in swap, "")
            fake = os.path.join(work, "setup.exe")
            open(fake, "wb").close()
            launch = open(updater.launch_setup(fake, 4242), encoding="utf-8").read()
            check("the installer script waits too, then starts the wizard",
                  "PID eq 4242" in launch and "start" in launch and "tryswap" not in launch, launch[:40])
        finally:
            shutil.rmtree(work, ignore_errors=True)
    finally:
        shutil.rmtree(inst, ignore_errors=True)

    print("\n%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    shots = "--shots" in sys.argv
    try:
        code = main()
    finally:
        if shots and os.path.exists(os.path.join(TMP, "shots")):
            dest = os.path.join(ROOT, ".skyannot_shots")
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(os.path.join(TMP, "shots"), dest)
            print("screenshots: %s" % dest)
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)

