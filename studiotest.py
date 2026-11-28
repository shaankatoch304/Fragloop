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

from PySide6.QtCore import QAbstractAnimation, QEvent, QObject, Qt, Slot
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

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
    check(wait_for(lambda: row._hover_anim.state() == QAbstractAnimation.Stopped),
          "hover-in did not finish")
    check(row._hover > 0.99, f"hover value {row._hover:.3f}")
    check(abs(row._wash_fx.opacity() - 1.0) < 0.01,
          f"hover opacity {row._wash_fx.opacity():.2f}")
    check(row._lay.contentsMargins().top() == 8,
          f"hover lift {row._lay.contentsMargins().top()}")
    row._animate_hover(1.0)
    check(row._hover_anim.state() == QAbstractAnimation.Stopped,
          "hover restarted while already at target")
    row._pointer_inside = lambda: True
    row.leaveEvent(QEvent(QEvent.Leave))
    check(row._hover_anim.state() == QAbstractAnimation.Stopped,
          "leave inside the row restarted hover-out")
    row._pointer_inside = lambda: False
    row.leaveEvent(QEvent(QEvent.Leave))
    check(row._hover_anim.state() == QAbstractAnimation.Running,
          "leave outside the row did not start hover-out")
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

    original = studio.CAPTURE_DIRS
    studio.CAPTURE_DIRS = (root,)
    try:
        files = studio.Studio.clips(SimpleNamespace(settings=Settings()))
    finally:
        studio.CAPTURE_DIRS = original
    got = [path.name for path in files]
    want = ["clip.mkv", "clip.mp4", "clip_replay.mp4", "shot.png"]
    check(got == want, f"library filter {got!r} != {want!r}")


def test_library_scans_capture_folders(root):
    folders = [root / "Screenshots", root / "Replay Buffer",
               root / "Recordings"]
    names = ["shot_screenshot.png", "clip_replay.mp4", "clip.mp4"]
    for folder, name in zip(folders, names):
        folder.mkdir()
        (folder / name).write_bytes(b"")

    class Settings:
        def value(self, key, default=None, type=None):
            return True

    original = studio.CAPTURE_DIRS
    studio.CAPTURE_DIRS = tuple(folders)
    try:
        files = studio.Studio.clips(SimpleNamespace(settings=Settings()))
    finally:
        studio.CAPTURE_DIRS = original
    check({p.name for p in files} == set(names),
          f"folders not scanned together: {[p.name for p in files]!r}")


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

    original_dir = studio.CAPTURE_DIRS
    studio.CAPTURE_DIRS = (root,)
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
        studio.CAPTURE_DIRS = original_dir

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

    original = studio.CAPTURE_DIRS
    studio.CAPTURE_DIRS = (root,)
    try:
        newest = [p.name for p in studio.Studio.clips(
            SimpleNamespace(settings=Settings(True))) if p.name in names]
        oldest = [p.name for p in studio.Studio.clips(
            SimpleNamespace(settings=Settings(False))) if p.name in names]
    finally:
        studio.CAPTURE_DIRS = original
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

    original = studio.CAPTURE_DIRS
    studio.CAPTURE_DIRS = (root,)
    try:
        files = studio.Studio.clips(SimpleNamespace(settings=Settings()))
    finally:
        studio.CAPTURE_DIRS = original
    check(shot not in files, "screenshot part was listed")


def test_pending_context_menu(root):
    part = root / "2026-11-27_01-00-00_replay.part.mp4"
    part.write_bytes(b"x")
    os.utime(part, None)
    row = row_for(part)
    actions = row.context_menu().actions()
    labels = [a.text() for a in actions if not a.isSeparator()]
    check(labels == ["Play", "Open file location", "Delete"],
          f"context menu actions {labels!r}")
    check(not any(a.isEnabled() for a in actions if not a.isSeparator()),
          "saving clip offered actions")
    reveal = next((a for a in actions if a.text() == "Open file location"), None)
    check(reveal is not None, "context menu lost Open file location")
    check(not reveal.isEnabled(), "saving clip offered Open file location")
    row.close()
    row.deleteLater()

    final = root / "2026-11-27_01-00-00_replay.mp4"
    final.write_bytes(b"x")
    final_row = row_for(final)
    actions = final_row.context_menu().actions()
    check(all(a.isEnabled() for a in actions if not a.isSeparator()),
          "finished clip disabled a context action")
    reveal = next((a for a in actions if a.text() == "Open file location"), None)
    check(reveal is not None and reveal.isEnabled(),
          "finished clip lost Open file location")
    final_row.close()
    final_row.deleteLater()
    app.processEvents()


