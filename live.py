"""Command-line version of Vox Lupine (the app is app.py).

  python live.py            run with config.json
  python live.py --list     list audio devices
  python live.py --monitor  also play into your headphones
  python live.py --selftest [file.wav]
                            check the live path against offline rendering
                            (uses a synthetic voice-like signal unless you give a WAV)

Stop with Ctrl+C.
"""
import sys
import time

import numpy as np
import sounddevice as sd

from engine import PREFILL_BLOCKS, Engine, _Output, load_config, make_voice


def run(monitor=False):
    cfg = load_config()
    cfg["monitor"] = cfg["monitor"] or monitor
    e = Engine(cfg)
    e.start()
    print(f"in : {e.names['in']}\nout: {e.names['out']}" + (f"\nmon: {e.names['mon']}" if monitor else ""))
    print(f"voice: preset={cfg['preset']} warmth={cfg['warmth']} bright={cfg['bright']} "
          f"cleanup={cfg['cleanup']}  |  delay ~{e.latency_ms():.0f} ms  |  Ctrl+C to stop")
    try:
        while True:
            time.sleep(2)
            print(f"\rCPU {e.cpu_ms():.2f} ms/block | in {e.in_db:5.0f} dB | out {e.out_db:5.0f} dB"
                  f" | underruns {e.underruns} | drops {e.drops}   ", end="", flush=True)
    except KeyboardInterrupt:
        e.stop()
        print("\nstopped")


def test_signal(sr, seconds=12):
    """Voice-like test audio: a gliding 120-220 Hz harmonic tone shaped into
    'syllables', with pauses and a little breath noise."""
    rng = np.random.default_rng(0)
    t = np.arange(int(seconds * sr)) / sr
    f0 = 170 + 50 * np.sin(2 * np.pi * 0.3 * t)
    phase = 2 * np.pi * np.cumsum(f0) / sr
    voice = sum(np.sin(k * phase) / k for k in range(1, 25))
    syll = np.clip(np.sin(2 * np.pi * 3.5 * t), 0, None) * (np.sin(2 * np.pi * 0.25 * t) > -0.3)
    x = 0.08 * voice * syll + 0.002 * rng.normal(size=len(t))
    return x.astype(np.float32)


def load_test_audio(path, sr):
    import soundfile as sf
    x, file_sr = sf.read(path, dtype="float32")
    x = x.mean(axis=1) if x.ndim > 1 else x
    if file_sr != sr:   # simple resample; fine for a test
        n = int(len(x) * sr / file_sr)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def selftest(path=None):
    """1) The engine's callbacks reproduce offline rendering exactly.
    2) Changing settings live gives exactly the same sound as starting with them."""
    from cleanup import process_blocks
    cfg = load_config()
    sr, block = cfg["samplerate"], cfg["block"]
    x = load_test_audio(path, sr) if path else test_signal(sr)

    def drive(changes_at=None):
        e = Engine(dict(cfg))
        e.voice = make_voice(e.cfg)
        e.main = _Output(e, None, sr, block)
        out = []
        for n, i in enumerate(range(0, len(x) - block + 1, block)):
            for key, val in (changes_at or {}).get(n, []):
                e.set(key, val)
            e._on_input(x[i:i + block, None], block, None, None)
            buf = np.zeros((block, 1), dtype=np.float32)
            e.main._cb(buf, block, None, None)
            out.append(buf[:, 0])
        return np.concatenate(out), e

    live, e = drive()
    ref = process_blocks(make_voice(cfg), x, block)
    lag = e.voice.latency + (PREFILL_BLOCKS - 1) * block
    n = min(len(live) - lag, len(ref))
    print(f"1) live path vs offline render: max difference {np.abs(live[lag:lag + n] - ref[:n]).max():.1e}"
          f" (0 = identical) | underruns {e.underruns}, drops {e.drops}")

    # Run 2 s with a different sound, then switch to the config via live controls.
    alt = dict(cfg, preset="subtle", warmth=1.2, bright=0.0, volume_db=-6.0)
    switch_block = 2 * sr // block
    switch = {switch_block: [(k, cfg[k]) for k in ("preset", "warmth", "bright", "volume_db")]}
    cfg_backup = dict(cfg)
    cfg.update(alt)
    live2, _ = drive(switch)
    cfg.clear()
    cfg.update(cfg_backup)
    # Before the switch the two runs must differ; ~1 s after, filters/compressor/warmth
    # follower have settled and the runs should converge.
    s = switch_block * block
    m = min(len(live2), len(live))
    rms = lambda a: 20 * np.log10(np.sqrt(np.mean(a ** 2)) + 1e-12)
    before = rms(live2[sr:s] - live[sr:s]) - rms(live[sr:s])
    after = rms(live2[s + sr:m] - live[s + sr:m]) - rms(live[s + sr:m])
    print(f"2) mid-stream slider changes: difference vs. starting with those settings is "
          f"{before:+.0f} dB before the switch, {after:+.0f} dB from 1 s after (very negative = identical)")


if __name__ == "__main__":
    if "--list" in sys.argv:
        print(sd.query_devices())
    elif "--selftest" in sys.argv:
        rest = sys.argv[sys.argv.index("--selftest") + 1:]
        selftest(rest[0] if rest else None)
    else:
        run(monitor="--monitor" in sys.argv)
