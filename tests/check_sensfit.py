"""Does the sensitivity finder's fit (the real aimtuner.sensfit) recover a known optimum from noisy
blocks - near the start, far from it, off the end of the scale, and when the curve is lopsided?"""
import os
import math, random, statistics as st, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aimtuner import sensfit

SHAPES = {
    "symmetric": lambda x: math.exp(-1.6 * x * x),
    "flat top": lambda x: math.exp(-6.0 * x ** 4),
    "fast side steeper": lambda x: math.exp(-1.6 * math.expm1(x) ** 2),
    "slow side steeper": lambda x: math.exp(-1.6 * math.expm1(-x) ** 2),
    "kinked": lambda x: math.exp(-(2.6 if x > 0 else 0.9) * x * x),
}
fails = []


def session(true, start, rounds, seed, kind="symmetric", noise=0.08, drift=0.10, lo=0.10, hi=4.00):
    """Plays the finder's own loop: plan a round, 'play' it, fit, close in or walk."""
    rng = random.Random(seed)
    f_true = SHAPES[kind]
    blocks, centre, level, side, f = [], start, 0, 0, None
    for r in range(rounds):
        for v in sensfit.plan(centre, level, lo, hi, rng, side):
            p = f_true(math.log(v / true)) * (1.0 + drift * len(blocks) / 30.0) + rng.gauss(0.0, noise)
            blocks.append((v, max(p, 0.0), len(blocks)))
        f = sensfit.fit(blocks, draws=120) or f
        if f:
            centre, side = f["best"], f["side"]
            level = sensfit.next_level(level, f, lo, hi)
    return f, blocks


def study(label, true, start, rounds, kind="symmetric", n=40, **kw):
    res = [session(true, start, rounds, seed, kind, **kw)[0] for seed in range(n)]
    errs = [abs(math.log(f["best"] / true)) for f in res]
    cov = 100 * sum(f["lo"] <= true <= f["hi"] for f in res) / n
    width = 100 * st.median(f["hi"] / f["lo"] - 1 for f in res)
    med = 100 * (math.exp(st.median(errs)) - 1)
    p90 = 100 * (math.exp(sorted(errs)[int(n * 0.9) - 1]) - 1)
    print(f"  {label:<46} median error {med:4.1f}%  9-in-10 within {p90:4.1f}%  range holds truth {cov:3.0f}%  "
          f"range width {width:3.0f}%")
    return med, cov, res


