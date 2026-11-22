"""Check that hotkey toggles never stack or block the caller (no capture)."""
import time

import recorder


def main():
    calls = []
    recorder._log = lambda text: None
    recorder._notify = lambda text: None
    recorder._recording = False
    recorder._start = lambda: (calls.append("start"), time.sleep(0.4))
    recorder._stop = lambda: calls.append("stop")

    t0 = time.time()
    recorder._toggle()
    assert time.time() - t0 < 0.1, "toggle blocked the hotkey thread"

    time.sleep(0.1)
    recorder._toggle()                 # ignored while the start is running
    time.sleep(0.5)
    assert calls == ["start"], calls

    recorder._recording = True
    recorder._toggle()
    time.sleep(0.5)
    assert calls == ["start", "stop"], calls
    print("hotkey toggle guard ok")


if __name__ == "__main__":
    main()
