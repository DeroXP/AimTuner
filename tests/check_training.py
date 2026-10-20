"""Progress / training playlist and the guided comparison: the maths on made-up runs, then both
screens driven offscreen by the self-test bot."""
import datetime, os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import codscn, config, duel, progress, scenarios, screens, stats, tracks
from aimtuner import pad as padmod
from aimtuner.app import App
from aimtuner.bot import Bot

errors, checks = [], [0]


def expect(cond, msg):
    checks[0] += 1
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


def near(a, b, tol=1e-6):
    return a is not None and abs(a - b) <= tol


# ------------------------------------------------------------------ the maths
def run(scn, who, value, fp="now", key=None, when="2026-10-07T12:00:00", duel_=None, **more):
    key = key or scn.primary
    return {"controller": who, "scenario": scn.id, "track": scn.track, "fingerprint": fp, "time": when,
            "binds": {"shoot": "RB", "ads": "LB"}, "stats": dict({key: value}, **more), "duel": duel_}


print("progress: one scenario over time")
F, R = scenarios.Flick, scenarios.Reaction
runs = [run(F, "A", v) for v in (100, 110, 105, 120, 125, 130)]
runs += [run(F, "A", 300, fp="old"), run(F, "B", 999), run(F, "A", 5, duel_={"warmup": True, "pass": None})]
p = stats.progress(runs, F, "A", "now")
expect(p["n"] == 6 and len(p["points"]) == 7, f"only this pad's runs; other settings are drawn but not counted ({p['n']}, {len(p['points'])})")
expect(p["last"] == 130 and p["best"] == 130 and near(p["recent"], 125.0), "last, best, and the mean of the last three")
expect(near(p["change_pct"], (125 - 105) / 105 * 100, 1e-9) and p["trend"] == "up", f"improving: {p['change_pct']:+.1f}%")
p = stats.progress(runs[:4], F, "A", "now")
expect(p["trend"] is None and p["change_pct"] is None, "four runs are too few to call a trend")
slow = [run(R, "A", v) for v in (250, 240, 245, 230, 225, 220)]
p = stats.progress(slow, R, "A", "now")
expect(p["best"] == 220 and p["trend"] == "up" and p["change_pct"] > 0, f"lower-is-better metrics: getting faster is improving ({p['change_pct']:+.1f}%)")
p = stats.progress([run(F, "A", v) for v in (100, 100, 100, 99, 101, 100)], F, "A", "now")
expect(p["trend"] == "steady", "a change inside 3% is steady")
p = stats.progress([run(F, "A", v) for v in (100, 102, 98, 90, 88, 86)], F, "A", "now")
expect(p["trend"] == "down", "and a fall is called slipping")

print("what 'weakest' is ranked by")
G, S, T = scenarios.Grid, scenarios.Spray, scenarios.SmoothTrack
acc = lambda scn, who, v, **kw: run(scn, who, 1, accuracy_pct=v, **kw)
runs = [acc(F, "A", 80), acc(F, "A", 70), acc(F, "A", 60), acc(F, "A", 50),      # last three: 60
        acc(G, "A", 90), acc(S, "A", 30, fp="old"), acc(S, "A", 40, fp="old"),   # only older settings: 35, stale
        run(T, "A", 45.0), run(R, "A", 210.0), acc(scenarios.Micro, "B", 5)]
expect(stats.grade(runs, F, "A", "now") == (60.0, False), "mean of the last three runs with the current settings")
expect(stats.grade(runs, S, "A", "now") == (35.0, True), "falls back to older settings, and says so")
expect(stats.grade(runs, T, "A", "now") == (45.0, False), "tracking drills are ranked by time on target")
expect(stats.grade(runs, R, "A", "now") is None and stats.grade(runs, scenarios.StickSnap, "A", "now") is None,
       "the two reaction drills have no hit rate and are not ranked")