def test_open_log(root):
    import recorder
    original_log = recorder.LOG
    original_start = recorder.os.startfile
    log = root / "nested" / "recorder.log"
    opened = []
    recorder.LOG = log
    recorder.os.startfile = lambda path: opened.append(path)
    try:
        recorder._open_log()
        check(opened == [log], f"log not opened: {opened!r}")
        check(log.exists(), "log file was not created")
    finally:
        recorder.LOG = original_log
        recorder.os.startfile = original_start


def test_prune_cleans_stale_temp_files(root):
    import recorder
    original_dir = recorder.BUFFER_DIR
    original_disk = recorder.shutil.disk_usage
    original_keep = recorder._keep_segments
    original_audio = recorder.KEEP_AUDIO_FILES
    recorder.BUFFER_DIR = root
    recorder.shutil.disk_usage = lambda path: SimpleNamespace(free=100 * 2**30)
    recorder._keep_segments = lambda: 2
    recorder.KEEP_AUDIO_FILES = 2
    try:
        for name in ("tail_1.wav", "save_x.mkv", "list_x.txt",
                     "buf_1.mkv", "buf_2.mkv", "buf_3.mkv",
                     "aud_1.pcm", "aud_2.pcm", "aud_3.pcm"):
            path = root / name
            path.write_bytes(b"x")
            stamp = time.time() - recorder.TEMP_MAX_AGE - 60
            os.utime(path, (stamp, stamp))
        fresh = root / "tail_fresh.wav"
        fresh.write_bytes(b"x")
        recorder._prune_buffer()
        check(fresh.exists(), "active tail was pruned")
        for name in ("tail_1.wav", "save_x.mkv", "list_x.txt",
                     "buf_1.mkv", "aud_1.pcm"):
            check(not (root / name).exists(), f"stale temp {name} kept")
        for name in ("buf_3.mkv", "aud_3.pcm"):
            check((root / name).exists(), f"newest buffer file {name} pruned")
    finally:
        recorder.BUFFER_DIR = original_dir
        recorder.shutil.disk_usage = original_disk
        recorder._keep_segments = original_keep
        recorder.KEEP_AUDIO_FILES = original_audio


def test_recording_room_check(root):
    import recorder
    original = recorder.shutil.disk_usage
    try:
        recorder.shutil.disk_usage = lambda path: SimpleNamespace(free=10)
        check(not recorder._has_room(root, 100),
              "low disk space did not block the save")
        recorder.shutil.disk_usage = lambda path: (_ for _ in ()).throw(
            OSError("unavailable"))
        check(recorder._has_room(root, 100),
              "unknown free space should not block the save")
    finally:
        recorder.shutil.disk_usage = original


def test_recording_path_avoids_collisions(root):
    import recorder
    original = recorder.RECORD_DIR
    recorder.RECORD_DIR = root
    stamp = "2026-11-27_01-00-00"
    try:
        check(recorder._recording_path(stamp).name == f"{stamp}.mp4",
              "first recording name changed")
        (root / f"{stamp}.mp4").write_bytes(b"")
        check(recorder._recording_path(stamp).name == f"{stamp}_2.mp4",
              "existing recording would be replaced")
        (root / f"{stamp}_2.part.mp4").write_bytes(b"")
        check(recorder._recording_path(stamp).name == f"{stamp}_3.mp4",
              "in-progress recording would be replaced")
        (root / f"{stamp}_3.mkv").write_bytes(b"")
        check(recorder._recording_path(stamp).name == f"{stamp}_4.mp4",
              "mkv fallback would be replaced")
    finally:
        recorder.RECORD_DIR = original


def test_log_rotation(root):
    import recorder
    original_log = recorder.LOG
    original_max = recorder.LOG_MAX_BYTES
    log = root / "recorder.log"
    recorder.LOG = log
    recorder.LOG_MAX_BYTES = 100
    try:
        log.write_text("x" * 200, encoding="utf-8")
        recorder._log("hello")
        rotated = root / "recorder.log.1"
        check(rotated.exists(), "oversized log was not rotated")
        check(rotated.read_text(encoding="utf-8") == "x" * 200,
              "rotation lost the old log")
        check("hello" in log.read_text(encoding="utf-8"),
              "new log line missing after rotation")
    finally:
        recorder.LOG = original_log
        recorder.LOG_MAX_BYTES = original_max


def test_zero_size_text(root):
    check(studio.size_text(0) == "0 KB", "empty file shown as non-zero")
    check(studio.size_text(1) == "1 KB", "tiny file should round to 1 KB")
    check(studio.size_text(1024) == "1 KB", "1 KB formatted wrong")


