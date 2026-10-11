"""The voice chain and its "base tone" EQs.

Voice = cleanup -> warmth -> brightness -> base tone -> effects -> volume.
Everything is zero/low-latency: filters, dynamics and light saturation only
(no pitch shifting), so it runs in real time with a few ms of processing delay.
"""
import numpy as np
from pedalboard import (Compressor, Distortion, Gain, HighShelfFilter, Limiter,
                        LowShelfFilter, Mix, PeakFilter, Pedalboard)

from bright import Bright
from cleanup import Cleanup
from fx import Effects
from warmth import Warmth


def _finish(gain_db):
    return [Compressor(threshold_db=-22, ratio=3, attack_ms=8, release_ms=120),
            Gain(gain_db=gain_db),
            Limiter(threshold_db=-1.5, release_ms=80)]


def _subtle_eq():
    """Gentle lift: a little more body and clarity."""
    return Pedalboard([
        LowShelfFilter(cutoff_frequency_hz=220, gain_db=3.5, q=0.7),
        PeakFilter(cutoff_frequency_hz=450, gain_db=-1.5, q=1.0),
        PeakFilter(cutoff_frequency_hz=2150, gain_db=2.0, q=1.2),
        HighShelfFilter(cutoff_frequency_hz=5000, gain_db=3.0, q=0.7),
    ] + _finish(6))


def _match_eq():
    """Fuller and more forward: chest body, forward vowels, crisp top."""
    return Pedalboard([
        LowShelfFilter(cutoff_frequency_hz=230, gain_db=6.0, q=0.7),
        PeakFilter(cutoff_frequency_hz=500, gain_db=-3.0, q=1.0),
        PeakFilter(cutoff_frequency_hz=1800, gain_db=-2.0, q=2.0),   # move the 2nd formant region
        PeakFilter(cutoff_frequency_hz=2200, gain_db=3.5, q=1.5),    # up: cut below, boost above
        PeakFilter(cutoff_frequency_hz=3700, gain_db=2.5, q=1.5),    # 3rd formant / presence
        HighShelfFilter(cutoff_frequency_hz=6000, gain_db=5.0, q=0.7),
    ] + _finish(6))


def _husky_eq():
    """'match' plus a little parallel saturation for a smoky rasp."""
    board = _match_eq()
    rasp = Pedalboard([PeakFilter(cutoff_frequency_hz=1500, gain_db=4, q=0.8),
                       Distortion(drive_db=18), Gain(gain_db=-26)])
    board.insert(len(board) - 3, Mix([Pedalboard([]), rasp]))
    return board


class Voice:
    """Block processor: cleanup -> warmth -> brightness -> preset EQ -> effects.
    Call with 1-D float32 blocks."""

    def __init__(self, eq_factory, sr, block=256, cleanup=True, warmth=0.0, bright=0.0):
        self.sr, self.block = sr, block
        self.cleanup = Cleanup(sr, block) if cleanup else None
        self.warmth = Warmth(sr, block, amount=warmth) if warmth else None
        self.bright = Bright(sr, amount=bright) if bright else None
        self.eq = eq_factory() if eq_factory else None
        self.fx = Effects(sr, block)
        self.latency = self.cleanup.latency if self.cleanup else 0
        self.bypass = False
        self.out_gain = 1.0

    # --- live controls (call from the audio thread, see engine.py) ---
    def set_warmth(self, a):
        if self.warmth is None:
            self.warmth = Warmth(self.sr, self.block, amount=a)
        self.warmth.set_amount(a)

    def set_bright(self, a):
        if self.bright is None:
            self.bright = Bright(self.sr, amount=a)
        self.bright.set_amount(a)

    def set_preset(self, name):
        self.eq = PRESETS[name]() if name in PRESETS else None

    def set_cleanup(self, on):
        if self.cleanup:
            self.cleanup.enabled = on

    def set_out_gain_db(self, db):
        self.out_gain = 10 ** (db / 20)

    def __call__(self, x):
        if self.bypass:
            # Raw voice with the same delay, so A/B switching doesn't jump in time.
            if self.cleanup:
                x = self.cleanup.gate.delay_only(x)
            return _soft_ceiling(x * self.out_gain)
        if self.cleanup:
            x = self.cleanup(x)
        if self.warmth:
            x = self.warmth(x)
        if self.bright:
            x = self.bright(x)
        if self.eq:
            x = self.eq(x[None, :], self.sr, reset=False)[0]
        x = self.fx(x)
        if self.out_gain != 1.0:
            x = x * self.out_gain
        return _soft_ceiling(x)


def _soft_ceiling(x, start_db=-1.5):
    """The Limiter lets rare peaks through (~14 samples per minute reached 0 dBFS).
    Above `start_db`, round peaks off smoothly so output never reaches full scale."""
    t = 10 ** (start_db / 20)
    a = np.abs(x)
    over = a > t
    if over.any():
        x = x.copy()
        x[over] = np.sign(x[over]) * (t + (1 - t) * np.tanh((a[over] - t) / (1 - t)) * 0.98)
    return x


PRESETS = {"subtle": _subtle_eq, "match": _match_eq, "husky": _husky_eq}
