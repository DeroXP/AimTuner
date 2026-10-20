"""Check the Controller Lab maths against synthetic pads with known properties, then capture the real pad."""
import os
import math, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aimtuner import lab
from aimtuner import pad as padmod

rng = random.Random(7)
fails = []


def check(name, got, want, tol):
    ok = got is not None and abs(got - want) <= tol
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got} (want {want} +/- {tol})")
    if not ok:
        fails.append(name)


def q(v, step):                 # quantise a -1..1 value to the pad's native step
    return int(round(v * 32767 / step)) * step


def row(t, pkt, btn=0, lt=0, rt=0, lx=0, ly=0, rx=0, ry=0):
    return (0, t, pkt, btn, lt, rt, lx, ly, rx, ry)


print("report rate: 8 kHz pad, 5 us timestamp noise, 1% of reports unseen")
rows, pkt, t = [], 0, 0.0
for i in range(40000):
    t = i * 125e-6
    pkt += 1
    if rng.random() < 0.01:
        continue                                    # this PC missed stamping it
    a = i * 0.004
    rows.append(row(t + rng.gauss(0, 5e-6), pkt, rx=q(math.cos(a), 1), ry=q(math.sin(a), 1)))
r = lab.report_rate(rows)
check("rate_hz", r["rate_hz"], 8000, 40)
check("jitter_us", r["jitter_us"], 7.1, 1.0)
check("untimed_pct", r["untimed_pct"], 1.0, 0.3)
check("steady_pct", r["steady_pct"], 100.0, 0.5)

print("report rate: pad that reports at 8 kHz but only has new data at 1 kHz")
rows = [row(i * 1e-3 + rng.gauss(0, 3e-6), i + 1, rx=q(math.cos(i * 0.03), 16)) for i in range(4000)]
check("rate_hz", lab.report_rate(rows)["rate_hz"], 1000, 5)

print("rim: reaches 100% except a weak spot at 94%, 12-bit stick")
rows = []
for i in range(30000):
    a = i * 0.0021
    rad = 0.94 if 1.0 < (a % (2 * math.pi)) < 1.3 else 1.0
    rows.append(row(i * 125e-6, i + 1, rx=q(rad * math.cos(a), 16), ry=q(rad * math.sin(a), 16)))
c = lab.circle(rows, "right")
check("reach_min_pct", c["reach_min_pct"], 94.0, 0.3)
check("max_deadzone", c["max_deadzone"], 93, 0)
check("reach_max_pct", c["reach_max_pct"], 100.0, 0.2)
res = lab.resolution(rows, "right")
check("step", res["step"], 16, 0)
check("bits", res["bits"], 12.0, 0.01)
print("  half a circle only ->", lab.circle(rows[:1200], "right"))

print("resolution: however fast or evenly the stick is rolled")


def rolled(scale, revs_per_s, seconds=6.0, wobble=0.0, seed=1):
    """Rim roll sampled at 8 kHz. scale(v) turns a -1..1 value into the pad's int16 output."""
    r2, out, a = random.Random(seed), [], 0.0
    for i in range(int(seconds * 8000)):
        speed = revs_per_s * (1.0 + wobble * math.sin(i * 0.0007) + wobble * 0.5 * r2.uniform(-1, 1))
        a += 2 * math.pi * speed * 125e-6
        rad = 0.99 + 0.01 * math.sin(a * 3.1)
        out.append(row(i * 125e-6, i + 1, rx=scale(rad * math.cos(a)), ry=scale(rad * math.sin(a))))
    return out


def bitrep12(v):                # 12-bit value widened the usual way: top bits copied into the bottom
    u = int(round((v + 1.0) / 2.0 * 4095))
    return ((u << 4) | (u >> 8)) - 32768


for label, scale, revs, wobble, bits in (
        ("12-bit, exactly 2 rev/s (every lap lands on the same samples)", lambda v: q(v, 16), 2.0, 0.0, 12.0),
        ("12-bit, slow uneven roll", lambda v: q(v, 16), 0.8, 0.3, 12.0),
        ("12-bit, fast uneven roll", lambda v: q(v, 16), 3.0, 0.3, 12.0),
        ("12-bit widened by bit copying, 1.5 rev/s", bitrep12, 1.5, 0.2, 12.0),
        ("16-bit, 1.5 rev/s uneven", lambda v: q(v, 1), 1.5, 0.2, 16.0),
        ("16-bit, exactly 2 rev/s", lambda v: q(v, 1), 2.0, 0.0, 16.0),
        ("10-bit, 1.5 rev/s uneven", lambda v: q(v, 64), 1.5, 0.2, 10.0),
        ("8-bit, 1.5 rev/s uneven", lambda v: q(v, 256), 1.5, 0.2, 8.0)):
    res = lab.resolution(rolled(scale, revs, wobble=wobble), "right")
    check(label, res and res["bits"], bits, 0.05)

