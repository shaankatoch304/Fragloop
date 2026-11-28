"""Fragloop - tiny tray recorder.

Alt+G starts/stops. Records the primary display at its current refresh rate
with NVENC straight from the GPU and system audio via WASAPI loopback,
optionally mixed with a microphone chosen in Preferences. Alt+P saves the last
1-15 minutes (5 by default) from the replay buffer. Alt+F saves a PNG
screenshot.
Files land in Videos\\Fragloop, split into Screenshots, Replay Buffer and
Recordings.
"""
import ctypes
import ctypes.wintypes
import datetime
import itertools
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
import winreg
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
OUT_DIR = Path(os.environ["USERPROFILE"]) / "Videos" / "Fragloop"
SHOT_DIR = OUT_DIR / "Screenshots"
REPLAY_DIR = OUT_DIR / "Replay Buffer"
RECORD_DIR = OUT_DIR / "Recordings"

RECORD_FPS = 120               # constant output rate; VFR 50-240 fps footage
                               # judders in players, CFR 120 plays smooth
VIDEO_BITRATE = "80M"          # average video rate; 7 min ≈ 4.2 GB with audio
VIDEO_MAXRATE = "110M"         # peaks for complex motion; average stays ~80
CODEC = "hevc_nvenc"           # 10-bit HEVC: ~2x smaller than H.264 at the
                               # same quality, but needs the HEVC extension
HOTKEY_VK = 0x47               # G
SHOT_VK = 0x46                 # F
HOTKEY_MODS = 0x0001 | 0x4000  # Alt | MOD_NOREPEAT

BUFFER_DIR = Path(os.environ["LOCALAPPDATA"]) / "Fragloop" / "buffer"
REPLAY_SECONDS = 300           # default; Preferences can set 60-900 s
SEGMENT_SECONDS = 2            # small chunks keep the freshest tail on disk
REPLAY_SECONDS_OVERRIDE = None  # tests pin a short buffer
AUDIO_ROTATE_SECONDS = 300     # rolling audio chunk size
KEEP_AUDIO_FILES = 12          # ~1 h, so a long manual recording keeps its audio
PROBE_TIMEOUT = 10             # seconds; a stuck ffprobe must not wedge a save
CONCAT_TIMEOUT = 300           # seconds; enough for a 15-minute replay concat
MUX_TIMEOUT = 600              # seconds; video copy + AAC + faststart
SHOT_TIMEOUT = 20              # seconds; one frame from the GPU
STOP_TIMEOUT = 15              # seconds; let ffmpeg finalize on stop
LOG_MAX_BYTES = 1_000_000      # rotate so the log cannot grow without bound
RECORD_MIN_FREE = 2 * 2**30    # don't start a clip that cannot fit
TEMP_MAX_AGE = 3600            # leave active tails alone; prune crashed ones later


def _replay_seconds():
    """Read the Preferences slider without pulling Qt into the engine."""
    if REPLAY_SECONDS_OVERRIDE is not None:
        return int(REPLAY_SECONDS_OVERRIDE)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Fragloop\Studio") as key:
            value, _ = winreg.QueryValueEx(key, "replay_seconds")
        return min(900, max(60, int(value)))
    except (OSError, ValueError, TypeError):
        return REPLAY_SECONDS


def _mic_setting():
    """None when the mic is off, else the chosen device name ('' = default)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Fragloop\Studio") as key:
            enabled, _ = winreg.QueryValueEx(key, "mic_enabled")
            try:
                device, _ = winreg.QueryValueEx(key, "mic_device")
            except OSError:
                device = ""
    except OSError:
        return None
    if str(enabled).strip().lower() not in ("1", "true", "yes"):
        return None
    return str(device).strip()


def _set_setting(name, value):
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Fragloop\Studio") as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
    except OSError as e:
        _log(f"setting write failed: {e!r}")


def _mic_device(pa, name):
    """The chosen WASAPI input, or Windows' default input for ''."""
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    if name:
        for dev in pa.get_device_info_generator():
            if (dev["hostApi"] == wasapi["index"]
                    and dev["maxInputChannels"] > 0
                    and not dev.get("isLoopbackDevice")
                    and dev["name"] == name):
                return dev
        return None
    index = wasapi.get("defaultInputDevice", -1)
    try:
        dev = pa.get_device_info_by_index(index)
        return None if dev.get("isLoopbackDevice") else dev
    except Exception:
        return None


