"""Creative effects stage, after the voice chain. Everything defaults to off, and an
effect at 0 is skipped entirely (no CPU, no change to the sound).

Order: 3-band EQ -> draggable EQ -> grit -> radio -> modulation -> echo -> reverb.
All parameters can change while running (call `set` from the audio thread).
"""
import copy

import numpy as np
from pedalboard import (Chorus, Delay, HighpassFilter, HighShelfFilter, LowpassFilter,
                        LowShelfFilter, Pedalboard, PeakFilter, Phaser, Reverb)

from peq import ParamEQ, default_bands

MOD_TYPES = ["off", "chorus", "vibrato", "flanger", "phaser", "tremolo", "robot"]

DEFAULTS = {
    "eq_bass": 0.0, "eq_mid": 0.0, "eq_treble": 0.0,     # dB
    "peq_on": True, "peq": default_bands(),              # draggable EQ (EQ tab)
    "grit": 0.0,                                         # 0..1
    "radio": 0.0,                                        # 0..1
    "mod_type": "off", "mod_amount": 0.5, "mod_rate": 0.5,  # rate 0..1 = slow..fast
    "echo": 0.0, "echo_time": 250.0,                     # mix 0..1, ms
    "reverb": 0.0, "reverb_size": 0.5,                   # 0..1
}


def _rms(x):
    return float(np.sqrt(np.mean(x ** 2)) + 1e-9)


class _Follower:
    """Smoothed gain that matches one signal's level to another's (no zipper)."""

    def __init__(self, sr, block, ms=200):
        self.k = np.exp(-block / (ms * 1e-3 * sr))
        self.ref = self.cur = None
        self.g = 1.0

    def __call__(self, ref, cur, n):
        r, c = _rms(ref), _rms(cur)
        if r < 1e-4:                      # don't learn from silence
            return np.full(n, self.g, dtype=np.float32)
        self.ref = r if self.ref is None else self.ref * self.k + r * (1 - self.k)
        self.cur = c if self.cur is None else self.cur * self.k + c * (1 - self.k)
        prev, self.g = self.g, min(self.ref / self.cur, 8.0)
        return np.linspace(prev, self.g, n, dtype=np.float32)