expect(stats.grade(runs, scenarios.Micro, "A", "now") is None, "another controller's runs do not count")
wk = stats.weakest(runs, "core", "A", "now", 3)
expect([s.id for s, _, _ in wk["rows"]] == [S.id, T.id, F.id], f"lowest first: {[(s.id, g) for s, g, _ in wk['rows']]}")
expect(wk["ranked"] == 4 and wk["total"] == 7, f"4 of the 7 rankable Core scenarios have runs ({wk['ranked']}/{wk['total']})")
expect(all(getattr(s, "grade", None) == "accuracy_pct" for s in tracks.COD.scenarios), "every Call of Duty scenario is ranked by accuracy")

print("guided comparison: pairs inside a block")


def block(session, k, a_true, b_true, drift=0.0, scn=F, seq="ABBA", names=("A", "B"), fp="now", key=None):
    out = []
    for i, ch in enumerate(seq):
        base = a_true if ch == "A" else b_true
        out.append(run(scn, names["AB".index(ch)], base + drift * i, fp=fp, key=key,
                       duel_={"session": session, "block": k, "pass": i, "warmup": False}))
    return out


cells = stats.duel_cells(block("s", 0, 100, 90), "core", "now", "A", "B")
expect(len(cells) == 1 and near(cells[0]["a"], 100) and near(cells[0]["b"], 90) and near(cells[0]["rel"], 10 / 95 * 100, 1e-9),
       f"one scenario in one block is one pair: A {cells[0]['rel']:+.1f}% ahead")
d0 = stats.duel_cells(block("s", 0, 100, 90), "core", "now", "A", "B")[0]
d1 = stats.duel_cells(block("s", 0, 100, 90, drift=8.0), "core", "now", "A", "B")[0]
expect(near(d1["a"] - d1["b"], d0["a"] - d0["b"], 1e-9), "steady improvement while playing (8 points a pass) leaves the gap untouched")
naive = (100 + 108) / 2 - (116 + 124) / 2      # the same eight-points-a-pass player doing A, A, B, B with equal pads
expect(naive < -10, f"...where playing all of one pad first would have handed the second pad {-naive:.0f} points")
cells = stats.duel_cells(block("s", 1, 100, 90, seq="BAAB"), "core", "now", "A", "B")
expect(len(cells) == 1 and cells[0]["rel"] > 0, "the mirrored block (B-A-A-B) pairs up the same way")
expect(stats.duel_cells(block("s", 0, 100, 90)[:3], "core", "now", "A", "B") == [], "an unfinished block gives nothing")
expect(stats.duel_cells(block("s", 0, 100, 90, seq="AABB"), "core", "now", "A", "B") == [], "passes in any other order are refused")
expect(stats.duel_cells(block("s", 0, 100, 90, fp="old"), "core", "now", "A", "B") == [], "other settings are kept out")
redo = block("s", 0, 100, 90)
redo.append(run(F, "B", 70, duel_={"session": "s", "block": 0, "pass": 1, "warmup": False}))
expect(near(stats.duel_cells(redo, "core", "now", "A", "B")[0]["b"], 80), "a pass played again replaces the first attempt")
warm = block("s", 0, 100, 90) + [run(F, "A", 1, duel_={"session": "s", "block": 0, "pass": None, "warmup": True})]
expect(len(stats.duel_cells(warm, "core", "now", "A", "B")) == 1 and len(stats.counted(warm)) == 4,
       "the warm-up pass is in no comparison at all")
cells = stats.duel_cells(block("s", 0, 220, 250, scn=R), "core", "now", "A", "B")
expect(cells[0]["rel"] > 0, "lower-is-better metrics: the faster pad is the one ahead")

print("guided comparison: the verdict")
five = (F, scenarios.Micro, G, T, scenarios.StrafeTrack)


