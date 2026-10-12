"""Real-time cleanup stage: rumble filter, de-popper, de-esser, noise reduction,
noise gate.

Processes audio block by block (default 256 samples = 5.3 ms at 48 kHz) so the
exact same code runs in previews and live. Detection is relative to the current
voice level, so it works regardless of mic gain.

De-pop / de-ess use a split-band design: a steep detector decides *when* a pop or
harsh 's' happens, and only the low (or high) part of the signal is turned down
for that moment. The band split is a complementary first-order pair, which sums
back to the original exactly when no reduction is applied.
"""
import numpy as np
from pedalboard import HighpassFilter, LowpassFilter, Pedalboard

from denoise import Denoise


def _db(x):
    return 10 * np.log10(np.mean(x ** 2) + 1e-12)


class _BandDucker:
    """Turns one band down while a detector band dominates the signal."""

    def __init__(self, det, lo, hi, duck_low, dominance_db, ratio, max_cut_db, release_ms, sr, block):
        self.det, self.lo, self.hi = det, lo, hi
        self.duck_low = duck_low
        self.dominance_db, self.ratio, self.max_cut_db = dominance_db, ratio, max_cut_db
        self.release = np.exp(-block / (release_ms * 1e-3 * sr))
        self.cut_db = 0.0
        self.sr = sr

    def __call__(self, x, total_db):
        det_db = _db(self.det(x, self.sr, reset=False))
        over = det_db - total_db - self.dominance_db          # how far the band dominates
        target = -min(max(over, 0) * (1 - 1 / self.ratio), self.max_cut_db)
        prev = self.cut_db
        self.cut_db = target if target < prev else prev * self.release + target * (1 - self.release)
        lo, hi = self.lo(x, self.sr, reset=False), self.hi(x, self.sr, reset=False)
        g = 10 ** (np.linspace(prev, self.cut_db, len(x), dtype=np.float32) / 20)  # no zipper
        return (lo * g + hi) if self.duck_low else (lo + hi * g)


class _LookaheadGate:
    """Noise gate that ignores short clicks and follows the room's noise floor.

    - Keyed on the voice band (300 Hz-3 kHz) so fan rumble/hiss don't open it.
    - Threshold = tracked noise floor + open_above_db (adapts to the room).
    - Only opens if the key stays above threshold for `min_open_ms` (speech does,
      keyboard clicks don't). The audio is delayed by the same amount, so word
      starts aren't clipped: that delay is this stage's latency.
    """

    SUB = 64  # analysis hop in samples

    def __init__(self, sr, min_open_ms=10.7, open_above_db=14, hysteresis_db=4,
                 hold_ms=150, release_ms=100, range_db=-30):
        self.sr = sr
        self.key = Pedalboard([HighpassFilter(cutoff_frequency_hz=300),
                               HighpassFilter(cutoff_frequency_hz=300),
                               LowpassFilter(cutoff_frequency_hz=3000),
                               LowpassFilter(cutoff_frequency_hz=3000)])
        sub_s = self.SUB / sr
        self.need = max(1, round(min_open_ms * 1e-3 / sub_s))
        self.latency = self.need * self.SUB
        self.delay = np.zeros(self.latency, dtype=np.float32)
        self.open_above, self.hyst = open_above_db, hysteresis_db
        self.hold_subs = round(hold_ms * 1e-3 / sub_s)
        self.rel = np.exp(-sub_s / (release_ms * 1e-3))
        self.floor_rise = 3.0 * sub_s                     # dB per hop (3 dB/s)
        self.range_db = range_db
        self.floor, self.is_open, self.run, self.held = -70.0, False, 0, 0
        self.smooth, self.smooth_k = None, np.exp(-sub_s / 0.020)
        self.gain_db = range_db

    def _hop(self, db):
        # Floor follows the *average* noise level (smoothed ~20 ms), not its dips.
        self.smooth = db if self.smooth is None else self.smooth * self.smooth_k + db * (1 - self.smooth_k)
        self.floor = min(self.smooth, self.floor + self.floor_rise)
        thr = max(self.floor + self.open_above, -70.0)
        if not self.is_open:
            self.run = self.run + 1 if db > thr else 0
            if self.run >= self.need:
                self.is_open, self.held = True, 0
        else:
            self.held = 0 if db > thr - self.hyst else self.held + 1
            if self.held > self.hold_subs:
                self.is_open, self.run = False, 0
        target = 0.0 if self.is_open else self.range_db
        # Opening is instant (lookahead already covers the onset); closing is smooth.
        self.gain_db = target if target > self.gain_db else self.gain_db * self.rel + target * (1 - self.rel)
        return self.gain_db

    def delay_only(self, x):
        buf = np.concatenate([self.delay, x])
        out, self.delay = buf[:len(x)], buf[len(x):]
        return out

    def __call__(self, x):
        return self.delay_only(x) * self.gains(x)

    def gains(self, x):
        """Gate gain for the block leaving the delay line (advances the gate's state).
        Gain decided at input time t is applied to output time t, which carries
        input from t - latency. A sustained onset at s confirms at s + latency,
        i.e. exactly when that onset leaves the delay line."""
        k = self.key(x[None, :], self.sr, reset=False)[0]
        g = np.empty(len(x), dtype=np.float32)
        for i in range(0, len(x), self.SUB):
            seg = k[i:i + self.SUB]
            prev = self.gain_db
            new = self._hop(10 * np.log10(np.mean(seg ** 2) + 1e-12))
            g[i:i + len(seg)] = np.linspace(prev, new, len(seg), endpoint=False)
        return 10 ** (g / 20)


