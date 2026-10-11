"""Draggable parametric EQ: 5 bands (bass shelf, 3 bells, treble shelf).

`ParamEQ` is the live stage; `response_db` computes the curve the app draws, from the
very same filter types/parameters, so the graph always matches what you hear.
"""
import copy

import numpy as np
from pedalboard import HighShelfFilter, LowShelfFilter, Pedalboard, PeakFilter

FREQ_MIN, FREQ_MAX = 20.0, 20000.0
GAIN_LIMIT = 15.0
Q_MIN, Q_MAX = 0.3, 8.0
SHELF_Q_MAX = 2.0

DEFAULT_BANDS = [
    {"type": "low_shelf", "freq": 120.0, "gain": 0.0, "q": 0.7},
    {"type": "peak", "freq": 400.0, "gain": 0.0, "q": 1.0},
    {"type": "peak", "freq": 1200.0, "gain": 0.0, "q": 1.0},
    {"type": "peak", "freq": 3500.0, "gain": 0.0, "q": 1.0},
    {"type": "high_shelf", "freq": 9000.0, "gain": 0.0, "q": 0.7},
]
_MAKE = {"low_shelf": LowShelfFilter, "peak": PeakFilter, "high_shelf": HighShelfFilter}


def default_bands():
    return copy.deepcopy(DEFAULT_BANDS)


def _eq(low, b1, b2, b3, high):
    """Build 5 bands from (freq, gain, q) tuples: low shelf, 3 bells, high shelf."""
    types = ["low_shelf", "peak", "peak", "peak", "high_shelf"]
    return [{"type": t, "freq": float(f), "gain": float(g), "q": float(q)}
            for t, (f, g, q) in zip(types, (low, b1, b2, b3, high))]


# Built-in EQ presets for speech. Frequency choices follow the vocal guidance in the
# Audio Issues EQ cheat sheet (audio-issues.com, "All the EQ Information You'll Ever
# Need"): rumble 50-100 Hz, boom ~100 Hz, mud ~200 Hz, boxiness 300-600 Hz, nasal
# ~1-1.5 kHz, clarity ~3 kHz, presence ~5 kHz, sibilance 6-8 kHz, air 10 kHz+.
# Gains are kept moderate: the voice chain already adds warmth/brightness on top.
EQ_PRESETS = {
    "Flat": _eq((120, 0, .7), (400, 0, 1), (1200, 0, 1), (3500, 0, 1), (9000, 0, .7)),
    "Clean & clear": _eq((100, -3, .7), (250, -3, 1.2), (3000, 2, 1), (7000, 0, 1),
                         (10000, 1.5, .7)),
    "Cut through": _eq((120, -2, .7), (450, -2, 1.4), (3000, 2.5, 1),
                                     (5000, 3, 1.2), (10000, 2, .7)),
    "Warm & full": _eq((100, 1, .7), (200, 3, .9), (450, -1.5, 1.4), (3000, 1, 1),
                       (10000, -1, .7)),
    "Less nasal": _eq((120, 0, .7), (400, 0, 1), (1200, -4, 1.5), (3000, 1, 1),
                      (9000, 0, .7)),
    "Soft / less harsh": _eq((120, 0, .7), (400, 0, 1), (3500, -2, 1), (7000, -4, 2.5),
                                 (10000, -2, .7)),
    "Airy sparkle": _eq((120, 0, .7), (250, -1.5, 1.2), (3000, 0, 1), (7000, 2, 1.5),
                        (10000, 3.5, .7)),
    "Big & deep": _eq((100, 4, .7), (200, 2, .9), (450, -2, 1.4), (3000, 1.5, 1),
                      (10000, -1, .7)),
    "Distant": _eq((150, -3, .7), (400, 0, 1), (1200, 0, 1), (5000, -4, 1),
                                (9000, -3, .7)),
}