class Effects:
    def __init__(self, sr, block=256, **params):
        self.sr, self.block = sr, block
        self.p = copy.deepcopy(DEFAULTS)
        self.peq = ParamEQ(sr)
        self.eq = Pedalboard([LowShelfFilter(cutoff_frequency_hz=150, q=0.7),
                              PeakFilter(cutoff_frequency_hz=1000, q=0.7),
                              HighShelfFilter(cutoff_frequency_hz=5000, q=0.7)])
        # Grit: band-limited soft clipping (growl, not fizz), level-matched to the dry voice.
        self.grit_pre = Pedalboard([HighpassFilter(cutoff_frequency_hz=150)])
        self.grit_post = Pedalboard([LowpassFilter(cutoff_frequency_hz=5500),
                                     LowpassFilter(cutoff_frequency_hz=5500)])
        self.grit_lvl = _Follower(sr, block)
        # Radio: narrow 400 Hz - 3 kHz band, level-matched.
        self.radio_bp = Pedalboard([HighpassFilter(cutoff_frequency_hz=400) for _ in range(2)] +
                                   [LowpassFilter(cutoff_frequency_hz=3000) for _ in range(2)])
        self.radio_lvl = _Follower(sr, block)
        self.mods = {
            "chorus": Pedalboard([Chorus(centre_delay_ms=7.0)]),
            "vibrato": Pedalboard([Chorus(centre_delay_ms=2.0, mix=1.0)]),
            "flanger": Pedalboard([Chorus(centre_delay_ms=1.5, depth=0.8)]),
            "phaser": Pedalboard([Phaser()]),
        }
        self.phase = 0.0                  # LFO / carrier phase for tremolo and robot
        self.echo = Pedalboard([Delay(feedback=0.35)])
        self.reverb = Pedalboard([Reverb(damping=0.5, width=1.0)])
        for k, v in params.items():
            self.set(k, v)
        self._apply_all()

    # ----- parameters -----
    def set(self, key, value):
        if key not in self.p:
            return
        self.p[key] = copy.deepcopy(value)
        if key == "peq":
            self.peq.set_bands(self.p["peq"])
            return
        self._apply_all()

    def _apply_all(self):
        p = self.p
        self.peq.set_bands(p["peq"])
        for f, k in zip(self.eq, ("eq_bass", "eq_mid", "eq_treble")):
            f.gain_db = float(p[k])
        a, r = float(p["mod_amount"]), float(p["mod_rate"])
        c = self.mods["chorus"][0]
        c.rate_hz, c.depth, c.mix = 0.2 + 2.8 * r, 0.1 + 0.4 * a, 0.5 * a
        v = self.mods["vibrato"][0]
        v.rate_hz, v.depth = 2.0 + 6.0 * r, 0.3 * a
        fl = self.mods["flanger"][0]
        fl.rate_hz, fl.feedback, fl.mix = 0.05 + 0.95 * r, 0.3 + 0.4 * a, 0.5 * a
        ph = self.mods["phaser"][0]
        ph.rate_hz, ph.depth, ph.feedback, ph.mix = 0.1 + 1.9 * r, 0.5 + 0.5 * a, 0.3, 0.6 * a
        d = self.echo[0]
        d.delay_seconds, d.mix = float(p["echo_time"]) / 1000, 0.5 * float(p["echo"])
        rv = self.reverb[0]
        # Wet/dry balanced so more reverb doesn't mean louder (headroom for Telegram etc).
        # JUCE's reverb scales dry x2 and wet x3 internally: dry_level 0.5 = unity.
        rv.room_size = float(p["reverb_size"])
        rv.wet_level = 0.12 * float(p["reverb"])
        rv.dry_level = 0.5 * (1.0 - 0.3 * float(p["reverb"]))

    # ----- processing -----
    def _run(self, board, x):
        return board(x[None, :], self.sr, reset=False)[0]

    def _lfo(self, freq, n):
        t = self.phase + 2 * np.pi * freq * np.arange(n) / self.sr
        self.phase = float((t[-1] + 2 * np.pi * freq / self.sr) % (2 * np.pi))
        return t

    def __call__(self, x):
        p = self.p
        if p["eq_bass"] or p["eq_mid"] or p["eq_treble"]:
            x = self._run(self.eq, x)
        if p["peq_on"] and self.peq.active():
            x = self.peq(x)
        if p["grit"] > 0:
            drive = 10 ** ((6 + 30 * p["grit"]) / 20)
            wet = self._run(self.grit_post, np.tanh(self._run(self.grit_pre, x) * drive))
            wet = wet * self.grit_lvl(x, wet, len(x))
            x = (1 - p["grit"] * 0.8) * x + p["grit"] * 0.8 * wet
        if p["radio"] > 0:
            bp = self._run(self.radio_bp, x)
            bp = bp * self.radio_lvl(x, bp, len(x))
            x = (1 - p["radio"]) * x + p["radio"] * bp
        mt, a, r = p["mod_type"], float(p["mod_amount"]), float(p["mod_rate"])
        if mt in self.mods and a > 0:
            x = self._run(self.mods[mt], x)
        elif mt == "tremolo" and a > 0:
            t = self._lfo(2.0 + 10.0 * r, len(x))
            x = x * (1 - 0.7 * a * (0.5 - 0.5 * np.cos(t))).astype(np.float32)
        elif mt == "robot" and a > 0:
            t = self._lfo(30.0 + 170.0 * r, len(x))
            x = (1 - a) * x + a * 1.4 * x * np.sin(t).astype(np.float32)
        if p["echo"] > 0:
            x = self._run(self.echo, x)
        if p["reverb"] > 0:
            x = self._run(self.reverb, x)
        return x.astype(np.float32)
