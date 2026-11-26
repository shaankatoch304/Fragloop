# Changelog

## 0.4.3 - 2026-11-27

- Preferences pages now use the same near-black background as the main
  window instead of the lighter default scroll-area panel color.
- Right-click a clip to open its folder in File Explorer with the file
  selected.
- Screenshots, replay saves and manual recordings now go to separate
  `Screenshots`, `Replay Buffer` and `Recordings` folders under
  `Videos\Fragloop` instead of one shared `Captures` folder. Older clips stay
  in `Captures` and remain in the library.

## 0.4.2 - 2026-11-26

- Preferences controls use neutral dark colors instead of the red recording
  accent, so checkboxes, the slider, the close button and the focused input
  no longer stand out in red.

## 0.4.1 - 2026-11-26

- Preferences is taller and each page scrolls, so the Microphone input is
  fully visible instead of being clipped at the bottom edge.

## 0.4.0 - 2026-11-26

- Optional microphone: choose Off, System default or a specific input in
  Preferences → Recording → Microphone, or toggle it from the tray. The mic is
  mixed with system audio in replay saves and manual recordings.
- Audio mixing is limited to keep peaks clean, and the mic follows device
  changes without restarting the replay buffer.

## 0.3.0 - 2026-11-26

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
- New screenshots show up in the library immediately after capture; the
  single-frame grab now finishes in about a quarter second instead of two.
- Replay saves use a violet glass card with a replay-arrow badge, and manual
  recordings use a red glass card with a record dot; every clip row also names
  its type beside the badge.
- Saved replays and manual recordings are muxed to a `.part` name and renamed
  into place when complete, so they appear in the library at once instead of
  after the old two-second guard.
- Clip rows animate on hover and press, and double-clicking starts the player
  off the UI thread, so the window stays responsive and reacts immediately.
- The main window no longer shows hotkey hints; they stay in Preferences.
- Media in `Videos\Captures` is treated as untrusted: preview thumbnails
  reject oversized or malformed images, and ffmpeg/ffprobe probes time out
  instead of freezing the window or holding the recorder busy forever.
- Replay saves quote buffer paths before handing them to ffmpeg's concat
  demuxer, so an apostrophe in the profile path cannot inject directives.
- Clip names, folder paths and dialogs render as plain text, so a filename
  cannot inject markup into the interface.
- Saving a replay or stopping a recording now shows a "Saving…" row in the
  library immediately, then swaps in the finished clip and thumbnail.
- Replay saves no longer overwrite an existing clip when two saves share a
  timestamp; the buffer also releases its ffmpeg log handle after launch.

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