def session(gaps_per_block, noise=()):
    out = []
    for k, gap in enumerate(gaps_per_block):
        for j, scn in enumerate(five):
            wob = noise[(k * 5 + j) % len(noise)] if noise else 0.0
            out += block("s", k, 100 + gap + wob, 100, scn=scn, seq=duel.order(k))
    return stats.duel_summary(stats.duel_cells(out, "core", "now", "A", "B"))


W = (1.5, -1.0, 0.5, -1.5, 1.0, -0.5, 2.0, -2.0, 0.0, 1.0)
sm = session([5, 5], W)
expect(sm["blocks"] == 2 and sm["cells"] == 10 and sm["conf"] == "clear" and sm["winner"] == 0 and sm["more"] is None,
       f"a steady 5% over two blocks is clear: {sm['mean']:+.1f}% ± {sm['sd']:.1f}, p = {sm['p']:.4f}")
sm = session([5], W)
expect(sm["conf"] == "leaning" and sm["p"] < 0.05, f"the same gap after one block is only leaning (p = {sm['p']:.3f}): one sitting is not enough")
sm = session([-5, -5], W)
expect(sm["conf"] == "clear" and sm["winner"] == 1, "and it can go to the other controller")
W2 = (6.0, -7.0, 5.0, -4.0, 3.0, -6.0, 7.0, -5.0, 4.0, -3.0)
sm = session([0.5, 0.5], W2)
expect(sm["conf"] == "noise" and sm["more"] > 20, f"half a percent under 5% of wobble is noise, and would take {sm['more']} more blocks to prove")
sm = session([3, 3], W2)
expect(sm["conf"] in ("noise", "leaning") and 1 <= sm["more"] <= 20, f"3% under the same wobble: not yet - about {sm['more']} more blocks")
expect(len(sm["rows"]) == 5 and all(r["n"] == 2 and sum(r["wins"]) <= 2 for r in sm["rows"]), "one row per scenario, with blocks won")
expect(stats.duel_summary([]) is None, "nothing played, nothing claimed")
expect(duel.order(0) == "ABBA" and duel.order(1) == "BAAB" and duel.order(2) == "ABBA", "blocks alternate A-B-B-A and B-A-A-B")