print("plans")
rng = random.Random(1)
for centre, level, side, lo, hi in ((1.95, 0, 0, 0.1, 4.0), (1.95, 1, 0, 0.1, 4.0), (1.95, 2, 0, 0.1, 4.0), (1.17, 0, -1, 0.1, 4.0),
                                    (3.22, 0, 1, 0.1, 4.0), (4.0, 1, 1, 0.1, 4.0), (0.1, 2, -1, 0.1, 4.0), (3.9, 2, 0, 0.1, 4.0),
                                    (0.12, 2, 0, 0.1, 2.0), (2.0, 0, 1, 0.1, 2.0), (0.95, 0, 0, 0.1, 2.0)):
    p = sensfit.plan(centre, level, lo, hi, rng, side)
    half = p[:len(p) // 2]
    ok = len(set(half)) == 5 and p[len(p) // 2:] == half[::-1] and all(lo <= v <= hi for v in p)
    print(f"  {'ok  ' if ok else 'FAIL'} centre {centre:4.2f} level {level} side {side:+d}: {sorted(set(p))}")
    if not ok:
        fails.append(f"plan {centre} {level} {side}")

print("the usual case: best value within ~30% of where you start")
for true in (1.80, 1.45, 2.50):
    for rounds in (2, 3):
        med, cov, _ = study(f"true {true:.2f} from 1.95, {rounds} rounds", true, 1.95, rounds)
        if med > 4.5 or cov < 65:
            fails.append(f"near {true} r{rounds}: {med:.1f}% / {cov:.0f}%")

print("far from the start: it has to walk there")
for true, rounds in ((0.50, 5), (0.80, 4), (3.60, 4), (3.90, 4), (0.40, 6)):
    med, cov, res = study(f"true {true:.2f} from 1.95, {rounds} rounds", true, 1.95, rounds)
    if med > 8.0:
        fails.append(f"far {true}: {med:.1f}%")

print("best is past the end of the game's scale")
med, cov, res = study("true 5.50 from 3.00, 3 rounds (scale ends at 4.00)", 5.5, 3.0, 3)
tops = sum(f["best"] >= 3.9 and f["side"] > 0 for f in res)
print(f"     ended at the top of the scale and said so: {tops}/40")
if tops < 34:
    fails.append(f"scale end: {tops}/40")

print("curves that are not a tidy parabola (3 rounds)")
for kind in ("flat top", "fast side steeper", "slow side steeper", "kinked"):
    med, cov, _ = study(f"{kind}, true 1.80 from 1.95", 1.80, 1.95, 3, kind)
    if med > 8.0 or cov < 65:
        fails.append(f"{kind}: {med:.1f}% / {cov:.0f}%")

print("a noisier player (scores swing 15% block to block)")
med, cov, res = study("true 1.80 from 1.95, 3 rounds", 1.80, 1.95, 3, noise=0.15)
if cov < 65:
    fails.append(f"noisy: coverage {cov:.0f}%")
print(f"     reported noise: {st.median(f['noise_pct'] for f in res):.0f}% (simulated 15%, plus drift)")



def sittings(true, start, sessions, rounds_each, seed, spread=0.15, noise=0.08, drift=0.10, lo=0.10, hi=4.00, ids=True):
    """The same loop over several sessions: each on a day of its own (sharper or duller by `spread`,
    warming up or tiring by up to `drift`), each picking the search up where the last one left it.
    ids=False fits the raw scores as if they were one session's - what stacking must not do."""
    rng = random.Random(seed)
    f_true = SHAPES["symmetric"]
    blocks, centre, level, side, f = [], start, 0, 0, None
    for s in range(sessions):
        day = math.exp(rng.gauss(0.0, spread))
        tilt = rng.uniform(-drift, drift)
        k = 0
        for r in range(rounds_each):
            for v in sensfit.plan(centre, level, lo, hi, rng, side):
                p = max(day * (f_true(math.log(v / true)) * (1.0 + tilt * k / 10.0) + rng.gauss(0.0, noise)), 0.0)
                blocks.append((v, p, len(blocks), s) if ids else (v, p, len(blocks)))
                k += 1
            f = sensfit.fit(blocks, draws=120) or f
            if f:
                centre, side = f["best"], f["side"]
                level = sensfit.next_level(level, f, lo, hi)
    return f, blocks


def stack_study(label, true, n=40, **kw):
    res = [sittings(true, 1.95, seed=seed, **kw)[0] for seed in range(n)]
    errs = [abs(math.log(f["best"] / true)) for f in res]
    cov = 100 * sum(f["lo"] <= true <= f["hi"] for f in res) / n
    width = 100 * st.median(f["hi"] / f["lo"] - 1 for f in res)
    med = 100 * (math.exp(st.median(errs)) - 1)
    p90 = 100 * (math.exp(sorted(errs)[int(n * 0.9) - 1]) - 1)
    print(f"  {label:<46} median error {med:4.1f}%  9-in-10 within {p90:4.1f}%  range holds truth {cov:3.0f}%  "
          f"range width {width:3.0f}%")
    return med, p90, cov, width


print("several sessions stacked: one round on each of four days, days 15% apart in level")
for true in (1.80, 1.45, 2.50):
    med, p90, cov, width = stack_study(f"true {true:.2f}: four sessions, levelled", true, sessions=4, rounds_each=1)
    _, _, _, one = stack_study(f"true {true:.2f}: one session of two rounds", true, sessions=1, rounds_each=2)
    if med > 4.5 or cov < 65:
        fails.append(f"stacked {true}: {med:.1f}% / {cov:.0f}%")
    if width >= one:
        fails.append(f"stacked {true}: four sessions did not narrow the range ({width:.0f}% against {one:.0f}%)")
print("  ...and why each session needs a level of its own: days 30% apart")
ours = theirs = 0.0
for true in (1.80, 1.45, 2.50):
    a = stack_study(f"true {true:.2f}: levelled", true, sessions=4, rounds_each=1, spread=0.30)
    b = stack_study(f"true {true:.2f}: raw scores thrown together", true, sessions=4, rounds_each=1, spread=0.30, ids=False)
    ours, theirs = ours + a[1], theirs + b[1]
    if a[0] > 4.5:
        fails.append(f"stacked, days 30% apart, {true}: {a[0]:.1f}%")
print(f"     9-in-10 error, averaged: {ours / 3:.1f}% levelled, {theirs / 3:.1f}% thrown together")
if ours >= theirs:
    fails.append(f"levelling by session did not help: {ours / 3:.1f}% against {theirs / 3:.1f}%")

f, blocks = sittings(1.80, 1.95, 4, 1, 5)
tripled = [(v, p * (3.0 if s == 2 else 1.0), i, s) for v, p, i, s in blocks]
g = sensfit.fit(tripled, draws=120)
same = abs(math.log(g["best"] / f["best"])) < 1e-9 and abs(math.log(g["lo"] / f["lo"])) < 1e-9 and abs(math.log(g["hi"] / f["hi"])) < 1e-9
print(f"  {'ok  ' if same else 'FAIL'} a day on which every score is three times higher gives the same answer: "
      f"{g['best']:.4f} ({g['lo']:.3f} - {g['hi']:.3f}) against {f['best']:.4f} ({f['lo']:.3f} - {f['hi']:.3f})")
if not same:
    fails.append("a session's level leaked into the answer")
lone = sensfit.fit(blocks + [(0.31, 0.05, len(blocks), "one block, far off and awful")], draws=0)
bare = sensfit.fit(blocks, draws=0)
same = abs(math.log(lone["best"] / bare["best"])) < 1e-9
print(f"  {'ok  ' if same else 'FAIL'} a session of a single block has nothing to compare it with, and moves nothing: "
      f"{lone['best']:.4f} against {bare['best']:.4f}")
if not same:
    fails.append("a one-block session moved the answer")
one = [(v, p, i) for v, p, i, s in blocks if s == 0]
a, b = sensfit.fit(one, draws=0), sensfit.fit([(v, p, i, "x") for v, p, i in one], draws=0)
same = a["best"] == b["best"] and a["sessions"] == 1 and max(abs(p - q[1]) for p, q in zip(a["adj"], one)) < 1e-9
print(f"  {'ok  ' if same else 'FAIL'} one session is fitted as it always was, and its scores are left as they are")
if not same:
    fails.append("single-session fit changed")
nothing =sensfit.fit([(1.5 + 0.5 * s, 1.0, i, s) for s in range(6) for i in range(2)]) is None
print(f"  {'ok  ' if nothing else 'FAIL'} sessions that each tested one value compare nothing with anything: no fit is claimed")
if not nothing:
    fails.append("fitted a curve to sessions with one value each")

f, blocks = session(1.80, 1.95, 2, 3)
t0 = time.perf_counter()
sensfit.fit(blocks)
dt = time.perf_counter() - t0
print(f"\nfit of {len(blocks)} blocks takes {dt * 1000:.0f} ms")
if dt > 1.5:
    fails.append(f"fit too slow: {dt:.2f} s")
_, many = sittings(1.80, 1.95, 20, 2, 3)
t0 = time.perf_counter()
sensfit.fit(many)
dt = time.perf_counter() - t0
print(f"fit of {len(many)} blocks from 20 sessions takes {dt * 1000:.0f} ms")
if dt > 1.5:
    fails.append(f"stacked fit too slow: {dt:.2f} s")
print("example:", {k: (round(v, 3) if isinstance(v, float) else (tuple(round(q, 3) for q in v) if isinstance(v, tuple) else v))
                   for k, v in f.items() if k not in ("coef", "wide", "adj")})
print("\nfit checks:", "all passed" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
