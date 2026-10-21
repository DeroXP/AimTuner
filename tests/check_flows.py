"""Drive the menu / pause / playlist flows the bot self-test does not reach (v0.2 menu structure)."""
import json, os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import codscn, config, duel, labscreen, progress, scenarios, screens, sensfinder, tracks
from aimtuner import pad as padmod
from aimtuner.app import App
from aimtuner.bot import Bot

scratch = tempfile.mkdtemp(prefix="aimtuner-flows-")
opts = types.SimpleNamespace(windowed=False, offscreen=(1280, 720), bench=False, selftest=True, only=None,
                             repeats=1, shots=None, duration=1.5, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
scenarios.Reaction.trials = 2
scenarios.StickSnap.trials = 4
app = App(opts)
app.bot = Bot(app)
errors, checks = [], [0]


def expect(cond, msg):
    checks[0] += 1
    if not cond:
        errors.append(msg)
        print("FAIL:", msg)


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


def press(btn=0, lt=0, rt=0):
    app.pad.inject(time.perf_counter(), btn, lt, rt, 0, 0, 0, 0)
    yield
    yield


def top():
    return app.screens[-1]


def script():
    cfg = app.cfg
    yield from frames(5)
    menu = top()
    expect(isinstance(menu, screens.MainMenu), "starts on main menu")
    I = menu.ITEMS.index

    # ---- every main-menu entry opens its screen and comes back
    for item, cls in (("core", screens.TrackScreen), ("cod", screens.TrackScreen), ("r6", screens.TrackScreen),
                      ("progress", progress.Progress),
                      ("sens", sensfinder.SensFinder), ("lab", labscreen.Lab), ("pad", screens.Controllers),
                      ("binds", screens.Binds), ("settings", screens.Settings), ("compare", screens.Compare),
                      ("duel", duel.Duel)):
        menu.sel = I(item)
        yield from frames(2)
        yield from key("enter")
        yield from frames(2)
        expect(isinstance(top(), cls), f"{item} opens {cls.__name__}")
        yield from key("esc")
        yield from frames(2)
        expect(top() is menu, f"{item} returns to the menu")

    # ---- controllers: rename, new, delete
    menu.sel = I("pad")
    yield from key("enter")
    c = top()
    first = cfg["profiles"][0]["name"]
    c.sel = 0
    yield from key("enter")
    expect(c.mode == "actions", "actions list opens")
    acts = [a for a, _ in c._actions(cfg["profiles"][0])]
    expect(acts == ["use", "rename", "delete", "back"], f"actions without a USB id: {acts}")
    yield from key("down")
    yield from key("enter")
    expect(c.mode == "name" and c.buf == first and c.text_mode, "rename prefilled, text mode on")
    for _ in range(2):
        yield from key("backspace")
    app.typed.extend("XL")
    yield
    app.typed.append("x")      # letters must type, not trigger the X action
    yield
    yield from key("enter")
    new_name = first[:-2] + "XLx"
    expect(cfg["profiles"][0]["name"] == new_name and cfg["active_profile"] == new_name, f"renamed to {new_name}")
    expect([p.get("kind") for p in cfg["profiles"]] == [None, None, "kbm"], "two controllers and mouse-and-keyboard to start with")
    c.sel = len(cfg["profiles"])
    yield from key("enter")
    yield from key("enter")
    expect(len(cfg["profiles"]) == 4 and cfg["profiles"][3] == {"name": "Controller 4", "devices": []}, "controller profile added")
    c.sel = 3
    yield from key("enter")
    c.act = [a for a, _ in c._actions(cfg["profiles"][3])].index("delete")
    yield from key("enter")
    expect(c.confirm_delete and len(cfg["profiles"]) == 4, "delete asks first")
    yield from key("enter")
    expect(len(cfg["profiles"]) == 3, "profile deleted")
    yield from key("esc")
    expect(top() is menu, "back to the main menu")

    # ---- device linking, auto switch, wrong-profile guard
    other = cfg["profiles"][1]["name"]
    app.pad.devices[padmod.VIRTUAL_SLOT]["id"] = "AAAA:0001"
    app.set_profile(other, link=True)
    expect(app.owner_of("AAAA:0001") == other, "unlinked pad gets linked on 'use'")
    app.set_profile(new_name, link=True)
    expect(app.owner_of("AAAA:0001") == other, "'use' never steals an existing link")
    app._last_dev = None
    app._sync_device()
    expect(cfg["active_profile"] == other, "linked pad switches the profile automatically")
    app.link_device(new_name)
    expect(app.owner_of("AAAA:0001") == new_name, "explicit link moves the pad")
    app.set_profile(new_name)
    app.pad.devices[padmod.VIRTUAL_SLOT]["id"] = "AAAA:0002"
    expect(app.start(scenarios.Flick) is False and isinstance(top(), screens.Controllers), "unknown pad: run refused")
    app.pop()
    expect(app.start(codscn.SnapShot) is False and isinstance(top(), screens.Controllers), "guard covers the COD track too")
    app.pop()
    app.pad.devices[padmod.VIRTUAL_SLOT]["id"] = None
    for p in cfg["profiles"]:
        p["devices"].clear()

    # ---- binds
    menu.sel = I("binds")
    yield from key("enter")
    b = top()
    b.sel = 0
    yield from key("enter")
    yield from press(btn=padmod.BIT["X"])
    expect(cfg["binds"]["shoot"] == "X", f"shoot bound to X: {cfg['binds']}")
    b.sel = 1
    yield from key("enter")
    yield from press(btn=padmod.BIT["X"])
    expect(cfg["binds"] == {"shoot": "LB", "ads": "X"}, f"taking the other bind swaps: {cfg['binds']}")
    b.sel = 0
    yield from key("enter")
    yield from press(rt=255)
    expect(cfg["binds"]["shoot"] == "RT", "analogue trigger can be bound")
    fp_rt = config.fingerprint(cfg)
    cfg["aim"]["trigger_threshold"] = 0.5
    expect(config.fingerprint(cfg) != fp_rt, "trigger actuation counts while a trigger is bound")
    cfg["aim"]["trigger_threshold"] = 0.12
    b.sel = b.ROWS.index("bumpers")
    yield from key("enter")
    expect(cfg["binds"] == {"shoot": "RB", "ads": "LB"}, "bumper preset")
    fp_rb = config.fingerprint(cfg)
    cfg["aim"]["trigger_threshold"] = 0.5
    expect(config.fingerprint(cfg) == fp_rb, "trigger actuation ignored on bumper binds")
    cfg["aim"]["trigger_threshold"] = 0.12
    yield from key("esc")

    # ---- settings: every row both ways (both tracks), fine steps, calibration
    menu.sel = I("settings")
    yield from key("enter")
    s = top()
    fps0 = config.fingerprints(cfg)
    rows = [r for r in s.rows if r[0] != "#"]
    for _ in range(len(rows)):
        row = s.rows[s.sel]
        if row[0] == "@dpi":
            yield from key("enter")
            expect(type(top()).__name__ == "DpiScreen", "the DPI screen opens from settings")
            yield from key("esc")
            expect(top() is s, "the DPI screen closes")
        elif row[0] == "@r6cal":
            yield from key("enter")
            cal = top()
            expect(isinstance(cal, screens.SiegeCalibrate), "Siege calibration opens from settings")
            rate0 = config.r6_rates(cfg)[0]
            cal.sel = 1
            app.typed.extend("4.5")
            yield
            yield from key("enter")
            expect(cfg["r6"]["cal"]["hip_seconds"] == 4.5 and abs(config.r6_rates(cfg)[0] - 2 * rate0) < 1e-9,
                   "Siege: halving the spin time doubles the turn speed")
            expect(config.fingerprint_r6(cfg) != fps0["r6"] and config.fingerprint_cod(cfg) == fps0["cod"],
                   "Siege: recalibrating starts a fresh Siege comparison, and only that")
            app.typed.extend("9")
            yield
            yield from key("enter")
            expect(cfg["r6"]["cal"]["hip_seconds"] == 9.0, "Siege calibration put back")
            yield from key("esc")
            expect(top() is s, "Siege calibration closes")
        elif row[0] == "@pccheck":
            from aimtuner import acscreens

            yield from key("enter")
            expect(isinstance(top(), acscreens.PCCheckScreen) and top().state == "intro",
                   "the PC check opens from settings, asking first")
            yield from key("esc")
            expect(top() is s, "the PC check closes without running")
        elif row[2] == "action":
            yield from key("enter")
            cal = top()
            expect(isinstance(cal, screens.CodCalibrate), "calibration opens from settings")
            cal.sel = 1
            app.typed.extend("6.10")
            yield
            expect(cal.buf == "6.10" and cal.text_mode, "typing a number")
            yield from key("enter")
            expect(cfg["cod"]["cal"]["hip_seconds"] == 6.10, f"typed value stored: {cfg['cod']['cal']['hip_seconds']}")
            expect(abs(config.cod_rates(cfg)[0] * 1.95 - 3600 / 6.10) < 0.01, "turn speed follows the calibration")
            expect(config.fingerprint_cod(cfg) != fps0["cod"], "recalibrating starts a fresh COD comparison")
            for _ in range(51):
                yield from key("q")
            expect(cfg["cod"]["cal"]["hip_seconds"] == 5.59, f"fine steps: {cfg['cod']['cal']['hip_seconds']}")
            cal.sel = 0
            yield from key("right")
            yield from key("left")
            expect(cfg["cod"]["cal"]["hip_turns"] == 10, "spin count steps by one")
            yield from key("esc")
            expect(top() is s, "calibration closes")
        else:
            before = config.get(cfg, row[0])
            if row[2] == "num" and before >= row[3][1]:
                yield from key("left")
                mid = config.get(cfg, row[0])
                yield from key("right")
            else:
                yield from key("right")
                mid = config.get(cfg, row[0])
                yield from key("left") if row[2] != "bool" else key("right")
            expect(mid != before, f"{row[0]} changes ({before} -> {mid})")
            expect(config.get(cfg, row[0]) == before, f"{row[0]} returns to {before!r}, got {config.get(cfg, row[0])!r}")
            if row[2] == "num" and len(row[3]) > 4:
                yield from key("e")
                expect(abs(config.get(cfg, row[0]) - before - row[3][4]) < 1e-9, f"{row[0]} fine step")
                yield from key("q")
        yield from key("down")
    expect(config.fingerprints(cfg) == fps0, "both fingerprints unchanged after the round trip")
    cfg["aim"]["sens_h"] += 10
    expect(config.fingerprint(cfg) != fps0["core"] and config.fingerprint_cod(cfg) == fps0["cod"],
           "Core sensitivity does not disturb the COD comparison")
    cfg["aim"]["sens_h"] -= 10
    cfg["cod"]["sens_h"] = 2.0
    expect(config.fingerprint(cfg) == fps0["core"] and config.fingerprint_cod(cfg) != fps0["cod"],
           "COD sensitivity does not disturb the Core comparison")
    cfg["cod"]["sens_h"] = 1.95
    yield from key("esc")

    # ---- a run: cancel, pause, resume, restart
    runs0 = len(app.log.runs)
    app.start(scenarios.Flick)
    yield from frames()
    yield from key("esc")
    expect(app.game is None and len(app.log.runs) == runs0, "countdown cancel logs nothing")
    app.start(codscn.StickyTrack)
    g = app.game
    yield from wait(lambda: g.state == "run", what="run start")
    yield from sleep(0.3)
    yield from key("esc")
    expect(g.state == "paused" and g.pauses == 1, "esc pauses")
    t_frozen, pos = g.t, (g.kit.x, g.kit.z)
    yield from sleep(0.3)
    expect(g.t == t_frozen and (g.kit.x, g.kit.z) == pos, "clock and player stop while paused")
    yield from key("enter")
    yield from wait(lambda: g.state == "run", what="resume")
    yield from key("esc")
    yield from key("x")
    g2 = app.game
    expect(g2 is not g and g2.state == "countdown" and len(app.log.runs) == runs0, "restart discards the run")
    yield from wait(lambda: app.game is None, what="run to finish")
    r = top()
    expect(isinstance(r, screens.Results) and len(app.log.runs) == runs0 + 1, "results shown, run logged")
    rec = app.log.runs[-1]
    expect(rec["track"] == "cod" and rec["fingerprint"] == config.fingerprint_cod(cfg), "COD run tagged with its track and fingerprint")
    expect("aa_full_pct" in rec["stats"] and rec["settings"]["cod.sens_h"] == 1.95, f"COD stats and settings recorded")
    yield from sleep(0.9)
    yield from key("esc")

    # ---- track screens: scroll every row, play one mechanic as a playlist
    menu.sel = I("cod")
    yield from key("enter")
    t = top()
    kinds = [k for k, _ in t.rows]
    expect(kinds.count("scn") == 27 and kinds.count("head") == 9 and kinds[0] == "all", f"COD track rows: {len(kinds)}")
    seen = []
    for _ in range(28):
        seen.append(t.rows[t.sel][0])
        yield from key("down")
    expect("head" not in seen and seen.count("scn") == 27, "cursor skips the mechanic headings")
    t.sel = next(i for i, (k, a) in enumerate(t.rows) if a is codscn.StrafeToStick)
    runs1 = len(app.log.runs)
    yield from key("x")
    for cls in (codscn.StrafeToStick, codscn.MirrorStrafe, codscn.StrafeDuel):
        expect(app.game is not None and type(app.game.scn) is cls, f"mechanic playlist plays {cls.__name__}")
        yield from wait(lambda: app.game is None, what=cls.__name__)
        yield from sleep(0.9)
        yield from key("enter")
        yield from frames(2)
    group = app.log.runs[runs1:]
    expect(len(group) == 3 and len({r["playlist"] for r in group}) == 1 and all(r["track"] == "cod" for r in group),
           f"3 runs share one playlist id ({len(group)})")
    cmp_ = top()
    expect(isinstance(cmp_, screens.Compare) and cmp_.track.id == "cod", "playlist ends on that track's compare")
    yield from key("e")
    expect(cmp_.track.id == "r6", "RB switches track in compare")
    yield from key("q")
    for _ in range(27):
        yield from key("right")
    yield from key("y")
    yield from frames(3)
    yield from key("esc")
    expect(top() is t, "compare closes back to the track")
    yield from key("esc")

    # ---- whole Core track as a playlist
    menu.sel = I("core")
    yield from key("enter")
    runs2 = len(app.log.runs)
    yield from key("enter")
    for cls in scenarios.ALL:
        expect(app.game is not None and type(app.game.scn) is cls, f"core playlist: {cls.id}")
        yield from wait(lambda: app.game is None, what=cls.id)
        yield from sleep(0.9)
        yield from key("enter")
        yield from frames(2)
    core = app.log.runs[runs2:]
    expect(len(core) == 9 and all(r["track"] == "core" and r["fingerprint"] == config.fingerprint(cfg) for r in core),
           f"9 core runs with the core fingerprint ({len(core)})")
    yield from key("esc")
    yield from key("esc")
    expect(top() is menu, "back on the main menu")

    # ---- the log on disk
    on_disk = [json.loads(l) for l in open(os.path.join(scratch, "runs.jsonl"), encoding="utf-8")]
    expect(len(on_disk) == len(app.log.runs), "every run is on disk")
    expect(len(os.listdir(os.path.join(scratch, "runs"))) == len(app.log.runs), "one detail file per run")
    expect(not os.path.exists(os.path.join(scratch, "sens.jsonl")), "no sensitivity blocks were written by ordinary runs")
    expect(len(tracks.ALL) == 60 and len({s.id for s in tracks.ALL}) == 60, "60 scenarios with unique ids")
    expect(len(tracks.R6.scenarios) == 24 and all(s.aa is False for s in tracks.R6.scenarios),
           "24 of them are the Siege track's, none with aim assist")


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
