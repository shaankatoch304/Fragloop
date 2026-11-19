Fragloop 0.1.0 - first build.

## Downloads

- **Setup (recommended):** `Fragloop-0.1.0-win64-setup.exe` - installs for the current user, adds a Start menu shortcut, and can start Fragloop with Windows.
- **Portable:** `Fragloop-0.1.0-win64-portable.zip` - extract anywhere and run `Fragloop.exe`.

## Requirements

- Windows 10/11, 64-bit
- NVIDIA GPU with NVENC (Turing or newer recommended)
- About 7.5 GB free disk for a five-minute replay buffer; saving needs up to twice that temporarily.

## Notes

- The build is unsigned, so Windows SmartScreen may show "Windows protected your PC". Choose **More info -> Run anyway**.
- Captures the Windows default output device. Games that use WASAPI exclusive mode, or that select a non-default output device in their own settings, may not be captured.
- Clips are saved to `Videos\Captures`.
- Logs are at `%LOCALAPPDATA%\Fragloop\recorder.log`.
