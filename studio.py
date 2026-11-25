"""Fragloop Studio - the window.

Read-only for now: shows what the recorder already saved in Videos\\Captures,
double-click a clip to play it. The recorder engine is untouched.
"""
import ctypes
import math
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import (QByteArray, QEasingCurve, QEvent,
                            QFileSystemWatcher, QPointF, QRectF, QSettings,
                            QSize, Signal, Qt, QTimer, QVariantAnimation)
from PySide6.QtGui import (QColor, QIcon, QImage, QImageReader, QKeySequence,
                           QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QSlider, QStackedWidget,
                               QVBoxLayout, QWidget)

APP_DIR = (Path(sys.executable).resolve().parent
           if getattr(sys, "frozen", False) else Path(__file__).resolve().parent)
FFMPEG = APP_DIR / "ffmpeg.exe"
FFPROBE = APP_DIR / "ffprobe.exe"
OUT_DIR = Path(os.environ["USERPROFILE"]) / "Videos" / "Captures"
THUMB_DIR = Path(os.environ["LOCALAPPDATA"]) / "Fragloop" / "thumbs"
BUFFER_DIR = Path(os.environ["LOCALAPPDATA"]) / "Fragloop" / "buffer"

THUMB_W, THUMB_H = 148, 84
APP_VERSION = "0.2.0"
PROBE_TIMEOUT = 10          # seconds; a crafted file must not hang the window
MAX_IMAGE_BYTES = 128 * 2**20
MAX_IMAGE_PIXELS = 40_000_000
PART_MAX_AGE = 30 * 60      # hide a .part left behind by a crashed save

