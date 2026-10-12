"""Audio engine shared by the app (app.py) and the command line (live.py).

mic -> Voice chain -> VB-Cable (+ optional monitor to headphones).
Setting changes are queued and applied by the audio thread at the start of the
next block, so audio never sees a half-applied change.
"""
import copy
import json
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd

from about import APP_NAME
from fx import DEFAULTS as FX_DEFAULTS
from presets import PRESETS, Voice

HERE = Path(__file__).parent
# Where settings and presets live. Running from source: next to the code. The
# installed .exe can't write to its own folder, so it uses %APPDATA%\Vox Lupine.
if getattr(sys, "frozen", False):
    DATA = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    DATA.mkdir(parents=True, exist_ok=True)
else:
    DATA = HERE
CONFIG = DATA / "config.json"
# Jitter cushion between the input and output devices. Wireless headset mics can
# deliver audio in bursts (gaps up to ~14 ms) and VB-Cable can stall ~22 ms; with
# 2 blocks (10.7 ms) that glitched often under load, 3 blocks + max 10 ran clean
# (measure your own setup with tools/diag_buffer.py). Costs ~5 ms extra delay.
MAX_BUFFER_BLOCKS = 10    # cap on queued output; beyond this, drop to keep latency bounded
PREFILL_BLOCKS = 3

DEFAULTS = {
    "input_device": "",              # "" = Windows default microphone
    "output_device": "CABLE Input",  # VB-Cable; apps then use "CABLE Output" as their mic
    "monitor_device": "",            # "" = Windows default speakers/headphones
    "monitor": False,
    "monitor_volume_db": 0.0,        # headphone playback only; not what apps receive
    "host_api": "Windows WASAPI",
    "samplerate": 48000,
    "block": 256,
    "preset": "match",
    "warmth": 0.5,
    "bright": 1.0,
    # Headroom: at 0 dB, peaks reach ~-0.4 dBFS, and voice/chat apps with automatic
    # gain control can clip that into static while you talk. -8.5 dB avoids it.
    "volume_db": -8.5,
    "cleanup": True,
    "bypass": False,
    "hotkeys": True,          # Ctrl+Alt+1..9 switch presets, even while a game has focus
    **FX_DEFAULTS,
}
FX_KEYS = set(FX_DEFAULTS)
LIVE_KEYS = {"preset", "warmth", "bright", "volume_db", "cleanup", "bypass"} | FX_KEYS
# What a saved "scene" stores: the sound, not devices or the bypass switch.
SCENE_KEYS = ["preset", "warmth", "bright", "volume_db", "cleanup"] + list(FX_DEFAULTS)
SCENES = DATA / "scenes.json"


def load_config():
    cfg = copy.deepcopy(DEFAULTS)   # deep: the EQ band list must never be shared
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text()))
    return cfg


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")


def load_scenes():
    return json.loads(SCENES.read_text()) if SCENES.exists() else {}


def save_scenes(scenes):
    SCENES.write_text(json.dumps(scenes, indent=2) + "\n")


def rescan_devices():
    """PortAudio builds its device list once, when it starts, so a device switched on
    later (a wireless headset waking from sleep, a USB mic plugged in) stays invisible.
    Restarting PortAudio re-reads the list. Only call this while no stream is open."""
    sd._terminate()
    sd._initialize()


def device_names(kind, host_api="Windows WASAPI"):
    """Names of input or output devices on one host API (WASAPI = lowest latency)."""
    names = []
    for d in sd.query_devices():
        ch = d["max_input_channels"] if kind == "input" else d["max_output_channels"]
        if ch > 0 and sd.query_hostapis(d["hostapi"])["name"] == host_api:
            names.append(d["name"])
    return names


def is_virtual_cable(device_name):
    return "vb-audio" in device_name.lower() or device_name.lower().startswith("cable")


