"""Brightness stage: lighter, softer, 'cuter' tone with EQ only (zero latency).

- Trim chest (~200 Hz): less 'big' body. Partly offsets warmth's chest lift, while
  warmth's saturation (richness) stays.
- Lift presence (~4 kHz, broad): forward, smiley clarity.
- Lift air (shelf from 10 kHz): breathy, light top end.
- Dip ~7 kHz slightly so the lifts don't sharpen 's' sounds.
"""
from pedalboard import HighShelfFilter, Pedalboard, PeakFilter


class Bright:
    def __init__(self, sr, amount=1.0):
        self.sr = sr
        self.eq = Pedalboard([
            PeakFilter(cutoff_frequency_hz=200, q=0.9),          # less chest
            PeakFilter(cutoff_frequency_hz=4000, q=0.7),         # presence
            PeakFilter(cutoff_frequency_hz=7000, q=2.5),         # guard 's'
            HighShelfFilter(cutoff_frequency_hz=10000, q=0.7),   # air
        ])
        self.base_gains = [-2.5, 2.5, -1.0, 3.0]                 # dB at amount 1.0
        self.set_amount(amount)

    def set_amount(self, a):
        """Change strength while running (filters keep their state: no clicks)."""
        for f, g in zip(self.eq, self.base_gains):
            f.gain_db = g * a

    def __call__(self, x):
        return self.eq(x[None, :], self.sr, reset=False)[0]