QSS = """
QMainWindow { background: transparent; }
QWidget#root {
    background: rgba(11, 13, 17, 0.95);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 18px;
}
QLabel#title { color: #eef1f6; font-family: "Segoe UI"; font-size: 21px; font-weight: 600; }
QLabel#count { color: #6f7789; font-family: "Segoe UI"; font-size: 11px; }
QFrame#divider { background: #1a1f28; border: none; }
QPushButton#winbtn, QPushButton#winbtnClose {
    color: #8a93a6; background: transparent; border: none;
    border-radius: 10px; font-family: "Segoe UI"; font-size: 18px;
}
QPushButton#winbtn:hover { background: #1a1f29; color: #eef1f6; }
QPushButton#winbtnClose:hover { background: #3a1a20; color: #ff8a95; }
QWidget#prefs {
    background: #0b0d11; border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 18px;
}
QLabel#prefsTitle { color: #eef1f6; font-family: "Segoe UI"; font-size: 15px; font-weight: 600; }
QLabel#pageTitle { color: #eef1f6; font-family: "Segoe UI"; font-size: 19px; font-weight: 600; }
QLabel#section { color: #6f7789; font-family: "Segoe UI"; font-size: 11px; font-weight: 600; }
QLabel#rowLabel { color: #c3cad8; font-family: "Segoe UI"; font-size: 12px; }
QLabel#rowValue { color: #6f7789; font-family: "Segoe UI"; font-size: 11px; }
QLabel#keycap {
    color: #c3cad8; background: #14181f; border: 1px solid #222835;
    border-radius: 7px; padding: 3px 8px;
    font-family: "Consolas"; font-size: 11px;
}
QLabel#path { color: #79829a; font-family: "Segoe UI"; font-size: 11px; }
QLabel#aboutName { color: #eef1f6; font-family: "Segoe UI"; font-size: 20px; font-weight: 600; }
QLabel#aboutText { color: #aab2c4; font-family: "Segoe UI"; font-size: 12px; }
QListWidget#nav {
    background: transparent; border: none;
    font-family: "Segoe UI"; font-size: 12px; outline: none;
}
QListWidget#nav::item { color: #aab2c4; padding: 10px 14px; border-radius: 10px; }
QListWidget#nav::item:hover { background: #14181f; color: #eef1f6; }
QListWidget#nav::item:selected { background: #1a1f29; color: #eef1f6; }
QFrame#navline { background: #222835; border: none; }
QPushButton#setOpen {
    color: #c3cad8; background: #14181f; border: 1px solid #222835;
    border-radius: 10px; padding: 6px 14px;
    font-family: "Segoe UI"; font-size: 11px;
}
QPushButton#setOpen:hover { background: #1a1f29; border-color: #2b3342; }
QSlider::groove:horizontal {
    height: 4px; background: #1a1f28; border-radius: 2px;
}
QSlider::sub-page:horizontal { background: #ff4d5e; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #e6e9f0; width: 12px; margin: -5px 0; border-radius: 6px;
}
QSlider::handle:horizontal:hover { background: #ffffff; }
QSlider::handle:horizontal:pressed { background: #ff8a95; }
QPushButton#setOpen:focus, QComboBox:focus { border-color: #ff4d5e; }
QCheckBox {
    color: #c3cad8; font-family: "Segoe UI"; font-size: 12px; spacing: 8px;
}
QCheckBox::indicator {
    width: 16px; height: 16px; border: 1px solid #2b3342;
    border-radius: 5px; background: #14181f;
}
QCheckBox::indicator:checked { background: #ff4d5e; border-color: #ff4d5e; }
QComboBox {
    color: #e6e9f0; background: #14181f; border: 1px solid #222835;
    border-radius: 10px; padding: 5px 10px;
    font-family: "Segoe UI"; font-size: 12px;
}
QComboBox:hover { border-color: #2b3342; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #14181f; color: #e6e9f0; border: 1px solid #222835;
    selection-background-color: #1a1f29; outline: none;
}
QMessageBox { background: #0e1117; }
QMessageBox QLabel { color: #e6e9f0; font-family: "Segoe UI"; font-size: 12px; }
QMessageBox QPushButton {
    color: #c3cad8; background: #14181f; border: 1px solid #222835;
    border-radius: 9px; padding: 6px 14px; min-width: 60px;
    font-family: "Segoe UI"; font-size: 12px;
}
QMessageBox QPushButton:hover { background: #1a1f29; border-color: #2b3342; }
QPushButton#del {
    background: transparent; border: 1px solid transparent;
    border-radius: 10px; padding: 0;
}
QPushButton#del:hover { background: #1c1216; border-color: #35202a; }
QScrollArea { border: none; background: transparent; }
QWidget#qt_scrollarea_viewport { background: transparent; }
QWidget#clipHost { background: transparent; }
QWidget#shotRow, QWidget#bufferRow, QWidget#recordRow {
    background: transparent;
    border: none;
}
QWidget#shotWash {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(255, 216, 140, 0.11),
        stop:0.55 rgba(255, 206, 112, 0.060),
        stop:1 rgba(255, 232, 176, 0.090));
    border: 1px solid rgba(255, 220, 150, 0.20);
    border-radius: 16px;
}
QWidget#bufferWash {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(200, 148, 255, 0.179),
        stop:0.55 rgba(172, 112, 255, 0.101),
        stop:1 rgba(224, 180, 255, 0.146));
    border: 1px solid rgba(206, 158, 255, 0.32);
    border-radius: 16px;
}
QWidget#recordWash {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(255, 64, 64, 0.179),
        stop:0.55 rgba(232, 32, 32, 0.101),
        stop:1 rgba(255, 112, 112, 0.146));
    border: 1px solid rgba(255, 84, 84, 0.32);
    border-radius: 16px;
}
QLabel#shotBadge {
    background: rgba(255, 214, 140, 0.13);
    border: 1px solid rgba(255, 214, 140, 0.22);
    border-radius: 6px;
}
QLabel#bufferBadge {
    background: rgba(200, 148, 255, 0.179);
    border: 1px solid rgba(200, 148, 255, 0.30);
    border-radius: 6px;
}
QLabel#recordBadge {
    background: rgba(255, 64, 64, 0.179);
    border: 1px solid rgba(255, 64, 64, 0.30);
    border-radius: 6px;
}
QLabel#shotTag, QLabel#bufferTag, QLabel#recordTag {
    font-family: "Segoe UI"; font-size: 11px; font-weight: 600;
    padding: 0 9px; border-radius: 6px;
}
QLabel#shotTag {
    color: #ffd88c; background: rgba(255, 214, 140, 0.13);
    border: 1px solid rgba(255, 214, 140, 0.22);
}
QLabel#bufferTag {
    color: #d2a4ff; background: rgba(200, 148, 255, 0.179);
    border: 1px solid rgba(200, 148, 255, 0.30);
}
QLabel#recordTag {
    color: #ff6b6b; background: rgba(255, 64, 64, 0.179);
    border: 1px solid rgba(255, 64, 64, 0.30);
}
QLabel#clipName { color: #e6e9f0; font-family: "Segoe UI"; font-size: 13px; font-weight: 600; }
QLabel#clipMeta { color: #79829a; font-family: "Segoe UI"; font-size: 11px; }
QLabel#emptyTitle { color: #4f586b; font-family: "Segoe UI"; font-size: 15px; font-weight: 600; }
QLabel#version { color: #6f7789; font-family: "Segoe UI"; font-size: 11px; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #262d39; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""


def _run(args):
    return subprocess.run(args, capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW,
                          timeout=PROBE_TIMEOUT)


_durations = {}


def duration(path):
    """Probe once per file version; a refresh rebuilds every row."""
    if path.suffix.lower() == ".png":
        return 0.0
    try:
        st = path.stat()
        key = (str(path), st.st_mtime, st.st_size)
        if key in _durations:
            return _durations[key]
    except OSError:
        return 0.0
    try:
        out = _run([str(FFPROBE), "-v", "error", "-show_entries",
                    "format=duration", "-of", "csv=p=0", str(path)]).stdout
        value = float(out.strip())
    except Exception:
        value = 0.0
    _durations[key] = value
    return value


def load_image(path):
    """Read a preview with size caps; a crafted image must not exhaust memory."""
    try:
        if path.stat().st_size > MAX_IMAGE_BYTES:
            return None
    except OSError:
        return None
    reader = QImageReader(str(path))
    size = reader.size()
    if size.isValid() and size.width() * size.height() > MAX_IMAGE_PIXELS:
        return None
    img = reader.read()
    return None if img.isNull() else img


def thumbnail(path):
    """A frame 10% in, cached next to the app data."""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".png":
        dest = THUMB_DIR / f"{path.stem}_{int(path.stat().st_mtime)}_shot.png"
        if not dest.exists():
            img = load_image(path)
            if img is None:
                return None
            img = img.scaled(THUMB_W * 2, THUMB_H * 2,
                             Qt.KeepAspectRatioByExpanding,
                             Qt.SmoothTransformation)
            if not img.save(str(dest)):
                return None
        return dest if dest.exists() else None
    dest = THUMB_DIR / f"{path.stem}_{int(path.stat().st_mtime)}_v2.png"
    if not dest.exists():
        dur = duration(path)
        ss = max(1.0, dur * 0.1) if dur else 1.0
        try:
            _run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                  "-ss", f"{ss:.2f}", "-i", str(path), "-frames:v", "1",
                  "-vf", f"scale={THUMB_W * 2}:-1", str(dest)])
        except (OSError, subprocess.TimeoutExpired):
            return None
    return dest if dest.exists() else None


def rounded(pixmap, radius=12):
    out = QPixmap(pixmap.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pixmap.width(), pixmap.height(), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pixmap)
    p.end()
    return out


def clock(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def size_text(n):
    if n >= 2**30:
        return f"{n / 2**30:.2f} GB"
    if n >= 2**20:
        return f"{n / 2**20:.0f} MB"
    return f"{max(1, round(n / 2**10))} KB"


def replay_length_text(seconds):
    minutes, sec = divmod(seconds, 60)
    if sec:
        return f"{minutes} min {sec} s"
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def replay_label(seconds):
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}:{sec:02d}" if sec else f"{minutes} min"


def recycle(path):
    """Move to the Recycle Bin (FOF_ALLOWUNDO) so a misclick is undoable."""
    class Op(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p),
                    ("wFunc", ctypes.c_uint),
                    ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p),
                    ("fFlags", ctypes.c_ushort),
                    ("aborted", ctypes.c_int),
                    ("nameMappings", ctypes.c_void_p),
                    ("progress", ctypes.c_wchar_p)]

    keep = ctypes.create_unicode_buffer(str(path), len(str(path)) + 2)
    op = Op()
    op.wFunc = 3                                    # FO_DELETE
    op.pFrom = ctypes.cast(keep, ctypes.c_wchar_p)  # double-NUL terminated
    op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400   # UNDO|NOCONFIRM|SILENT|NOERRORUI
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0


def logo_pixmap(size=32):
    """The app mark: rounded tile with the red record dot."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    k = size / 64.0
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#151a23"))
    p.drawRoundedRect(2 * k, 2 * k, 60 * k, 60 * k, 14 * k, 14 * k)
    p.setBrush(QColor("#ff4d5e"))
    p.drawEllipse(18 * k, 18 * k, 28 * k, 28 * k)
    p.end()
    return pm