def find_device(name, kind, host_api):
    """Index of the device whose name contains `name`, preferring `host_api`.
    An empty name means the Windows default device for that host API."""
    if not name:
        for api_index, api in enumerate(sd.query_hostapis()):
            if api["name"] != host_api:
                continue
            i = api["default_input_device" if kind == "input" else "default_output_device"]
            if i >= 0 and not (kind == "input" and is_virtual_cable(sd.query_devices(i)["name"])):
                return i, sd.query_devices(i)["name"]
            if kind == "input":
                # Installing VB-Cable often makes "CABLE Output" the default recording
                # device. Listening to it would feed our own output back in, so pick
                # the first real microphone instead.
                for j, d in enumerate(sd.query_devices()):
                    if (d["hostapi"] == api_index and d["max_input_channels"] > 0
                            and not is_virtual_cable(d["name"])):
                        return j, d["name"]
        raise RuntimeError(f"No default {kind} device found. Pick one on the Devices tab.")
    matches = []
    for i, d in enumerate(sd.query_devices()):
        ch = d["max_input_channels"] if kind == "input" else d["max_output_channels"]
        if ch > 0 and name and name.lower() in d["name"].lower():
            api = sd.query_hostapis(d["hostapi"])["name"]
            matches.append((api != host_api, i, d["name"]))
    if not matches:
        raise RuntimeError(f"No {kind} device matching '{name}'")
    _, i, dname = sorted(matches)[0]
    return i, dname


def make_voice(cfg):
    v = Voice(PRESETS.get(cfg["preset"]), cfg["samplerate"], cfg["block"],
              warmth=cfg["warmth"], bright=cfg["bright"])
    apply_setting(v, "cleanup", cfg["cleanup"])
    apply_setting(v, "volume_db", cfg["volume_db"])
    apply_setting(v, "bypass", cfg["bypass"])
    for k in FX_KEYS:
        v.fx.set(k, cfg[k])
    return v


def apply_setting(v, key, value):
    if key in FX_KEYS:
        v.fx.set(key, value)
    elif key == "preset":
        v.set_preset(value)
    elif key == "warmth":
        v.set_warmth(value)
    elif key == "bright":
        v.set_bright(value)
    elif key == "volume_db":
        v.set_out_gain_db(value)
    elif key == "cleanup":
        v.set_cleanup(value)
    elif key == "bypass":
        v.bypass = value


class _Output:
    """One output device fed from its own queue."""

    def __init__(self, engine, device, sr, block):
        self.q = deque()
        self.started = False
        self.engine = engine
        self.stream = None
        if device is not None:   # None = no real device (self-test drives _cb directly)
            ch = min(2, sd.query_devices(device)["max_output_channels"])
            self.stream = sd.OutputStream(device=device, channels=ch, samplerate=sr, blocksize=block,
                                          dtype="float32", latency="low", callback=self._cb)

    def push(self, y):
        self.q.append(y)
        while len(self.q) > MAX_BUFFER_BLOCKS:
            self.q.popleft()
            self.engine.drops += 1
        if len(self.q) >= PREFILL_BLOCKS:
            self.started = True

    def _cb(self, outdata, frames, t, status):
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        while filled < frames and self.started and self.q:
            chunk = self.q[0]
            take = min(len(chunk), frames - filled)
            out[filled:filled + take] = chunk[:take]
            if take < len(chunk):
                self.q[0] = chunk[take:]
            else:
                self.q.popleft()
            filled += take
        if self.started and filled < frames:
            self.engine.underruns += 1
            self.started = False          # re-prefill instead of stuttering repeatedly
        outdata[:] = out[:, None]


