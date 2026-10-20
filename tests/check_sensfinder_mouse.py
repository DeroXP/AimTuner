"""The sensitivity finder on mouse and keyboard, played offscreen by the self-test bot: a round of
mouse sensitivity (searched in centimetres per turn), apply, a round of the ADS multiplier, apply.

    python tests/check_sensfinder_mouse.py [folder for screenshots]
"""
import json, math, os, random, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import codkit, config, screens, sens, sensfinder, sensfit
from aimtuner.app import App
from aimtuner.bot import Bot

errors = []


def expect(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


def near(a, b, tol):
    return abs(a - b) <= tol


print("the search plan, in centimetres")
a = sensfit.plan(1.95, 0, 0.10, 4.00, random.Random(3))
b = sensfit.plan(1.95, 0, 0.10, 4.00, random.Random(3), 0, 2)
expect(a == b and len(set(a)) == 5, f"the game's 0.01 steps are what they were: {sorted(set(a))}")
p = sensfit.plan(50.0, 0, 5.0, 150.0, random.Random(3), 0, 1)
expect(len(p) == 10 and p[:5] == p[5:][::-1] and len(set(p)) == 5 and 50.0 in p and all(round(v, 1) == v for v in p),
       f"centimetres move in steps of 0.1, mirrored: {sorted(set(p))}")
p = sensfit.plan(6.0, 0, 5.0, 150.0, random.Random(3), 0, 1)
expect(min(p) >= 5.0 and len(set(p)) == 5, f"squeezed against the fast end, still five values: {sorted(set(p))}")
p = sensfit.plan(40.0, 0, 5.0, 150.0, random.Random(3), -1, 1)
expect(min(p) <= 20.5 and max(p) < 50.0, f"best at the fast edge: the next round reaches for fewer centimetres: {sorted(set(p))}")
cfg0 = {"mouse": {"dpi": 800, "cm360": 30.0}, "cod": {"mouse_sens": 6.0}}
cfg0["cod"]["mouse_sens"] = config.cod_mouse_sens_for(cfg0, 41.5)
expect(near(config.cod_mouse_cm360(cfg0), 41.5, 1e-9), f"41.5 cm per turn at 800 DPI is {cfg0['cod']['mouse_sens']:.3f} on the game's scale, and back")

scratch = tempfile.mkdtemp(prefix="aimtuner-sensm-")
shots = sys.argv[1] if len(sys.argv) > 1 else os.path.join(scratch, "shots")
opts = types.SimpleNamespace(windowed=False, offscreen=(1920, 1080), bench=False, selftest=True, only=None,
                             repeats=1, shots=None, duration=5.0, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
app.bot = Bot(app)


def key(*names):
    app.keys.update(names)
    yield


def frames(n=3):
    for _ in range(n):
        yield


def top():
    return app.screens[-1]


peaks: list = []        # per block played while `watch` is on: the fastest the view turned, deg/s
watch = {"on": False, "g": None}


def sample():
    """Once a frame: how fast is the view turning sideways, over fifths of a second of game time?"""
    g = app.game
    if not watch["on"] or g is None or g.state != "run":
        return
    if watch["g"] is not g:
        watch.update(g=g, yaw=g.yaw, t0=g.t, turned=0.0)
        peaks.append(0.0)
    watch["turned"] += abs((g.yaw - watch["yaw"] + 180.0) % 360.0 - 180.0)
    watch["yaw"] = g.yaw
    if g.t - watch["t0"] >= 0.2:
        peaks[-1] = max(peaks[-1], watch["turned"] / (g.t - watch["t0"]))
        watch.update(t0=g.t, turned=0.0)


def wait(pred, secs=240.0, what=""):
    end = time.perf_counter() + secs
    while not pred():
        if time.perf_counter() > end:
            expect(False, "timeout: " + what)
            return
        if isinstance(top(), sensfinder.SensFinder) and top().state == "rest" and app.game is None:
            app.keys.add("enter")
        sample()
        yield


def shot(name):
    yield from frames(3)
    app.pending_shot = os.path.join(shots, name + ".png")
    yield from frames(2)


def script():
    cfg = app.cfg
    c, m = cfg["cod"], cfg["mouse"]
    pad_name = cfg["active_profile"]
    kbm = next(pr["name"] for pr in cfg["profiles"] if pr.get("kind") == "kbm")
    yield from frames(5)
    menu = top()

    print("what you are on")
    app.set_profile(kbm)
    expect(sensfinder.mouse_now(app) == (30.0, None), "fresh settings: the tracks agree (30.0, 28.9 and 28.5 cm), so that is it")
    m["cm360"] = 50.0
    cm, src = sensfinder.mouse_now(app)
    expect(src == "cod" and near(cm, config.cod_mouse_cm360(cfg), 1e-9), f"tracks differ, no mouse run yet: the Call of Duty track's {cm:.1f} cm")
    app.log.runs.append({"track": "cod", "input": {"kind": "pad"}})
    app.log.runs.append({"track": "core", "input": {"kind": "kbm"}})
    app.log.runs.append({"track": "cod", "input": {"kind": "pad"}})
    expect(sensfinder.mouse_now(app) == (50.0, "core"), "tracks differ: the one the last MOUSE run was played on - Core's 50.0 cm")

    print("opening it on a mouse")
    menu.sel = menu.ITEMS.index("sens")
    yield from shot("sensm_0_menu")
    yield from key("enter")
    S = top()
    expect(isinstance(S, sensfinder.SensFinder) and S.kbm and S.phases is sensfinder.MOUSE_PHASES,
           "Sensitivity finder opens on mouse and keyboard, as the mouse's own search")
    expect(S.base == {"cm": 50.0} and S.src == "core" and S._value_now("hip") == 50.0 and S._value_now("ads") == c["mouse_ads"],
           "it starts from 50.0 cm per turn and the current ADS multiplier")
    yield from shot("sensm_1_intro")
    seen = []
    done = S._block_done
    S._block_done = lambda st: (seen.append(dict(st)), done(st))[1]

    print("mouse sensitivity pass")
    # Left alone the bot turns as fast as it likes at any sensitivity, so every distance would score
    # the same. Give it a slow hand (8 cm/s): now more centimetres per turn really is a slower turn,
    # which can be measured on the view itself - and the search has something to find, at the fast end.
    HAND = app.bot.hand = 8.0
    watch["on"] = True
    yield from key("enter")
    yield from wait(lambda: app.game is not None, what="first block")
    g = app.game
    v = S.queue[0]
    expect(g.kbm and isinstance(g.kit.assist, codkit.NoAssist), "a block is a mouse run with no aim assist attached")
    expect(near(g.kit.m_deg, 360.0 / (v / 2.54 * m["dpi"]), 1e-12), f"it turns one full turn in the planned {v:.1f} cm ({360.0 / g.kit.m_deg:,.0f} counts)")
    expect(g.over["sens"] == {"cm360": v} and near(g.kit.m_ads / codkit.mouse_ads_ratio(g.fov, g.zoom_max, c["mouse_mdc"]), c["mouse_ads"], 1e-12),
           "the ADS multiplier is left alone, so the sights scale with the sensitivity")
    expect(c["mouse_sens"] == 6.0 and m["cm360"] == 50.0, "the saved settings are not touched by a block")
    yield from wait(lambda: S.state == "result", what="round 1")
    hip = S.blocks["hip"]
    vals = [b_["value"] for b_ in hip]
    expect(len(hip) == 10 and vals[:5] == vals[5:][::-1] and len(set(vals)) == 5 and 50.0 in vals,
           f"ten blocks, five distances, mirrored: {vals}")
    expect(all(5.0 <= x <= 150.0 and round(x, 1) == x for x in vals) and all(b_["unit"] == "cm per turn" for b_ in hip),
           "every value is a whole tenth of a centimetre inside 5 - 150 cm, and logged as centimetres")
    expect(all(b_["settings"] == {"cm360": b_["value"], "dialect": "cod"} for b_ in hip),
           "each block is logged with the distance it was played at and whose settings were in use")
    expect(len(seen) == 10 and all(not any(k.startswith("aa_") for k in st) and "coach" not in st for st in seen),
           "no aim-assist numbers came back from any block")
    # The bot turns with the sights up, its hand flat out at HAND cm/s for most of each big turn. At v cm
    # per turn that must show on screen as HAND / v turns per second, times the sights' ratio.
    watch["on"] = False
    sights = c["mouse_ads"] * codkit.mouse_ads_ratio(c["fov"], codkit.sight_zoom(c), c["mouse_mdc"])
    off = [(b_["value"], pk, HAND / b_["value"] * 360.0 * sights) for b_, pk in zip(hip, peaks)]
    print("   fastest turn, measured against expected:", [(x, round(pk, 1), round(ex, 1)) for x, pk, ex in off])
    expect(len(peaks) == 10 and all(abs(pk / ex - 1.0) < 0.05 for _, pk, ex in off),
           f"a hand at {HAND:.0f} cm/s turns the view at exactly the speed each block's centimetres-per-turn calls for")
    by = {}
    for b_ in hip:
        by.setdefault(b_["value"], []).append(b_["perf"])
    order = sorted(by)
    mean = lambda vs: sum(sum(by[x]) for x in vs) / sum(len(by[x]) for x in vs)
    fast, slow = mean(order[:2]), mean(order[-2:])
    print("   score by distance:", {x: [round(p_, 1) for p_ in by[x]] for x in order})
    expect(fast >= slow, f"so with a slow hand, fewer centimetres per turn scores at least as well ({fast:.1f} against {slow:.1f})")
    f = S.fits["hip"]
    expect(f is not None, f"a curve was fitted: best {f and round(f['best'], 1)} cm")
    app.bot.hand = None
    yield from shot("sensm_2_result_hip")
    old_ads, old_core_ads = c["mouse_ads"], m["ads_mult"]
    yield from key("x")
    cm = round(f["best"], 1)
    expect(m["cm360"] == cm and S.phase == "ads" and S.state == "intro", f"applied to the Core track: {m['cm360']} cm per turn")
    expect(c["mouse_sens"] == round(config.cod_mouse_sens_for(cfg, cm), 2) and near(config.cod_mouse_cm360(cfg), cm, cm * 0.01),
           f"and to the Call of Duty track: {c['mouse_sens']:.2f} = {config.cod_mouse_cm360(cfg):.1f} cm per turn")
    expect(c["mouse_ads"] == old_ads and m["ads_mult"] == old_core_ads, "neither ADS setting moved")
    expect(near(sens.own_mouse_cm(cfg, "r6"), cm, cm * 0.011), f"and to the Siege track: sensitivity {cfg['r6']['mouse_sens']} at unit "
           f"{cfg['r6']['mouse_unit']:g} = {sens.own_mouse_cm(cfg, 'r6'):.1f} cm per turn")
    expect(sensfinder.mouse_now(app) == (cm, None) and S.src is None and S.base == {"cm": cm}, "the tracks agree from here on")
    saved = json.load(open(opts.config, encoding="utf-8"))
    expect(saved["mouse"]["cm360"] == cm and saved["cod"]["mouse_sens"] == c["mouse_sens"], "config saved")
    yield from shot("sensm_3_intro_ads")

    print("ADS multiplier pass")
    yield from key("enter")
    yield from wait(lambda: app.game is not None, what="first ADS block")
    g = app.game
    v = S.queue[0]
    expect(near(g.kit.m_deg, 360.0 / (cm / 2.54 * m["dpi"]), 1e-12), "ADS blocks run at the sensitivity just applied")
    expect(near(g.kit.m_ads, v * codkit.mouse_ads_ratio(g.fov, g.zoom_max, c["mouse_mdc"]), 1e-12) and isinstance(g.kit.assist, codkit.NoAssist),
           f"with the planned multiplier ({v:.2f}) on the sights, and no aim assist")
    yield from wait(lambda: S.state == "result", what="ADS round")
    ads = S.blocks["ads"]
    vals = [b_["value"] for b_ in ads]
    expect(len(ads) == 10 and len(set(vals)) == 5 and all(0.10 <= x <= 3.00 and round(x, 2) == x for x in vals),
           f"ten blocks at five multipliers: {sorted(set(vals))}")
    fa = S.fits["ads"]
    yield from shot("sensm_4_result_ads")
    yield from key("x")
    expect(fa is not None and c["mouse_ads"] == round(fa["best"], 2) and m["ads_mult"] == old_core_ads,
           f"applied to the Call of Duty track only: {c['mouse_ads']}; the Core track's ADS setting is its own")
    expect(len(seen) == 20 and all(not any(k.startswith("aa_") for k in st) for st in seen), "still no aim-assist numbers")

    print("the log, and keeping the two devices apart")
    recs = sensfinder.load(scratch)
    expect(len(recs) == 20 and all(r["context"].get("input") == "kbm" and r["device"] is None for r in recs)
           and not os.path.exists(os.path.join(scratch, "runs.jsonl")), "20 blocks in sens.jsonl, marked as mouse; nothing in the run log")
    S2 = sensfinder.SensFinder(app)
    expect(len(S2.blocks["hip"]) == 10 and len(S2.blocks["ads"]) == 10 and S2.fits["hip"] is not None and S2.rounds == {"hip": 1, "ads": 1},
           "a fresh mouse finder opens with that session already counted, both passes")
    logged = S2.blocks["ads"]
    expect(all(r["sights_unit"] == "cm per turn" and near(r["sights"], cm / S2._sights(r["value"]), 0.01) for r in logged),
           f"each ADS block is logged with the distance per turn through the sights (e.g. x{logged[0]['value']:.2f} = {logged[0]['sights']:.1f} cm)")
    expect([p[0] for p in S2.pts["ads"]] == [r["value"] for r in logged], "at the same sensitivity they sit where they were played")
    S2.base = {"cm": cm * 1.25}         # as if the sensitivity had been changed since
    S2._refit("ads")
    ok, kept = True, 0
    for r in logged:
        v, want = S2._placed(r, {}), r["value"] * 1.25
        kept += v is not None
        if 0.101 <= want <= 2.98:
            ok = ok and v is not None and near(v / want, 1.0, 1e-4) and near(S2.base["cm"] / S2._sights(v), r["sights"], 0.01)
        elif want > 3.02:
            ok = ok and v is None
    expect(ok and len(S2.pts["ads"]) == kept, "at a sensitivity a quarter slower, each ADS block moves to the multiplier that gives the "
           f"same distance through the sights (x1.25); {len(logged) - kept} fall off the scale and are left out")
    S2.base = {"cm": cm}
    S2._refit("ads")
    app.set_profile(pad_name)
    P = sensfinder.SensFinder(app)
    expect(not P.kbm and P.phases is sensfinder.PHASES and not P.blocks["hip"] and not P.blocks["ads"],
           "on a controller it is the controller's search, and the mouse session is not counted")
    expect(set(P._settings_for(2.0)) == {"hip", "ads_speed"}, "its blocks try a stick sensitivity, with ADS speed held")
    app.push(P)
    yield from frames(2)
    app.set_profile(kbm)            # what a click on the mouse does while the finder is open
    yield from frames(2)
    expect(top() is not P and isinstance(top(), sensfinder.SensFinder) and top().kbm, "switching device while it is open restarts it for the new one")
    app.pop()

    print("a DPI at which the game's 0.01 steps are coarse")
    keep = (m["dpi"], m["cm360"], c["mouse_sens"])
    m["dpi"] = 16000
    S3 = sensfinder.SensFinder(app)
    S3.fits["hip"] = {"best": 60.04}
    S3._apply()
    got = config.cod_mouse_cm360(cfg)
    expect(m["cm360"] == 60.0 and c["mouse_sens"] == 0.14 and "nearest" in S3.msg and f"{got:.1f}" in S3.msg,
           f"60.0 cm needs 0.144; the game's scale gives 0.14 = {got:.1f} cm, and the player is told")
    m["dpi"], m["cm360"], c["mouse_sens"] = keep

    print("a result with a peak inside what was tested (simulated scores)")
    rng = random.Random(11)
    S4 = sensfinder.SensFinder(app)
    expect(len(S4.blocks["hip"]) == 10, "(the bot's session is counted here too...")
    S4._start_over()
    expect(not S4.blocks["hip"] and len(S4.blocks["ads"]) == 10 and not sensfinder.SensFinder(app).blocks["hip"],
           " ...until the sensitivity search is started over: its blocks stop counting, the ADS pass keeps its own)")
    S4.base = {"cm": 50.0}
    truth = 41.0
    score = lambda v: 31.0 * (1.0 - 1.5 * math.log(v / truth) ** 2) * (1.0 + rng.gauss(0.0, 0.06))
    for rnd in range(2):
        f4 = S4.fits["hip"]
        for v in sensfit.plan(f4["best"] if f4 else 50.0, S4.level["hip"], 5.0, 150.0, rng, f4["side"] if f4 else 0, 1):
            S4.blocks["hip"].append({"value": v, "perf": round(score(v), 3), "phase": "hip"})
        S4.rounds["hip"] += 1
        S4._refit("hip")
        S4.level["hip"] = sensfit.next_level(S4.level["hip"], S4.fits["hip"], 5.0, 150.0)
    f4 = S4.fits["hip"]
    expect(abs(math.log(f4["best"] / truth)) < 0.15, f"a hand that peaks at {truth} cm: found {f4['best']:.1f} cm, "
           f"likely {f4['lo']:.1f} - {f4['hi']:.1f}")
    S4.state = "result"
    app.push(S4)
    end = time.perf_counter() + 4.5         # let the "restarted" toast from above clear before the picture
    while time.perf_counter() < end:
        yield
    yield from shot("sensm_5_result_sim")
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
print("sens finder (mouse):", "all passed" if not errors else f"FAILED {errors}")
sys.exit(1 if errors else 0)
