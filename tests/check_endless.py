"""Endless mode: every scenario, played by the bot with no end, goes on past the round length (and
Reflex / Stick Snap past their trial counts) until it is stopped from the pause menu; the run is
kept in logs/endless.jsonl and nowhere a comparison could see it.

    python tests/check_endless.py [folder for screenshots]
"""
import json, os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import game as gamemod, scenarios, screens, stats, tracks
from aimtuner.app import App
from aimtuner.bot import Bot

ROUND = 1.0         # the configured round length: an endless run must sail past it
PAST = 2.6          # ...to here, still running

scratch = tempfile.mkdtemp(prefix="aimtuner-endless-")
shots = sys.argv[1] if len(sys.argv) > 1 else None
opts = types.SimpleNamespace(windowed=False, offscreen=(1920, 1080), bench=False, selftest=True, only=None,
                             repeats=1, shots=None, duration=ROUND, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
app.bot = Bot(app)
errors = []


def expect(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


def key(*names):
    app.keys.update(names)
    yield


def frames(n=3):
    for _ in range(n):
        yield


def wait(pred, secs=60.0, what=""):
    end = time.perf_counter() + secs
    while not pred():
        if time.perf_counter() > end:
            expect(False, "timeout waiting for " + what)
            return False
        yield
    return True


def shot(name):
    if shots:
        yield from frames(3)
        app.pending_shot = os.path.join(shots, name + ".png")
        yield from frames(2)


def top():
    return app.screens[-1]


def endless_run(cls, prof, label, past=PAST, setup=None, pictures=False, until=None):
    """Start `cls` endless, check it outlives the round length (and `until`, if given), stop it,
    check what was kept."""
    n_runs, n_endless = len(app.log.runs), len(app.log.endless)
    if not app.start(cls, over={"endless": True}):
        expect(False, f"{label}: {cls.name} would not start")
        return None
    g = app.game
    expect(g.endless and g.duration == float("inf") and g.scn.endless, f"{label}: {cls.name} starts endless")
    if setup:
        setup(g)
    if pictures:
        yield from wait(lambda: g.state == "countdown", 5.0, "countdown")
        yield from shot(f"endless_countdown_{cls.id}")
    ok = yield from wait(lambda: g.state == "run" and g.t >= past and (until is None or until(g)), past + 60.0,
                         f"{cls.name} to pass {past} s")
    if not ok:
        return None
    alive = app.game is g and g.state == "run" and not g.scn.done
    expect(alive, f"{label}: {cls.name} is still going at {g.t:.1f} s (the round length is {ROUND} s)")
    if pictures:
        yield from shot(f"endless_hud_{cls.id}")
    yield from key("esc")                               # Esc / Start: pause...
    yield from frames(2)
    expect(g.state == "paused", f"{label}:    Esc pauses it")
    if pictures:
        yield from shot(f"endless_paused_{cls.id}")
    yield from key("esc")                               # ...and again: finish
    yield from frames(2)
    res = top()
    rec = getattr(res, "rec", None)
    expect(app.game is None and isinstance(res, screens.Results) and rec and rec.get("endless")
           and rec["scenario"] == cls.id and rec["duration_s"] >= past and rec["run_id"].endswith("-endless"),
           f"{label}:    and Esc again finishes it: a results screen for {rec and rec['duration_s']} s of endless play")
    expect(len(app.log.runs) == n_runs and len(app.log.endless) == n_endless + 1 and app.log.endless[-1] is rec,
           f"{label}:    kept with the endless runs, not the runs")
    return rec


def script():
    cfg = app.cfg
    pad_name = cfg["active_profile"]
    kbm = next(pr["name"] for pr in cfg["profiles"] if pr.get("kind") == "kbm")
    yield from frames(5)

    print("the track screen")
    menu = top()
    menu.sel = menu.ITEMS.index("core")
    yield from key("enter")
    ts = top()
    expect(isinstance(ts, screens.TrackScreen), "Core track opens")
    ts.sel = 0
    yield from key("y")
    yield from frames(2)
    expect(app.game is None and top() is ts, "Y on 'Run the whole track' does nothing")
    ts._move(1)
    ts._move(1)
    kind, cls = ts.rows[ts.sel]
    yield from shot("endless_track_screen")
    yield from key("y")
    yield from frames(2)
    expect(app.game is not None and app.game.endless and type(app.game.scn) is cls, f"Y on {cls.name} starts it endless")
    g = app.game
    yield from key("esc")                       # during the first countdown: nothing played, nothing kept
    yield from frames(2)
    expect(app.game is None and top() is ts and not app.log.endless, "cancelled in the countdown, it keeps nothing")

    print("every scenario, on a controller")
    played = 0
    for cls in tracks.ALL:
        if not app.playable(cls):
            continue
        setup = until = None
        if cls is scenarios.Reaction:           # a round is 2 trials here; it must go on to a third and fourth
            setup = lambda g: setattr(g.scn, "trials", 2)
            until = lambda g: len(g.scn.rts) >= 4
        elif cls is scenarios.StickSnap:        # 4 trials, and only 4 cues dealt: it must deal more
            def setup(g):
                g.scn.trials = 4
                g.scn.queue = g.scn.queue[:4]
            until = lambda g: g.scn.n >= 6
        rec = yield from endless_run(cls, pad_name, "pad", setup=setup, until=until,
                                     pictures=cls.id in ("cod_track_strafe", "reaction"))
        if rec is None:
            continue
        played += 1
        if cls is scenarios.Reaction:
            n = rec["stats"]["trials"]
            expect(n > 2, f"pad:    Reflex went past its trial count: {n} trials against 2")
        elif cls is scenarios.StickSnap:
            expect(rec["stats"]["trials"] > 4, f"pad:    Stick Snap went past its trial count and dealt more cues: "
                   f"{rec['stats']['trials']} trials against 4")
        if cls.id == "cod_track_strafe":
            yield from shot("endless_results")
        if top() is not ts:
            app.pop()
    expect(played == len(tracks.ALL), f"{played} of {len(tracks.ALL)} scenarios played endless on a controller")

    print("on a mouse")
    app.set_profile(kbm)
    yield from frames(2)
    for cls in (tracks.BY_ID["flick"], tracks.BY_ID["cod_flick_snap"], tracks.R6.scenarios[0]):
        rec = yield from endless_run(cls, kbm, "mouse")
        if rec:
            expect(rec["input"]["kind"] == "kbm" and rec["input"]["aim_assist"] is False
                   and not any(k.startswith("aa_") for k in rec["stats"]), "mouse:    a mouse run, with no aim assist in it")
        if top() is not ts:
            app.pop()
    app.set_profile(pad_name)
    yield from frames(2)

    print("restart, resume and run again")
    cls = tracks.BY_ID["grid"]
    app.start(cls, over={"endless": True})
    g = app.game
    yield from wait(lambda: g.state == "run" and g.t > 0.5, 20.0, "grid running")
    yield from key("esc")
    yield from frames(2)
    yield from key("x")                          # Restart keeps it endless
    yield from frames(2)
    g2 = app.game
    expect(g2 is not g and g2 is not None and g2.endless, "Restart from the pause menu starts it endless again")
    yield from wait(lambda: g2.state == "run" and g2.t > 0.5, 20.0, "grid running again")
    yield from key("esc")
    yield from frames(2)
    yield from key("enter")                      # resume: a short countdown...
    yield from frames(2)
    expect(g2.state == "countdown" and g2.started, "Resume counts back in")
    yield from key("esc")                        # ...and B / Esc in it finishes, keeping what was played
    yield from frames(2)
    res = top()
    expect(app.game is None and isinstance(res, screens.Results) and res.rec.get("endless"),
           "B / Esc during the resume countdown finishes the run and keeps it")
    end = time.perf_counter() + 1.0             # (results ignore input for a moment: a mashed button must not skip them)
    while time.perf_counter() < end:
        yield
    yield from key("enter")
    yield from frames(2)
    expect(app.game is not None and app.game.endless and type(app.game.scn) is cls, "A on its results plays it endless again")
    app.game.abort()
    yield

    print("the logs")
    lines = stats.load_runs(scratch, "endless.jsonl")
    expect(len(lines) == len(app.log.endless) and all(r.get("endless") for r in lines),
           f"{len(lines)} endless runs in logs/endless.jsonl")
    expect(not os.path.exists(os.path.join(scratch, "runs.jsonl")) and not app.log.runs,
           "and not one line in runs.jsonl - Progress, Compare and analyze.py never see them")
    again = stats.Log(scratch)
    expect(len(again.endless) == len(lines) and not again.runs, "they load back as endless runs")
    app.log.rename_controller(pad_name, pad_name + " (renamed)")
    expect(all(r["controller"] != pad_name for r in stats.load_runs(scratch, "endless.jsonl")),
           "renaming a profile carries its endless runs along too")

    print("a timed run is untouched")
    app.start(tracks.BY_ID["flick"])
    g = app.game
    expect(not g.endless and g.duration == ROUND, "a normal start is a normal round")
    yield from wait(lambda: app.game is None, 30.0, "a timed round to end by itself")
    expect(len(app.log.runs) == 1 and not app.log.runs[0].get("endless"), "it ended on its own and went to runs.jsonl")


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
print("endless:", "all passed" if not errors else f"FAILED {errors}")
sys.exit(1 if errors else 0)