def _default_input_name(pa):
    try:
        return _mic_device(pa, "")["name"]
    except Exception:
        return None


def _save_segments():
    return _replay_seconds() // SEGMENT_SECONDS + 1


def _keep_segments():
    return _save_segments() + 5

# Seconds of audio to trim from the end, measured with a white flash + 1 kHz
# tone that was primed so PyAudio's cold start could not skew the timing.
AUDIO_SYNC_BY_DEVICE = {"JBL": -0.084, "Realtek": -0.025}
# Manual (Alt+G) files close differently; Bluetooth needed its own value.
AUDIO_MANUAL_SYNC_BY_DEVICE = {"JBL": 0.088}
AUDIO_SYNC_SECONDS = -0.025    # fallback when the endpoint is unknown
# Microphones report later than loopback and vary by endpoint. This is a
# starting point; the per-device table above also applies to mic names.
MIC_SYNC_SECONDS = -0.05
# The file mtime is when the muxer last wrote, which is before the encoder
# has flushed its queue. Measured with a flash + 1 kHz tone: HEVC's queue
# trails a 2 s segment by ~0.03 s but a final file close by ~0.37 s.
REPLAY_MTIME_LAG = 0.025
MANUAL_MTIME_LAG = 0.365

LOG.parent.mkdir(parents=True, exist_ok=True)
for _capture_dir in (SHOT_DIR, REPLAY_DIR, RECORD_DIR):
    _capture_dir.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
_log_lock = threading.Lock()
_quitting = False
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

_mic_stop = threading.Event()
_mic_ready = threading.Event()
_mic_thread = None
_mic_fh = None
_mic_lock = threading.Lock()
_mic_from_buffer = False
_mic_manual = False
_mic_state = {"pa": None, "stream": None, "dev": None, "rate": 48000,
              "ch": 1, "t0": 0.0, "frames": 0}
_tail_seq = itertools.count()
# PortAudio initializes globally; two PyAudio instances opening at once (the
# loopback and mic loops both start with the buffer) can fail or crash.
_pa_lock = threading.RLock()


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
        with _log_lock:
            try:
                if LOG.stat().st_size > LOG_MAX_BYTES:
                    LOG.replace(LOG.with_name(LOG.name + ".1"))
            except OSError:
                pass
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


def _pcm_pad(fh, st):
    """Fill an open PCM file up to wall clock. Caller holds its lock."""
    # Keep a small reserve so silence is never written ahead of live
    # samples that arrive a callback late.
    gap = (int((time.monotonic() - st["t0"]) * st["rate"])
           - st["frames"] - int(st["rate"] * 0.05))
    if gap > 0:
        n = min(gap, st["rate"])
        fh.write(b"\x00" * (n * st["ch"] * 2))
        st["frames"] += n


def _buf_audio_pad():
    """Fill the open file up to wall clock. Call with _buf_audio_lock held."""
    if _buf_audio_fh is not None:
        _pcm_pad(_buf_audio_fh, _audio_state)


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
    with _pa_lock:
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
    with _pa_lock:
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
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    while not _buf_audio_stop.is_set():
        try:
            if st["stream"] is None:
                dev = _buf_audio_open()
                _log(f"buffer audio started: {dev['name']}")
            time.sleep(0.05)
            with _buf_audio_lock:
                _buf_audio_pad()
                # WASAPI sends no callbacks while the endpoint is silent, so
                # rotation cannot live only in the callback or one PCM file
                # grows for the whole silent stretch.
                if (_buf_audio_fh is not None and st["rate"] > 0
                        and st["frames"] >= st["rate"] * AUDIO_ROTATE_SECONDS):
                    _buf_audio_new_file()
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


