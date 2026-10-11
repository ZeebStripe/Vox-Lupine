"""Check that no stage adds static: feed a clean tone through each stage in
live-style blocks and measure broadband junk that wasn't in the input.

Junk = energy above 9 kHz (a 220 Hz tone + a few harmonics has none there),
relative to the output's total level. Everything should be around -65 dB or lower.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

import numpy as np  # noqa: E402
from pedalboard import HighpassFilter, Pedalboard  # noqa: E402

from cleanup import Cleanup, process_blocks  # noqa: E402
from presets import PRESETS, Voice  # noqa: E402
from warmth import Warmth  # noqa: E402

SR = 48000


def junk_db(y):
    y = y[SR // 2:]                                   # skip settling
    hf = Pedalboard([HighpassFilter(cutoff_frequency_hz=9000) for _ in range(6)])(y[None, :], SR)[0]
    return 10 * np.log10(np.mean(hf ** 2) / (np.mean(y ** 2) + 1e-20) + 1e-20)


def run(name, fn, x):
    print(f"{name:28s} junk {junk_db(process_blocks(fn, x)):7.1f} dB")


t = np.arange(3 * SR) / SR
# Speech-like test: 220 Hz tone with syllable-rate amplitude changes.
env = (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) ** 2
tone = (0.2 * env * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

run("input (reference)", lambda b: b, tone)
run("cleanup", Cleanup(SR), tone)
w = Warmth(SR)
run("warmth (full)", w, tone)
w2 = Warmth(SR); w2.mix = 0
run("warmth EQ only", w2, tone)
for name, eq in PRESETS.items():
    run(f"preset {name} (no cleanup)", Voice(eq, SR, cleanup=False), tone)
run("match + warmth (full chain)", Voice(PRESETS["match"], SR, warmth=1.0), tone)
