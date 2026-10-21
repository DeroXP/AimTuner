"""Frame pacing on the real display: a bot plays a scenario fullscreen for a while, and every
frame's start time, lateness against its slot, CPU work and swap time is reported.

    python tools/frametest.py [scenario] [seconds] [quality]      e.g. r6_place_ping 20 high

Uses a scratch config and log folder, never the real ones."""
import os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import tracks
from aimtuner.app import App
from aimtuner.bot import Bot

scn_id = sys.argv[1] if len(sys.argv) > 1 else "r6_place_ping"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
quality = sys.argv[3] if len(sys.argv) > 3 else "low"
scratch = tempfile.mkdtemp(prefix="aimtuner-frametest-")
opts = types.SimpleNamespace(windowed=bool(os.environ.get("FT_WINDOWED")), offscreen=None, bench=False, selftest=False, only=None, repeats=1,
                             shots=None, duration=secs + 10, cap=(int(os.environ["FT_CAP"]) if os.environ.get("FT_CAP") else None), exit_after=None, logdir=scratch,
                             config=os.path.join(scratch, "config.json"), fast=False)
app = App(opts)
app.cfg["video"]["quality"] = quality
app.cfg["game"]["capture"] = True
app.bot = Bot(app)
app.pace = []
app.start(tracks.BY_ID[scn_id])
t_end = time.perf_counter() + secs + 3.0
orig = app.frame_cap
import glfw
def stop_check():
    if time.perf_counter() > t_end:
        glfw.set_window_should_close(app.win, True)
    return orig()
app.frame_cap = stop_check
app.run()

rows = [r for r in app.pace if r[0] > app.pace[0][0] + 3.0]         # skip the countdown / warm-up
cap = orig() or 1000.0
starts = [r[0] for r in rows]
iv = sorted((b - a) * 1000 for a, b in zip(starts, starts[1:]))
late = sorted(r[1] * 1000 for r in rows)
work = sorted(r[2] * 1000 for r in rows)
swap = sorted(r[3] * 1000 for r in rows)
q = lambda a, p: a[min(int(len(a) * p), len(a) - 1)]
target = 1000.0 / cap
dev = sorted(abs(x - target) for x in iv)
print(f"{scn_id} {quality}  cap {cap} fps (slot {target:.3f} ms)  frames {len(iv)}  refresh {app.refresh_exact}")
print(f"interval   p1 {q(iv, .01):.3f}  median {q(iv, .5):.3f}  p99 {q(iv, .99):.3f}  p99.9 {q(iv, .999):.3f}  max {iv[-1]:.3f} ms")
print(f"off slot   median {q(dev, .5) * 1000:.0f} us  p99 {q(dev, .99) * 1000:.0f} us  max {dev[-1] * 1000:.0f} us  "
      f"frames >0.1 ms off {sum(d > 0.1 for d in dev)}  >0.5 ms off {sum(d > 0.5 for d in dev)}")
print(f"late start p99 {q(late, .99) * 1000:.0f} us  max {late[-1] * 1000:.0f} us")
print(f"margin     {getattr(app, '_margin', None)}")
print(f"cpu work   median {q(work, .5):.3f}  p99 {q(work, .99):.3f}  max {work[-1]:.3f} ms")
print(f"swap       median {q(swap, .5):.3f}  p99 {q(swap, .99):.3f}  max {swap[-1]:.3f} ms")
app.close()