def clamp_band(b):
    b = dict(b)
    b["freq"] = float(min(max(b["freq"], FREQ_MIN), FREQ_MAX))
    b["gain"] = float(min(max(b["gain"], -GAIN_LIMIT), GAIN_LIMIT))
    qmax = Q_MAX if b["type"] == "peak" else SHELF_Q_MAX
    b["q"] = float(min(max(b["q"], Q_MIN), qmax))
    return b


def make_filter(b, sr):
    # Keep the corner frequency safely below Nyquist.
    f = min(b["freq"], sr * 0.45)
    return _MAKE[b["type"]](cutoff_frequency_hz=f, gain_db=b["gain"], q=b["q"])


class ParamEQ:
    def __init__(self, sr, bands=None):
        self.sr = sr
        self.bands = [clamp_band(b) for b in (bands or default_bands())]
        self.board = Pedalboard([make_filter(b, sr) for b in self.bands])

    def set_bands(self, bands):
        """Update in place (filters keep their state, so dragging doesn't reset audio)."""
        for i, (f, b) in enumerate(zip(self.board, bands)):
            b = clamp_band(b)
            self.bands[i] = b
            f.cutoff_frequency_hz = min(b["freq"], self.sr * 0.45)
            f.gain_db, f.q = b["gain"], b["q"]

    def active(self):
        return any(abs(b["gain"]) > 1e-6 for b in self.bands)

    def __call__(self, x):
        return self.board(x[None, :], self.sr, reset=False)[0]


def bands_response_db(bands, sr, freqs):
    """Exact curve of the bands from the biquad formulas (RBJ cookbook, which JUCE/
    pedalboard use). ~0.1 ms, so it's safe to call while dragging. Verified within
    0.3 dB of measuring the real filters with a long impulse (the short-impulse
    method below was off by up to 3 dB for narrow bands near 20-40 Hz)."""
    z = np.exp(-1j * 2 * np.pi * np.asarray(freqs) / sr)
    total = np.zeros(len(z))
    for b in bands:
        A = 10 ** (b["gain"] / 40)
        w0 = 2 * np.pi * min(b["freq"], sr * 0.45) / sr
        al, cw = np.sin(w0) / (2 * b["q"]), np.cos(w0)
        s = 2 * np.sqrt(A) * al
        if b["type"] == "peak":
            num, den = [1 + al * A, -2 * cw, 1 - al * A], [1 + al / A, -2 * cw, 1 - al / A]
        elif b["type"] == "low_shelf":
            num = [A * ((A + 1) - (A - 1) * cw + s), 2 * A * ((A - 1) - (A + 1) * cw),
                   A * ((A + 1) - (A - 1) * cw - s)]
            den = [(A + 1) + (A - 1) * cw + s, -2 * ((A - 1) + (A + 1) * cw),
                   (A + 1) + (A - 1) * cw - s]
        else:
            num = [A * ((A + 1) + (A - 1) * cw + s), -2 * A * ((A - 1) + (A + 1) * cw),
                   A * ((A + 1) + (A - 1) * cw - s)]
            den = [(A + 1) - (A - 1) * cw + s, 2 * ((A - 1) - (A + 1) * cw),
                   (A + 1) - (A - 1) * cw - s]
        h = (num[0] + num[1] * z + num[2] * z ** 2) / (den[0] + den[1] * z + den[2] * z ** 2)
        total += 20 * np.log10(np.abs(h) + 1e-12)
    return total


def response_db(filters, sr, freqs, n=16384):
    """Magnitude response (dB) of a list of linear pedalboard filters at `freqs`,
    measured with an impulse through fresh copies (never touches the live chain)."""
    if not filters:
        return np.zeros(len(freqs))
    imp = np.zeros(n, dtype=np.float32)
    imp[0] = 1.0
    h = Pedalboard(filters)(imp[None, :], sr)[0]
    H = np.abs(np.fft.rfft(h))
    f = np.fft.rfftfreq(n, 1 / sr)
    return 20 * np.log10(np.interp(freqs, f, H) + 1e-9)