def test_folder_open_failure_is_reported(root):
    seen = []
    original_start = studio.os.startfile
    original_exec = studio.QMessageBox.exec
    studio.os.startfile = lambda path: (_ for _ in ()).throw(
        OSError("access denied"))
    studio.QMessageBox.exec = lambda box: seen.append(box.text()) or 0
    try:
        studio.Settings.open_folder(root / "blocked")
    finally:
        studio.os.startfile = original_start
        studio.QMessageBox.exec = original_exec
    check(seen and "access denied" in seen[0],
          f"folder failure not reported: {seen!r}")


def test_bitrate_estimate(root):
    text = studio.bitrate_estimate_text(80, 300)
    check("~574 MB per minute" in text, f"80 Mbps estimate {text!r}")
    check("2.80 GB" in text, f"5 min buffer estimate {text!r}")
    check("Manual recording" in text, f"missing manual estimate {text!r}")
    text = studio.bitrate_estimate_text(200, 60)
    check("~1.40 GB per minute" in text, f"200 Mbps estimate {text!r}")


def test_preferences_escape(root):
    class MemorySettings:
        def __init__(self):
            self.data = {}

        def value(self, key, default=None, type=None):
            value = self.data.get(key, default)
            if type is bool:
                return bool(value)
            if type is int:
                return int(value)
            return value

        def setValue(self, key, value):
            self.data[key] = value

        def sync(self):
            pass

    class Root:
        def set_grain(self, on):
            pass

    class FakeStudio(QWidget):
        def __init__(self):
            super().__init__()
            self.settings = MemorySettings()
            self.replay_seconds = 300
            self.root = Root()

        def set_replay_seconds(self, value):
            self.replay_seconds = value

        def set_always_on_top(self, on):
            pass

        def refresh(self):
            pass

    original = studio.mic_devices
    studio.mic_devices = lambda: []
    try:
        dialog = studio.Settings(FakeStudio())
        dialog.show()
        app.processEvents()
        dialog.bitrate.setValue(120)
        dialog.bitrate_timer.stop()
        dialog.save_bitrate()
        check(dialog.bitrate_value.text() == "120 Mbps",
              f"bitrate label {dialog.bitrate_value.text()!r}")
        check(dialog.studio.settings.data.get("video_bitrate") == 120,
              "bitrate slider did not save")
        check("Manual recording" in dialog.bitrate_hint.text(),
              "bitrate estimate missing from Preferences")
        QTest.keyClick(dialog, Qt.Key_Escape)
        app.processEvents()
        check(not dialog.isVisible(), "Escape did not close Preferences")
        dialog.close()
        dialog.deleteLater()
    finally:
        studio.mic_devices = original


def test_missing_clip_mtime(root):
    missing = root / "already-gone.mp4"
    check(studio.file_mtime(missing) == 0.0,
          "a vanished clip should not break the refresh key")
    present = root / "still-here.mp4"
    present.write_bytes(b"")
    check(studio.file_mtime(present) > 0, "a real clip lost its mtime")


def test_explorer_select_command(root):
    path = Path(r"C:\Videos\Captures\clip one.mp4")
    command = studio.explorer_select_command(path)
    check(command == 'explorer /select,"C:\\Videos\\Captures\\clip one.mp4"',
          f"explorer select command {command!r}")


def test_video_args(root):
    import recorder
    args = recorder._video_args(120)
    check(args[args.index("-b:v") + 1] == "120M", f"bitrate args {args!r}")
    check(args[args.index("-maxrate") + 1] == "165M",
          f"maxrate args {args!r}")
    check(args[args.index("-bufsize") + 1] == "330M",
          f"bufsize args {args!r}")


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
             test_library_filter, test_library_scans_capture_folders,
             test_filename_is_plain_text,
             test_pending_rows, test_pending_finishes, test_library_sort,
             test_screenshot_parts_stay_hidden, test_image_caps,
             test_pending_context_menu, test_open_log,
             test_prune_cleans_stale_temp_files,
             test_recording_room_check,
             test_recording_path_avoids_collisions,
             test_log_rotation, test_zero_size_text,
             test_folder_open_failure_is_reported,
             test_bitrate_estimate, test_preferences_escape,
             test_missing_clip_mtime,
             test_concat_quoting, test_explorer_select_command,
             test_video_args, test_audio_args, test_pcm_tail]
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for test in tests:
            test(root)
            print(f"ok {test.__name__}")
    print(f"{len(tests)} studio checks passed")


if __name__ == "__main__":
    main()