def app_icon():
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        icon.addPixmap(logo_pixmap(size))
    return icon


def gear_pixmap(size=18):
    """Eight-tooth gear mark for the settings button."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(size / 2, size / 2)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#aab2c4"))
    for i in range(8):
        p.save()
        p.rotate(i * 45)
        p.drawRoundedRect(QRectF(-size * 0.09, -size * 0.46,
                                 size * 0.18, size * 0.22), 1, 1)
        p.restore()
    p.drawEllipse(QPointF(0, 0), size * 0.30, size * 0.30)
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.drawEllipse(QPointF(0, 0), size * 0.13, size * 0.13)
    p.end()
    return pm


def noise_pixmap(size=192):
    """Fine white grain tile, built once and tiled over the panel."""
    rng = random.Random(7)
    data = bytearray()
    for _ in range(size * size):
        data += bytes((255, 255, 255, rng.getrandbits(3)))
    img = QImage(bytes(data), size, size, QImage.Format_ARGB32)
    return QPixmap.fromImage(img.copy())


class Panel(QWidget):
    """Root surface: the translucent stylesheet background plus faint grain."""

    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._grain = None
        self.grain = True

    def set_alpha(self, percent):
        self.setStyleSheet(
            f"QWidget#root {{ background: rgba(11, 13, 17, {percent / 100:.2f});"
            " border: 1px solid rgba(255, 255, 255, 0.07);"
            " border-radius: 18px; }")

    def set_grain(self, on):
        self.grain = on
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.grain:
            return
        if self._grain is None:
            self._grain = noise_pixmap()
        p = QPainter(self)
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2),
                            17, 17)
        p.setClipPath(path)
        p.drawTiledPixmap(0, 0, self.width(), self.height(), self._grain)
        p.end()


class Header(QWidget):
    """Empty header space drags the frameless window; buttons still click."""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._grab = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._grab = event.globalPosition().toPoint() - self._window.pos()

    def mouseMoveEvent(self, event):
        if self._grab is not None and event.buttons() & Qt.LeftButton:
            if not self._window.isMaximized():
                self._window.move(event.globalPosition().toPoint() - self._grab)

    def mouseReleaseEvent(self, event):
        self._grab = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            toggle = getattr(self._window, "toggle_max", None)
            if toggle:
                toggle()


class Combo(QComboBox):
    """Dropdown with a chevron; the styled sub-control draws none."""

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#8a93a6"), 1.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        x = self.width() - 16
        y = self.height() / 2 - 2
        p.drawLine(QPointF(x - 4, y), QPointF(x, y + 4))
        p.drawLine(QPointF(x, y + 4), QPointF(x + 4, y))
        p.end()


class Check(QCheckBox):
    """Checkbox with a drawn tick; the styled indicator is just a square."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#0b0d11"), 1.8)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        top = (self.height() - 16) / 2
        p.drawLine(QPointF(4, top + 7), QPointF(6.5, top + 10))
        p.drawLine(QPointF(6.5, top + 10), QPointF(11.5, top + 4))
        p.end()


