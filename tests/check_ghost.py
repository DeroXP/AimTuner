"""The capture, the ghost and the replay.

Bots play one drill of each kind; for each: the run is captured; the ghost plays the same targets,
in the same order, with the player's body (leans, crouches, position), through the game's own
rules (and no aim assist where the player had none); it never turns faster than the stick or a
hand can; its numbers come out on the drill's own metrics; the comparison gives advice; and the
results, replay and every-target screens all work.

    python tests/check_ghost.py [folder for screenshots]
"""
import math, os, sys, tempfile, threading, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np

from aimtuner import capture, codkit, engage, ghost, replay, screens, tracks
from aimtuner.app import App
from aimtuner.bot import Bot

scratch = tempfile.mkdtemp(prefix="aimtuner-ghost-")
shots = sys.argv[1] if len(sys.argv) > 1 else None
opts = types.SimpleNamespace(windowed=False, offscreen=(1920, 1080), bench=False, selftest=True, only=None,
                             repeats=1, shots=None, duration=8.0, cap=240, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
app.bot = Bot(app, react=0.25, gain=10.0, noise=0.08, throw=0.06)
errors = []
DRILLS = ("flick", "grid", "track_strafe", "spray", "ads", "cod_track_jump", "cod_flick_180", "cod_first_bullet",
          "r6_peek_side", "r6_press_shield", "r6_place_ping", "r6_press_clear", "r6_tap_one")


def expect(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        errors.append(msg)


def frames(n=3):
    for _ in range(n):
        yield


def key(*k):
    app.keys.update(k)
    yield
    yield


def wait(secs):
    end = time.perf_counter() + secs
    while time.perf_counter() < end:
        yield


def shot(name):
    if shots:
        yield from frames(3)
        app.pending_shot = os.path.join(shots, name + ".png")
        yield from frames(2)


def saved():
    while any(t.name == "capture-save" and t.is_alive() for t in threading.enumerate()):
        yield


def check_run(sid, cap, res, kbm):
    """What must hold for any ghost run."""
    gcap = res["capture"]
    book = ghost.TargetBook(cap)
    # the same targets, in the same order
    order = [k for k in range(book.n)]
    gkeys = [engage._orig_key(gcap, k) for k in range(len(gcap.head["keys"]))]
    first_lap = [k for k, lap in gkeys if lap == 0]
    expect(first_lap == sorted(first_lap) and set(first_lap) <= set(order) and len(first_lap) >= min(book.n, 2),
           f"{sid}: the ghost met the run's targets in the run's order ({len(first_lap)} of {book.n} in the time)")
    # ...moving as they moved (each one, from when it came in)
    worst = 0.0
    for gk, (k, lap) in enumerate(gkeys[:6]):
        if lap:
            continue
        gfr, gps = gcap.track_of(gk)
        rel = float(gcap.t[gfr[0]])
        for f, ps in list(zip(gfr, gps))[::25][:8]:
            want = book.pose(k, rel, float(gcap.t[f]))
            worst = max(worst, float(np.max(np.abs(np.asarray(ps[:3]) - want[:3]))))
    expect(worst < 0.15, f"{sid}:    each moving as it did in your run (worst {worst:.3f} m off)")
    # never faster than a hand can turn it
    v = gcap.view
    dt = np.maximum(np.diff(gcap.t), 1e-6)
    dyaw = (np.diff(v[:, capture.V["yaw"]]) + 180.0) % 360.0 - 180.0
    rate = np.abs(dyaw) / dt
    head = cap.head
    cfg = head["cfg"]
    if kbm:
        dpi = cfg["mouse"]["dpi"]
        cm = 360.0 / max(np.max(np.abs(v[:, capture.V["ax"]])), 1) if False else None
        peak_counts = np.max(np.hypot(v[:, capture.V["ax"]], v[:, capture.V["ay"]]) / np.concatenate([[1e-3], dt]))
        cms = peak_counts / dpi * 2.54
        expect(cms <= ghost.PARAMS["kbm"]["hand"] * 1.15, f"{sid}:    its hand never moved faster than {ghost.PARAMS['kbm']['hand']:.0f} cm/s "
               f"(peak {cms:.0f} cm/s)")
    else:
        stick = np.hypot(v[:, capture.V["ax"]], v[:, capture.V["ay"]])
        expect(np.max(stick) <= 1.0 + 1e-6, f"{sid}:    its stick never went past full deflection")
    # the rules: no aim assist where the player had none
    st = res["stats"]
    if kbm or head["track"] == "r6" or head["track"] == "core":
        expect(not any(k.startswith("aa_") for k in st), f"{sid}:    and no aim assist in its numbers")
    metrics = [m[0] for m in tracks.BY_ID[sid].metrics if not (kbm and m[0].startswith("aa_"))]
    expect(all(k in st for k in metrics), f"{sid}:    its numbers are the drill's own: " +
           ", ".join(f"{k}={st.get(k)}" for k in metrics[:4]))


def script():
    cfg = app.cfg
    pad_name = cfg["active_profile"]
    kbm_name = next(p["name"] for p in cfg["profiles"] if p.get("kind") == "kbm")
    yield from frames(5)

    for kbm in (False, True):
        app.set_profile(kbm_name if kbm else pad_name)
        yield from frames(2)
        label = "mouse" if kbm else "pad"
        print(f"--- {label}", flush=True)
        for sid in (DRILLS if not kbm else ("flick", "cod_track_jump", "r6_tap_one", "r6_peek_side")):
            cls = tracks.BY_ID[sid]
            if not app.playable(cls):
                continue
            app.start(cls)
            while app.game is not None:
                yield
            yield from saved()
            res = app.screens[-1]
            rid = app.log.runs[-1]["run_id"]
            path = capture.path_for(scratch, rid)
            expect(isinstance(res, screens.Results) and os.path.exists(path) and res.capture is not None,
                   f"{label}: {sid}: captured ({os.path.getsize(path) / 1e3:.0f} KB)")
            cap = capture.Capture.load(path)
            expect(cap.frames == res.capture.frames and cap.head["scenario"] == sid, f"{label}: {sid}:    and it loads back whole")
            end = time.perf_counter() + 30.0
            while res.job is not None and not res.job.done and time.perf_counter() < end:
                yield
            job = res.job
            expect(job is not None and job.result is not None and job.error is None,
                   f"{label}: {sid}:    the ghost played it ({'error: ' + str(job.error) if job and job.error else 'ok'})")
            if job is None or job.result is None:
                app.pop()
                continue
            check_run(sid, cap, job.result, kbm)
            if sid == "r6_peek_side":           # its body is yours: the same lean, relative to each target
                gcap = job.result["capture"]
                expect(np.max(np.abs(gcap.col("lean"))) > 0.5 and np.max(np.abs(cap.col("lean"))) > 0.5,
                       f"{label}: {sid}:    it leans as you leaned")
            adv = job.result["compare"]["advice"]
            expect(isinstance(adv, str) and len(adv) > 20, f"{label}: {sid}:    advice: {adv[:110]}")
            expect(os.path.exists(capture.path_for(scratch, rid, ghost=True)), f"{label}: {sid}:    the ghost's run is kept too")
            if sid in ("flick", "cod_track_jump") and not kbm:
                yield from wait(1.0)
                yield from shot(f"ghost_{sid}_results")
                yield from key("y")
                rp = app.screens[-1]
                expect(isinstance(rp, replay.ReplayScreen), f"{label}: {sid}:    Y opens the replay")
                yield from wait(1.5)
                yield from key("y")
                yield from wait(0.5)
                yield from shot(f"ghost_{sid}_replay")
                yield from key("right")
                yield from key("lb")
                yield from key("up")
                yield from key("x")
                yield from frames(5)
                expect(rp.ghost_view, f"{label}: {sid}:    X shows the ghost's own view")
                yield from shot(f"ghost_{sid}_ghostview")
                yield from key("esc")
                yield from frames(3)
                yield from key("x")
                rt = app.screens[-1]
                expect(isinstance(rt, replay.RouteScreen), f"{label}: {sid}:    X opens every target")
                yield from key("down")
                yield from key("y")
                yield from shot(f"ghost_{sid}_route")
                yield from key("enter")
                yield from frames(4)
                expect(isinstance(app.screens[-1], replay.ReplayScreen), f"{label}: {sid}:    A watches that target")
                yield from key("esc")
                yield from key("esc")
                yield from frames(3)
            if app.screens[-1] is res:
                app.pop()
            yield

    print("--- human, not an aimbot (pad, Flick, three seeds)", flush=True)
    path = next(capture.path_for(scratch, r["run_id"]) for r in app.log.runs if r["scenario"] == "flick" and r["input"]["kind"] == "pad")
    cap = capture.Capture.load(path)
    reacts, ttk, eff, acc, corr = [], [], [], [], []
    for seed in (1, 2, 3):
        run = ghost.GhostRun(cap, seed=seed)
        run.run()
        r = run.result()
        st = r["stats"]
        ev = engage.engagements(r["capture"])
        reacts += [e["parts"]["react"] for e in ev if e["parts"]["react"]]
        corr += [e["corrections"] for e in ev if e["corrections"] is not None]
        ttk.append(st["ttk_median_ms"])
        eff.append(st["path_eff_pct"])
        acc.append(st["accuracy_pct"])
    rt = sorted(reacts)[len(reacts) // 2] * 1000 if reacts else None
    expect(rt is not None and 150 <= rt <= 300, f"reaction like a strong human's: median {rt and round(rt)} ms (research: ~185-230)")
    expect(all(350 <= x <= 800 for x in ttk), f"a target in {[round(x) for x in ttk]} ms - quick, not instant")
    expect(all(x < 99.5 for x in eff), f"paths not ruler-straight: {[round(x, 1) for x in eff]}% efficient")
    expect(sum(corr) / max(len(corr), 1) >= 0.2, f"it corrects after its first movement ({sum(corr) / max(len(corr), 1):.2f} a target; "
           "research: 0.2-0.8)")
    seeds = [ghost.GhostRun(cap, seed=s) for s in (4, 5)]
    for s in seeds:
        s.run()
    a, b = (s.result()["stats"]["ttk_median_ms"] for s in seeds)
    expect(a != b, f"and not a machine: two runs differ ({a} / {b} ms)")

    print("--- an endless session", flush=True)
    app.set_profile(pad_name)
    app.start(tracks.BY_ID["cod_track_jump"], over={"endless": True})
    g = app.game
    while g.state != "run" or g.t < 6.0:
        yield
    yield from key("esc")
    yield from frames(2)
    yield from key("esc")
    yield from frames(3)
    res = app.screens[-1]
    end = time.perf_counter() + 30.0
    while res.job is not None and not res.job.done and time.perf_counter() < end:
        yield
    expect(res.rec.get("endless") and res.job is not None and res.job.result is not None,
           "endless sessions are captured and get a ghost too")
    app.pop()

    print("--- keeping the folder in check", flush=True)
    d = capture.folder(scratch)
    for i in range(capture.KEEP + 5):
        open(os.path.join(d, f"19990101-{i:06d}-x.npz"), "wb").close()
    capture.prune(scratch)
    left = [f for f in os.listdir(d) if not f.endswith(".ghost.npz")]
    oldest = {f"19990101-{i:06d}-x.npz" for i in range(5)}
    expect(len(left) == capture.KEEP and not oldest & set(left) and os.path.exists(os.path.join(scratch, "runs.jsonl")),
           f"only the newest {capture.KEEP} captures are kept (and the run log is never touched)")


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
print("ghost:", "all passed" if not errors else f"FAILED {errors}")
sys.exit(1 if errors else 0)
