# Changelog

## Unreleased

- Preferences opens with Ctrl+, and double-clicking the title bar
  maximizes or restores the window.
- Slider handles and focused controls show clearer hover and focus states.
- Rounded surfaces got a softer, roomier look: the window and Preferences
  panels use 18 px corners, clip cards 16 px, and controls and menus 7-10 px,
  with more breathing room around the header and clip list.
- Window controls are larger, and Close is now a red record dot instead of
  an X, matching the app mark.
- `Alt+F` saves a PNG screenshot of the display to `Videos\Captures`.
- Screenshots appear in the main library with a warm glass card and a camera
  badge, so they read differently from clips.
- The main window no longer shows hotkey hints; they stay in Preferences.

## 0.2.0 - 2026-11-22

- Alt+G start/stop runs off the hotkey thread now: a slow stop no longer
  swallows or queues presses. Repeat presses while busy are ignored instead of
  replaying as a burst of start/stop toggles.
- Studio is now a frameless, translucent window: the desktop shows through the
  dark panel, with custom minimize, maximize and close controls and edge
  resizing.
- A gear in the header opens a preferences window with a section rail:
  general window behavior and hotkeys, appearance (panel opacity and grain),
  clip sorting and delete confirmation, storage folders and free space, and
  an About page. The old Open folder button moved there.
- The replay buffer length is now a Preferences slider from 1 to 15 minutes
  with second-level precision (5 minutes by default). The engine picks up the
  change without a restart.
- Studio shows the clip count with total size, a persistent hotkey hint, and
  remembers its window size and position. Clip sizes switch from MB to GB
  above 1024 MB.
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