class CloseDot(QPushButton):
    """Close control drawn as a record dot, like the app mark."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("winbtnClose")
        self.setFixedSize(36, 32)
        self.setToolTip("Close")
        self.setAccessibleName("Close")
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ff8a95" if self.underMouse() else "#ff4d5e"))
        d = 12
        p.drawEllipse(QRectF((self.width() - d) / 2,
                             (self.height() - d) / 2, d, d))
        p.end()


class Settings(QDialog):
    """Preferences: a section rail on the left, settings on the right."""

    def __init__(self, studio):
        super().__init__(studio)
        self.studio = studio
        self.setWindowTitle("Fragloop Preferences")
        self.setWindowIcon(app_icon())
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.NonModal)
        self.setFixedSize(760, 520)

        panel = QWidget()
        panel.setObjectName("prefs")
        panel.setAttribute(Qt.WA_StyledBackground, True)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.addWidget(panel)

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = Header(self)
        header.setFixedHeight(52)
        head = QHBoxLayout(header)
        head.setContentsMargins(22, 0, 14, 0)
        head.setSpacing(9)
        head.addWidget(Logo(20))
        title = QLabel("Preferences")
        title.setObjectName("prefsTitle")
        head.addWidget(title)
        head.addStretch(1)
        close = CloseDot(self)
        close.clicked.connect(self.close)
        head.addWidget(close)
        outer.addWidget(header)

        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        outer.addWidget(line)

        body = QHBoxLayout()
        body.setContentsMargins(16, 14, 20, 0)
        body.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(168)
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for name in ("General", "Appearance", "Clips", "Storage", "About"):
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(0, 38))
            self.nav.addItem(item)
        body.addWidget(self.nav)

        navline = QFrame()
        navline.setObjectName("navline")
        navline.setFixedWidth(1)
        body.addWidget(navline)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 22, 12)
        foot.addStretch(1)
        hint = QLabel("Changes save automatically")
        hint.setObjectName("rowValue")
        foot.addWidget(hint)
        outer.addLayout(foot)

        for page in (self.general_page(), self.appearance_page(),
                     self.clips_page(), self.storage_page(),
                     self.about_page()):
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

    def page(self, title):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(14)
        head = QLabel(title)
        head.setObjectName("pageTitle")
        lay.addWidget(head)
        return page, lay

    @staticmethod
    def section(lay, text):
        label = QLabel(text)
        label.setObjectName("section")
        lay.addWidget(label)

    def check(self, lay, text, key, default, on_change=None):
        box = Check(text)
        box.setChecked(self.studio.settings.value(key, default, bool))

        def changed(on):
            self.studio.settings.setValue(key, on)
            if on_change:
                on_change(on)

        box.toggled.connect(changed)
        lay.addWidget(box)
        return box

    @staticmethod
    def info(lay, text, value, keycap=False):
        row = QHBoxLayout()
        label = QLabel(text)
        label.setObjectName("rowLabel")
        row.addWidget(label)
        row.addStretch(1)
        val = QLabel(value)
        val.setObjectName("keycap" if keycap else "rowValue")
        row.addWidget(val)
        lay.addLayout(row)

    def general_page(self):
        page, lay = self.page("General")
        self.section(lay, "Window")
        self.check(lay, "Remember window size and position",
                   "remember_geometry", True)
        self.check(lay, "Keep Fragloop above other windows",
                   "always_on_top", False, self.studio.set_always_on_top)

        self.section(lay, "Recording")
        self.info(lay, "Save replay", "Alt + P", True)
        self.info(lay, "Take screenshot", "Alt + F", True)
        self.info(lay, "Start or stop recording", "Alt + G", True)

        row = QHBoxLayout()
        label = QLabel("Replay buffer")
        label.setObjectName("rowLabel")
        row.addWidget(label)
        row.addStretch(1)
        self.replay_value = QLabel()
        self.replay_value.setObjectName("rowValue")
        row.addWidget(self.replay_value)
        lay.addLayout(row)

        self.replay = QSlider(Qt.Horizontal)
        self.replay.setRange(60, 900)
        self.replay.setSingleStep(1)
        self.replay.setPageStep(30)
        self.replay.setAccessibleName("Replay buffer length")
        value = self.studio.replay_seconds
        self.replay.setValue(value)
        self.replay_value.setText(replay_label(value))
        self.replay.valueChanged.connect(self.on_replay_seconds)
        lay.addWidget(self.replay)

        ends = QHBoxLayout()
        low = QLabel("1 min")
        low.setObjectName("rowValue")
        high = QLabel("15 min")
        high.setObjectName("rowValue")
        ends.addWidget(low)
        ends.addStretch(1)
        ends.addWidget(high)
        lay.addLayout(ends)

        self.replay_hint = QLabel()
        self.replay_hint.setObjectName("rowValue")
        self.replay_hint.setWordWrap(True)
        self.replay_hint.setText(self.replay_text(value))
        lay.addWidget(self.replay_hint)
        lay.addStretch(1)
        return page

    @staticmethod
    def replay_text(seconds):
        return (f"Alt+P saves the last {replay_length_text(seconds)}. "
                "Longer buffers use more disk; 15 minutes is about 9 GB.")

    def appearance_page(self):
        page, lay = self.page("Appearance")
        self.section(lay, "Glass")
        row = QHBoxLayout()
        label = QLabel("Panel opacity")
        label.setObjectName("rowLabel")
        row.addWidget(label)
        row.addStretch(1)
        self.opacity_value = QLabel()
        self.opacity_value.setObjectName("rowValue")
        row.addWidget(self.opacity_value)
        lay.addLayout(row)

        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(75, 100)
        value = int(self.studio.settings.value("opacity", 95))
        self.opacity.setValue(value)
        self.opacity_value.setText(f"{value}%")
        self.opacity.valueChanged.connect(self.on_opacity)
        lay.addWidget(self.opacity)

        self.check(lay, "Grain texture", "grain", True,
                   self.studio.root.set_grain)
        hint = QLabel("Lower opacity lets more of the desktop show through.")
        hint.setObjectName("rowValue")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)
        return page

    def clips_page(self):
        page, lay = self.page("Clips")
        self.section(lay, "Library")
        row = QHBoxLayout()
        label = QLabel("Sort by")
        label.setObjectName("rowLabel")
        row.addWidget(label)
        row.addStretch(1)
        self.sort = Combo()
        self.sort.addItems(["Newest first", "Oldest first"])
        self.sort.setCurrentIndex(
            0 if self.studio.settings.value("sort_newest", True, bool) else 1)
        self.sort.currentIndexChanged.connect(self.on_sort)
        row.addWidget(self.sort)
        lay.addLayout(row)

        self.check(lay, "Ask before deleting a clip", "confirm_delete", True)
        hint = QLabel("Deleted clips go to the Recycle Bin, so a misclick "
                      "can be undone.")
        hint.setObjectName("rowValue")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)
        return page

    def storage_page(self):
        page, lay = self.page("Storage")
        self.section(lay, "Folders")
        self.folder_row(lay, "Saved clips", OUT_DIR)
        self.folder_row(lay, "Replay buffer", BUFFER_DIR)
        self.storage_hint = QLabel(
            f"The recorder keeps the last "
            f"{replay_length_text(self.studio.replay_seconds)} in the buffer. "
            "Saved clips stay in Captures until you delete them.")
        self.storage_hint.setObjectName("rowValue")
        self.storage_hint.setWordWrap(True)
        lay.addWidget(self.storage_hint)

        self.section(lay, "Space")
        try:
            probe = OUT_DIR if OUT_DIR.exists() else OUT_DIR.parent
            free = shutil.disk_usage(probe).free
        except OSError:
            free = 0
        self.info(lay, "Free on this drive",
                  f"{size_text(free)} available" if free else "Unavailable")
        lay.addStretch(1)
        return page

    def folder_row(self, lay, title, path):
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        label = QLabel(title)
        label.setObjectName("rowLabel")
        where = QLabel(str(path))
        where.setObjectName("path")
        where.setTextFormat(Qt.PlainText)
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(label)
        col.addWidget(where)
        row.addLayout(col, 1)
        btn = QPushButton("Open")
        btn.setObjectName("setOpen")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self.open_folder(path))
        row.addWidget(btn)
        lay.addLayout(row)

    @staticmethod
    def open_folder(path):
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def about_page(self):
        page, lay = self.page("About")
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(Logo(54))
        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel("Fragloop")
        name.setObjectName("aboutName")
        ver = QLabel(f"Version {APP_VERSION}")
        ver.setObjectName("rowValue")
        col.addWidget(name)
        col.addWidget(ver)
        col.addStretch(1)
        row.addLayout(col)
        row.addStretch(1)
        lay.addLayout(row)

        text = QLabel("A local replay buffer and clip library for Windows. "
                      "Clips stay on this PC.")
        text.setObjectName("aboutText")
        text.setWordWrap(True)
        lay.addWidget(text)
        link = QLabel('<a href="https://github.com/shaankatoch304/Fragloop" '
                      'style="color:#ff8a95; text-decoration:none;">'
                      'GitHub repository</a>')
        link.setOpenExternalLinks(True)
        lay.addWidget(link)
        note = QLabel("Bundled FFmpeg is licensed under GPLv3.")
        note.setObjectName("rowValue")
        lay.addWidget(note)
        lay.addStretch(1)
        return page

    def on_opacity(self, value):
        self.opacity_value.setText(f"{value}%")
        self.studio.root.set_alpha(value)
        self.studio.settings.setValue("opacity", value)

    def on_replay_seconds(self, value):
        self.replay_value.setText(replay_label(value))
        self.replay_hint.setText(self.replay_text(value))
        self.studio.settings.setValue("replay_seconds", value)
        self.studio.settings.sync()      # the engine reads this live
        self.studio.set_replay_seconds(value)
        self.storage_hint.setText(
            f"The recorder keeps the last {replay_length_text(value)} "
            "in the buffer. Saved clips stay in Captures until you delete them.")

    def on_sort(self, index):
        self.studio.settings.setValue("sort_newest", index == 0)
        self.studio.refresh()


def trash_icon(color="#aeb8ca"):
    """Bin glyph at 2x, so it stays sharp on scaled displays."""
    icon = QIcon()
    for size in (16, 20, 24, 32):
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        k = size / 32.0
        pen = QPen(QColor(color), max(1.5, 2.6 * k))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        p.drawLine(QPointF(5 * k, 9 * k), QPointF(27 * k, 9 * k))   # lid
        handle = QPainterPath()                                     # handle
        handle.moveTo(12 * k, 9 * k)
        handle.lineTo(12.5 * k, 5.5 * k)
        handle.lineTo(19.5 * k, 5.5 * k)
        handle.lineTo(20 * k, 9 * k)
        p.drawPath(handle)
        body = QPainterPath()                                       # body
        body.moveTo(8 * k, 10 * k)
        body.lineTo(9.2 * k, 26 * k)
        body.quadTo(9.4 * k, 27 * k, 10.6 * k, 27 * k)
        body.lineTo(21.4 * k, 27 * k)
        body.quadTo(22.6 * k, 27 * k, 22.8 * k, 26 * k)
        body.lineTo(24 * k, 10 * k)
        p.drawPath(body)
        for x1, x2 in ((13, 13.6), (16, 16), (19, 18.4)):           # ribs
            p.drawLine(QPointF(x1 * k, 13 * k), QPointF(x2 * k, 23 * k))
        p.end()
        icon.addPixmap(pm)
    return icon


def camera_pixmap(size=18, color="#f0cd8b"):
    """Clean camera outline at 2x, so it stays sharp on scaled displays."""
    dpr = 2.0
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    k = size / 24.0
    pen = QPen(QColor(color), max(1.2, 1.6 * k))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(3.2 * k, 8 * k, 17.6 * k, 10.8 * k), 2.8 * k, 2.8 * k)
    p.drawLine(QPointF(9 * k, 8 * k), QPointF(10 * k, 5.2 * k))
    p.drawLine(QPointF(10 * k, 5.2 * k), QPointF(14 * k, 5.2 * k))
    p.drawLine(QPointF(14 * k, 5.2 * k), QPointF(15 * k, 8 * k))
    p.drawEllipse(QPointF(12 * k, 13.3 * k), 3 * k, 3 * k)
    p.end()
    return pm


def replay_pixmap(size=18, color="#d0a2ff"):
    """Counter-clockwise replay arrow at 2x, matching the camera badge."""
    dpr = 2.0
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    k = size / 24.0
    pen = QPen(QColor(color), max(1.2, 1.6 * k))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(5 * k, 5 * k, 14 * k, 14 * k), 100 * 16, 330 * 16)
    angle = math.radians(70)
    end = QPointF(12 * k + 7 * k * math.cos(angle),
                  12 * k - 7 * k * math.sin(angle))
    tangent = QPointF(-math.sin(angle), -math.cos(angle))
    normal = QPointF(-tangent.y(), tangent.x())
    head = QPainterPath()
    head.moveTo(end + tangent * (4.2 * k))
    head.lineTo(end + normal * (2.7 * k))
    head.lineTo(end - normal * (2.7 * k))
    head.closeSubpath()
    p.setBrush(QColor(color))
    p.drawPath(head)
    p.end()
    return pm


def record_pixmap(size=18, color="#ff5c5c"):
    """Filled record dot at 2x, matching the other badges."""
    dpr = 2.0
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    k = size / 24.0
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QPointF(12 * k, 12 * k), 5 * k, 5 * k)
    p.end()
    return pm


class Logo(QWidget):
    """Painted at the widget's real pixel size so it never clips or blurs."""

    def __init__(self, size=30):
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        scale = self.devicePixelRatioF()
        p.drawPixmap(self.rect(),
                     logo_pixmap(round(self.width() * scale)))


