"""Headless replay-buffer check: 2s segments, short save, verify mp4."""
import subprocess
import time

import recorder

recorder.SEGMENT_SECONDS = 2
recorder.REPLAY_SECONDS_OVERRIDE = 6
recorder.AUDIO_ROTATE_SECONDS = 3
recorder.KEEP_AUDIO_FILES = 4

recorder._buffer_start()
time.sleep(17)

segs = sorted(recorder.BUFFER_DIR.glob("buf_*.mkv"))
print("segments at save time:")
for p in segs:
    try:
        d = subprocess.run([str(recorder.FFPROBE), "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(p)],
                           capture_output=True, text=True).stdout.strip()
        print(f"  {p.name} {p.stat().st_size:>10} B  {d}s  "
              f"age {time.time() - p.stat().st_mtime:.1f}s")
    except FileNotFoundError:
        print(f"  {p.name} (pruned while listing)")

recorder._buffer_save()
time.sleep(1)
recorder._buffer_stop_all()
time.sleep(3)

out = sorted(recorder.OUT_DIR.glob("*_replay.mp4"))[-1]
print("file:", out.name, out.stat().st_size, "bytes")
print(subprocess.run(
    [str(recorder.FFPROBE), "-v", "error", "-count_frames", "-show_entries",
     "stream=codec_type,codec_name,width,height,channels,duration,nb_read_frames",
     "-of", "compact", str(out)],
    capture_output=True, text=True).stdout)
print("buffer files left:", sorted(p.name for p in recorder.BUFFER_DIR.iterdir()))
print("ffmpeg left:", subprocess.run(
    ["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe"],
    capture_output=True, text=True).stdout.count("ffmpeg.exe"))