class Engine:
    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        self.voice = None
        self.running = False
        self.pending = deque()
        self.lock = threading.Lock()
        self.inp = self.main = self.mon = None
        self.in_db = self.out_db = -90.0
        self.underruns = self.drops = 0
        self.proc_ms = deque(maxlen=400)
        self.names = {}
        self._reset_mic_health()
        # Mute silences everything we send (VB-Cable and headphones) while the mic
        # stays open, so headsets that un-mute themselves when recording starts
        # can't interfere. Not a saved setting: the app always opens unmuted.
        self.muted = False
        self._send_gain = 1.0                 # ramps between 0 and 1 (no clicks)
        self.mon_gain = 10 ** (self.cfg["monitor_volume_db"] / 20)

    # ----- microphone health -----
    # A working mic always picks up some room noise, so long stretches of *exact*
    # digital silence mean something blocks it: Windows privacy settings, a muted or
    # disabled mic, or an input volume of 0.
    SILENT_SECONDS = 3.0      # pure silence this long -> "silent"
    QUIET_SECONDS = 8.0       # nothing above QUIET_DB since Start this long -> "quiet"
    QUIET_DB = -55.0

    def _reset_mic_health(self):
        self.mic_blocks = 0
        self.mic_zero_run = 0
        self.mic_loudest_db = -200.0

    def mic_status(self):
        """'silent', 'quiet' or 'ok' (only meaningful while running)."""
        if not self.running:
            return "ok"
        block_s = self.cfg["block"] / self.cfg["samplerate"]
        if self.mic_zero_run * block_s >= self.SILENT_SECONDS:
            return "silent"
        if self.mic_blocks * block_s >= self.QUIET_SECONDS and self.mic_loudest_db < self.QUIET_DB:
            return "quiet"
        return "ok"

    # ----- control (any thread) -----
    def set(self, key, value):
        value = copy.deepcopy(value)
        self.cfg[key] = value
        if key in LIVE_KEYS:
            self.pending.append((key, value))
        elif key == "monitor_volume_db":
            self.mon_gain = 10 ** (value / 20)
        elif key == "monitor" and self.running:
            self._set_monitor(value)
        elif key in ("input_device", "output_device", "monitor_device") and self.running:
            self.stop()
            self.start()

    def start(self):
        c = self.cfg
        sr, block = c["samplerate"], c["block"]
        if self.inp is None and self.main is None and self.mon is None:
            rescan_devices()           # pick up a mic that woke up after the app opened
        try:
            in_idx, self.names["in"] = find_device(c["input_device"], "input", c["host_api"])
        except RuntimeError:
            if not c["input_device"]:
                raise
            raise RuntimeError(
                f"Your microphone '{c['input_device']}' wasn't found. It may be unplugged, "
                f"switched off (wireless headsets often sleep), or disabled in Windows.\n"
                f"Turn it on and press Start again, or choose another mic on the Devices tab.")
        out_idx, self.names["out"] = find_device(c["output_device"], "output", c["host_api"])
        if is_virtual_cable(self.names["in"]) and is_virtual_cable(self.names["out"]):
            raise RuntimeError(
                f"The mic is set to '{self.names['in']}', which is the other end of the virtual "
                f"cable this app sends to. That would feed your voice back into itself.\n"
                f"Pick your real microphone under Devices → Mic.")
        self.voice = make_voice(c)
        self.pending.clear()
        self.underruns = self.drops = 0
        self._reset_mic_health()
        self.main = _Output(self, out_idx, sr, block)
        self.inp = sd.InputStream(device=in_idx, channels=1, samplerate=sr, blocksize=block,
                                  dtype="float32", latency="low", callback=self._on_input)
        self.main.stream.start()
        self.inp.start()
        self.running = True
        if c["monitor"]:
            self._set_monitor(True)

    def stop(self):
        self.running = False
        for s in (self.inp, self.main and self.main.stream, self.mon and self.mon.stream):
            if s is not None:
                s.stop()
                s.close()
        self.inp = self.main = self.mon = None
        self.in_db = self.out_db = -90.0

    def _set_monitor(self, on):
        with self.lock:
            old, self.mon = self.mon, None
        if old:
            old.stream.stop()
            old.stream.close()
        if on:
            idx, self.names["mon"] = find_device(self.cfg["monitor_device"], "output", self.cfg["host_api"])
            mon = _Output(self, idx, self.cfg["samplerate"], self.cfg["block"])
            mon.stream.start()
            with self.lock:
                self.mon = mon

    def latency_ms(self):
        if not self.running:
            return None
        c = self.cfg
        fixed = (self.voice.latency + PREFILL_BLOCKS * c["block"]) / c["samplerate"] * 1000
        return fixed + (self.inp.latency + self.main.stream.latency) * 1000

    def cpu_ms(self):
        return float(np.median(self.proc_ms)) if self.proc_ms else 0.0

    # ----- audio thread -----
    def _on_input(self, indata, frames, t, status):
        start = time.perf_counter()
        while self.pending:
            apply_setting(self.voice, *self.pending.popleft())
        x = indata[:, 0].astype(np.float32)
        y = self.voice(x)
        peak = float(np.abs(x).max())
        self.mic_blocks += 1
        self.mic_zero_run = self.mic_zero_run + 1 if peak < 1e-7 else 0
        peak_db = 20 * np.log10(peak + 1e-12)
        self.mic_loudest_db = max(self.mic_loudest_db, peak_db)
        self.in_db = max(max(peak_db, -180.0), self.in_db - 1.5)
        target = 0.0 if self.muted else 1.0
        if target != self._send_gain or target != 1.0:
            y = y * np.linspace(self._send_gain, target, len(y), dtype=np.float32)  # 5 ms fade
            self._send_gain = target
        self.out_db = max(20 * np.log10(np.abs(y).max() + 1e-9), self.out_db - 1.5)
        self.main.push(y)
        with self.lock:
            if self.mon:
                self.mon.push(np.clip(y * self.mon_gain, -1.0, 1.0).astype(np.float32))
        self.proc_ms.append((time.perf_counter() - start) * 1000)