def _mic_new_file(rate=None, channels=None):
    """Pad/close the old PCM and start a new one. Call with the lock held."""
    global _mic_fh
    st = _mic_state
    if _mic_fh is not None:
        try:
            _pcm_pad(_mic_fh, st)
            _mic_fh.close()
        except Exception:
            pass
    if rate is not None:
        st["rate"], st["ch"] = int(rate), int(channels)
    _mic_fh = open(BUFFER_DIR / f"mic_{time.time():.3f}.pcm", "wb")
    st["t0"] = time.monotonic()
    st["frames"] = 0


def _mic_cb(in_data, frame_count, time_info, status):
    st = _mic_state
    with _mic_lock:
        try:
            if _mic_fh is None:
                return None, pyaudio.paContinue
            _pcm_pad(_mic_fh, st)
            _mic_fh.write(in_data)
            st["frames"] += len(in_data) // (st["ch"] * 2)
            if st["frames"] >= st["rate"] * AUDIO_ROTATE_SECONDS:
                _mic_new_file()
        except Exception:
            pass
    return None, pyaudio.paContinue


def _mic_close():
    st = _mic_state
    with _pa_lock:
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


def _mic_open(setting):
    """(Re)create PyAudio and the chosen mic stream at its native format."""
    global _mic_fh
    st = _mic_state
    with _pa_lock:
        _mic_close()
        st["pa"] = pyaudio.PyAudio()
        dev = _mic_device(st["pa"], setting)
        if dev is None:
            raise RuntimeError("chosen microphone is not connected")
        rate = int(dev["defaultSampleRate"])
        ch = max(1, min(2, int(dev["maxInputChannels"])))
        with _mic_lock:
            same = _mic_fh is not None and rate == st["rate"] and ch == st["ch"]
            if not same:
                if _mic_fh is not None:
                    # Old chunks are in another format; mixing rates would
                    # corrupt the timeline, so drop them and start clean.
                    try:
                        _pcm_pad(_mic_fh, st)
                    except Exception:
                        pass
                    try:
                        _mic_fh.close()
                    except Exception:
                        pass
                    _mic_fh = None
                    for p in BUFFER_DIR.glob("mic_*.pcm"):
                        try:
                            p.unlink()
                        except OSError:
                            pass
                _mic_new_file(rate, ch)
        st["dev"] = dev
        _log(f"microphone opening: {dev['name']} {rate} Hz {ch} ch "
             f"(index {dev['index']})")
        st["stream"] = st["pa"].open(
            format=pyaudio.paInt16, channels=ch, rate=rate,
            frames_per_buffer=1024, input=True,
            input_device_index=dev["index"],
            stream_callback=_mic_cb, start=False)
        st["stream"].start_stream()
    _mic_ready.set()
    return dev


def _mic_loop():
    """Capture the chosen mic into rotating PCM chunks, or idle when off."""
    global _mic_fh
    st = _mic_state
    last_check = 0.0
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    while not _mic_stop.is_set():
        try:
            setting = _mic_setting()
            if setting is None:
                if st["stream"] is not None:
                    _mic_close()
                    with _mic_lock:
                        try:
                            if _mic_fh:
                                _pcm_pad(_mic_fh, st)
                                _mic_fh.close()
                        except Exception:
                            pass
                        _mic_fh = None
                time.sleep(0.2)
                continue
            if st["stream"] is None:
                dev = _mic_open(setting)
                _log(f"microphone started: {dev['name']}")
            time.sleep(0.05)
            with _mic_lock:
                if _mic_fh is not None:
                    _pcm_pad(_mic_fh, st)
                    if st["frames"] >= st["rate"] * AUDIO_ROTATE_SECONDS:
                        _mic_new_file()
                    try:
                        _mic_fh.flush()
                    except Exception:
                        pass
            now = time.monotonic()
            # Follow a device change without restarting the buffer.
            if now - last_check >= 5:
                last_check = now
                want = setting or _default_input_name(st["pa"])
                if want and st["dev"] and want != st["dev"]["name"]:
                    _log(f"microphone switched: {st['dev']['name']} -> "
                         f"{want}")
                    _mic_open(want)
        except Exception as e:
            _log(f"microphone error: {e!r}")
            _mic_close()
            time.sleep(1)
    _mic_close()
    with _mic_lock:
        try:
            if _mic_fh:
                _mic_fh.close()
        except Exception:
            pass
        _mic_fh = None


