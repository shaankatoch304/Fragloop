"""Headless checks for the studio clip rows and library filter.

Run: python studiotest.py
"""
import os
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

import studio

app = QApplication([])
app.setStyleSheet(studio.QSS)

# Keep rows fast and independent of the bundled ffmpeg.
studio.thumbnail = lambda path: None
studio.duration = lambda path: 0.0


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def wait_for(predicate, timeout=1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def make_png(path):
    image = QImage(32, 18, QImage.Format_RGB32)
    image.fill(0x2a3340)
    check(image.save(str(path)), f"could not write {path}")


def row_for(path):
    row = studio.ClipRow(path, lambda: None)
    row.resize(700, studio.THUMB_H + 20)
    row.show()
    app.processEvents()
    return row


def test_types_and_labels(root):
    make_png(root / "shot.png")
    for name in ("clip_replay.mp4", "clip.mp4", "clip.mkv"):
        (root / name).write_bytes(b"")
    cases = [
        ("shot.png", "shotTag", "Screenshot"),
        ("clip_replay.mp4", "bufferTag", "Replay"),
        ("clip.mp4", "recordTag", "Recording"),
        ("clip.mkv", "recordTag", "Recording"),
    ]
    for name, tag_name, label in cases:
        path = root / name
        row = row_for(path)
        tag = row.findChild(QLabel, tag_name)
        check(tag is not None and tag.text() == label,
              f"{name}: tag {tag and tag.text()!r} != {label!r}")
        check(row.toolTip() == f"{label}  ·  double-click to open",
              f"{name}: tooltip {row.toolTip()!r}")
        check(row.accessibleName() == f"{label}: {name}",
              f"{name}: accessible name {row.accessibleName()!r}")
        check(row.accessibleDescription(), f"{name}: no accessible description")
        row.close()
        row.deleteLater()
    app.processEvents()


def test_animations(root):
    row = row_for(root / "shot.png")
    row._animate_hover(1.0)
    check(wait_for(lambda: row._hover > 0.99), "hover-in did not finish")
    check(abs(row._wash_fx.opacity() - 1.0) < 0.01,
          f"hover opacity {row._wash_fx.opacity():.2f}")
    check(row._lay.contentsMargins().top() == 8,
          f"hover lift {row._lay.contentsMargins().top()}")
    row._animate_hover(0.0)
    check(wait_for(lambda: row._hover < 0.01), "hover-out did not finish")
    check(abs(row._wash_fx.opacity() - 0.68) < 0.01,
          f"rest opacity {row._wash_fx.opacity():.2f}")
    row._press_anim.start()
    check(wait_for(lambda: row._press > 0.5, 0.6), "press did not peak")
    check(wait_for(lambda: row._press < 0.01, 0.6), "press did not settle")
    row.close()
    row.deleteLater()
    app.processEvents()


def test_open_does_not_block(root):
    path = root / "shot.png"
    row = row_for(path)
    opened = []
    original = studio.os.startfile

    def slow_startfile(target):
        time.sleep(0.4)
        opened.append(target)

    studio.os.startfile = slow_startfile
    try:
        started = time.monotonic()
        QTest.mouseDClick(row, Qt.LeftButton, pos=row.rect().center())
        elapsed = time.monotonic() - started
        check(elapsed < 0.1, f"double-click blocked for {elapsed:.2f}s")
        check(wait_for(lambda: opened == [path], 1.5),
              f"player launch missing: {opened!r}")
    finally:
        studio.os.startfile = original
    row.close()
    row.deleteLater()
    app.processEvents()


def test_failed_open_reports_on_ui_thread(root):
    row = row_for(root / "shot.png")
    seen = []

    class Receiver(QObject):
        @Slot(str)
        def take(self, message):
            seen.append((message, threading.current_thread()))

    receiver = Receiver()
    row.open_failed.disconnect()
    row.open_failed.connect(receiver.take)
    original = studio.os.startfile
    studio.os.startfile = lambda path: (_ for _ in ()).throw(
        OSError("no player"))
    try:
        row._open()
        check(wait_for(lambda: seen), "failure signal never arrived")
    finally:
        studio.os.startfile = original
    message, thread = seen[0]
    check(message == "no player", f"failure message {message!r}")
    check(thread is threading.main_thread(),
          "failure reported off the UI thread")
    row.close()
    row.deleteLater()
    app.processEvents()


def test_library_filter(root):
    names = ["shot.png", "clip_replay.mp4", "clip.mp4", "clip.mkv",
             "old.part.png", "new.part.mp4", "notes.txt"]
    for index, name in enumerate(names):
        path = root / name
        path.write_bytes(b"")
        stamp = 1000 + index
        os.utime(path, (stamp, stamp))

    class Settings:
        def value(self, key, default=None, type=None):
            return True

    original = studio.OUT_DIR
    studio.OUT_DIR = root
    try:
        files = studio.Studio.clips(SimpleNamespace(settings=Settings()))
    finally:
        studio.OUT_DIR = original
    got = [path.name for path in files]
    want = ["clip.mkv", "clip.mp4", "clip_replay.mp4", "shot.png"]
    check(got == want, f"library filter {got!r} != {want!r}")


def main():
    tests = [test_types_and_labels, test_animations,
             test_open_does_not_block, test_failed_open_reports_on_ui_thread,
             test_library_filter]
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for test in tests:
            test(root)
            print(f"ok {test.__name__}")
    print(f"{len(tests)} studio checks passed")


if __name__ == "__main__":
    main()
