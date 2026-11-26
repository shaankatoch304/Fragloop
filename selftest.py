"""Headless check: record 4s, stop, verify the mp4 has video + audio."""
import subprocess
import time

import recorder

recorder._start()
time.sleep(4)
recorder._stop()

out = sorted(recorder.RECORD_DIR.glob("*.mp4"))[-1]
print("file:", out.name, out.stat().st_size, "bytes")
print(subprocess.run(
    [str(recorder.FFPROBE), "-v", "error", "-show_entries",
     "stream=codec_name,width,height,r_frame_rate,channels",
     "-of", "compact", str(out)],
    capture_output=True, text=True).stdout)
print("leftovers:", [p.name for p in recorder.BUFFER_DIR.iterdir()
                     if p.suffix in (".mkv", ".wav")
                     and not p.name.startswith("buf_")])