class ClipRow(QWidget):
    open_failed = Signal(str)

    def __init__(self, path, on_delete, confirm=None):
        super().__init__()
        self.path = path
        self._on_delete = on_delete
        self._confirm = confirm
        self.open_failed.connect(self._open_failed)
        self.pending = ".part." in path.name
        self.shot = path.suffix.lower() == ".png"
        stem = path.stem.removesuffix(".part")
        self.buffer = not self.shot and stem.endswith("_replay")
        self.setObjectName("shotRow" if self.shot else
                            "bufferRow" if self.buffer else "recordRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(THUMB_H + 20)
        self.setCursor(Qt.ArrowCursor if self.pending
                       else Qt.PointingHandCursor)

        self._opened_at = 0.0
        self._hover = 0.0
        self._press = 0.0
        self._wash = QWidget(self)
        self._wash.setObjectName("shotWash" if self.shot else
                                 "bufferWash" if self.buffer else "recordWash")
        self._wash.setAttribute(Qt.WA_StyledBackground, True)
        self._wash.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._wash_fx = QGraphicsOpacityEffect(self._wash)
        self._wash_fx.setOpacity(0.68)
        self._wash.setGraphicsEffect(self._wash_fx)
        self._wash.setGeometry(0, 0, self.width(), self.height())

        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.valueChanged.connect(self._set_hover)
        self._press_anim = QVariantAnimation(self)
        self._press_anim.setDuration(220)
        self._press_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._press_anim.setKeyValueAt(0.0, 0.0)
        self._press_anim.setKeyValueAt(0.3, 1.0)
        self._press_anim.setKeyValueAt(1.0, 0.0)
        self._press_anim.valueChanged.connect(self._set_press)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 14, 10)
        lay.setSpacing(14)
        self._lay = lay

        thumb = QLabel()
        thumb.setFixedSize(THUMB_W, THUMB_H)
        thumb.setStyleSheet("background: transparent;")
        thumb.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        png = None if self.pending else thumbnail(path)
        if png:
            pm = QPixmap(str(png)).scaled(THUMB_W, THUMB_H,
                                          Qt.KeepAspectRatioByExpanding,
                                          Qt.SmoothTransformation)
            thumb.setPixmap(rounded(pm))
        lay.addWidget(thumb)

        text = QVBoxLayout()
        text.setSpacing(4)
        name = QLabel(stem)
        name.setObjectName("clipName")
        # A filename is untrusted text; never let it render as rich text.
        name.setTextFormat(Qt.PlainText)
        name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        meta = QLabel()
        meta.setObjectName("clipMeta")
        meta.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        when = time.strftime("%H:%M", time.localtime(path.stat().st_mtime))
        size = size_text(path.stat().st_size)
        label = ("Screenshot" if self.shot else
                 "Replay" if self.buffer else "Recording")
        if self.pending:
            meta.setText(f"{label}  ·  Saving…")
        elif self.shot:
            meta.setText(f"Screenshot  ·  {size}  ·  {when}")
        elif self.buffer:
            meta.setText(f"Replay  ·  {clock(duration(path))}  ·  {size}  ·  {when}")
        else:
            meta.setText(f"Recording  ·  {clock(duration(path))}  ·  {size}  ·  {when}")
        text.addStretch(1)
        text.addWidget(name)
        text.addWidget(meta)
        text.addStretch(1)
        lay.addLayout(text, 1)

        badge = QLabel()
        badge.setObjectName("shotBadge" if self.shot else
                             "bufferBadge" if self.buffer else "recordBadge")
        badge.setFixedSize(24, 24)
        badge.setAlignment(Qt.AlignCenter)
        badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if self.shot:
            badge.setPixmap(camera_pixmap(13))
        elif self.buffer:
            badge.setPixmap(replay_pixmap(13))
        else:
            badge.setPixmap(record_pixmap(13))
        tag = QLabel(label)
        tag.setObjectName("shotTag" if self.shot else
                          "bufferTag" if self.buffer else "recordTag")
        tag.setFixedHeight(24)
        tag.setAlignment(Qt.AlignCenter)
        tag.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if self.pending:
            self.setToolTip(f"{label}  ·  saving, please wait")
            self.setAccessibleName(f"{label}: {stem}")
            self.setAccessibleDescription(
                "Saving the clip; it will open when ready")
        else:
            self.setToolTip(f"{label}  ·  double-click to open")
            self.setAccessibleName(f"{label}: {path.name}")
            self.setAccessibleDescription(
                f"{size}  ·  saved {when}  ·  double-click to open")
        lay.addWidget(tag, 0, Qt.AlignTop)
        lay.addWidget(badge, 0, Qt.AlignTop)

        remove = QPushButton()
        remove.setObjectName("del")
        remove.setIcon(trash_icon())
        remove.setIconSize(QSize(18, 18))
        remove.setFixedSize(34, 30)
        remove.setCursor(Qt.PointingHandCursor)
        remove.setToolTip("Delete screenshot (moves it to the Recycle Bin)"
                          if self.shot else
                          "Delete clip (moves it to the Recycle Bin)")
        remove.setAccessibleName("Delete screenshot" if self.shot
                                 else "Delete clip")
        remove.clicked.connect(self._delete)
        if self.pending:
            remove.setEnabled(False)
            remove.setToolTip("Available when the save finishes")
        lay.addWidget(remove)
        self._wash.lower()

    def resizeEvent(self, event):
        self._wash.setGeometry(self.rect())
        super().resizeEvent(event)

    def enterEvent(self, event):
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def _animate_hover(self, target):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(target)
        self._hover_anim.setDuration(160 if target else 220)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.start()

    def _set_hover(self, value):
        self._hover = float(value)
        self._apply_visuals()

    def _set_press(self, value):
        self._press = float(value)
        self._apply_visuals()

    def _apply_visuals(self):
        # QSS paints the hover tint; rest is the same tint at 68% opacity, so
        # one animation crossfades the whole glass effect.
        opacity = 0.68 + 0.32 * self._hover - 0.10 * self._press
        self._wash_fx.setOpacity(max(0.0, min(1.0, opacity)))
        lift = round(2 * self._hover - 3 * self._press)
        self._lay.setContentsMargins(10, 10 - lift, 14, 10 + lift)

    def _delete(self):
        if self.pending:
            return
        if self._confirm and not self._confirm(self.path):
            return
        if recycle(self.path):
            self._on_delete()
        else:
            self.setToolTip("Could not delete - is the clip open in a player?")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._open()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _open(self):
        if self.pending:
            return
        # ShellExecute can stall for a moment; keep it off the UI thread and
        # give the row a press animation immediately.
        now = time.monotonic()
        if now - self._opened_at < 1.0:
            return
        self._opened_at = now
        self._press_anim.stop()
        self._press_anim.start()
        threading.Thread(target=self._launch, daemon=True).start()

    def _launch(self):
        try:
            os.startfile(self.path)
        except Exception as e:
            self.open_failed.emit(str(e))

    def _open_failed(self, message):
        box = QMessageBox(QMessageBox.Warning, "Could not open",
                          f"{self.path.name}\n\n{message}",
                          QMessageBox.Ok)
        box.setTextFormat(Qt.PlainText)
        box.exec()


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fragloop Studio")
        self.setWindowIcon(app_icon())
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(760, 520)
        self.settings = QSettings("Fragloop", "Studio")
        try:
            self.replay_seconds = min(900, max(60, int(
                self.settings.value("replay_seconds", 300))))
        except (TypeError, ValueError):
            self.replay_seconds = 300
        if (not self.settings.value("remember_geometry", True, bool)
                or not self.restoreGeometry(
                    self.settings.value("geometry", QByteArray()))):
            self.resize(1000, 700)
        self._seen = None
        self._window_styled = False
        self.settings_dialog = None
        self.settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        self.settings_shortcut.activated.connect(self.open_settings)
        QApplication.instance().installEventFilter(self)

        root = Panel()
        self.root = root
        self.setCentralWidget(root)
        root.set_alpha(self.settings.value("opacity", 95, int))
        root.set_grain(self.settings.value("grain", True, bool))
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(18)

        header = Header(self)
        head = QHBoxLayout(header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(11)
        title = QLabel("Fragloop")
        title.setObjectName("title")
        head.addWidget(Logo(30))
        head.addWidget(title)
        head.addStretch(1)
        gear = QPushButton()
        gear.setObjectName("winbtn")
        gear.setIcon(QIcon(gear_pixmap(20)))
        gear.setIconSize(QSize(20, 20))
        gear.setToolTip("Settings (Ctrl+,)")
        gear.setAccessibleName("Settings")
        gear.setFixedSize(36, 32)
        gear.setCursor(Qt.PointingHandCursor)
        gear.clicked.connect(self.open_settings)
        head.addWidget(gear)
        for text, tip, slot, name in (
                ("\u2212", "Minimize", self.showMinimized, "winbtn"),
                ("\u25a1", "Maximize", self.toggle_max, "winbtn"),
                ("", "Close", self.close, "winbtnClose")):
            btn = CloseDot(self) if name == "winbtnClose" else QPushButton(text)
            btn.setObjectName(name)
            btn.setToolTip(tip)
            btn.setFixedSize(36, 32)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            head.addWidget(btn)
        outer.addWidget(header)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        outer.addWidget(divider)

        self.count = QLabel()
        self.count.setObjectName("count")
        outer.addWidget(self.count)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self.host.setObjectName("clipHost")
        self.list = QVBoxLayout(self.host)
        self.list.setContentsMargins(0, 0, 6, 0)
        self.list.setSpacing(10)
        self.list.addStretch(1)
        self.scroll.setWidget(self.host)
        outer.addWidget(self.scroll, 1)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setSpacing(6)
        el.addStretch(1)
        el.addWidget(Logo(54), 0, Qt.AlignHCenter)
        el.addSpacing(4)
        t = QLabel("No clips yet")
        t.setObjectName("emptyTitle")
        t.setAlignment(Qt.AlignCenter)
        el.addWidget(t)
        el.addStretch(1)
        outer.addWidget(self.empty, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        ver = QLabel(f"v{APP_VERSION}")
        ver.setObjectName("version")
        foot.addWidget(ver)
        outer.addLayout(foot)

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.watcher = QFileSystemWatcher([str(OUT_DIR)], self)
        self.watcher.directoryChanged.connect(self.refresh)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self.settings.value("always_on_top", False, bool):
            self.set_always_on_top(True)
        if self._window_styled:
            return
        self._window_styled = True
        hwnd = int(self.winId())
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), 33,
                ctypes.byref(ctypes.c_int(2)), 4)   # DWMWCP_ROUND
        except (AttributeError, OSError):
            pass

    def open_settings(self):
        if self.settings_dialog is None:
            self.settings_dialog = Settings(self)
        dlg = self.settings_dialog
        center = self.frameGeometry().center()
        dlg.move(center.x() - dlg.width() // 2,
                 center.y() - dlg.height() // 2)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def set_replay_seconds(self, seconds):
        self.replay_seconds = seconds

    def set_always_on_top(self, on):
        try:
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(int(self.winId())),
                ctypes.c_void_p(-1 if on else -2), 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010)
        except (AttributeError, OSError):
            pass

    def confirm_delete(self, path):
        if not self.settings.value("confirm_delete", True, bool):
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Delete clip")
        box.setText(f"Move {path.name} to the Recycle Bin?")
        box.setTextFormat(Qt.PlainText)
        box.setIcon(QMessageBox.Question)
        yes = box.addButton("Delete", QMessageBox.YesRole)
        cancel = box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is yes

    def toggle_max(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.LeftButton
                and isinstance(obj, QWidget) and obj.window() is self
                and not self.isMaximized()):
            pos = event.globalPosition().toPoint() - self.pos()
            edges = Qt.Edges()
            if pos.x() <= 6:
                edges |= Qt.LeftEdge
            if pos.x() >= self.width() - 6:
                edges |= Qt.RightEdge
            if pos.y() <= 6:
                edges |= Qt.TopEdge
            if pos.y() >= self.height() - 6:
                edges |= Qt.BottomEdge
            if edges:
                self.windowHandle().startSystemResize(edges)
                return True
        return super().eventFilter(obj, event)

    def clips(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        found = []
        for p in OUT_DIR.iterdir():
            if not p.is_file():
                continue
            suffix = p.suffix.lower()
            if suffix not in (".mp4", ".mkv", ".png"):
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if ".part." in p.name:
                # A save in progress is shown at once as "Saving…". Ignore
                # screenshot parts (gone in a blink) and crashed leftovers.
                if suffix == ".png" or time.time() - mtime > PART_MAX_AGE:
                    continue
            found.append((mtime, p))
        found.sort(key=lambda item: item[0],
                   reverse=self.settings.value("sort_newest", True, bool))
        return [p for _, p in found]

    def refresh(self):
        files = self.clips()
        key = (self.settings.value("sort_newest", True, bool),
               tuple((p.name, 0.0 if ".part." in p.name
                      else p.stat().st_mtime) for p in files))
        if key == self._seen:
            return
        self._seen = key

        while self.list.count() > 1:
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for path in files:
            self.list.insertWidget(self.list.count() - 1,
                                   ClipRow(path, self.refresh, self.confirm_delete))

        has = bool(files)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        if has:
            try:
                total = sum(p.stat().st_size for p in files)
            except OSError:
                total = 0
            self.count.setText(f"{len(files)} item{'s' if len(files) != 1 else ''}"
                               f"  ·  {size_text(total)}")
        else:
            self.count.setText("")

    def closeEvent(self, event):
        if self.settings.value("remember_geometry", True, bool):
            self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)


def main():
    # One window only: a second launch focuses the open one instead of
    # adding a duplicate taskbar entry.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "FragloopStudio")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = ctypes.windll.user32.FindWindowW(None, "Fragloop Studio")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        return

    app = QApplication([])
    app.setApplicationName("Fragloop")
    app.setStyleSheet(QSS)
    win = Studio()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
