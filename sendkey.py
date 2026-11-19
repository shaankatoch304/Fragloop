"""Send a synthetic Alt+<key> press (same path as a real keypress)."""
import ctypes
import sys
import time

user32 = ctypes.windll.user32
VK_MENU, KEYUP = 0x12, 0x0002
VK = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x47  # default G

user32.keybd_event(VK_MENU, 0, 0, 0)
time.sleep(0.03)
user32.keybd_event(VK, 0, 0, 0)
time.sleep(0.05)
user32.keybd_event(VK, 0, KEYUP, 0)
time.sleep(0.03)
user32.keybd_event(VK_MENU, 0, KEYUP, 0)
print(f"sent Alt+{VK:02X}")
