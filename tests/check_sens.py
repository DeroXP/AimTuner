"""One sensitivity for every track: a track can be told to turn the way another track's settings
say, separately for a controller and for a mouse. Checked in the maths, in the fingerprints, in
real runs on all three tracks, in the sensitivity finder and on the Settings screen.

    python tests/check_sens.py [folder for screenshots]
"""
import copy, math, os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import codkit, codscn, config, mouse, r6kit, r6scn, scenarios, screens, sens, sensfinder
from aimtuner import pad as padmod

errors = []


def expect(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


def near(a, b, tol):
    return abs(a - b) <= tol


PAD, KBM = "Controller 1", "Mouse & keyboard"
TRACKS = ("core", "cod", "r6")

print("left alone, nothing has changed")
cfg = copy.deepcopy(config.DEFAULTS)
fp = config.fingerprints(cfg)
expect(cfg["sens"] == {"pad": "own", "kbm": "own"} and (fp["core"], fp["cod"]) == ("fbda7b", "c0012b"),
       f"each track on its own settings is the default, and the controller fingerprints are what they always were ({fp['core']}, {fp['cod']})")
cfg["active_profile"] = KBM
fp = config.fingerprints(cfg)
expect((fp["core"], fp["cod"]) == ("923eaf", "b4c3ea"), f"so are the mouse's ({fp['core']}, {fp['cod']})")

print("a controller following one track")
for src in TRACKS:
    cfg = copy.deepcopy(config.DEFAULTS)
    own = {t: sens.own_pad(cfg, t) for t in TRACKS}
    fp0 = config.fingerprints(cfg)
    cfg["sens"]["pad"] = src
    got = {t: sens.pad(cfg, t) for t in TRACKS}
    same = all(g.src == src and (g.rate_h, g.rate_v, g.ads) == (own[src].rate_h, own[src].rate_v, own[src].ads)
               and all(g.shape(x, y) == own[src].shape(x, y) for x, y in ((0.2, 0.0), (0.5, 0.3), (0.0, -0.9), (1.0, 0.0)))
               for g in got.values())
    fp1 = config.fingerprints(cfg)
    expect(same, f"follows {src}: every track turns at {own[src].rate_h:.0f} deg/s, x{own[src].ads:.3f} through the sights, "
           "with that track's deadzones and curve")
    expect(fp1[src] == fp0[src] and all(fp1[t] != fp0[t] for t in TRACKS if t != src),
           "   the followed track's runs still compare with its old ones; the others start fresh")
    cfg["active_profile"] = KBM
    expect(sens.mouse(cfg, "cod").src == "cod" and sens.mouse(cfg, "core").src == "core", "   and the mouse is not affected")
cfg = copy.deepcopy(config.DEFAULTS)
cfg["sens"]["pad"] = "cod"
fp1 = config.fingerprints(cfg)
cfg["aim"]["sens_h"] += 50
cfg["aim"]["curve"] = "linear"
cfg["r6"]["sens_h"] = 80
expect(config.fingerprints(cfg) == fp1, "while every track follows Call of Duty, the Core and Siege stick settings change nothing")
cfg["cod"]["sens_h"] = 2.5
fp2 = config.fingerprints(cfg)
expect(all(fp2[t] != fp1[t] for t in TRACKS) and sens.pad(cfg, "core").rate_h == sens.own_pad(cfg, "cod").rate_h,
       "and the Call of Duty stick sensitivity moves all three")
p = sens.pad(cfg, "core")
for rate in (20.0, 150.0, 600.0):
    x = p.stick_for(rate)
    expect(near(p.shape(x, 0.0)[2] * p.rate_h, min(rate, p.rate_h), 0.02), f"   (stick position for {rate:.0f} deg/s found: {x:.3f})")

print("a mouse following one track")
for src in TRACKS:
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["active_profile"] = KBM
    cfg["mouse"]["cm360"], cfg["cod"]["mouse_sens"], cfg["r6"]["mouse_sens"] = 50.0, 8.0, 12
    cms = {t: sens.own_mouse_cm(cfg, t) for t in TRACKS}
    fp0 = config.fingerprints(cfg)
    cfg["sens"]["kbm"] = src
    got = {t: sens.mouse(cfg, t) for t in TRACKS}
    expect(all(near(g.cm360(cfg), cms[src], 1e-9) and g.src == src for g in got.values()) and len({round(v, 1) for v in cms.values()}) == 3,
           f"follows {src}: {cms[src]:.1f} cm per turn on every track (their own were {cms['core']:.1f}, {cms['cod']:.1f}, {cms['r6']:.1f})")
    fp1 = config.fingerprints(cfg)
    expect(fp1[src] == fp0[src] and all(fp1[t] != fp0[t] for t in TRACKS if t != src),
           "   the followed track's runs still compare with its old ones; the others start fresh")
cfg["sens"]["kbm"] = "core"
ms = sens.mouse(cfg, "r6")
expect(near(ms.ads(100.0, 1.5), cfg["mouse"]["ads_mult"] / 1.5, 1e-12) and near(ms.ads(100.0, 2.8), cfg["mouse"]["ads_mult"] / 2.8, 1e-12),
       "the followed track's ADS rule is applied to each track's own zoom (Core's: scaled by the zoom)")
cfg["sens"]["kbm"] = "cod"
ms = sens.mouse(cfg, "core")
expect(near(ms.ads(105.0, 1.3), cfg["cod"]["mouse_ads"] * codkit.mouse_ads_ratio(105.0, 1.3, cfg["cod"]["mouse_mdc"]), 1e-12),
       "(Call of Duty's: its monitor-distance formula, at the other track's field of view and zoom)")
cfg["sens"]["kbm"] = "r6"
ms = sens.mouse(cfg, "cod")
expect(near(ms.ads(100.0, 1.5), cfg["r6"]["mouse_ads"] * 0.02 / 1.5, 1e-12), "(Siege's: its slider x 0.02, scaled by the zoom)")
cfg["sens"]["kbm"] = "core"
fp1 = config.fingerprints(cfg)
cfg["cod"]["mouse_sens"] = 3.0
cfg["r6"]["mouse_sens"] = 30
expect(config.fingerprints(cfg) == fp1, "while every track follows Core, the other tracks' mouse sensitivities change nothing")
cfg["mouse"]["cm360"] = 40.0
expect(all(config.fingerprints(cfg)[t] != fp1[t] for t in TRACKS), "and the Core track's centimetres move all three")
cfg["active_profile"] = PAD
cfg["sens"]["pad"] = "cod"
expect(sens.pad(cfg, "r6").src == "cod" and sens.mouse(cfg, "r6").src == "core",
       "the two choices are separate: the mouse can follow Core while the controller follows Call of Duty")

# ---------------------------------------------------------------- the real loop
from aimtuner.app import App
from aimtuner.bot import Bot

scratch = tempfile.mkdtemp(prefix="aimtuner-sens-")
shots = sys.argv[1] if len(sys.argv) > 1 else None
opts = types.SimpleNamespace(windowed=False, offscreen=(1920, 1080) if shots else (1280, 720), bench=False, selftest=True,
                             only=None, repeats=1, shots=None, duration=30.0, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
app.pad.use_virtual("bot")
app.mouse.use_virtual()


def frames(n=3):
    for _ in range(n):
        yield


def key(*names):
    app.keys.update(names)
    yield


def sleep(s):
    end = time.perf_counter() + s
    while time.perf_counter() < end:
        yield


def wait(pred, secs=60.0, what=""):
    end = time.perf_counter() + secs
    while not pred():
        if time.perf_counter() > end:
            expect(False, "timeout waiting for " + what)
            return
        top = app.screens[-1]
        if isinstance(top, sensfinder.SensFinder) and top.state == "rest" and app.game is None:
            app.keys.add("enter")           # the finder's half-time break
        yield


def shot(name):
    if shots:
        yield from frames(3)
        app.pending_shot = os.path.join(shots, name + ".png")
        yield from frames(2)


def running(cls):
    app.start(cls)
    g = app.game
    while g.state != "run":
        yield
    return g


def script():
    cfg = app.cfg
    c = cfg["cod"]
    yield from frames(5)
    ads_bit = padmod.BIT[cfg["binds"]["ads"]]
    drills = {"core": scenarios.SmoothTrack, "cod": codscn.StrafeSpray, "r6": r6scn.CrouchSpam}

    print("runs on a controller that follows the Call of Duty track")
    cfg["sens"]["pad"] = "cod"
    want = sens.own_pad(cfg, "cod")
    for t, cls in drills.items():
        g = yield from running(cls)
        app.pad.inject(time.perf_counter(), 0, 0, 0, 0, 0, 32767, 0)       # right stick hard over, from the hip
        yield from frames(4)
        expect(near(g.yaw_rate, want.rate_h, 1e-6), f"{t}: full stick turns at Call of Duty's {want.rate_h:.0f} deg/s ({g.yaw_rate:.1f})")
        app.pad.inject(time.perf_counter(), 0, 0, 0, 0, 0, 16000, 0)
        yield from frames(4)
        half = want.shape(16000 / 32767, 0.0)[2] * want.rate_h
        expect(near(g.yaw_rate, half, 0.05), f"   and half stick at {half:.1f} deg/s - Call of Duty's curve and deadzones, not this track's own")
        app.pad.inject(time.perf_counter(), 0, 0, 0, 0, 0, 0, 0)
        if g.kit:
            kind_ = type(g.kit.assist).__name__
            expect(kind_ == ("AimAssist" if t == "cod" else "NoAssist"),
                   f"   aim assist is still the track's own business: {'on the Call of Duty track only' if t == 'cod' else 'none here'}")
        g.abort()
        yield
    opts.duration = 3.0
    app.bot = Bot(app)
    for t, cls in drills.items():
        app.start(cls)
        yield from wait(lambda: app.game is None, 30.0, cls.name)
        yield from sleep(0.3)
        rec = app.log.runs[-1]
        follows = rec["settings"].get("sens.source")
        expect(rec["fingerprint"] == config.fingerprint_for(cfg, t) and follows == (None if t == "cod" else "cod")
               and ("cod.sens_h" in rec["settings"]) and ("aim.sens_h" not in rec["settings"]) and ("r6.sens_h" not in rec["settings"]),
               f"{t}: the run is logged with the sensitivity it was really played on" + ("" if t == "cod" else " (follows cod)"))
        if len(app.screens) > 1:
            app.pop()
    app.bot = None
    opts.duration = 30.0
    cfg["sens"]["pad"] = "own"

    print("runs on a mouse that follows the Core track")
    app.set_profile(KBM)
    cfg["mouse"]["cm360"] = 50.0
    cfg["sens"]["kbm"] = "core"
    deg = 360.0 / (50.0 / 2.54 * cfg["mouse"]["dpi"])
    for t, cls in drills.items():
        g = yield from running(cls)
        y0 = g.yaw
        app.mouse.inject(time.perf_counter(), mouse.MOTION, 400, 0)
        yield from frames(3)
        expect(near((g.yaw - y0 + 180) % 360 - 180, 400 * deg, 1e-9), f"{t}: 400 counts turn {400 * deg:.3f} deg - 50 cm per turn, the Core track's setting")
        app.mouse.inject(time.perf_counter(), mouse.BUTTON, "M2", True)
        yield from sleep(0.75)
        y0 = g.yaw
        app.mouse.inject(time.perf_counter(), mouse.MOTION, 400, 0)
        yield from frames(3)
        expect(near((g.yaw - y0 + 180) % 360 - 180, 400 * deg * cfg["mouse"]["ads_mult"] / g.zoom_max, 1e-9),
               f"   through the sights: the Core track's rule on this track's {g.zoom_max:.2f}x zoom")
        app.mouse.inject(time.perf_counter(), mouse.BUTTON, "M2", False)
        if g.kit:
            expect(isinstance(g.kit.assist, codkit.NoAssist), "   and no aim assist, as ever on a mouse")
        g.abort()
        yield

    print("the sensitivity finder searches the settings that are in use")
    S = sensfinder.SensFinder(app)
    expect(S.kbm and S.dialect == "core" and S.phases["ads"]["key"] == "mouse.ads_mult" and S.phases["hip"]["name"] == "Mouse sensitivity",
           "mouse, following Core: centimetres per turn, then the Core track's ADS setting")
    S.phase = "ads"
    app.push(S)
    opts.duration = 2.0
    app.bot = Bot(app)
    yield from key("enter")
    yield from wait(lambda: app.game is not None, 20.0, "an ADS block")
    g = app.game
    v = S.queue[0]
    expect(set(g.over["sens"]) == {"cm360", "ads"} and near(g.over["sens"]["cm360"], 50.0, 1e-9) and g.over["sens"]["ads"] == v
           and near(g.kit.m_ads, v / g.zoom_max, 1e-12) and near(g.kit.m_deg, deg, 1e-12),
           f"a block tries a value of that setting ({v:.2f}): x{g.kit.m_ads:.3f} through the Call of Duty track's sights")
    yield from wait(lambda: S.state == "result", 240.0, "the ADS round")
    best = S.fits["ads"]["best"]
    cod_ads = c["mouse_ads"]
    yield from key("x")
    expect(cfg["mouse"]["ads_mult"] == round(min(max(best, 0.2), 2.0), 2) and c["mouse_ads"] == cod_ads and "every track follows" in S.msg,
           f"the result goes into the Core track's setting ({cfg['mouse']['ads_mult']}), which every track follows; Call of Duty's own is untouched")
    S.fits["hip"] = {"best": 44.0}
    S.phase, S.state = "hip", "result"
    yield from key("x")
    cms = {t: sens.own_mouse_cm(cfg, t) for t in TRACKS}
    expect(all(near(v_, 44.0, 0.5) for v_ in cms.values()), f"a sensitivity result is written to all three tracks: {cms}")
    app.pop()
    app.bot = None
    cfg["sens"]["kbm"] = "own"

    app.set_profile(PAD)
    cfg["sens"]["pad"] = "core"
    cfg["aim"].update(sens_h=240, sens_v=180, ads_mult=0.75)
    S = sensfinder.SensFinder(app)
    expect(not S.kbm and S.dialect == "core" and S.phases["hip"]["key"] == "aim.sens_h" and (S.phases["hip"]["lo"], S.phases["hip"]["hi"]) == (40.0, 600.0)
           and S._fmt(240.0, "hip") == "240°/s", "controller, following Core: the search is in degrees per second, 40 to 600")
    app.push(S)
    app.bot = Bot(app)
    yield from shot("sens_finder_core")
    yield from key("enter")
    yield from wait(lambda: app.game is not None, 20.0, "a hip block")
    g = app.game
    v = S.queue[0]
    expect(near(g.kit.rate_h, v, 1e-9) and near(g.kit.rate_v, 180.0 * v / 240.0, 1e-9) and near(g.kit.rate_h * g.kit.ads_scale, 180.0, 1e-6),
           f"a block plays the Call of Duty drill at {v:.0f} deg/s, with the ADS speed held at 180 deg/s")
    expect(type(g.kit.assist).__name__ == "AimAssist", "   (aim assist as usual: the drill is the Call of Duty track's)")
    yield from wait(lambda: S.state == "result", 240.0, "the hip round")
    S.fits["hip"] = dict(S.fits["hip"], best=300.4)
    yield from key("x")
    a = cfg["aim"]
    expect(a["sens_h"] == 300 and a["sens_v"] == 225 and a["ads_mult"] == 0.6 and S.phase == "ads",
           f"applied to the Core track's settings: {a['sens_h']} / {a['sens_v']} deg/s, ADS x{a['ads_mult']} (still 180 deg/s)")
    expect(c["sens_h"] == config.DEFAULTS["cod"]["sens_h"], "the Call of Duty track's own stick sensitivity was not touched")
    app.pop()
    app.bot = None
    cfg["sens"]["pad"] = "own"

    print("the Settings screen")
    st = screens.Settings(app, start="sens.pad")
    app.push(st)
    row = st.rows[st.sel]
    yield from key("right")
    expect(cfg["sens"]["pad"] == "core", "Right on 'Controller sensitivity follows' picks the Core track")
    yield from key("right")
    expect(cfg["sens"]["pad"] == "cod", "then Call of Duty")
    yield from shot("sens_settings")
    expect(screens._unused(cfg, "aim.sens_h") == ("Controller", "cod") and screens._unused(cfg, "cod.sens_h") is None
           and screens._unused(cfg, "r6.ads") == ("Controller", "cod") and screens._unused(cfg, "mouse.cm360") is None,
           "the other tracks' stick settings are marked as not in use, the followed one's are not")
    saved = __import__("json").load(open(opts.config, encoding="utf-8"))
    expect(saved["sens"]["pad"] == "cod", "the choice is saved")
    yield from key("right")
    yield from key("right")
    expect(cfg["sens"]["pad"] == "own" and st._text(row) == "Each track's own", "and round to each track's own again")
    for other in ("aim.sens_h", "r6.weapon", "r6.mouse_sens", "mouse.dpi", "@dpi", "@r6cal"):
        while st.rows[st.sel][0] != other:
            st._move(1)
        yield from frames(2)
    yield from shot("sens_settings_gun")
    app.pop()


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
print("one sensitivity:", "all passed" if not errors else f"FAILED {errors}")
sys.exit(1 if errors else 0)
