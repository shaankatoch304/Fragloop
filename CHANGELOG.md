# Changelog

## Unreleased

- Encode 10-bit HEVC at CQ 19 with a 90 Mbit/s cap: a 7-minute clip now stays
  under 5 GB instead of ~10 GB. Existing H.264 clips are unaffected.
- Rotate the buffer's PCM audio during silent stretches too, so one silent
  file cannot grow for hours and inflate the buffer folder.

## 0.1.1 - 2026-11-21

- Fix replay saves playing audio ~70 ms late by using the same close-time
  correction as manual recordings.
- Show the app version in the bottom-right corner of the window.
- Prevent duplicate app windows when the exe is opened twice.

## 0.1.0 - 2026-11-20

- Initial public release.
- Windows installer and portable zip.
- GPU-direct capture (`ddagrab` -> `h264_nvenc`), constant 120 fps.
- Five-minute replay buffer with hotkey save.
- WASAPI loopback audio that follows the Windows default output device.
- Native clip library window and tray menu.