print("rest: sits 0.6% off centre with a little noise")
rows = [row(i * 0.004, i + 1, rx=q(0.006 + rng.uniform(-0.002, 0.002), 1), ry=q(rng.uniform(-0.002, 0.002), 1)) for i in range(700)]
rs = lab.rest(rows, "right")
check("offset_pct", rs["offset_pct"], 0.6, 0.05)
check("min_deadzone", rs["min_deadzone"], 2, 0)
rs0 = lab.rest([row(0.0, 1), row(3.0, 2)], "right")
check("perfect stick min_deadzone", rs0["min_deadzone"], 0, 0)

print("flick: 30 ms throw, hold, release with a 12% overshoot")
rows, pkt = [], 0
def add(t, v, ang):
    global pkt
    pkt += 1
    rows.append(row(t, pkt, rx=q(v * math.cos(ang), 1), ry=q(v * math.sin(ang), 1)))
for n in range(6):
    t0, ang = n * 1.0, n * 1.1
    add(t0 - 0.05, 0.0, ang)
    for k in range(241):                            # throw out over 30 ms
        add(t0 + k * 125e-6, k / 240, ang)
    for k in range(800):                            # hold 100 ms
        add(t0 + 0.03 + k * 125e-6, 1.0, ang)
    for k in range(1200):                           # release: damped swing through centre
        tt = k * 125e-6
        add(t0 + 0.13 + tt, math.exp(-tt / 0.012) * math.cos(tt / 0.012 * 1.46), ang)
f = lab.flicks(rows, "right")
check("count", f["count"], 6, 0)
check("throw_median_ms", f["throw_median_ms"], 22.5, 0.6)
# the far side of the swing is where the derivative is zero (not where the cosine is -1)
ph = math.pi - math.atan(1 / 1.46)
check("overshoot_avg_pct", f["overshoot_avg_pct"], -100 * math.exp(-ph / 1.46) * math.cos(ph), 0.3)

print("taps: 60 ms presses, 70 ms gaps, one 1 ms bounce")
rows, pkt, t = [], 0, 0.0
for n in range(20):
    pkt += 1; rows.append(row(t, pkt, btn=padmod.BIT["RB"]))
    if n == 7:
        pkt += 1; rows.append(row(t + 0.020, pkt)); pkt += 1; rows.append(row(t + 0.021, pkt, btn=padmod.BIT["RB"]))
    pkt += 1; rows.append(row(t + 0.060, pkt)); t += 0.130
tp = lab.taps(rows, padmod.BIT["RB"])
check("per_second", tp["per_second"], 1 / 0.130, 0.5)
check("bounces", tp["bounces"], 1, 0)

print("chord: RB lands 6 ms after LB")
rows, pkt = [], 0
for n in range(8):
    t0 = n * 0.5
    d = 0.006 + rng.gauss(0, 0.001)
    for tt, b in ((t0, padmod.BIT["LB"]), (t0 + d, padmod.BIT["LB"] | padmod.BIT["RB"]), (t0 + 0.1, 0)):
        pkt += 1; rows.append(row(tt, pkt, btn=b))
ch = lab.chord(rows, padmod.BIT["LB"], padmod.BIT["RB"])
check("mean_ms", ch["mean_ms"], 6.0, 1.0)

print("trigger: analogue, 25 ms pulls to 100%")
rows, pkt = [], 0
for n in range(4):
    for k in range(201):
        pkt += 1; rows.append(row(n * 0.6 + k * 125e-6, pkt, rt=int(255 * k / 200)))
    pkt += 1; rows.append(row(n * 0.6 + 0.2, pkt, rt=0))
tg = lab.trigger(rows, "RT")
check("max_pct", tg["max_pct"], 100.0, 0.1)
check("pull_median_ms", tg["pull_median_ms"], 25 * (230 - 25) / 255, 1.0)
print("  mode:", tg["mode"], "| untouched trigger ->", lab.trigger(rows, "LT"))

print("\nREAL PAD, 3 s capture at rest")
p = padmod.Pad()
t_end = time.perf_counter() + 0.8
while time.perf_counter() < t_end:
    p.update(time.perf_counter()); time.sleep(0.01)
print("  device:", p.info())
if p.info() and p.info()["backend"] == "xinput":
    rows = p.capture_start()
    t_end = time.perf_counter() + 3.0
    while time.perf_counter() < t_end:               # main thread idles like the Lab screen does
        p.update(time.perf_counter()); time.sleep(0.05)
    p.capture_stop()
    rr = lab.report_rate(rows)
    print("  rows:", len(rows))
    if rr:
        print("  ", {k: v for k, v in rr.items() if k != "hist"})
        print("   hist:", rr["hist"])
    for s in ("left", "right"):
        print(f"   rest {s}:", lab.rest(rows, s), "| resolution:", lab.resolution(rows, s))
p.stop()
print("\nlab checks:", "all passed" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
