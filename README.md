# Fragloop

GPU-direct game clip recorder for Windows, with a five-minute replay buffer.

![Fragloop Studio](docs/screenshot.png)

Fragloop records your display at a constant 120 fps straight from the GPU and
keeps the last five minutes in a rolling buffer. Press `Alt+P` and the buffer is
saved as an MP4 — no need to have been recording. `Alt+G` records manually.

## Features

- **Direct GPU pipeline** — Desktop Duplication (`ddagrab`) feeds `h264_nvenc`
  without a CPU frame copy, so fast games stay smooth.
- **Constant 120 fps** — CFR output avoids the judder VFR capture produces in
  players, even on a 240 Hz panel.
- **Five-minute replay buffer** — two-second segments, saved in seconds.
- **System audio** — WASAPI loopback, no microphone. Switches automatically when
  you change the Windows default output device.
- **Native window and tray** — clip library with thumbnails, hotkeys that work
  while a game has focus.
- **MP4 (H.264 + AAC)** — plays anywhere without extra codecs.

## Requirements

- Windows 10/11, 64-bit
- NVIDIA GPU with NVENC (Turing or newer recommended)
- ffmpeg 7 or newer: `ffmpeg.exe` and `ffprobe.exe` next to `Fragloop.exe`
- Free disk space: a five-minute replay is ~7.5 GB at the default 200 Mbit/s,
  and saving needs room for the temporary concat as well (~2x).

Tested on Windows 11, RTX 5070 Ti Laptop GPU, 2560x1600 @ 240 Hz.

## Hotkeys

| Shortcut | Action |
| --- | --- |
| `Alt+G` / `Ctrl+Alt+G` | Start / stop a manual recording |
| `Alt+P` / `Ctrl+Alt+P` | Save the last five minutes |

Clips are written to `Videos\Captures`. Settings live at the top of
`recorder.py` (`RECORD_FPS`, `BITRATE`, `REPLAY_SECONDS`, `SEGMENT_SECONDS`).

## How it works

1. `ddagrab` captures the display on the GPU and duplicates frames to a
   constant 120 fps.
2. `h264_nvenc` encodes directly into two-second Matroska segments tagged with
   forced IDR frames, so the replay buffer can be concatenated without
   re-encoding.
3. WASAPI loopback writes 48 kHz stereo PCM into rotating five-minute chunks,
   padded against the wall clock so silence does not shift the timeline.
4. A replay save concatenates the newest 151 segments, trims the matching audio
   tail, and remuxes video (copy) plus AAC audio into an MP4.
5. The engine runs as a tray process; `Fragloop.exe` opens the window and starts
   the engine as a child process, so a window crash cannot stop a recording.

## Build from source

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Put ffmpeg.exe and ffprobe.exe in the project root first.
pyinstaller --noconfirm --windowed --name Fragloop --icon icon.ico `
  --hidden-import pystray._win32 app.py
Copy-Item ffmpeg.exe,ffprobe.exe dist\Fragloop\
```

The build lands in `dist\Fragloop`. Run it from there; the app expects
`ffmpeg.exe` and `ffprobe.exe` beside the executable.

Run from source instead:

```powershell
pythonw app.py        # window + engine
python recorder.py    # engine only (tray, hotkeys, capture)
```

Development checks:

- `python selftest.py` — record four seconds and verify video + audio.
- `python buftest.py` — small replay-buffer save and container check.
- `python sendkey.py 47` — synthetic `Alt+G` (hex virtual-key code).

## Troubleshooting

**No audio in a clip.** The recorder captures the Windows *default* output
device. If the game selects a different device in its own settings, or uses
WASAPI exclusive mode, loopback will not hear it. Everything else is captured,
including Bluetooth headphones.

**Hotkeys do nothing.** Another application owns `Alt+G`/`Alt+P`. Close it or
change `HOTKEY_VK` in `recorder.py`.

**The buffer stopped by itself.** Check `%LOCALAPPDATA%\Fragloop\recorder.log`.
The usual causes are low disk space, a missing/broken ffmpeg, or another process
holding an NVENC session.

**No video encoder.** Fragloop uses `h264_nvenc` and has no software fallback.
Update the NVIDIA driver if the encoder is unavailable.

## Project layout

```text
app.py            entry point; window mode spawns the engine
recorder.py       engine: hotkeys, buffer, GPU capture, audio, saves
studio.py         PySide6 clip library window
Fragloop.spec     PyInstaller build definition
icon.ico          application icon
docs/             README screenshot
selftest.py       headless recording smoke test
buftest.py        headless buffer smoke test
sendkey.py        synthetic hotkey helper
```

## License

Source is published for reference; all rights reserved. See [LICENSE](LICENSE).

ffmpeg is not included in this repository. If you redistribute a build, follow
the license of the ffmpeg binaries you ship. Fragloop is not affiliated with
NVIDIA.
