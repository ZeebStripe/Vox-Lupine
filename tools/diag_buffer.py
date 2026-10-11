"""Find the smallest jitter buffer that stops glitches on your devices.

Runs the real filter (your config.json devices) for a while at each buffer size and
counts underruns/drops. Then set PREFILL_BLOCKS / MAX_BUFFER_BLOCKS in engine.py.

Usage: python tools/diag_buffer.py [seconds per setting] [prefill/max ...]
   e.g. python tools/diag_buffer.py 30 2/6 3/10 4/10
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere
import engine as E  # noqa: E402

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20
combos = [tuple(map(int, a.split("/"))) for a in sys.argv[2:]] or [(p, p + 4) for p in (2, 3, 4, 5)]
for prefill, max_blocks in combos:
    E.PREFILL_BLOCKS = prefill
    E.MAX_BUFFER_BLOCKS = max_blocks
    print(f"[max {max_blocks} blocks] ", end="")
    e = E.Engine(E.load_config())
    e.start()
    time.sleep(secs)
    lat = e.latency_ms()
    e.stop()
    print(f"buffer {prefill} blocks ({prefill * 256 / 48:.1f} ms): underruns {e.underruns}, "
          f"drops {e.drops}, total delay ~{lat:.0f} ms", flush=True)