def _pcm_tail(pattern, rate, channels, dur, end_time, sync):
    """Build a WAV holding the last `dur` seconds of `pattern` PCM files."""
    frame_bytes = channels * 2
    need = int(dur * rate) * frame_bytes
    # video ends when the last closed segment was finished; drop the audio
    # recorded since then, *before* cutting to `need`. The sync value trims
    # a little extra because captured audio runs early vs. the video.
    drop = max(0, int((time.time() - end_time + sync) * rate)) * frame_bytes
    keep = need + drop
    files = sorted(BUFFER_DIR.glob(pattern))
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
    path = BUFFER_DIR / f"tail_{next(_tail_seq)}_{time.time():.3f}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return path


def _buffer_audio_tail(dur, end_time, manual=False):
    """Build a WAV holding the last `dur` seconds of buffer audio."""
    with _buf_audio_lock:
        rate, channels = _buf_rate, _buf_channels
        dev_name = (_audio_state["dev"] or {}).get("name", "")
    sync = None
    if manual:
        sync = next((v for k, v in AUDIO_MANUAL_SYNC_BY_DEVICE.items()
                     if k in dev_name), None)
    if sync is None:
        sync = next((v for k, v in AUDIO_SYNC_BY_DEVICE.items()
                     if k in dev_name), AUDIO_SYNC_SECONDS)
    with _buf_audio_lock:
        try:
            _buf_audio_fh.flush()
        except Exception:
            pass
    return _pcm_tail("aud_*.pcm", rate, channels, dur, end_time, sync)


def _mic_tail(dur, end_time, manual=False):
    """Build the mic WAV for the same window, or None when it is off."""
    if _mic_setting() is None:
        return None
    with _mic_lock:
        rate, channels = _mic_state["rate"], _mic_state["ch"]
        dev_name = (_mic_state["dev"] or {}).get("name", "")
        try:
            if _mic_fh:
                _mic_fh.flush()
        except Exception:
            pass
    if not any(BUFFER_DIR.glob("mic_*.pcm")):
        return None
    sync = None
    if manual:
        sync = next((v for k, v in AUDIO_MANUAL_SYNC_BY_DEVICE.items()
                     if k in dev_name), None)
    if sync is None:
        sync = next((v for k, v in AUDIO_SYNC_BY_DEVICE.items()
                     if k in dev_name), MIC_SYNC_SECONDS)
    return _pcm_tail("mic_*.pcm", rate, channels, dur, end_time, sync)


def _audio_args(wav, mic):
    """ffmpeg audio inputs and maps for loopback and/or mic (video is 0)."""
    inputs = []
    if wav:
        inputs += ["-i", str(wav)]
    if mic:
        inputs += ["-i", str(mic)]
    if wav and mic:
        return inputs, ["-filter_complex",
                        "[1:a][2:a]amix=inputs=2:duration=longest:"
                        "normalize=0,alimiter=limit=0.95:level=false[a]",
                        "-map", "0:v", "-map", "[a]"]
    if inputs:
        return inputs, ["-map", "0:v", "-map", "1:a"]
    return [], []


