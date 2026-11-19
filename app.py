"""Fragloop - single entry point for the packaged app.

`Fragloop.exe`        opens the window and starts the engine in the background.
`Fragloop.exe --engine` is the engine itself: tray, hotkeys, capture.

The engine runs as a child process so a crash in the window (or the reverse)
cannot take the recording down with it.
"""
import ctypes
import subprocess
import sys
from pathlib import Path


def base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def engine_running():
    """The engine holds this named mutex for its whole lifetime."""
    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False,
                                               "FragloopTray")
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def main():
    if "--engine" in sys.argv:
        import recorder
        recorder.main()
        return

    if not engine_running():
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--engine"]
        else:
            cmd = [sys.executable, str(base_dir() / "recorder.py")]
        subprocess.Popen(cmd, cwd=str(base_dir()),
                         creationflags=subprocess.CREATE_NO_WINDOW)

    import studio
    studio.main()


if __name__ == "__main__":
    main()