class Cleanup:
    def __init__(self, sr, block=256, denoise=0.5):
        self.sr = sr
        # 12 dB/oct rumble filter: desk bumps, AC rumble, breath thumps.
        self.rumble = Pedalboard([HighpassFilter(cutoff_frequency_hz=80),
                                  HighpassFilter(cutoff_frequency_hz=80)])
        # De-pop: trigger when <120 Hz suddenly dominates; duck everything below 150 Hz.
        self.depop = _BandDucker(
            det=Pedalboard([LowpassFilter(cutoff_frequency_hz=120) for _ in range(3)]),
            lo=Pedalboard([LowpassFilter(cutoff_frequency_hz=150)]),
            hi=Pedalboard([HighpassFilter(cutoff_frequency_hz=150)]),
            duck_low=True, dominance_db=-6, ratio=10, max_cut_db=24,
            release_ms=60, sr=sr, block=block)
        # De-ess: trigger when 5-10 kHz dominates; duck everything above 4.5 kHz.
        self.deess = _BandDucker(
            det=Pedalboard([HighpassFilter(cutoff_frequency_hz=5000) for _ in range(3)]
                           + [LowpassFilter(cutoff_frequency_hz=10000)]),
            lo=Pedalboard([LowpassFilter(cutoff_frequency_hz=4500)]),
            hi=Pedalboard([HighpassFilter(cutoff_frequency_hz=4500)]),
            # Measured: normal speech sits ~-35 dB here, harsh 's' reaches -6..-8 dB.
            duck_low=False, dominance_db=-16, ratio=4, max_cut_db=10,
            release_ms=40, sr=sr, block=block)
        # Gate: silences keyboard clicks and fan noise between words.
        self.gate = _LookaheadGate(sr)
        # Noise reduction: steady noise *under* the voice too. Its frame delay equals
        # the gate's lookahead, so both share it and the stage adds no extra latency.
        self.denoise = Denoise(sr, denoise)
        assert self.denoise.latency == self.gate.latency
        self.latency = self.gate.latency
        self.enabled = True

    def __call__(self, x):
        """x: 1-D float32 block. Returns cleaned block of the same length."""
        if not self.enabled:   # pass through, but keep the same delay so toggling doesn't jump
            return self.gate.delay_only(x)
        x = self.rumble(x[None, :], self.sr, reset=False)[0]
        total = _db(x)
        x = self.depop(x[None, :], total)[0]
        x = self.deess(x[None, :], total)[0]
        g = self.gate.gains(x)
        self.gate.delay_only(x)       # keep the plain delay line current for bypass
        return self.denoise(x, speech=self.gate.is_open) * g


def process_blocks(fn, x, block=256):
    """Run a block processor over a whole file, exactly like live streaming would.
    The processor's lookahead latency is removed so output lines up with input."""
    lat = getattr(fn, "latency", 0)
    x = np.concatenate([x, np.zeros(lat + block, dtype=np.float32)])
    out = np.empty_like(x)
    for i in range(0, len(x) - block + 1, block):
        out[i:i + block] = fn(x[i:i + block])
    return out[lat:lat + len(x) - lat - block]