def _probe_duration(path):
    try:
        out = subprocess.run(
            [str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def _prune_buffer():
    # A long save reads the oldest segments for many seconds; deleting them
    # underneath ffmpeg either breaks the save or kills this thread via
    # PermissionError. The keep limit leaves only 10 s of slack beyond a save,
    # so skip deletion entirely while one is running.
    if not _save_lock.locked():
        for pat, keep in (("buf_*.mkv", _keep_segments()),
                          ("aud_*.pcm", KEEP_AUDIO_FILES),
                          ("mic_*.pcm", KEEP_AUDIO_FILES),
                          ("tail_*.wav", 0),
                          ("save_*.mkv", 0),
                          ("list_*.txt", 0)):
            files = sorted(BUFFER_DIR.glob(pat))
            if keep:
                files = files[:-keep]
            for p in files:
                if not keep:
                    try:
                        if time.time() - p.stat().st_mtime < TEMP_MAX_AGE:
                            continue
                    except OSError:
                        continue
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
    try:
        # 80 Mbit/s is about 0.6 GB/min, so a longer buffer needs more room.
        reserve = max(8, 2 + _replay_seconds() / 60 * 0.75) * 2**30
        if shutil.disk_usage(BUFFER_DIR).free < reserve:
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
            "-profile:v", "main10", "-highbitdepth", "1",
            "-rc", "vbr", "-b:v", VIDEO_BITRATE,
            "-maxrate", VIDEO_MAXRATE, "-bufsize", "220M",
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
        log = None
        try:
            log = open(BUFFER_DIR / "ffmpeg.log", "ab")
            _buffer_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as e:
            _log(f"buffer ffmpeg could not start: {e!r}")
            _notify("Replay buffer could not start - see recorder.log")
            _buffer_on = False
            return
        finally:
            if log:
                log.close()  # the child keeps its own handle
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
    global _buffer_on, _buffer_thread, _buf_audio_thread, _mic_thread
    if _save_lock.locked():
        return False
    for t in (_buffer_thread, _buf_audio_thread, _mic_thread):
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
    _mic_stop.clear()
    _buffer_on = True
    _buffer_thread = threading.Thread(target=_buffer_supervisor, daemon=True)
    _buffer_thread.start()
    _buf_audio_thread = threading.Thread(target=_buffer_audio_loop, daemon=True)
    _buf_audio_thread.start()
    _mic_thread = threading.Thread(target=_mic_loop, daemon=True)
    _mic_thread.start()
    _log("replay buffer started")
    return True


def _buffer_stop_all():
    global _buffer_on
    _buffer_on = False
    _buffer_stop.set()
    _buf_audio_stop.set()
    _mic_stop.set()
    # wait so ffmpeg exits cleanly and no orphan keeps writing to disk
    if _buffer_thread and _buffer_thread is not threading.current_thread():
        _buffer_thread.join(15)
    if _buf_audio_thread and _buf_audio_thread is not threading.current_thread():
        _buf_audio_thread.join(5)
    if _mic_thread and _mic_thread is not threading.current_thread():
        _mic_thread.join(5)


_save_lock = threading.Lock()


def _buffer_save(icon=None, item=None):
    # Never run this on the hotkey thread: a long save takes 10-20 s, and
    # before this any exception there killed Alt+G/Alt+P for good.
    threading.Thread(target=_save_replay, daemon=True).start()


def _concat_line(path):
    """Quote a path for ffmpeg's concat demuxer; a raw quote would inject."""
    return "file '" + path.as_posix().replace("'", "'\\''") + "'"


def _save_replay():
    if not _save_lock.acquire(blocking=False):
        _log("replay save skipped: already running")
        _notify("A replay save is already running")
        return
    tmp = listfile = wav = mic = part = None
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
        chosen = segs[-_save_segments():]
        if not chosen:
            _log("replay save skipped: no segments yet")
            _notify("Buffer still filling - try again shortly")
            return
        _log(f"replay save: {len(chosen)} segments, newest {chosen[-1].name}, "
             f"age {time.time() - chosen[-1].stat().st_mtime:.1f}s")
        # The save needs room for the concat and the MP4 at the same time.
        # Filling the disk mid-save leaves a broken clip in the replay folder.
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
        out = REPLAY_DIR / f"{stamp}_replay.mp4"
        part = out.with_name(out.stem + ".part.mp4")
        n = 2
        while out.exists() or part.exists():
            out = REPLAY_DIR / f"{stamp}_replay_{n}.mp4"
            part = out.with_name(out.stem + ".part.mp4")
            n += 1
        # Mux beside the final name, then rename, so the library shows the
        # replay the moment it is complete instead of hiding it for 2 s.
        try:
            part.touch()  # show the "Saving…" row before the concat/mux
        except OSError:
            pass  # the mux will report the real failure
        tmp = BUFFER_DIR / f"save_{stamp}.mkv"
        listfile = BUFFER_DIR / f"list_{stamp}.txt"

        def concat(parts):
            # Every closed segment holds exactly SEGMENT_SECONDS of CFR video.
            # File ctimes drift by tens of ms, and using those deltas as the
            # concat durations crams boundary frames onto the same timestamp
            # (visible judder every 2 s), so declare the exact segment length.
            lines = []
            for p in parts:
                lines += [_concat_line(p),
                          f"duration {SEGMENT_SECONDS:.6f}"]
            listfile.write_text("\n".join(lines), encoding="utf-8")
            return subprocess.run(
                [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "concat", "-safe", "0", "-i", str(listfile),
                 "-c", "copy", str(tmp)],
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=CONCAT_TIMEOUT)

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
            # The segment mtime trails its last frame by the encoder queue.
            # Add that lag back or replay audio plays early.
            wav = _buffer_audio_tail(dur, chosen[-1].stat().st_mtime
                                     + REPLAY_MTIME_LAG)
            mic = _mic_tail(dur, chosen[-1].stat().st_mtime
                            + REPLAY_MTIME_LAG)
        cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(tmp)]
        ain, amap = _audio_args(wav, mic)
        cmd += ain + amap
        if ain:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest"]
        cmd += ["-c:v", "copy", "-tag:v", "hvc1", "-fps_mode", "passthrough",
                "-movflags", "+faststart", str(part)]
        r = subprocess.run(cmd, creationflags=subprocess.CREATE_NO_WINDOW,
                           timeout=MUX_TIMEOUT)
        if r.returncode == 0:
            os.replace(part, out)
            _log(f"replay saved: {out.name} ({dur:.0f}s)")
            _notify(f"Saved {out.name}")
        else:
            _log("replay save: mux failed")
            _notify("Replay save failed - see recorder.log")
    except Exception as e:
        _log(f"replay save failed: {e!r}")
        _notify("Replay save failed - see recorder.log")
    finally:
        try:
            for p in (tmp, listfile, wav, mic, part):
                if p:
                    p.unlink(missing_ok=True)
        finally:
            _save_lock.release()


def _screenshot(icon=None, item=None):
    # ffmpeg takes a moment; keep the hotkey loop free.
    threading.Thread(target=_take_screenshot, daemon=True).start()


def _take_screenshot():
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = SHOT_DIR / f"{stamp}_screenshot.png"
    tmp = out.with_name(out.stem + ".part.png")
    n = 2
    while out.exists() or tmp.exists():
        out = SHOT_DIR / f"{stamp}_screenshot_{n}.png"
        tmp = out.with_name(out.stem + ".part.png")
        n += 1
    # Write beside the final name, then rename, so the library never shows a
    # half-written PNG.
    try:
        r = subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
             # A fast input rate makes the first frame ready at once.
             "-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=60",
             "-vf", "hwdownload,format=bgra", "-frames:v", "1", str(tmp)],
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=SHOT_TIMEOUT)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(tmp, out)
            _log(f"screenshot saved: {out.name}")
            _notify(f"Screenshot saved: {out.name}")
            return
        _log(f"screenshot failed ({r.returncode})")
    except Exception as e:
        _log(f"screenshot failed: {e!r}")
    tmp.unlink(missing_ok=True)
    _notify("Screenshot failed - see recorder.log")


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


