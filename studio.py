"""Fragloop Studio - the window.

Read-only for now: shows what the recorder already saved in Videos\\Captures,
double-click a clip to play it. The recorder engine is untouched.
"""
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QIcon, QPainter, QPainterPath, QPen,
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

QSS = """
QWidget#root { background: #0b0d11; }
QLabel#title { color: #eef1f6; font-family: "Segoe UI"; font-size: 21px; font-weight: 600; }
QLabel#count { color: #6f7789; font-family: "Segoe UI"; font-size: 11px; }
QFrame#divider { background: #1a1f28; border: none; }
QPushButton#ghost {
    color: #c3cad8; background: #14181f; border: 1px solid #222835;
    border-radius: 8px; padding: 7px 14px;
    font-family: "Segoe UI"; font-size: 12px;
}
QPushButton#ghost:hover { background: #1a1f29; border-color: #2b3342; }
QPushButton#del {
    background: transparent; border: 1px solid transparent;
    border-radius: 8px; padding: 0;
}
QPushButton#del:hover { background: #1c1216; border-color: #35202a; }
QScrollArea { border: none; background: transparent; }
QWidget#clipHost { background: transparent; }
QWidget#clipRow { background: #10131a; border: 1px solid #191e27; border-radius: 12px; }
QWidget#clipRow:hover { background: #151a23; border-color: #29313f; }
QLabel#clipName { color: #e6e9f0; font-family: "Segoe UI"; font-size: 13px; font-weight: 600; }
QLabel#clipMeta { color: #79829a; font-family: "Segoe UI"; font-size: 11px; }
QLabel#emptyTitle { color: #4f586b; font-family: "Segoe UI"; font-size: 15px; font-weight: 600; }
QLabel#emptyHint  { color: #3d4557; font-family: "Segoe UI"; font-size: 12px; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #262d39; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""


def _run(args):
    return subprocess.run(args, capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW)


def duration(path):
    try:
        out = _run([str(FFPROBE), "-v", "error", "-show_entries",
                    "format=duration", "-of", "csv=p=0", str(path)]).stdout
        return float(out.strip())
    except Exception:
        return 0.0


def thumbnail(path):
    """First frame a second in, cached next to the app data."""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMB_DIR / f"{path.stem}_{int(path.stat().st_mtime)}.png"
    if not dest.exists():
        _run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
              "-ss", "1", "-i", str(path), "-frames:v", "1",
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
        size_mb = path.stat().st_size / 2 ** 20
        when = time.strftime("%H:%M", time.localtime(path.stat().st_mtime))
        meta.setText(f"{clock(duration(path))}  ·  {size_mb:.0f} MB  ·  {when}")
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
        self.resize(1000, 700)
        self.setMinimumSize(760, 520)
        self._seen = None

        root = QWidget()
        root.setObjectName("root")
        root.setAttribute(Qt.WA_StyledBackground, True)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(16)

        head = QHBoxLayout()
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
        outer.addLayout(head)

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

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def clips(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        return sorted((p for p in OUT_DIR.iterdir()
                       if p.is_file() and p.suffix.lower() in (".mp4", ".mkv")),
                      key=lambda p: p.stat().st_mtime, reverse=True)

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
        self.count.setText(f"{len(files)} clip{'s' if len(files) != 1 else ''}"
                           if has else "")


def main():
    app = QApplication([])
    app.setApplicationName("Fragloop")
    app.setStyleSheet(QSS)
    win = Studio()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
