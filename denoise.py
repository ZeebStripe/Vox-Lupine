"""Real-time noise reduction for steady background noise (fans, PC hum, hiss, AC).

The noise gate only silences the gaps *between* words. This stage also takes the
noise out from *under* your voice, which matters because the tone stages that
follow (voice style, brightness, levelling) raise the noise along with the voice.

How it works (short-time spectral gating):
- Each block is analysed in overlapping 768-sample frames (16 ms, 62.5 Hz bins).
- A noise profile per frequency bin is learned while the gate says you're quiet,
  and can only fall (never rise) while you talk, so speech isn't learned as noise.
- Each bin is turned down by how close it is to the noise profile, with a floor so
  it never cuts more than `max_cut_db` (deeper cuts sound watery/"underwater").
- Gains rise instantly (word starts stay crisp) and fall over ~20 ms, and are
  smoothed across neighbouring bins, which avoids "musical noise" twinkles.

Latency is frame - hop = 512 samples, the same as the gate's lookahead, so the two
share it and this stage adds no delay. With strength 0 the output is exactly the
input delayed by 512 samples (perfect reconstruction), so it can stay in the chain.
"""
import numpy as np


class Denoise:
    FRAME, HOP = 768, 256          # periodic sqrt-Hann at 1/3 overlap is exactly COLA

    def __init__(self, sr, strength=0.5):
        assert sr in (44100, 48000), "tuned for 44.1/48 kHz"
        n, h = self.FRAME, self.HOP
        self.sr = sr
        self.latency = n - h
        w = np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)
        self.win = w
        self.ola_scale = np.float32(1 / 1.5)        # sum of Hann at hop N/3
        self.inbuf = np.zeros(n, dtype=np.float32)
        self.outbuf = np.zeros(n, dtype=np.float32)
        bins = n // 2 + 1
        self.noise = None                           # learned noise power per bin
        self.frames_learned = 0
        self.power = np.zeros(bins, dtype=np.float32)
        self.slow = np.zeros(bins, dtype=np.float32)
        self.gain = np.ones(bins, dtype=np.float32)
        hop_s = h / sr
        self.power_k = np.float32(np.exp(-hop_s / 0.020))   # power smoothing ~20 ms
        self.slow_k = np.float32(np.exp(-hop_s / 0.400))    # slow average ~400 ms
        self.fall = np.float32(np.exp(-hop_s / 0.020))      # gain release ~20 ms
        self.learn_slow = np.float32(np.exp(-hop_s / 1.0))  # noise profile follows ~1 s
        self.set_strength(strength)

    def set_strength(self, s):
        """0 = off, 1 = strongest. Maps to the deepest cut and how hard noise is pushed."""
        self.strength = float(min(max(s, 0.0), 1.0))
        self.floor = np.float32(10 ** (-18 * self.strength / 20))   # up to -18 dB
        self.over = np.float32(1.0 + 2.0 * self.strength)           # oversubtraction

    def _frame(self, speech):
        spec = np.fft.rfft(self.inbuf * self.win)
        p = (spec.real ** 2 + spec.imag ** 2).astype(np.float32)
        # Noise power jitters a lot from frame to frame; average over ~20 ms so random
        # peaks don't keep re-opening the gain. (Not across bins: voice harmonics are
        # only ~3 bins apart, and spreading them would keep the gaps between them open.)
        self.power = self.power * self.power_k + p * (1 - self.power_k)
        self.slow = self.slow * self.slow_k + p * (1 - self.slow_k)
        # --- learn the noise profile ---
        if self.noise is None:
            self.noise = self.power.copy()
        elif not speech:
            # Learn fast for the first ~0.5 s of quiet, then settle to a slow average.
            k = np.float32(0.7) if self.frames_learned < 90 else self.learn_slow
            self.noise = self.noise * k + self.power * (1 - k)
            self.frames_learned += 1
        else:
            # While talking, only let the profile fall, and only to a ~400 ms average
            # (following brief dips would learn the noise too low).
            self.noise = np.minimum(self.noise, self.slow)
        if self.strength <= 0.0:
            return spec                               # exact pass-through
        # --- gain per bin (power subtraction with a floor) ---
        g = 1.0 - self.over * self.noise / (self.power + 1e-20)
        g = np.sqrt(np.clip(g, 0.0, 1.0)).astype(np.float32)
        g = np.maximum(g, self.floor)
        g[1:-1] = 0.15 * g[:-2] + 0.7 * g[1:-1] + 0.15 * g[2:]      # across frequency
        self.gain = np.maximum(g, self.gain * self.fall + g * (1 - self.fall))  # rise fast
        return spec * self.gain

    def __call__(self, x, speech):
        """x: 1-D float32 block (a multiple of HOP). `speech`: the gate's open state.
        Returns the cleaned block, delayed by `latency` samples."""
        h, n = self.HOP, self.FRAME
        out = np.empty(len(x), dtype=np.float32)
        for i in range(0, len(x), h):
            self.inbuf[:-h] = self.inbuf[h:]
            self.inbuf[-h:] = x[i:i + h]
            y = np.fft.irfft(self._frame(speech), n).astype(np.float32) * self.win
            self.outbuf += y * self.ola_scale
            out[i:i + h] = self.outbuf[:h]
            self.outbuf[:-h] = self.outbuf[h:]
            self.outbuf[-h:] = 0.0
        return out