def _toggle_mic(icon=None, item=None):
    on = _mic_setting() is not None
    _set_setting("mic_enabled", "false" if on else "true")
    _notify("Microphone off" if on else "Microphone on")


def _recording_path(stamp):
    """A name that will not replace an existing clip or its temp file."""
    out = RECORD_DIR / f"{stamp}.mp4"
    n = 2
    while (out.exists() or out.with_suffix(".mkv").exists()
           or out.with_name(out.stem + ".part.mp4").exists()):
        out = RECORD_DIR / f"{stamp}_{n}.mp4"
        n += 1
    return out


def _has_room(folder, need):
    """True when free space is unknown; only a known shortage blocks a save."""
    try:
        return shutil.disk_usage(folder).free >= need
    except OSError:
        return True


def _start():
    global _proc, _recording, _final, _video, _wav, _audio_thread
    global _audio_ok, _audio_from_buffer, _mic_from_buffer, _mic_manual
    global _mic_thread
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    if not _has_room(RECORD_DIR, RECORD_MIN_FREE):
        _log("recording not started: low disk space")
        _notify("Not enough disk space to start recording")
        return
    _final = _recording_path(stamp)
    # Keep in-progress files out of the library folders: Studio would
    # otherwise show a growing clip and rebuild its thumbnail every 2 s.
    _video = BUFFER_DIR / f"{stamp}.video.mkv"
    _wav = BUFFER_DIR / f"{stamp}.wav"
    # Windows rejects a second loopback capture, so reuse the buffer's audio
    _audio_from_buffer = bool(_buffer_on and _buf_audio_thread
                              and _buf_audio_thread.is_alive())
    _mic_from_buffer = bool(_buffer_on and _mic_thread
                            and _mic_thread.is_alive())
    mic_on = _mic_setting() is not None
    _mic_manual = bool(mic_on and not _mic_from_buffer)
    if _mic_manual:
        _mic_ready.clear()
        _mic_stop.clear()
        _mic_thread = threading.Thread(target=_mic_loop, daemon=True)
        _mic_thread.start()
        _mic_ready.wait(2)
    _audio_ok = _audio_from_buffer or mic_on
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
        "-profile:v", "main10", "-highbitdepth", "1",
        "-rc", "vbr", "-b:v", VIDEO_BITRATE,
        "-maxrate", VIDEO_MAXRATE, "-bufsize", "220M",
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
    global _proc, _recording, _wav, _audio_ok, _mic_manual, _mic_from_buffer
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
            p.wait(timeout=STOP_TIMEOUT)
        except Exception:
            p.kill()
    # Stop the manual mic only after ffmpeg flushed its queue, so the mic
    # covers the same last frames as the video instead of ending early.
    if _mic_manual:
        _mic_stop.set()
        if _mic_thread:
            _mic_thread.join(3)
    if _icon:
        _icon.icon = _icon_image("gray")
        _icon.title = "Fragloop - Alt+G"
    if not (_video and _video.exists() and _video.stat().st_size):
        _notify("Nothing recorded - see recorder.log")
        return
    mic = None
    if _audio_from_buffer or _mic_from_buffer or _mic_manual:
        vdur = _probe_duration(_video)
        # Anchor to when ffmpeg closed the file, not the hotkey press:
        # ffmpeg needs ~0.3 s to start, and anchoring to the press made
        # manual clips play 0.3 s late. The close also flushes the encoder
        # queue, so add its measured lag back before cutting the audio tail.
        end_mtime = _video.stat().st_mtime + MANUAL_MTIME_LAG
        if _audio_from_buffer:
            _wav = (_buffer_audio_tail(vdur, end_mtime, manual=True)
                    if vdur > 1 else None)
            _audio_ok = _wav is not None
        if vdur > 1 and (_mic_from_buffer or _mic_manual):
            mic = _mic_tail(vdur, end_mtime, manual=True)
        if _mic_manual:
            for pcm in BUFFER_DIR.glob("mic_*.pcm"):
                try:
                    pcm.unlink(missing_ok=True)
                except OSError:
                    pass
    # Mux beside the final name, then rename, so the clip appears the moment
    # it is complete instead of being hidden as a fresh file.
    part = _final.with_name(_final.stem + ".part.mp4")
    try:
        part.touch()  # show the "Saving…" row before the mux
    except OSError:
        pass  # the mux will report the real failure
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(_video)]
    loop = _wav if _wav and _wav.exists() and _wav.stat().st_size > 44 else None
    ain, amap = _audio_args(loop, mic)
    cmd += ain + amap
    if ain:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest"]
    cmd += ["-c:v", "copy", "-tag:v", "hvc1", "-fps_mode", "passthrough",
            "-movflags", "+faststart",
            str(part)]
    try:
        r = subprocess.run(cmd, creationflags=subprocess.CREATE_NO_WINDOW,
                           timeout=MUX_TIMEOUT)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    if rc == 0:
        os.replace(part, _final)
        _video.unlink(missing_ok=True)
        for tmp in (_wav, mic):
            try:
                if tmp:
                    tmp.unlink(missing_ok=True)
            except OSError:
                pass  # still held by the audio thread; harmless leftover
        _log(f"recording saved: {_final.name}")
        _notify(f"Saved {_final.name}")
    else:
        _log(f"recording mux failed ({rc})")
        part.unlink(missing_ok=True)
        _final.unlink(missing_ok=True)
        _video.rename(_final.with_suffix(".mkv"))
        _notify("Saved as .mkv (mux failed)")


