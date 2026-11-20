"""Fragloop - tiny tray recorder.

Alt+G starts/stops. Records the primary display at a constant 120 fps with
NVENC straight from the GPU and system audio via WASAPI loopback (never the
mic). Alt+P saves the last five minutes from the replay buffer. Files land in
Videos\\Captures as .mp4.
"""
import ctypes
import ctypes.wintypes
import datetime
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import pyaudiowpatch as pyaudio
import pystray
import comtypes
from PIL import Image, ImageDraw
from pycaw.pycaw import AudioUtilities

def _app_dir():
    # one-folder build: the engine lives next to the exe, not in the bundle
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
FFMPEG = APP_DIR / "ffmpeg.exe"
FFPROBE = APP_DIR / "ffprobe.exe"
LOG = Path(os.environ["LOCALAPPDATA"]) / "Fragloop" / "recorder.log"
OUT_DIR = Path(os.environ["USERPROFILE"]) / "Videos" / "Captures"

def _display_hz():
    """Refresh rate of the primary panel (kept for logging/diagnostics)."""
    hdc = ctypes.windll.user32.GetDC(0)
    try:
        hz = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 116))  # VREFRESH
    finally:
        ctypes.windll.user32.ReleaseDC(0, hdc)
    return hz if 24 <= hz <= 1000 else 240


RECORD_FPS = 120               # constant output rate; VFR 50-240 fps footage
                               # judders in players, CFR 120 plays smooth
BITRATE = "200M"               # hardcoded video rate; ~1.5 GB per minute
CODEC = "h264_nvenc"           # plays anywhere without the HEVC extension;
                               # hevc_nvenc is ~40% smaller at the same quality
HOTKEY_VK = 0x47               # G
HOTKEY_MODS = 0x0001 | 0x4000  # Alt | MOD_NOREPEAT

BUFFER_DIR = Path(os.environ["LOCALAPPDATA"]) / "Fragloop" / "buffer"
REPLAY_SECONDS = 300           # how much past gameplay Alt+P saves
SEGMENT_SECONDS = 2            # small chunks keep the freshest tail on disk
SAVE_SEGMENTS = REPLAY_SECONDS // SEGMENT_SECONDS + 1
KEEP_SEGMENTS = SAVE_SEGMENTS + 5
AUDIO_ROTATE_SECONDS = 300     # rolling audio chunk size
KEEP_AUDIO_FILES = 12          # ~1 h, so a long manual recording keeps its audio
# Seconds of audio to trim from the end, measured with a white flash + 1 kHz
# tone that was primed so PyAudio's cold start could not skew the timing.
AUDIO_SYNC_BY_DEVICE = {"JBL": -0.084, "Realtek": -0.025}
# Manual (Alt+G) files close differently; Bluetooth needed its own value.
AUDIO_MANUAL_SYNC_BY_DEVICE = {"JBL": 0.088}
AUDIO_SYNC_SECONDS = -0.025    # fallback when the endpoint is unknown

LOG.parent.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
_icon = None
_proc = None
_recording = False
_final = None
_video = None
_wav = None
_audio_ok = False
_audio_from_buffer = False
_audio_ready = threading.Event()
_audio_stop = threading.Event()
_audio_thread = None

_buffer_on = False
_buffer_proc = None
_buffer_thread = None
_buffer_stop = threading.Event()
_buf_audio_stop = threading.Event()
_buf_audio_thread = None
_buf_audio_fh = None
_buf_audio_lock = threading.Lock()
_buf_rate = 48000
_buf_channels = 2


def _icon_image(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=color)
    d.ellipse((24, 24, 40, 40), fill="white")
    return img


def _notify(text):
    try:
        if _icon:
            _icon.notify(text, "Fragloop")
    except Exception:
        pass  # a notification must never take a worker down


def _log(text):
    # A full disk or AV lock must never take down the hotkey loop or a
    # capture thread; logging is best-effort.
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
    except OSError:
        pass


def _loopback_device(pa):
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    dev = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if not dev.get("isLoopbackDevice"):
        dev = next((d for d in pa.get_loopback_device_info_generator()
                    if dev["name"] in d["name"]), None)
    if not dev:
        raise RuntimeError("no loopback device for the default output")
    return dev


