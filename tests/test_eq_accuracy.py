"""Checks for the draggable EQ:
1) all bands at 0 dB = no change at all
2) the drawn curve matches what the audio actually gets (measured with noise)
3) dragging a band while audio runs doesn't click (compare with a static setting)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

import numpy as np  # noqa: E402

from peq import ParamEQ, bands_response_db  # noqa: E402

SR = 48000
rng = np.random.default_rng(0)

# 1) flat = identity
x = (rng.normal(0, 0.1, SR * 2)).astype(np.float32)
eq = ParamEQ(SR)
print(f"1) all bands 0 dB: active={eq.active()} (should be False: the stage is skipped)")

# 2) curve vs actual: process white noise, compare spectra ratio to the drawn curve
bands = [{"type": "low_shelf", "freq": 150, "gain": 6, "q": 0.7},
         {"type": "peak", "freq": 500, "gain": -8, "q": 2.0},
         {"type": "peak", "freq": 1500, "gain": 5, "q": 1.0},
         {"type": "peak", "freq": 4000, "gain": -4, "q": 4.0},
         {"type": "high_shelf", "freq": 9000, "gain": 8, "q": 0.7}]
eq.set_bands(bands)
noise = rng.normal(0, 0.1, SR * 20).astype(np.float32)
y = np.concatenate([eq(noise[i:i + 256]) for i in range(0, len(noise), 256)])
n = 8192
def spec(s):
    fr = s[:len(s) // n * n].reshape(-1, n) * np.hanning(n)
    return (np.abs(np.fft.rfft(fr, axis=1)) ** 2).mean(0)
f = np.fft.rfftfreq(n, 1 / SR)
measured = 10 * np.log10(spec(y) / spec(noise))
check = np.array([60, 150, 300, 500, 800, 1500, 2500, 4000, 6000, 9000, 14000])
drawn = bands_response_db(bands, SR, check)   # what the EQ graph draws
meas = np.interp(check, f, measured)
print("2) freq   drawn  measured")
for c, d, m in zip(check, drawn, meas):
    print(f"   {c:6d} {d:+6.1f} {m:+7.1f}")
print(f"   max error {np.abs(drawn - meas).max():.2f} dB")

# 3) drag the 1.5 kHz bell from 300 Hz to 6 kHz and back over 2 s, 30 updates/s,
# on a voice-like tone; clicks show up as broadband energy above 12 kHz.
from pedalboard import HighpassFilter, Pedalboard
t = np.arange(SR * 4) / SR
tone = (0.2 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)
def run(drag):
    e = ParamEQ(SR, bands)
    out = []
    for k, i in enumerate(range(0, len(tone), 256)):
        if drag and k % 6 == 0:     # ~31 updates per second
            ph = (i / SR) % 2 / 2
            fr = 300 * (20 ** (1 - abs(2 * ph - 1)))
            bb = [dict(b) for b in bands]
            bb[2].update(freq=fr, gain=12 * np.sin(np.pi * ph))
            e.set_bands(bb)
        out.append(e(tone[i:i + 256]))
    return np.concatenate(out)
hf = lambda s: Pedalboard([HighpassFilter(cutoff_frequency_hz=12000) for _ in range(6)])(s[None, :], SR)[0]
db = lambda s: 10 * np.log10(np.mean(s ** 2) + 1e-20)
static, dragged = run(False), run(True)
print(f"3) junk above 12 kHz: static {db(hf(static)) - db(static):.1f} dB, "
      f"while dragging {db(hf(dragged)) - db(dragged):.1f} dB (relative to signal)")