def _toggle(icon=None, item=None):
    # A stop waits up to 15 s for ffmpeg to close. Run it off the caller's
    # thread so the hotkey message loop keeps pumping; otherwise presses queue
    # up and replay as a burst of start/stop toggles once the stop finishes.
    if not _lock.acquire(blocking=False):
        _log("toggle ignored: previous start/stop still running")
        _notify("Recorder is busy - try again")
        return

    def run():
        try:
            if _quitting:
                return
            _stop() if _recording else _start()
        except Exception as e:
            _log(f"toggle failed: {e!r}")
        finally:
            _lock.release()

    threading.Thread(target=run, daemon=True).start()


def _hotkey_loop():
    user32 = ctypes.windll.user32
    keys = [("Alt+G", HOTKEY_MODS, HOTKEY_VK),
            ("Ctrl+Alt+G", 0x0002 | HOTKEY_MODS, HOTKEY_VK),
            ("Alt+P", HOTKEY_MODS, 0x50),
            ("Ctrl+Alt+P", 0x0002 | HOTKEY_MODS, 0x50),
            ("Alt+F", HOTKEY_MODS, SHOT_VK),
            ("Ctrl+Alt+F", 0x0002 | HOTKEY_MODS, SHOT_VK)]
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
                if hid >= 5:
                    _screenshot()
                elif hid >= 3:
                    _buffer_save()
                else:
                    _toggle()
            except Exception as e:
                # A broken action must never take the hotkey loop down with it.
                _log(f"hotkey action failed: {e!r}")


def _quit(icon=None, item=None):
    global _quitting
    _quitting = True
    # Wait for an in-flight start/stop so quitting cannot leave an orphan
    # ffmpeg writing after the engine exits.
    with _lock:
        if _recording:
            _stop()
    _buffer_stop_all()
    _icon.stop()


def _open_log(icon=None, item=None):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.touch(exist_ok=True)
        os.startfile(LOG)
    except OSError as e:
        _log(f"could not open log: {e!r}")
        _notify("Could not open recorder.log")


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
            pystray.MenuItem("Take screenshot (Alt+F)", _screenshot),
            pystray.MenuItem("Replay buffer on/off", _toggle_buffer,
                             checked=lambda item: _buffer_on),
            pystray.MenuItem("Microphone on/off", _toggle_mic,
                             checked=lambda item: _mic_setting() is not None),
            pystray.MenuItem("Open Fragloop folder", lambda: os.startfile(str(OUT_DIR))),
            pystray.MenuItem("Open recorder log", _open_log),
            pystray.MenuItem("Quit", _quit),
        ),
    )
    threading.Thread(target=_hotkey_loop, daemon=True).start()
    _buffer_start()
    _icon.run()


if __name__ == "__main__":
    main()
