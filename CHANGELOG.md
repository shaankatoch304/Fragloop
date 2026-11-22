# Changelog

## Unreleased

- Alt+G start/stop runs off the hotkey thread now: a slow stop no longer
  swallows or queues presses. Repeat presses while busy are ignored instead of
  replaying as a burst of start/stop toggles.
- Studio shows live buffer/recording status, clip count with total size, a
  persistent hotkey hint, and remembers its window size and position.
- Studio no longer re-probes clip durations on every refresh or rebuilds a
  thumbnail for a clip that is still being muxed.

## 0.1.1 - 2026-11-22

- Encode 10-bit HEVC at an 80 Mbit/s average with 110 Mbit/s peaks: a
  7-minute clip now stays under 5 GB instead of ~10 GB. Existing H.264 clips
  are unaffected.
- Retune the audio close-time anchors for HEVC: replay saves now land within a
  few milliseconds of the video and buffer-backed manual clips within ~30 ms.
- Rotate the buffer's PCM audio during silent stretches too, so one silent
  file cannot grow for hours and inflate the buffer folder.
- Show the app version in the bottom-right corner of the window.
- Prevent duplicate app windows when the exe is opened twice.

## 0.1.0 - 2026-11-20

- Initial public release.
- Windows installer and portable zip.
- GPU-direct capture (`ddagrab` -> `h264_nvenc`), constant 120 fps.
- Five-minute replay buffer with hotkey save.
- WASAPI loopback audio that follows the Windows default output device.
- Native clip library window and tray menu.
