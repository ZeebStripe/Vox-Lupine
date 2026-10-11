"""Warmth stage: fuller low-mids, softer top, gentle tube-style saturation.

- EQ: lift the 'chest' region (~180 Hz) your headset mic under-captures, ease
  boxiness, and soften the upper-mid/treble edge.
- Saturation: asymmetric soft clip, which adds mostly *even* harmonics (the
  'tube/tape' warmth) instead of the harsher odd harmonics of plain tanh.
  Drive is level-independent: an envelope follower keeps the saturator seeing
  the same speech level whatever your mic gain is.
"""
import numpy as np
from pedalboard import (HighpassFilter, HighShelfFilter, LowShelfFilter, Pedalboard,
                        PeakFilter)


class Warmth:
    # Tuned on a 200 Hz tone: 2nd harmonic ~-27 dB (audible warmth), 3rd ~-43 dB.
    def __init__(self, sr, block=256, amount=1.0, target_rms_db=-8.0, bias=0.4, mix=0.5):
        self.sr = sr
        self.eq = Pedalboard([
            # Top-end cuts kept small: at -2/-2.5 dB it sounded muffled.
            PeakFilter(cutoff_frequency_hz=180, q=0.8),        # chest/body
            LowShelfFilter(cutoff_frequency_hz=300, q=0.7),    # overall fullness
            PeakFilter(cutoff_frequency_hz=550, q=1.4),        # less boxy
            PeakFilter(cutoff_frequency_hz=3500, q=1.0),       # slight edge relief
            HighShelfFilter(cutoff_frequency_hz=10000, q=0.7),  # only the very top
        ])
        self.base_gains = [3.0, 1.0, -1.5, -0.5, -1.0]       # dB at amount 1.0
        self.target = 10 ** (target_rms_db / 20)
        self.bias, self.base_mix = bias, mix
        self.set_amount(amount)
        self.env = None
        self.env_k = np.exp(-block / (0.3 * sr))   # ~300 ms level follower
        self.offset = np.tanh(bias)
        self.dc = Pedalboard([HighpassFilter(cutoff_frequency_hz=15)])

    def set_amount(self, a):
        """Change strength while running (filters keep their state: no clicks)."""
        for f, g in zip(self.eq, self.base_gains):
            f.gain_db = g * a
        self.mix = self.base_mix * a

    def _saturate(self, x):
        # Asymmetric: shifting the curve makes + and - halves clip differently.
        return np.tanh(x + self.bias) - self.offset

    def __call__(self, x):
        x = self.eq(x[None, :], self.sr, reset=False)[0]
        rms = np.sqrt(np.mean(x ** 2))
        if rms > 1e-4:                                    # only learn level from sound
            self.env = rms if self.env is None else self.env * self.env_k + rms * (1 - self.env_k)
        if not self.env or self.mix <= 0:
            return x
        pre = self.target / self.env
        wet = self._saturate(x * pre) / pre
        # Asymmetry adds a little DC; remove it with a continuous filter.
        # (Subtracting each block's mean caused steps at every block edge = static.)
        wet = self.dc(wet[None, :].astype(np.float32), self.sr, reset=False)[0]
        return (1 - self.mix) * x + self.mix * wet
