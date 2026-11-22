"""Fragloop Studio - the window.

Read-only for now: shows what the recorder already saved in Videos\\Captures,
double-click a clip to play it. The recorder engine is untouched.
"""
import ctypes
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import (QByteArray, QEvent, QPointF, QRectF, QSettings,
                            QSize, Qt, QTimer)
from PySide6.QtGui import (QColor, QIcon, QImage, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

APP_DIR = (Path(sys.executable).resolve().parent
           if getattr(sys, "frozen", False) else Path(__file__).resolve().parent)
FFMPEG = APP_DIR / "ffmpeg.exe"
FFPROBE = APP_DIR / "ffprobe.exe"
OUT_DIR = Path(os.environ["USERPROFILE"]) / "Videos" / "Captures"
THUMB_DIR = Path(os.environ["LOCALAPPDATA"]) / "Fragloop" / "thumbs"

THUMB_W, THUMB_H = 148, 84
APP_VERSION = "0.1.1"

QSS = """
QMainWindow { background: transparent; }
QWidget#root {
    background: rgba(11, 13, 17, 0.90);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 12px;
}
QLabel#title { color: #eef1f6; font-family: "Segoe UI"; font-size: 21px; font-weight: 600; }
QLabel#count { color: #6f7789; font-family: "Segoe UI"; font-size: 11px; }
QFrame#divider { background: #1a1f28; border: none; }
QPushButton#ghost {
    color: #c3cad8; background: #14181f; border: 1px solid #222835;
    border-radius: 8px; padding: 7px 14px;
    font-family: "Segoe UI"; font-size: 12px;
}
QPushButton#ghost:hover { background: #1a1f29; border-color: #2b3342; }
QPushButton#winbtn, QPushButton#winbtnClose {
    color: #8a93a6; background: transparent; border: none;
    border-radius: 6px; font-family: "Segoe UI"; font-size: 13px;
}
QPushButton#winbtn:hover { background: #1a1f29; color: #eef1f6; }
QPushButton#winbtnClose:hover { background: #3a1a20; color: #ff8a95; }
QPushButton#del {
    background: transparent; border: 1px solid transparent;
    border-radius: 8px; padding: 0;
}
QPushButton#del:hover { background: #1c1216; border-color: #35202a; }
QScrollArea { border: none; background: transparent; }
QWidget#qt_scrollarea_viewport { background: transparent; }
QWidget#clipHost { background: transparent; }
QWidget#clipRow { background: #10131a; border: 1px solid #191e27; border-radius: 12px; }
QWidget#clipRow:hover { background: #151a23; border-color: #29313f; }
QLabel#clipName { color: #e6e9f0; font-family: "Segoe UI"; font-size: 13px; font-weight: 600; }
QLabel#clipMeta { color: #79829a; font-family: "Segoe UI"; font-size: 11px; }
QLabel#emptyTitle { color: #4f586b; font-family: "Segoe UI"; font-size: 15px; font-weight: 600; }
QLabel#emptyHint  { color: #3d4557; font-family: "Segoe UI"; font-size: 12px; }
QLabel#version { color: #6f7789; font-family: "Segoe UI"; font-size: 11px; }
QLabel#hint { color: #4f586b; font-family: "Segoe UI"; font-size: 11px; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #262d39; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""


def _run(args):
    return subprocess.run(args, capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW)


_durations = {}


def duration(path):
    """Probe once per file version; a refresh rebuilds every row."""
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


def thumbnail(path):
    """A frame 10% in, cached next to the app data."""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMB_DIR / f"{path.stem}_{int(path.stat().st_mtime)}_v2.png"
    if not dest.exists():
        dur = duration(path)
        ss = max(1.0, dur * 0.1) if dur else 1.0
        _run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
              "-ss", f"{ss:.2f}", "-i", str(path), "-frames:v", "1",
              "-vf", f"scale={THUMB_W * 2}:-1", str(dest)])
    return dest if dest.exists() else None


def rounded(pixmap, radius=8):
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
    return f"{n / 2**30:.2f} GB" if n >= 2**30 else f"{n / 2**20:.0f} MB"


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

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._grain is None:
            self._grain = noise_pixmap()
        p = QPainter(self)
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2),
                            11, 11)
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


def trash_icon(color="#8a93a6"):
    """Bin glyph drawn at every size Qt may ask for, so it stays sharp."""
    icon = QIcon()
    for size in (16, 20, 24, 32):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        k = size / 32.0
        pen = QPen(QColor(color), max(1.3, 2.2 * k))
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
    def __init__(self, path, on_delete):
        super().__init__()
        self.path = path
        self._on_delete = on_delete
        self.setObjectName("clipRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(THUMB_H + 20)
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 14, 10)
        lay.setSpacing(14)

        thumb = QLabel()
        thumb.setFixedSize(THUMB_W, THUMB_H)
        thumb.setStyleSheet("background: transparent;")
        png = thumbnail(path)
        if png:
            pm = QPixmap(str(png)).scaled(THUMB_W, THUMB_H,
                                          Qt.KeepAspectRatioByExpanding,
                                          Qt.SmoothTransformation)
            thumb.setPixmap(rounded(pm))
        lay.addWidget(thumb)

        text = QVBoxLayout()
        text.setSpacing(4)
        name = QLabel(path.stem)
        name.setObjectName("clipName")
        meta = QLabel()
        meta.setObjectName("clipMeta")
        when = time.strftime("%H:%M", time.localtime(path.stat().st_mtime))
        meta.setText(f"{clock(duration(path))}  ·  "
                     f"{size_text(path.stat().st_size)}  ·  {when}")
        text.addStretch(1)
        text.addWidget(name)
        text.addWidget(meta)
        text.addStretch(1)
        lay.addLayout(text, 1)

        remove = QPushButton()
        remove.setObjectName("del")
        remove.setIcon(trash_icon())
        remove.setIconSize(QSize(18, 18))
        remove.setFixedSize(34, 30)
        remove.setCursor(Qt.PointingHandCursor)
        remove.setToolTip("Delete clip (moves it to the Recycle Bin)")
        remove.setAccessibleName("Delete clip")
        remove.clicked.connect(self._delete)
        lay.addWidget(remove)

    def _delete(self):
        if recycle(self.path):
            self._on_delete()
        else:
            self.setToolTip("Could not delete - is the clip open in a player?")

    def mouseDoubleClickEvent(self, event):
        if self.path.exists():
            os.startfile(self.path)


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fragloop Studio")
        self.setWindowIcon(app_icon())
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(760, 520)
        self.settings = QSettings("Fragloop", "Studio")
        if not self.restoreGeometry(self.settings.value("geometry", QByteArray())):
            self.resize(1000, 700)
        self._seen = None
        self._window_styled = False
        QApplication.instance().installEventFilter(self)

        root = Panel()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(16)

        header = Header(self)
        head = QHBoxLayout(header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(11)
        title = QLabel("Fragloop")
        title.setObjectName("title")
        head.addWidget(Logo(30))
        head.addWidget(title)
        head.addStretch(1)
        open_btn = QPushButton("Open folder")
        open_btn.setObjectName("ghost")
        open_btn.clicked.connect(lambda: os.startfile(OUT_DIR))
        head.addWidget(open_btn)
        for text, tip, slot, name in (
                ("\u2212", "Minimize", self.showMinimized, "winbtn"),
                ("\u25a1", "Maximize", self.toggle_max, "winbtn"),
                ("\u2715", "Close", self.close, "winbtnClose")):
            btn = QPushButton(text)
            btn.setObjectName(name)
            btn.setToolTip(tip)
            btn.setFixedSize(30, 28)
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
        self.list.setSpacing(8)
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
        h = QLabel("Alt+G records  ·  Alt+P saves the last 5 minutes")
        h.setObjectName("emptyHint")
        h.setAlignment(Qt.AlignCenter)
        el.addWidget(t)
        el.addWidget(h)
        el.addStretch(1)
        outer.addWidget(self.empty, 1)

        foot = QHBoxLayout()
        self.hint = QLabel("Alt+G record  ·  Alt+P save replay")
        self.hint.setObjectName("hint")
        foot.addWidget(self.hint)
        foot.addStretch(1)
        ver = QLabel(f"v{APP_VERSION}")
        ver.setObjectName("version")
        foot.addWidget(ver)
        outer.addLayout(foot)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
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
        now = time.time()
        found = []
        for p in OUT_DIR.iterdir():
            if not (p.is_file() and p.suffix.lower() in (".mp4", ".mkv")):
                continue
            mtime = p.stat().st_mtime
            # A save is muxed straight into Captures; hide it until the last
            # write so the list does not rebuild a growing clip every 2 s.
            if now - mtime >= 2:
                found.append((mtime, p))
        found.sort(key=lambda item: item[0], reverse=True)
        return [p for _, p in found]

    def refresh(self):
        files = self.clips()
        key = tuple((p.name, p.stat().st_mtime) for p in files)
        if key == self._seen:
            return
        self._seen = key

        while self.list.count() > 1:
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for path in files:
            self.list.insertWidget(self.list.count() - 1,
                                   ClipRow(path, self.refresh))

        has = bool(files)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        self.hint.setVisible(has)
        if has:
            try:
                total = sum(p.stat().st_size for p in files)
            except OSError:
                total = 0
            self.count.setText(f"{len(files)} clip{'s' if len(files) != 1 else ''}"
                               f"  ·  {size_text(total)}")
        else:
            self.count.setText("")

    def closeEvent(self, event):
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