def _default_output_name():
    """Windows' current default playback endpoint.

    PortAudio caches the default device while any PyAudio instance is alive,
    so the fresh instance in _default_loopback() kept reporting the old one
    and audio stayed on a silent endpoint after switching to headphones.
    """
    try:
        comtypes.CoInitialize()
        return AudioUtilities.GetSpeakers().FriendlyName
    except Exception:
        return None


def _audio_loop():
    """Capture the default output device's loopback into _wav (no mic)."""
    global _audio_ok
    pa = stream = wf = None
    lock = threading.Lock()
    rate, frame_bytes, t0 = 48000, 4, 0.0

    def pad():
        # shortcut: Bluetooth endpoints deliver nothing while silent; pad
        # zeros by wall clock so the WAV stays in sync with the video
        gap = int((time.monotonic() - t0) * rate) - wf.tell()
        if gap > 0:
            wf.writeframes(b"\x00" * (min(gap, rate) * frame_bytes))

    def on_frames(in_data, frame_count, time_info, status):
        with lock:
            try:
                pad()
                wf.writeframes(in_data)
            except Exception:
                pass
        return None, pyaudio.paContinue

    try:
        pa = pyaudio.PyAudio()
        dev = _loopback_device(pa)
        channels = int(dev["maxInputChannels"])
        rate = int(dev["defaultSampleRate"])
        frame_bytes = channels * 2
        wf = wave.open(str(_wav), "wb")
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                         frames_per_buffer=1024, input=True,
                         input_device_index=dev["index"],
                         stream_callback=on_frames, start=False)
        _audio_ok = True
        t0 = time.monotonic()
        stream.start_stream()
        _audio_ready.set()

        def padder():
            while not _audio_stop.is_set():
                time.sleep(0.05)
                with lock:
                    try:
                        pad()
                    except Exception:
                        pass

        threading.Thread(target=padder, daemon=True).start()
        _audio_stop.wait()
    except Exception as e:
        _audio_ok = False
        _log(f"audio failed: {e}")
    finally:
        _audio_ready.set()
        try:
            if stream:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass
        try:
            if wf:
                wf.close()
        except Exception:
            pass
        try:
            if pa:
                pa.terminate()
        except Exception:
            pass


_audio_state = {"pa": None, "stream": None, "dev": None, "rate": 48000,
                "ch": 2, "t0": 0.0, "frames": 0}


def _buf_audio_pad():
    """Fill the open file up to wall clock. Call with _buf_audio_lock held."""
    st = _audio_state
    fh = _buf_audio_fh
    if fh is None:
        return
    # Keep a small reserve so silence is never written ahead of live
    # samples that arrive a callback late.
    gap = (int((time.monotonic() - st["t0"]) * st["rate"])
           - st["frames"] - int(st["rate"] * 0.05))
    if gap > 0:
        n = min(gap, st["rate"])
        fh.write(b"\x00" * (n * st["ch"] * 2))
        st["frames"] += n


def _buf_audio_new_file(rate=None, channels=None):
    """Pad/close the old PCM and start a new one. Call with the lock held."""
    global _buf_audio_fh, _buf_rate, _buf_channels
    st = _audio_state
    if _buf_audio_fh is not None:
        try:
            _buf_audio_pad()
            _buf_audio_fh.close()
        except Exception:
            pass
    if rate is not None:
        st["rate"], st["ch"] = int(rate), int(channels)
    _buf_audio_fh = open(BUFFER_DIR / f"aud_{time.time():.3f}.pcm", "wb")
    _buf_rate, _buf_channels = st["rate"], st["ch"]
    st["t0"] = time.monotonic()
    st["frames"] = 0


def _buf_audio_cb(in_data, frame_count, time_info, status):
    st = _audio_state
    with _buf_audio_lock:
        try:
            if _buf_audio_fh is None:
                return None, pyaudio.paContinue
            _buf_audio_pad()
            _buf_audio_fh.write(in_data)
            st["frames"] += len(in_data) // (st["ch"] * 2)
            if st["frames"] >= st["rate"] * AUDIO_ROTATE_SECONDS:
                _buf_audio_new_file()
        except Exception:
            pass
    return None, pyaudio.paContinue


def _buf_audio_close():
    st = _audio_state
    try:
        if st["stream"]:
            st["stream"].stop_stream()
            st["stream"].close()
    except Exception:
        pass
    st["stream"] = None
    try:
        if st["pa"]:
            st["pa"].terminate()
    except Exception:
        pass
    st["pa"] = None


