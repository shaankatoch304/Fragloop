"""Headless checks for the studio clip rows and library filter.

Run: python studiotest.py
"""
import os
import tempfile
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

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


def test_filename_is_plain_text(root):
    path = root / "a & b.mp4"
    path.write_bytes(b"")
    row = row_for(path)
    name = row.findChild(QLabel, "clipName")
    check(name is not None and name.text() == path.stem,
          f"name label {name and name.text()!r}")
    check(name.textFormat() == Qt.PlainText, "filename rendered as rich text")
    row.close()
    row.deleteLater()
    app.processEvents()


def test_image_caps(root):
    # A tiny file claiming huge dimensions must be rejected before decoding.
    import struct
    import zlib

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    bomb = root / "bomb.png"
    bomb.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 100_000, 100_000,
                                     8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00"))
        + chunk(b"IEND", b""))
    check(studio.load_image(bomb) is None, "huge image dimensions were loaded")

    tiny = root / "tiny.png"
    make_png(tiny)
    check(studio.load_image(tiny) is not None, "valid image was rejected")
    original = studio.MAX_IMAGE_BYTES
    studio.MAX_IMAGE_BYTES = 8
    try:
        check(studio.load_image(tiny) is None, "oversized file was loaded")
    finally:
        studio.MAX_IMAGE_BYTES = original


def test_concat_quoting(root):
    import recorder
    line = recorder._concat_line(Path("C:/x/it's/buf_1.mkv"))
    check(line == "file 'C:/x/it'\\''s/buf_1.mkv'",
          f"concat path not quoted safely: {line!r}")


def test_pending_rows(root):
    part = root / "2026-11-25_12-00-00_replay.part.mp4"
    part.write_bytes(b"x" * 1024)
    os.utime(part, None)

    calls = []
    original_thumb = studio.thumbnail
    studio.thumbnail = lambda path: calls.append(path) or None
    try:
        row = row_for(part)
    finally:
        studio.thumbnail = original_thumb
    check(row.pending, "part row not marked as saving")
    check(not calls, "pending row spent time on a thumbnail")
    meta = row.findChild(QLabel, "clipMeta")
    check(meta is not None and "Saving" in meta.text(),
          f"pending meta {meta and meta.text()!r}")
    remove = row.findChild(QPushButton, "del")
    check(remove is not None and not remove.isEnabled(),
          "delete is enabled while saving")

    opened = []
    original_open = studio.os.startfile
    studio.os.startfile = lambda path: opened.append(path)
    try:
        row._open()
    finally:
        studio.os.startfile = original_open
    check(not opened, "pending clip was opened")

    class Settings:
        def value(self, key, default=None, type=None):
            return True

    original_dir = studio.OUT_DIR
    studio.OUT_DIR = root
    try:
        files = studio.Studio.clips(SimpleNamespace(settings=Settings()))
        check(part in files, "fresh part missing from the library")
        stale = root / "stale.part.mp4"
        stale.write_bytes(b"")
        stamp = time.time() - studio.PART_MAX_AGE - 60
        os.utime(stale, (stamp, stamp))
        files = studio.Studio.clips(SimpleNamespace(settings=Settings()))
        check(stale not in files, "stale part shown forever")
    finally:
        studio.OUT_DIR = original_dir

    row.close()
    row.deleteLater()
    app.processEvents()


def test_pending_finishes(root):
    part = root / "2026-11-26_00-00-00_replay.part.mp4"
    part.write_bytes(b"x")
    os.utime(part, None)
    row = row_for(part)
    check(row.pending, "part row not marked as saving")

    final = root / "2026-11-26_00-00-00_replay.mp4"
    part.rename(final)
    final_row = row_for(final)
    check(not final_row.pending, "finished row still marked as saving")
    check(final_row.buffer, "finished replay lost its type")
    meta = final_row.findChild(QLabel, "clipMeta")
    check(meta is not None and "Saving" not in meta.text(),
          f"finished meta {meta and meta.text()!r}")

    for widget in (row, final_row):
        widget.close()
        widget.deleteLater()
    app.processEvents()


