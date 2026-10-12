"""System-wide hotkeys (work while a game or OBS has focus):
Ctrl+Alt+1..9 switch presets, Ctrl+Alt+0 toggles mute.

Uses the Windows RegisterHotKey API on its own thread; presses are put on a queue
that the app polls. A combo another program already owns is reported, not stolen.
"""
import ctypes
import queue
import threading
from ctypes import wintypes

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
VK_0, VK_1 = 0x30, 0x31
MUTE = 0                  # number reported for Ctrl+Alt+0; presets are 1..9

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class GlobalHotkeys:
    def __init__(self, count=9):
        self.count = count
        self.presses = queue.Queue()
        self.failed = []          # numbers (0 = mute) that another program already uses
        self._thread = None
        self._tid = None
        self._ready = threading.Event()

    def start(self):
        if self._thread:
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._loop, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(2)

    def stop(self):
        if self._thread and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = self._tid = None

    def _keys(self):
        """(number, Windows hotkey id, key). Ids must be >= 1, so mute uses count + 1."""
        yield MUTE, self.count + 1, VK_0
        for n in range(1, self.count + 1):
            yield n, n, VK_1 + n - 1

    def _loop(self):
        self._tid = kernel32.GetCurrentThreadId()
        self.failed = []
        ids = {}
        for number, hk_id, vk in self._keys():
            if user32.RegisterHotKey(None, hk_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk):
                ids[hk_id] = number
            else:
                self.failed.append(number)
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and int(msg.wParam) in ids:
                    self.presses.put(ids[int(msg.wParam)])
        finally:
            for hk_id in ids:
                user32.UnregisterHotKey(None, hk_id)