# --------------------------------------------------------------- the screens
scratch = tempfile.mkdtemp(prefix="aimtuner-train-")
shots = sys.argv[1] if len(sys.argv) > 1 else None
opts = types.SimpleNamespace(windowed=False, offscreen=(1920, 1080) if shots else (1280, 720), bench=False, selftest=True,
                             only=None, repeats=1, shots=None, duration=1.5, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
app.bot = Bot(app)
duel.SETS = {"core": (scenarios.Flick, scenarios.Grid), "cod": (codscn.SnapShot, codscn.RecoilHold)}   # short passes for the test


def key(*names):
    app.keys.update(names)
    yield


def frames(n=3):
    for _ in range(n):
        yield


def wait(pred, secs=90.0, what=""):
    end = time.perf_counter() + secs
    while not pred():
        if time.perf_counter() > end:
            expect(False, "timeout waiting for " + what)
            return
        yield


def sleep(s):
    end = time.perf_counter() + s
    while time.perf_counter() < end:
        yield


def shot(name):
    if shots:
        yield from frames(3)
        app.pending_shot = os.path.join(shots, name + ".png")
        yield from frames(2)


def top():
    return app.screens[-1]


def play_out(n):
    """Let the bot play the next n scenarios of a playlist, pressing A on each results screen."""
    for _ in range(n):
        yield from wait(lambda: app.game is not None, what="a scenario to start")
        yield from wait(lambda: app.game is None, what="a scenario to finish")
        yield from sleep(0.9)
        yield from key("enter")
        yield from frames(2)


def script():
    cfg = app.cfg
    A, B = (p["name"] for p in cfg["profiles"][:2])
    yield from frames(5)
    menu = top()
    I = menu.ITEMS.index

    print("progress screen")
    menu.sel = I("progress")
    yield from key("enter")
    pg = top()
    expect(isinstance(pg, progress.Progress) and pg.track.id == "core", "opens from the main menu (Core when there are no runs)")
    yield from frames(2)
    yield from key("enter")
    expect(app.game is None, "nothing to train on before any run exists")
    yield from shot("progress_empty")
    # a history for controller A: three passes of four Core scenarios, getting better, plus runs with older settings
    fp = config.fingerprint(cfg)
    base = {scenarios.Flick: 82.0, scenarios.Micro: 64.0, scenarios.Grid: 91.0, scenarios.Spray: 38.0, scenarios.AdsSnap: 75.0}
    for day in range(6):
        for scn, a0 in base.items():
            app.log.runs.append(dict(run(scn, A, 2000 + 150 * day, accuracy_pct=a0 + day, when=f"2026-10-0{day + 1}T20:00:00"),
                                     fingerprint=fp))
    for day in range(3):
        app.log.runs.append(dict(run(scenarios.SmoothTrack, A, 31.0 + day, when=f"2026-09-2{day}T20:00:00"), fingerprint="older"))
    yield from frames(2)
    per, weak = pg._data()
    want = [scenarios.SmoothTrack, scenarios.Spray, scenarios.Micro, scenarios.AdsSnap, scenarios.Flick]
    expect([s for s, _, _ in weak["rows"]] == want, f"weakest five, lowest first: {[s.name for s, _, _ in weak['rows']]}")
    expect(weak["rows"][0][2] is True and weak["rows"][1][2] is False, "the one known only from older settings is marked")
    expect(per[scenarios.Flick.id][0]["trend"] == "up" and per[scenarios.SmoothTrack.id][0]["n"] == 0, "trend where there is history; none without current runs")
    yield from shot("progress_train")
    pg.sel = next(i for i, (k, a) in enumerate(pg.rows) if a is scenarios.Flick)
    yield from shot("progress_scenario")
    pg.sel = next(i for i, (k, a) in enumerate(pg.rows) if a is scenarios.SmoothTrack)
    yield from shot("progress_scenario_old_settings")
    yield from key("x")
    expect(pg.track.id == "cod" and pg.rows[0][0] == "train", "X switches track")
    yield from frames(2)
    yield from shot("progress_cod_empty")
    yield from key("x")
    expect(pg.track.id == "r6" and pg.rows[0][0] == "train", "X again: the Siege track")
    yield from frames(2)
    yield from key("x")
    expect(pg.track.id == "core", "and round to Core")
    pg.sel = 0
    n0 = len(app.log.runs)
    yield from key("enter")
    expect(app.game is not None and type(app.game.scn) is want[0] and app.game.playlist["items"] == want, "A starts those five as a playlist")
    yield from play_out(5)
    got = app.log.runs[n0:]
    expect(len(got) == 5 and all(r["playlist"].startswith("weakest-") for r in got) and top() is pg,
           f"five runs logged, and it ends back on the progress screen ({len(got)}, {type(top()).__name__})")
    expect(pg._data()[0][scenarios.SmoothTrack.id][0]["n"] == 1, "the screen picks the new runs up")
    pg.sel = next(i for i, (k, a) in enumerate(pg.rows) if a is scenarios.Grid)
    yield from key("enter")
    expect(app.game is not None and type(app.game.scn) is scenarios.Grid and app.game.playlist is None, "A on a scenario plays just that one")
    yield from key("esc")
    yield from frames(2)
    yield from key("esc")
    expect(top() is menu, "back to the menu")

    print("guided comparison")
    menu.sel = I("duel")
    yield from key("enter")
    du = top()
    expect(isinstance(du, duel.Duel) and du.state == "setup" and du.pair == [A, B], f"opens on the two profiles: {du.pair}")
    yield from shot("duel_setup")
    expect(du.rows() == ("a", "b", "track", "go"), "nothing to carry on with yet")
    du.sel = du.rows().index("b")
    yield from key("left")
    expect(du.pair[1] == A, "controllers can be changed")
    du.sel = du.rows().index("go")
    yield from key("enter")
    expect(du.state == "setup", "it will not start with the same controller twice")
    du.sel = du.rows().index("b")
    yield from key("right")
    du.sel = du.rows().index("go")
    n0 = len(app.log.runs)
    yield from key("enter")
    expect(du.state == "handoff" and [q["pass"] for q in du.queue] == [None, 0, 1, 2, 3]
           and [q["pad"] for q in du.queue] == [0, 0, 1, 1, 0], "plan: warm-up on A, then A, B, B, A")
    yield from shot("duel_handoff_warmup")
    # it must refuse to start on the wrong pad
    app.pad.devices[padmod.VIRTUAL_SLOT]["id"] = "AAAA:000B"
    config.profile(cfg, B)["devices"].append("AAAA:000B")
    config.profile(cfg, A)["devices"].append("AAAA:000A")
    ok, why = du._ready()
    expect(not ok and B in why and A in why, f"wrong pad in hand: '{why}'")
    yield from shot("duel_wrong_pad")
    yield from key("enter")
    expect(app.game is None and du.state == "handoff", "...and A does nothing until the right one is picked up")
    app.pad.devices[padmod.VIRTUAL_SLOT]["id"] = "AAAA:000A"
    expect(du._ready()[0], "right pad: ready")
    app.pad.devices[padmod.VIRTUAL_SLOT]["id"] = None       # the bot has no USB id; from here it stands in for both pads
    for p in cfg["profiles"]:
        p["devices"].clear()
    yield from key("enter")
    expect(app.game is not None and cfg["active_profile"] == A and app.game.playlist["duel"]["warmup"] is True, "warm-up pass starts on A")
    yield from key("esc")                                   # abandon it during the countdown
    yield from frames(2)
    expect(du.state == "broken" and len(app.log.runs) == n0, "an abandoned pass is noticed, and nothing was logged for it")
    yield from shot("duel_broken")
    yield from key("enter")
    expect(du.state == "handoff" and du.queue[0]["pass"] is None, "A offers the same pass again")
    seen = []

    def play_pass(step):
        expect(du.state == "handoff", f"pass {step}: waiting for the hand-over")
        yield from key("enter")
        yield from frames(1)
        seen.append((cfg["active_profile"], app.game.playlist["duel"]["pass"] if app.game else "?"))
        yield from play_out(2)
        yield from frames(2)

    yield from play_pass(0)
    # the first player took "same pad again" for the screen being stuck: it has to be spelled out
    expect(du.warmed and du.steps[du.at] == {"pad": 0, "pass": 0} and len(du.steps) == 5,
           "after the warm-up the next pass is on the same pad, and the screen knows to say so")
    yield from shot("duel_handoff_after_warmup")
    yield from play_pass(1)
    expect(not du.warmed and du.queue[0] == {"pad": 1, "pass": 1}, "then it asks for the other pad")
    # ...and they left and came back, which used to mean starting again from the warm-up
    session, first = du.session, du
    yield from key("esc")
    expect(du.state == "summary", "B stops part-way")
    yield from key("enter")
    expect(du.state == "handoff" and du.queue[0]["pass"] == 1, "A on the verdict carries on with the unfinished block")
    yield from key("esc")
    yield from key("esc")
    expect(top() is menu, "out to the menu with the block unfinished")
    yield from key("enter")
    du = top()
    u = du._unfinished()
    expect(isinstance(du, duel.Duel) and du is not first and "resume" in du.rows() and du.rows()[du.sel] == "resume"
           and u["done"] == 1 and u["session"] == session, f"coming back offers to carry on: {u}")
    yield from shot("duel_setup_resume")
    mine = [r for r in app.log.runs[n0:] if r.get("duel")]
    stamp = lambda mins: (datetime.datetime.now() - datetime.timedelta(minutes=mins)).isoformat(timespec="seconds")
    for r in mine:
        r["time"] = stamp(240)
    du._open = None
    expect("resume" not in du.rows(), "but not four hours later: by then it is a different sitting")
    for r in mine:
        r["time"] = stamp(30)
    du._open = None
    du.sel = du.rows().index("resume")
    yield from key("enter")
    expect(du.state == "handoff" and du.session == session and du.at == 1 and du.steps[1] == {"pad": 1, "pass": None}
           and [s["pass"] for s in du.steps] == [0, None, 1, 2, 3], "after half an hour away it carries on, with a fresh warm-up first")
    yield from shot("duel_handoff_resume_warmup")
    yield from key("x")
    expect(du.state == "handoff" and du.queue[0] == {"pad": 1, "pass": 1}, "X skips a warm-up")
    yield from shot("duel_handoff_counts")
    for step in (2, 3, 4):
        yield from play_pass(step)
    expect(seen == [(A, None), (A, 0), (B, 1), (B, 2), (A, 3)], f"who played what: {seen}")
    got = app.log.runs[n0:]
    expect(len(got) == 10 and [r["controller"] for r in got] == [A, A, A, A, B, B, B, B, A, A], "ten runs, logged under the right controllers")
    expect(du.state == "summary" and du.played, "the block ends on the verdict")
    sm = du._summary()
    expect(sm and sm["blocks"] == 1 and sm["cells"] == 2 and sm["conf"] == "few", f"one block of two scenarios: {sm and (sm['blocks'], sm['cells'], sm['conf'])}")
    expect(len(stats.groups(app.log.runs, scenarios.Flick.id, fp).get(A, [])) == 6 + 1 + 2, "Compare counts the two real passes, not the warm-up")
    yield from shot("duel_summary_one_block")
    # a fuller picture for the screenshot: tidy numbers for the block just played (two bots are not two pads),
    # then two more blocks of five scenarios with the first controller a few percent ahead on most
    for r in got:
        if not r["duel"]["warmup"]:
            r["stats"][tracks.BY_ID[r["scenario"]].primary] = (2100.0 if r["controller"] == A else 2000.0) + 15.0 * r["duel"]["pass"]
    extra = []
    for k in (1, 2):
        for j, scn in enumerate(five):
            a_true, wob = 100 + (4.0, 6.0, 3.0, 5.0, -1.0)[j], W[(k * 5 + j) % len(W)]
            extra += [dict(r, fingerprint=fp, controller={"A": A, "B": B}[r["controller"]])
                      for r in block("x", k, a_true + wob, 100, scn=scn, seq=duel.order(k))]
    app.log.runs.extend(extra)
    yield from frames(2)
    sm = du._summary()
    expect(sm["blocks"] == 3 and sm["conf"] == "clear" and sm["winner"] == 0, f"with two more blocks the verdict is clear: {sm['mean']:+.1f}%, p = {sm['p']:.4f}")
    yield from shot("duel_summary_clear")
    yield from key("enter")
    expect(du.state == "handoff" and du.block == 1 and [q["pad"] for q in du.queue] == [1, 0, 0, 1] and du.queue[0]["pass"] == 0,
           "another block: B, A, A, B, and no second warm-up")
    yield from key("esc")
    expect(du.state == "summary", "B from the hand-over goes back to the verdict")
    yield from key("esc")
    expect(top() is menu, "and out")
    du2 = duel.Duel(app)
    du2.sel = du2.rows().index("go")
    du2.update({"accept"}, 0.0)
    expect(du2.block == 3 and du2.queue[0]["pass"] is None, "a later session carries on from the blocks already played, after its own warm-up")


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
print(f"{checks[0]} checks, {len(errors)} failed")
sys.exit(1 if errors else 0)
