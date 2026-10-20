"""Drive the Controller Lab screen offscreen with a synthetic 8 kHz pad; screenshots + result checks."""
import json, math, os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import lab, labscreen
from aimtuner import pad as padmod
from aimtuner.app import App

scratch = tempfile.mkdtemp(prefix="aimtuner-lab-")
shots = sys.argv[1] if len(sys.argv) > 1 else os.path.join(scratch, "shots")
opts = types.SimpleNamespace(windowed=False, offscreen=(1920, 1080), bench=False, selftest=True, only=None,
                             repeats=1, shots=None, duration=None, cap=None, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
app.pad.use_virtual("bot")
errors = []


def expect(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


def q(v, step=16):
    return int(round(max(-1.0, min(1.0, v)) * 32767 / step)) * step


class Feeder:
    """Generates driver rows at 8 kHz for whatever step the Lab is recording."""

    def __init__(self):
        self.t = time.perf_counter()

    def feed(self, now, key, t0):
        B = padmod.BIT
        while self.t < now:
            self.t += 125e-6
            s = self.t - t0
            btn = lt = rt = 0
            lx = 0.015
            ly = rx = ry = 0.0
            rx = 0.003
            if key == "circle_r":
                a = s * 2 * math.pi * 2.0
                rx, ry = 0.985 * math.cos(a), 0.985 * math.sin(a)
            elif key == "circle_l":
                a = s * 2 * math.pi * 2.0
                rad = 0.95 if 0.4 < (a % (2 * math.pi)) < 0.8 else 1.0
                lx, ly = rad * math.cos(a), rad * math.sin(a)
            elif key == "flick":
                ph, ang = s % 1.2, int(s / 1.2) * 1.3
                if ph < 0.025:
                    v = ph / 0.025
                elif ph < 0.175:
                    v = 1.0
                else:
                    tt = ph - 0.175
                    v = math.exp(-tt / 0.010) * math.cos(tt / 0.010 * 1.3)
                rx, ry = v * math.cos(ang), v * math.sin(ang)
            elif key == "tap":
                if s % 0.115 < 0.055:
                    btn |= B["RB"]
            elif key == "chord":
                ph = s % 0.6
                if 0.1 <= ph < 0.2:
                    btn |= B["LB"]
                if 0.105 <= ph < 0.2:
                    btn |= B["RB"]
            elif key == "trig":
                ph = s % 1.0
                v = min(ph / 0.03, 1.0) if ph < 0.4 else 0.0
                lt = rt = int(255 * v)
            app.pad.inject(self.t, btn, lt, rt, q(lx), q(ly), q(rx), q(ry))


def script():
    L = labscreen.Lab(app)
    app.push(L)
    feeder = Feeder()
    for _ in range(10):
        feeder.feed(time.perf_counter(), None, 0)
        yield
    app.pending_shot = os.path.join(shots, "lab_1_intro.png")
    yield
    app.keys.add("enter")
    yield
    while L.state != "results":
        feeder.feed(time.perf_counter(), labscreen.STEPS[L.i][0] if L.state == "capturing" else None, L.t0)
        if L.state == "ready" and time.perf_counter() >= L.lock:
            if L.i == 1 and not os.path.exists(os.path.join(shots, "lab_2_ready.png")):
                app.pending_shot = os.path.join(shots, "lab_2_ready.png")
                yield
            app.keys.add("enter")
        elif L.state == "capturing" and L.i == 1 and time.perf_counter() - L.t0 > 2.0 \
                and not os.path.exists(os.path.join(shots, "lab_3_capturing.png")):
            app.pending_shot = os.path.join(shots, "lab_3_capturing.png")
        yield
    for _ in range(5):
        yield
    app.pending_shot = os.path.join(shots, "lab_4_results.png")
    yield
    yield
    rec = L.rec
    print(json.dumps({k: v for k, v in rec.items() if k not in ("aim_stick", "move_stick")}, default=str)[:900])
    rep = rec["report"]
    expect(rep and abs(rep["rate_hz"] - 8000) < 80, f"rate ~8000: {rep and rep['rate_hz']}")
    expect(abs(rec["aim_stick"]["rim"]["reach_min_pct"] - 98.5) < 0.4, f"aim reach: {rec['aim_stick']['rim']}"[:120])
    expect(rec["aim_stick"]["rim"]["max_deadzone"] == 97, "aim max deadzone 97")
    expect(rec["move_stick"]["rim"]["max_deadzone"] == 94, f"move max deadzone 94: {rec['move_stick']['rim']['max_deadzone']}")
    # rests 1.51% off centre; the rule keeps half a percent clear above that, so 3 and not 2
    expect(rec["move_stick"]["rest"]["min_deadzone"] == 3, f"move min deadzone: {rec['move_stick']['rest']}")
    expect(rec["aim_stick"]["resolution"]["bits"] == 12.0, f"resolution: {rec['aim_stick']['resolution']}")
    fl = rec["aim_stick"]["flick"]
    ph = math.pi - math.atan(1 / 1.3)               # far side of the damped swing: where its derivative is zero
    want = -100 * math.exp(-ph / 1.3) * math.cos(ph)
    expect(fl and 6 <= fl["count"] <= 9 and abs(fl["overshoot_avg_pct"] - want) < 0.5, f"flicks (want {want:.1f}% overshoot): {fl}")
    expect(abs(rec["shoot"]["per_second"] - 8.7) < 0.3 and rec["shoot"]["bounces"] == 0, f"taps: {rec['shoot']}")
    expect(abs(rec["chord"]["mean_ms"] - 5.0) < 0.6, f"chord: {rec['chord']}")
    expect(rec["triggers"]["RT"]["mode"] == "analogue" and rec["triggers"]["RT"]["max_pct"] == 100.0, f"trigger: {rec['triggers']['RT']}")
    expect(len(lab.load(scratch)) == 1, "result written to lab.jsonl")
    expect(not app.capturing and app.pad._cap is None, "capture released")


class Auto:
    def __init__(self):
        self.gen = script()

    def step(self, now):
        try:
            next(self.gen)
        except StopIteration:
            app.quit()


app.auto = Auto()
app.run()
print("lab ui:", "all passed" if not errors else f"FAILED {errors}")
sys.exit(1 if errors else 0)