def _buf_audio_open():
    """(Re)create PyAudio and the loopback stream at the current device."""
    global _buf_audio_fh
    st = _audio_state
    _buf_audio_close()
    st["pa"] = pyaudio.PyAudio()
    dev = _loopback_device(st["pa"])
    rate, ch = int(dev["defaultSampleRate"]), int(dev["maxInputChannels"])
    with _buf_audio_lock:
        same = (_buf_audio_fh is not None
                and rate == st["rate"] and ch == st["ch"])
        if not same:
            if _buf_audio_fh is not None:
                # Old chunks are in another format; mixing rates would
                # corrupt the timeline, so drop them and start clean.
                try:
                    _buf_audio_pad()
                except Exception:
                    pass
                try:
                    _buf_audio_fh.close()
                except Exception:
                    pass
                _buf_audio_fh = None
                for p in BUFFER_DIR.glob("aud_*.pcm"):
                    try:
                        p.unlink()
                    except OSError:
                        pass
            _buf_audio_new_file(rate, ch)
    st["dev"] = dev
    st["stream"] = st["pa"].open(
        format=pyaudio.paInt16, channels=ch, rate=rate,
        frames_per_buffer=1024, input=True,
        input_device_index=dev["index"],
        stream_callback=_buf_audio_cb, start=False)
    st["stream"].start_stream()
    return dev


def _buffer_audio_loop():
    """Continuous loopback capture. The wall clock drives the timeline.

    WASAPI loopback sends no packets at all while the endpoint is silent,
    so silence must be padded by this loop, not waited for. Reopens only
    happen on device changes or errors and never reset the clock.
    """
    global _buf_audio_fh
    st = _audio_state
    last_check = 0.0
    while not _buf_audio_stop.is_set():
        try:
            if st["stream"] is None:
                dev = _buf_audio_open()
                _log(f"buffer audio started: {dev['name']}")
            time.sleep(0.05)
            with _buf_audio_lock:
                _buf_audio_pad()
                try:
                    _buf_audio_fh.flush()
                except Exception:
                    pass
            now = time.monotonic()
            # Follow the Windows default output when the user switches
            # speakers/headphones; otherwise loopback stays on the old device.
            if now - last_check >= 5:
                last_check = now
                current = _default_output_name()
                if current and current not in st["dev"]["name"]:
                    _log(f"buffer audio switched: {st['dev']['name']} -> "
                         f"{current}")
                    _buf_audio_open()
        except Exception as e:
            _log(f"buffer audio error: {e!r}")
            _buf_audio_close()
            time.sleep(1)
    _buf_audio_close()
    with _buf_audio_lock:
        try:
            if _buf_audio_fh:
                _buf_audio_fh.close()
        except Exception:
            pass
        _buf_audio_fh = None


def _buffer_audio_tail(dur, end_time, manual=False):
    """Build a WAV holding the last `dur` seconds of buffer audio."""
    with _buf_audio_lock:
        rate, channels = _buf_rate, _buf_channels
        dev_name = (_audio_state["dev"] or {}).get("name", "")
    frame_bytes = channels * 2
    need = int(dur * rate) * frame_bytes
    # video ends when the last closed segment was finished; drop the audio
    # recorded since then, *before* cutting to `need`. The sync value trims
    # a little extra because captured audio runs early vs. the video.
    sync = None
    if manual:
        sync = next((v for k, v in AUDIO_MANUAL_SYNC_BY_DEVICE.items()
                     if k in dev_name), None)
    if sync is None:
        sync = next((v for k, v in AUDIO_SYNC_BY_DEVICE.items()
                     if k in dev_name), AUDIO_SYNC_SECONDS)
    drop = max(0, int((time.time() - end_time + sync) * rate)) * frame_bytes
    keep = need + drop
    with _buf_audio_lock:
        try:
            _buf_audio_fh.flush()
        except Exception:
            pass
    files = sorted(BUFFER_DIR.glob("aud_*.pcm"))
    chunks, total = [], 0
    for p in reversed(files):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        chunks.append(data)
        total += len(data)
        if total >= keep + rate * frame_bytes:
            break
    data = b"".join(reversed(chunks))
    if drop and drop < len(data):
        data = data[:-drop]
    data = data[-need:]
    if len(data) < need:
        # buffer just started: pad the start so the track stays aligned
        data = b"\x00" * (need - len(data)) + data
    path = BUFFER_DIR / f"tail_{time.time():.3f}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return path