def test_library_sort(root):
    names = ["sort_a.mp4", "sort_b.mp4", "sort_c.mp4"]
    for index, name in enumerate(names):
        path = root / name
        path.write_bytes(b"")
        stamp = 3000 + index
        os.utime(path, (stamp, stamp))

    class Settings:
        def __init__(self, newest):
            self.newest = newest

        def value(self, key, default=None, type=None):
            return self.newest if key == "sort_newest" else default

    original = studio.OUT_DIR
    studio.OUT_DIR = root
    try:
        newest = [p.name for p in studio.Studio.clips(
            SimpleNamespace(settings=Settings(True))) if p.name in names]
        oldest = [p.name for p in studio.Studio.clips(
            SimpleNamespace(settings=Settings(False))) if p.name in names]
    finally:
        studio.OUT_DIR = original
    check(newest == ["sort_c.mp4", "sort_b.mp4", "sort_a.mp4"],
          f"newest order {newest!r}")
    check(oldest == ["sort_a.mp4", "sort_b.mp4", "sort_c.mp4"],
          f"oldest order {oldest!r}")


def test_screenshot_parts_stay_hidden(root):
    shot = root / "hidden.part.png"
    shot.write_bytes(b"")
    os.utime(shot, None)

    class Settings:
        def value(self, key, default=None, type=None):
            return True

    original = studio.OUT_DIR
    studio.OUT_DIR = root
    try:
        files = studio.Studio.clips(SimpleNamespace(settings=Settings()))
    finally:
        studio.OUT_DIR = original
    check(shot not in files, "screenshot part was listed")


def test_explorer_select_command(root):
    path = Path(r"C:\Videos\Captures\clip one.mp4")
    command = studio.explorer_select_command(path)
    check(command == 'explorer /select,"C:\\Videos\\Captures\\clip one.mp4"',
          f"explorer select command {command!r}")


def test_audio_args(root):
    import recorder
    check(recorder._audio_args(None, None) == ([], []),
          "no audio tracks should add no inputs")
    inputs, maps = recorder._audio_args("a.wav", None)
    check(inputs == ["-i", "a.wav"]
          and maps == ["-map", "0:v", "-map", "1:a"],
          f"loopback args {inputs!r} {maps!r}")
    inputs, maps = recorder._audio_args(None, "m.wav")
    check(inputs == ["-i", "m.wav"]
          and maps == ["-map", "0:v", "-map", "1:a"],
          f"mic-only args {inputs!r} {maps!r}")
    inputs, maps = recorder._audio_args("a.wav", "m.wav")
    check(inputs == ["-i", "a.wav", "-i", "m.wav"], f"mix inputs {inputs!r}")
    check(any("amix=inputs=2" in arg for arg in maps), f"mix maps {maps!r}")
    check("[a]" in maps and "0:v" in maps, f"mix output map {maps!r}")


def test_pcm_tail(root):
    import recorder
    original = recorder.BUFFER_DIR
    recorder.BUFFER_DIR = root
    try:
        rate, channels, seconds = 48000, 2, 1
        samples = b"\x01\x00" * channels * rate * 2  # 2 s of non-silence
        (root / "mic_1.pcm").write_bytes(samples)
        wav = recorder._pcm_tail("mic_*.pcm", rate, channels, seconds,
                                 time.time(), 0.0)
        with wave.open(str(wav), "rb") as w:
            frames = w.getnframes()
            data = w.readframes(frames)
        check(frames == rate * seconds,
              f"tail frames {frames} != {rate * seconds}")
        check(data == samples[-len(data):], "tail kept the newest samples")
    finally:
        recorder.BUFFER_DIR = original


def main():
    tests = [test_types_and_labels, test_animations,
             test_open_does_not_block, test_failed_open_reports_on_ui_thread,
             test_library_filter, test_filename_is_plain_text,
             test_pending_rows, test_pending_finishes, test_library_sort,
             test_screenshot_parts_stay_hidden, test_image_caps,
             test_concat_quoting, test_explorer_select_command,
             test_audio_args, test_pcm_tail]
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for test in tests:
            test(root)
            print(f"ok {test.__name__}")
    print(f"{len(tests)} studio checks passed")


if __name__ == "__main__":
    main()
