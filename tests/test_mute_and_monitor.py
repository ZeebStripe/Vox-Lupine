"""Mute and headphone volume, driven through the real engine callbacks:
1) unmuted: voice reaches the output; 2) muted: pure silence within one block, with
no click; 3) unmuted again: voice returns; 4) headphones get the same signal scaled
by the headphone volume, and VB-Cable is unaffected by it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

import numpy as np  # noqa: E402

from engine import Engine, _Output, load_config, make_voice  # noqa: E402

cfg = load_config()
cfg.update(input_device="", monitor=False)
sr, block = cfg["samplerate"], cfg["block"]
e = Engine(cfg)
e.voice = make_voice(cfg)
e.main = _Output(e, None, sr, block)
e.mon = _Output(e, None, sr, block)
e.set("monitor_volume_db", -6.0)

t = np.arange(sr * 3) / sr
x = (0.1 * np.sin(2 * np.pi * 200 * t) * (0.7 + 0.3 * np.sin(2 * np.pi * 2 * t))).astype(np.float32)
main, mon = [], []
mute_at, unmute_at = sr // block, 2 * sr // block          # mute at 1 s, unmute at 2 s
for n, i in enumerate(range(0, len(x) - block + 1, block)):
    if n == mute_at:
        e.muted = True
    if n == unmute_at:
        e.muted = False
    e._on_input(x[i:i + block, None], block, None, None)
    for out, store in ((e.main, main), (e.mon, mon)):
        buf = np.zeros((block, 1), dtype=np.float32)
        out._cb(buf, block, None, None)
        store.append(buf[:, 0])
main, mon = np.concatenate(main), np.concatenate(mon)
# Mute acts on the processed output, so in the output it only lags by the buffer
# (PREFILL_BLOCKS - 1 blocks), not by the voice chain's processing delay.
lag = 2 * block


def seg(a, b):
    return main[a * block + lag:b * block + lag]


rms = lambda s: 20 * np.log10(np.sqrt(np.mean(s ** 2)) + 1e-12)
print(f"1) unmuted level           {rms(seg(20, mute_at)):6.1f} dBFS")
print(f"2) muted level             {rms(seg(mute_at + 1, unmute_at)):6.1f} dBFS "
      f"(pure silence: {np.all(seg(mute_at + 1, unmute_at) == 0)})")
edge = main[mute_at * block + lag - 4: mute_at * block + lag + block + 4]
print(f"   biggest sample jump at the mute moment {np.abs(np.diff(edge)).max():.4f} "
      f"(normal sample-to-sample change ~{np.abs(np.diff(seg(20, mute_at))).max():.4f})")
print(f"3) after unmute level      {rms(seg(unmute_at + 20, unmute_at + 150)):6.1f} dBFS")
both = slice(20 * block + lag, mute_at * block + lag)
print(f"4) headphones vs VB-Cable  {rms(mon[both]) - rms(main[both]):+.1f} dB (expected -6.0)")