def _probe_duration(path):
    try:
        out = subprocess.run(
            [str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def _prune_buffer():
    # A long save reads the oldest segments for many seconds; deleting them
    # underneath ffmpeg either breaks the save or kills this thread via
    # PermissionError. KEEP_SEGMENTS leaves only 10 s of slack beyond the
    # 151 a save consumes, so skip deletion entirely while one is running.
    if not _save_lock.locked():
        for pat in ("buf_*.mkv", "aud_*.pcm"):
            keep = KEEP_SEGMENTS if pat.startswith("buf") else KEEP_AUDIO_FILES
            for p in sorted(BUFFER_DIR.glob(pat))[:-keep]:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
    try:
        if shutil.disk_usage(BUFFER_DIR).free < 8 * 2**30:
            _log("replay buffer stopped: low disk space")
            _notify("Replay buffer stopped: low disk space")
            _buffer_stop_all()
    except OSError:
        pass


def _buffer_supervisor():
    global _buffer_proc, _buffer_on
    fails = 0
    while not _buffer_stop.is_set():
        cmd = [
            str(FFMPEG), "-hide_banner", "-loglevel", "warning", "-y",
            "-nostdin",
            "-f", "lavfi", "-i",
            # ddagrab duplicates frames on the GPU itself: frames keep
            # flowing on a static screen, and we skip the hwdownload →
            # software fps → NVENC round-trip (2 GB/s through RAM) that
            # made the encoder fall seconds behind during real gameplay.
            f"ddagrab=output_idx=0:framerate={RECORD_FPS}:dup_frames=true",
            "-c:v", CODEC, "-preset", "p1", "-tune", "hq",
            "-rc", "vbr", "-b:v", BITRATE, "-maxrate", "250M", "-bufsize", "500M",
            "-bf", "0",
            # Keyframe every 2 s of media time, not every 60 frames: at
            # 150-240 fps a frame-count GOP is 0.25-0.4 s and spends a big
            # slice of the bitrate on keyframes. nvenc silently ignores
            # -force_key_frames unless -forced-idr is set too.
            "-g", str(RECORD_FPS * SEGMENT_SECONDS),
            "-forced-idr", "1",
            "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})",
            "-fps_mode", "cfr",
            "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
            "-segment_format", "matroska",
            "-reset_timestamps", "1",
            str(BUFFER_DIR / f"buf_{int(time.time())}_%06d.mkv"),
        ]
        try:
            _buffer_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL,
                stderr=open(BUFFER_DIR / "ffmpeg.log", "ab"),
                creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as e:
            _log(f"buffer ffmpeg could not start: {e!r}")
            _notify("Replay buffer could not start - see recorder.log")
            _buffer_on = False
            return
        started = time.time()
        _log("buffer ffmpeg started")
        while _buffer_proc.poll() is None and not _buffer_stop.is_set():
            time.sleep(1)
            _prune_buffer()
        if _buffer_proc.poll() is None:
            _buffer_proc.terminate()
            try:
                _buffer_proc.wait(10)
            except subprocess.TimeoutExpired:
                _buffer_proc.kill()
        if _buffer_stop.is_set():
            break
        # A crash right after start means the pipeline is broken (missing
        # NVENC session, bad folder permission); don't spin forever silently.
        fails = fails + 1 if time.time() - started < 10 else 0
        _log(f"buffer ffmpeg exited ({_buffer_proc.returncode}), "
             f"restarting (fail {fails})")
        if fails >= 5:
            _log("replay buffer stopped: ffmpeg keeps failing")
            _notify("Replay buffer stopped - see recorder.log")
            _buffer_on = False
            return
        time.sleep(2 if fails == 0 else min(30, 2 ** fails))


def _buffer_start():
    global _buffer_on, _buffer_thread, _buf_audio_thread
    if _save_lock.locked():
        return False
    for t in (_buffer_thread, _buf_audio_thread):
        if t and t.is_alive():
            t.join(12)  # let the old ffmpeg exit before reusing the folder
    if _buffer_thread and _buffer_thread.is_alive():
        return False
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    for p in BUFFER_DIR.iterdir():
        try:
            p.unlink()
        except OSError:
            pass
    _buffer_stop.clear()
    _buf_audio_stop.clear()
    _buffer_on = True
    _buffer_thread = threading.Thread(target=_buffer_supervisor, daemon=True)
    _buffer_thread.start()
    _buf_audio_thread = threading.Thread(target=_buffer_audio_loop, daemon=True)
    _buf_audio_thread.start()
    _log("replay buffer started")
    return True


def _buffer_stop_all():
    global _buffer_on
    _buffer_on = False
    _buffer_stop.set()
    _buf_audio_stop.set()
    # wait so ffmpeg exits cleanly and no orphan keeps writing to disk
    if _buffer_thread and _buffer_thread is not threading.current_thread():
        _buffer_thread.join(15)
    if _buf_audio_thread and _buf_audio_thread is not threading.current_thread():
        _buf_audio_thread.join(5)


_save_lock = threading.Lock()


def _buffer_save(icon=None, item=None):
    # Never run this on the hotkey thread: a 5-minute save takes 10-20 s,
    # and before this any exception there killed Alt+G/Alt+P for good.
    threading.Thread(target=_save_replay, daemon=True).start()


def _save_replay():
    if not _save_lock.acquire(blocking=False):
        _log("replay save skipped: already running")
        _notify("A replay save is already running")
        return
    tmp = listfile = wav = None
    try:
        if not _buffer_on:
            _log("replay save skipped: buffer off")
            _notify("Replay buffer is off")
            return
        segs = sorted(BUFFER_DIR.glob("buf_*.mkv"))
        if segs and segs[-1].stat().st_size == 0:
            segs = segs[:-1]  # just created, no header yet
        # An open segment's mtime keeps moving while concat reads it, which
        # made one replay play ~0.1 s late. Use only finished segments.
        if segs and time.time() - segs[-1].stat().st_mtime < 0.3:
            segs = segs[:-1]
        chosen = segs[-SAVE_SEGMENTS:]
        if not chosen:
            _log("replay save skipped: no segments yet")
            _notify("Buffer still filling - try again shortly")
            return
        _log(f"replay save: {len(chosen)} segments, newest {chosen[-1].name}, "
             f"age {time.time() - chosen[-1].stat().st_mtime:.1f}s")
        # The save needs room for the concat and the MP4 at the same time.
        # Filling the disk mid-save leaves a broken clip in Captures.
        need = sum(p.stat().st_size for p in chosen) * 2 + 2 * 2**30
        try:
            free = shutil.disk_usage(OUT_DIR).free
            if free < need:
                _log(f"replay save skipped: {free / 2**30:.1f} GB free, "
                     f"{need / 2**30:.1f} GB needed")
                _notify("Not enough disk space to save this replay")
                return
        except OSError:
            pass
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out = OUT_DIR / f"{stamp}_replay.mp4"
        tmp = BUFFER_DIR / f"save_{stamp}.mkv"
        listfile = BUFFER_DIR / f"list_{stamp}.txt"

        def concat(parts):
            # Every closed segment holds exactly SEGMENT_SECONDS of CFR video.
            # File ctimes drift by tens of ms, and using those deltas as the
            # concat durations crams boundary frames onto the same timestamp
            # (visible judder every 2 s), so declare the exact segment length.
            lines = []
            for p in parts:
                lines += [f"file '{p.as_posix()}'",
                          f"duration {SEGMENT_SECONDS:.6f}"]
            listfile.write_text("\n".join(lines), encoding="utf-8")
            return subprocess.run(
                [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "concat", "-safe", "0", "-i", str(listfile),
                 "-c", "copy", str(tmp)],
                creationflags=subprocess.CREATE_NO_WINDOW)

        _notify("Saving last minutes...")
        r = concat(chosen)
        if r.returncode != 0 and len(chosen) > 1:
            # newest segment is still being written; retry without it
            chosen = chosen[:-1]
            r = concat(chosen)
        if r.returncode != 0 or not tmp.exists():
            _log("replay save: concat failed")
            _notify("Replay save failed - see recorder.log")
            return
        dur = _probe_duration(tmp)
        _log(f"replay save: concat {dur:.1f}s")
        if dur > 1:
            # The segment's mtime is its close time; the muxer and loopback
            # capture both land ~0.1 s after the last frame. Add it back the
            # same way the manual path does, or replay audio plays late.
            wav = _buffer_audio_tail(dur, chosen[-1].stat().st_mtime + 0.105)
        cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(tmp)]
        if wav:
            cmd += ["-i", str(wav), "-map", "0:v", "-map", "1:a",
                    "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest"]
        cmd += ["-c:v", "copy", "-fps_mode", "passthrough",
                "-movflags", "+faststart", str(out)]
        r = subprocess.run(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0:
            _log(f"replay saved: {out.name} ({dur:.0f}s)")
            _notify(f"Saved {out.name}")
        else:
            _log("replay save: mux failed")
            out.unlink(missing_ok=True)
            _notify("Replay save failed - see recorder.log")
    except Exception as e:
        _log(f"replay save failed: {e!r}")
        _notify("Replay save failed - see recorder.log")
    finally:
        try:
            for p in (tmp, listfile, wav):
                if p:
                    p.unlink(missing_ok=True)
        finally:
            _save_lock.release()


def _toggle_buffer(icon=None, item=None):
    if _recording:
        _notify("Stop the recording before changing the buffer")
        return
    if _buffer_on:
        _buffer_stop_all()
        _notify("Replay buffer off")
    elif _save_lock.locked():
        _notify("Replay save in progress - try again")
    elif _buffer_start():
        _notify("Replay buffer on")
    else:
        _notify("Replay buffer is still stopping - try again")


def _start():
    global _proc, _recording, _final, _video, _wav, _audio_thread
    global _audio_ok, _audio_from_buffer
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    _final = OUT_DIR / f"{stamp}.mp4"
    # Keep in-progress files out of Captures: Studio lists that folder and
    # would otherwise show a growing clip and rebuild its thumbnail every 2 s.
    _video = BUFFER_DIR / f"{stamp}.video.mkv"
    _wav = BUFFER_DIR / f"{stamp}.wav"
    # Windows rejects a second loopback capture, so reuse the buffer's audio
    _audio_from_buffer = bool(_buffer_on and _buf_audio_thread
                              and _buf_audio_thread.is_alive())
    _audio_ok = _audio_from_buffer
    _audio_ready.clear()
    _audio_stop.clear()
    if not _audio_from_buffer:
        _audio_thread = threading.Thread(target=_audio_loop, daemon=True)
        _audio_thread.start()
        _audio_ready.wait(3)
    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i",
        # Same on-GPU pipeline as the replay buffer; see _buffer_supervisor.
        f"ddagrab=output_idx=0:framerate={RECORD_FPS}:dup_frames=true",
        "-c:v", CODEC, "-preset", "p1", "-tune", "hq",
        "-rc", "vbr", "-b:v", BITRATE, "-maxrate", "250M", "-bufsize", "500M",
        "-bf", "0",
        "-g", str(RECORD_FPS * 2),
        "-forced-idr", "1",
        "-force_key_frames", "expr:gte(t,n_forced*2)",
        "-fps_mode", "cfr",
        "-f", "matroska", str(_video),
    ]
    _proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    _log(f"recording started: {_final.name}")
    _recording = True
    threading.Thread(target=_watch_recording, args=(_proc,), daemon=True).start()
    if _icon:
        _icon.icon = _icon_image("red")
        _icon.title = f"Recording - {_final.name}"
    _notify(f"Recording -> {_final.name}" + ("" if _audio_ok else " (NO AUDIO)"))


def _watch_recording(p):
    # ffmpeg can exit mid-recording (second NVENC session refused, disk full,
    # driver reset). Without this the tray stayed red and the clip was lost.
    while _recording and _proc is p:
        time.sleep(1)
        rc = p.poll()
        if rc is not None:
            # _stop may have finished during the sleep above; only a real
            # early exit should be reported (and finalized).
            if not _recording or _proc is not p:
                return
            _log(f"recording ffmpeg exited early ({rc})")
            _notify("Recording stopped unexpectedly - saving what was written")
            _toggle()
            return


def _stop():
    global _proc, _recording, _wav, _audio_ok
    p, _proc, _recording = _proc, None, False
    _audio_stop.set()
    if _audio_thread:
        _audio_thread.join(5)
    # Anchor the audio before asking ffmpeg to quit: finalizing the file can
    # take a second, and using the time *after* that made audio play late.
    if p and p.poll() is None:
        try:
            p.stdin.write(b"q")
            p.stdin.flush()
            p.wait(timeout=15)
        except Exception:
            p.kill()
    if _icon:
        _icon.icon = _icon_image("gray")
        _icon.title = "Fragloop - Alt+G"
    if not (_video and _video.exists() and _video.stat().st_size):
        _notify("Nothing recorded - see recorder.log")
        return
    if _audio_from_buffer:
        vdur = _probe_duration(_video)
        # Anchor to when ffmpeg closed the file, not the hotkey press:
        # ffmpeg needs ~0.3 s to start, and anchoring to the press made
        # manual clips play 0.3 s late. Matroska's close then lands ~0.1 s
        # after its last mtime update, so add that back (measured with a
        # callback-timed beep); replay saves need the same correction.
        end_mtime = _video.stat().st_mtime + 0.105
        _wav = _buffer_audio_tail(vdur, end_mtime, manual=True) if vdur > 1 else None
        _audio_ok = _wav is not None
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(_video)]
    if _audio_ok and _wav and _wav.exists() and _wav.stat().st_size > 44:
        cmd += ["-i", str(_wav), "-map", "0:v", "-map", "1:a",
                "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest"]
    cmd += ["-c:v", "copy", "-fps_mode", "passthrough",
            "-movflags", "+faststart",
            str(_final)]
    r = subprocess.run(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode == 0:
        _video.unlink(missing_ok=True)
        if _wav:
            try:
                _wav.unlink(missing_ok=True)
            except OSError:
                pass  # still held by the audio thread; harmless leftover
        _log(f"recording saved: {_final.name}")
        _notify(f"Saved {_final.name}")
    else:
        _log(f"recording mux failed ({r.returncode})")
        _final.unlink(missing_ok=True)
        _video.rename(_final.with_suffix(".mkv"))
        _notify("Saved as .mkv (mux failed)")


def _toggle(icon=None, item=None):
    with _lock:
        _stop() if _recording else _start()


def _hotkey_loop():
    user32 = ctypes.windll.user32
    keys = [("Alt+G", HOTKEY_MODS, HOTKEY_VK),
            ("Ctrl+Alt+G", 0x0002 | HOTKEY_MODS, HOTKEY_VK),
            ("Alt+P", HOTKEY_MODS, 0x50),
            ("Ctrl+Alt+P", 0x0002 | HOTKEY_MODS, 0x50)]
    ok = False
    for i, (name, mods, vk) in enumerate(keys, start=1):
        if user32.RegisterHotKey(None, i, mods, vk):
            _log(f"hotkey {name} registered")
            ok = True
        else:
            _log(f"hotkey {name} NOT registered - taken by another app")
    if not ok:
        _notify("Hotkeys taken by another app - close it or edit HOTKEY_VK")
        return
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            hid = int(msg.wParam)
            try:
                _log(f"hotkey {hid} pressed")
                if hid >= 3:
                    _buffer_save()
                else:
                    _toggle()
            except Exception as e:
                # A broken action must never take the hotkey loop down with it.
                _log(f"hotkey action failed: {e!r}")


def _quit(icon=None, item=None):
    if _recording:
        _stop()
    _buffer_stop_all()
    _icon.stop()


def main():
    global _icon
    if ctypes.windll.kernel32.CreateMutexW(None, False, "FragloopTray"):
        if ctypes.windll.kernel32.GetLastError() == 183:  # already running
            return
    _icon = pystray.Icon(
        "Fragloop",
        _icon_image("gray"),
        "Fragloop - Alt+G",
        pystray.Menu(
            pystray.MenuItem("Start / stop (Alt+G)", _toggle, default=True),
            pystray.MenuItem("Save replay now (Alt+P)", _buffer_save),
            pystray.MenuItem("Replay buffer on/off", _toggle_buffer,
                             checked=lambda item: _buffer_on),
            pystray.MenuItem("Open captures folder", lambda: os.startfile(str(OUT_DIR))),
            pystray.MenuItem("Quit", _quit),
        ),
    )
    threading.Thread(target=_hotkey_loop, daemon=True).start()
    _buffer_start()
    _icon.run()


if __name__ == "__main__":
    main()
