"""One-shot cleanup after the exact guard process exits, including console close."""
import sys
import win32api, win32event
from fan_control import Fans
handle=win32api.OpenProcess(0x00100000,False,int(sys.argv[1]))
print('READY',flush=True)
win32event.WaitForSingleObject(handle,win32event.INFINITE)
handle.Close()
Fans().restore()
