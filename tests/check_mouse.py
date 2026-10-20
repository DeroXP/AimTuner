"""Mouse and keyboard: the raw-report parser, the sensitivity maths, that a mouse can never get aim
assist, and whole runs on both tracks played by the self-test bot through a virtual mouse.

The last part listens to the real mouse for a moment and - unless AIMTUNER_NO_NUDGE is set - nudges
the pointer one count right and one count left a few times (net zero, no clicks) to prove that raw
reports really reach the input thread on this machine, unaccelerated and in order.
"""
import ctypes, ctypes.wintypes as wt, math, os, struct, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import codkit, codscn, config, mouse, scenarios, screens, tracks
from aimtuner import pad as padmod
from aimtuner.app import App
from aimtuner.bot import Bot

errors, checks = [], [0]


def expect(cond, msg):
    checks[0] += 1
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


def near(a, b, tol):
    return a is not None and abs(a - b) <= tol


# ---------------------------------------------------------------- the parser
print("raw reports -> events")


def rec_mouse(dx=0, dy=0, bflags=0, bdata=0, flags=0):
    return struct.pack("<IIQQ", 0, 48, 0x1234, 0) + struct.pack("<HxxHHIiiI", flags, bflags, bdata, 0, dx, dy, 0)


def rec_key(vkey, up=False):
    return struct.pack("<IIQQ", 1, 40, 0x5678, 0) + struct.pack("<HHHHII", 0x11, 1 if up else 0, 0, vkey, 0x100, 0)


def run_parse(records, held=None, state=None):
    buf = b"".join(r + b"\0" * (-len(r) % 8) for r in records)
    out = []
    moved = mouse.parse(ctypes.create_string_buffer(buf, len(buf) + 8), len(records), 1.5, out, held if held is not None else set(),
                        state if state is not None else {})
    return out, moved


out, moved = run_parse([rec_mouse(3, -2), rec_mouse(-1, 0), rec_mouse(0, 0, 0x0001), rec_mouse(5, 5, 0x0002 | 0x0004)])
expect(out == [(1.5, 0, 3, -2), (1.5, 0, -1, 0), (1.5, 1, "M1", True), (1.5, 0, 5, 5), (1.5, 1, "M1", False), (1.5, 1, "M2", True)]
       and moved == 3, f"movement and buttons, in order, counts untouched: {out}")
out, _ = run_parse([rec_mouse(0, 0, 0x0400, 120), rec_mouse(0, 0, 0x0400, 0x10000 - 240), rec_mouse(0, 0, 0x0040), rec_mouse(0, 0, 0x0200)])
expect(out == [(1.5, 2, 1.0, 0), (1.5, 2, -2.0, 0), (1.5, 1, "M4", True), (1.5, 1, "M5", False)], f"wheel and side buttons: {out}")
held = set()
out, _ = run_parse([rec_key(0x57), rec_key(0x57), rec_key(0x57), rec_key(0x41), rec_key(0x57, up=True), rec_key(0x57, up=True),
                    rec_key(0x20), rec_key(0xFF)], held)
expect(out == [(1.5, 1, "W", True), (1.5, 1, "A", True), (1.5, 1, "W", False), (1.5, 1, "SPACE", True)] and held == {"A", "SPACE"},
       f"keys: a held key's repeats are one press, a release is one release: {out}")
out, _ = run_parse([rec_mouse(-7, 4), rec_key(0x44), rec_mouse(1, 1)])
expect([e[1] for e in out] == [0, 1, 0], "mouse and keyboard records of different sizes are walked correctly")
st = {"w": 1920, "h": 1080}
out, _ = run_parse([rec_mouse(32768, 32768, flags=1), rec_mouse(32768 + 3413, 32768, flags=1)], state=st)
expect(out == [(1.5, 0, 100, 0)], f"a device that reports positions (tablet, remote desktop) is differenced: {out}")
big = [rec_mouse(1, 0)] * 500
out, moved = run_parse(big)
expect(len(out) == 500 and moved == 500 and sum(e[2] for e in out) == 500, "500 reports in one read: none lost")
expect(mouse.label("M1") == "Left click" and mouse.label("W") == "W" and "ESC" in mouse.RESERVED, "bind names read as words")

print("the queue the game reads")
m = mouse.Mouse(start=False)
for row in ((0.10, 0, 4, 0), (0.11, 1, "M1", True), (0.12, 0, -1, 2), (0.13, 1, "W", True), (0.14, 1, "M1", False), (0.15, 2, 1.0, 0)):
    m.inject(*row)
m.update(0.2)
expect((m.dx, m.dy, m.wheel) == (3, 2, 1.0) and m.held == {"W"} and len(m.events) == 6 and m.first_press() == "M1",
       f"one frame's counts, what is down, and the first press: {m.dx, m.dy, m.held}")
expect(near(m.age_ms, 80.0, 1e-6), f"how old the newest movement was when the frame took it: {m.age_ms:.1f} ms")
m.update(0.3)
expect((m.dx, m.dy) == (0, 0) and m.events == [] and m.down("W"), "the next frame starts empty, held keys stay held")
m.resync()
expect(not m.held, "resync forgets what was held")

# ------------------------------------------------------- sensitivity and fingerprints
print("sensitivity")
cfg = config.load(r"Z:\nope\missing.json")
kbm_name = next(p["name"] for p in cfg["profiles"] if p.get("kind") == "kbm")
expect(config.kind(cfg) == "pad" and config.kind(cfg, kbm_name) == "kbm", "a fresh config has a mouse-and-keyboard profile, not selected")
fp_pad = config.fingerprints(cfg)
cfg["active_profile"] = kbm_name
fp_kbm = config.fingerprints(cfg)
expect(set(fp_pad.values()).isdisjoint(fp_kbm.values()), f"mouse runs never share a settings fingerprint with controller runs: {fp_pad} / {fp_kbm}")
deg = config.mouse_deg(cfg)
expect(near(360.0 / deg, 30.0 / 2.54 * 800, 1e-6), f"30 cm per turn at 800 DPI is {360 / deg:,.0f} counts per turn")
cfg["mouse"]["dpi"] = 1600
expect(near(config.mouse_deg(cfg), deg / 2, 1e-12) and config.fingerprint(cfg) == fp_kbm["core"],
       "Core: doubling DPI halves the degrees per count - same centimetres, same fingerprint")
expect(config.fingerprint_cod(cfg) != fp_kbm["cod"], "Call of Duty: the game's number is per count, so there DPI changes the fingerprint")
cfg["mouse"]["dpi"] = 800
cfg["mouse"]["cm360"] = 25.0
expect(config.fingerprint(cfg) != fp_kbm["core"] and config.fingerprint_cod(cfg) == fp_kbm["cod"], "each track's mouse settings split only that track")
cfg["mouse"]["cm360"] = 30.0
cfg["aim"]["sens_h"] += 10
cfg["cod"]["sens_h"] = 2.5
cfg["cod"]["aa_style"] = "classic"
expect(config.fingerprints(cfg) == fp_kbm, "stick settings and the aim-assist style mean nothing to a mouse run")
cfg["aim"]["sens_h"] -= 10
cfg["cod"]["sens_h"], cfg["cod"]["aa_style"] = 1.95, "bo7"
expect(near(config.cod_mouse_cm360(cfg), 360 / (0.0066 * 6.0 * 800) * 2.54, 1e-9), f"Call of Duty 6.00 at 800 DPI = {config.cod_mouse_cm360(cfg):.1f} cm per turn")
r = codkit.mouse_ads_ratio
t = math.tan(math.radians(50)) / (16 / 9)
expect(near(r(100, 1.5, 0.0), 1 / 1.5, 1e-12), "ADS, coefficient 0: scaled by the zoom exactly")
expect(near(r(100, 1.5, 1.33), math.atan(1.33 * t / 1.5) / math.atan(1.33 * t), 1e-12) and 1 / 1.5 < r(100, 1.5, 1.33) < r(100, 1.5, 1.78) < 1.0,
       f"ADS, the game's default 1.33: x{r(100, 1.5, 1.33):.4f}, between the zoom ratio and no change")
expect(near(r(100, 1.0, 1.33), 1.0, 1e-12), "no zoom, no change")
x = 1.33 * t        # the matched point: 1.33 half-heights out. The same hand movement must cover it at both zooms.
expect(near(math.atan(x / 1.5) / r(100, 1.5, 1.33), math.atan(x), 1e-12), "the coefficient does what it says: that point is matched")

# ------------------------------------------------------------------ whole runs
scratch = tempfile.mkdtemp(prefix="aimtuner-mouse-")
shots = sys.argv[1] if len(sys.argv) > 1 else None
opts = types.SimpleNamespace(windowed=False, offscreen=(1920, 1080) if shots else (1280, 720), bench=False, selftest=True,
                             only=None, repeats=1, shots=None, duration=4.0, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
scenarios.Reaction.trials = 3
app = App(opts)
app.bot = Bot(app)


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


def finish_run():
    yield from wait(lambda: app.game is None, what="the run to finish")
    yield from sleep(0.9)
    rec = app.log.runs[-1]
    yield from key("esc")
    yield from frames(2)
    return rec


def script():
    cfg = app.cfg
    pad_name = cfg["active_profile"]
    kbm = next(p["name"] for p in cfg["profiles"] if p.get("kind") == "kbm")
    yield from frames(5)
    menu = top()
    I = menu.ITEMS.index

    print("selecting it")
    expect(not app.mouse.thread and app.mouse.virtual, "an offscreen run never listens to the real mouse")
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "M1", True)
    yield from frames(2)
    expect(cfg["active_profile"] == kbm, "a click in the menus selects the mouse-and-keyboard profile")
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "M1", False)
    yield from shot("mouse_menu")
    app.pad.inject(time.perf_counter(), padmod.BIT["A"], 0, 0, 0, 0, 0, 0)
    yield from frames(2)
    expect(cfg["active_profile"] == pad_name, "a button on the controller selects the controller again")
    app.pad.inject(time.perf_counter(), 0, 0, 0, 0, 0, 0, 0)
    yield from frames(2)
    if not isinstance(top(), screens.MainMenu):     # that A opened whatever was highlighted
        yield from key("esc")
    app.set_profile(kbm)
    for item in ("lab", "duel"):
        menu.sel = I(item)
        yield from key("enter")
        expect(top() is menu, f"{item}: controller-only, not opened on mouse and keyboard")
    menu.sel = I("sens")            # (the mouse has a sensitivity finder of its own: check_sensfinder_mouse.py)
    yield from key("enter")
    expect(top() is not menu and getattr(top(), "kbm", False), "sens: opens the mouse's own sensitivity finder")
    yield from key("esc")
    menu.sel = I("binds")
    yield from key("enter")
    kb = top()
    expect(isinstance(kb, screens.KbmBinds), "Binds opens the mouse-and-keyboard binds")
    yield from shot("mouse_binds")
    kb.sel = kb.rows.index("shoot")
    yield from key("enter")
    yield from frames(2)
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "SPACE", True)
    yield from frames(2)
    expect(cfg["kbm"]["shoot"] == "SPACE", f"a key can be bound to Shoot: {cfg['kbm']}")
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "SPACE", False)
    kb.sel = kb.rows.index("ads")
    yield from key("enter")
    yield from frames(2)
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "SPACE", True)
    yield from frames(2)
    expect(cfg["kbm"]["ads"] == "SPACE" and cfg["kbm"]["shoot"] == "M2", f"taking another action's key swaps them: {cfg['kbm']}")
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "SPACE", False)
    kb.sel = kb.rows.index("left")
    yield from key("enter")
    yield from frames(2)
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "ESC", True)
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "ESC", False)
    yield from key("esc")
    expect(cfg["kbm"]["left"] == "A" and kb.capture is None, "Esc cancels a capture and cannot be bound")
    kb.sel = kb.rows.index("@reset")
    yield from key("enter")
    expect(cfg["kbm"] == mouse.DEFAULT_BINDS, "reset restores left click, right click, W A S D")
    yield from key("esc")
    app.push(screens.Settings(app, start="mouse.cm360"))
    yield from shot("mouse_settings")
    top().sel = next(i for i, r in enumerate(top().rows) if r[0] == "cod.mouse_sens")
    top().top = top().sel - 6
    yield from shot("mouse_settings_cod")
    app.pop()

    print("Core track on a mouse")
    expect(app.start(scenarios.StickSnap) is False and app.game is None, "Stick Snap times a thumbstick: refused on a mouse")
    core = app.start_playlist(tracks.CORE.scenarios, "core")
    expect(core and scenarios.StickSnap not in core["items"] and len(core["items"]) == 8, "the whole-track playlist leaves it out")
    app.game.abort()
    yield from frames(2)
    app.start(scenarios.Flick)
    g = app.game
    expect(g.kbm and g.kit is None and near(g.m_deg, 360.0 / (30.0 / 2.54 * 800), 1e-12), "the run is a mouse run at the set sensitivity")
    yield from wait(lambda: g.state == "run", what="Flick to start")
    yield from shot("mouse_core_run")
    # exactly one turn's worth of counts brings the view back to where it was
    app.bot = None
    yield from frames(2)
    yaw0 = g.yaw
    n_turn = round(360.0 / g.m_deg)
    for chunk in (n_turn // 4, n_turn // 4, n_turn // 4, n_turn - 3 * (n_turn // 4)):
        app.mouse.inject(time.perf_counter(), mouse.MOTION, chunk, 0)
        yield
    yield from frames(2)        # (what is injected in one frame is read in the next and applied before the one after)
    expect(abs((g.yaw - yaw0 + 180) % 360 - 180) < g.m_deg, f"{n_turn:,} counts is one full turn (off by {abs((g.yaw - yaw0 + 180) % 360 - 180):.4f} deg)")
    p0 = g.pitch
    app.mouse.inject(time.perf_counter(), mouse.MOTION, 0, -100)
    yield from frames(2)
    expect(near(g.pitch - p0, 100 * g.m_deg, 1e-9), "pushing the mouse away looks up, count for count")
    app.mouse.inject(time.perf_counter(), mouse.MOTION, 0, 100)
    yield from frames(2)
    # a controller's stick must do nothing in a mouse run
    y1 = g.yaw
    app.pad.inject(time.perf_counter(), 0, 0, 0, 0, 0, 32767, 0)
    yield from sleep(0.1)
    expect(g.yaw == y1, "the controller's stick does not turn the view in a mouse run")
    app.pad.inject(time.perf_counter(), 0, 0, 0, 0, 0, 0, 0)
    # a click is judged where the crosshair was AT the click: movement before it counts, movement after does not
    yield from wait(lambda: g.scn.cur is not None, what="a target")
    c = g.scn.cur
    eye, pos = g.eye, c.pos
    want_y = math.degrees(math.atan2(pos[0] - eye[0], -(pos[2] - eye[2])))
    want_p = math.degrees(math.asin((pos[1] - eye[1]) / math.dist(pos, eye)))
    dx = round(((want_y - g.yaw + 180) % 360 - 180) / g.m_deg)
    dy = round(-(want_p - g.pitch) / g.m_deg)
    kills = g.scn.kills
    t = time.perf_counter()
    app.mouse.inject(t, mouse.MOTION, dx, dy)                   # onto the target...
    app.mouse.inject(t, mouse.BUTTON, "M1", True)               # ...click...
    app.mouse.inject(t, mouse.MOTION, round(40 / g.m_deg), 0)   # ...and 40 degrees away, all inside one frame
    app.mouse.inject(t, mouse.BUTTON, "M1", False)
    yield from frames(2)
    expect(g.scn.kills == kills + 1, "flick, click, flick away inside one frame: the shot lands where the click was")
    yield from wait(lambda: g.scn.cur is not None, what="the next target")
    c = g.scn.cur
    pos = c.pos
    want_y = math.degrees(math.atan2(pos[0] - eye[0], -(pos[2] - eye[2])))
    want_p = math.degrees(math.asin((pos[1] - eye[1]) / math.dist(pos, eye)))
    dx = round(((want_y - g.yaw + 180) % 360 - 180) / g.m_deg)
    dy = round(-(want_p - g.pitch) / g.m_deg)
    kills, shots_ = g.scn.kills, g.scn.shots
    t = time.perf_counter()
    app.mouse.inject(t, mouse.BUTTON, "M1", True)               # click first...
    app.mouse.inject(t, mouse.MOTION, dx, dy)                   # ...and only then arrive on the target
    app.mouse.inject(t, mouse.BUTTON, "M1", False)
    yield from frames(2)
    expect(g.scn.kills == kills and g.scn.shots == shots_ + 1 and g.err_deg(pos) < 0.1,
           "click first and arrive after, inside one frame: a miss, though the frame ends dead on target")
    app.bot = Bot(app)
    rec = yield from finish_run()
    expect(rec["controller"] == kbm and rec["input"]["kind"] == "kbm" and rec["input"]["aim_assist"] is False
           and rec["binds"]["shoot"] == "M1" and rec["device"]["backend"] == "bot"
           and rec["fingerprint"] == config.fingerprint(cfg) and rec["settings"]["input"] == "kbm",
           f"logged as a mouse run: {rec['input']}")
    for cls in (scenarios.Reaction, scenarios.Grid, scenarios.SmoothTrack, scenarios.AdsSnap):
        app.start(cls)
        rec = yield from finish_run()
        key_, _, unit, _, _ = next(m_ for m_ in cls.metrics if m_[0] == cls.primary)
        expect(rec["stats"].get(key_) not in (None, 0), f"{cls.name}: the bot plays it on a mouse ({key_} = {rec['stats'].get(key_)}{unit})")

    print("Call of Duty track on a mouse: no aim assist, whatever the scenario says")
    assisted = [s for s in tracks.COD.scenarios if s.aa]
    app.bot = None
    bad = []
    for cls in tracks.COD.scenarios:
        app.start(cls)
        g = app.game
        if not (isinstance(g.kit.assist, codkit.NoAssist) and g.kit.kbm):
            bad.append(cls.name)
        g.abort()
        yield
    expect(len(assisted) >= 15 and not bad, f"all 27 scenarios - the {len(assisted)} that give a controller aim assist included - "
           f"are built with no aim assist object at all on a mouse {bad}")
    try:
        codkit.NoAssist().compute(None, [], 0.004, 0, 0, 0, 0)
        expect(False, "asking the stand-in for aim assist must fail")
    except RuntimeError:
        expect(True, "anything that asked for aim assist on a mouse would fail loudly, not quietly help")

    def off_target(g, d):
        """Degrees between the crosshair and the dummy's chest."""
        return g.err_deg(d.chest())

    def aim_at(g, d):
        dx, dy, dz = d.x - g.kit.x, d.chest_y() - codkit.EYE_H, d.z - g.kit.z
        g.yaw, g.pitch = math.degrees(math.atan2(dx, -dz)), math.degrees(math.atan2(dy, math.hypot(dx, dz)))

    def face_to_face(profile, cls=codscn.StickyTrack):
        """Start a run and put the crosshair on its strafing dummy; return the game and the dummy."""
        app.set_profile(profile)
        app.start(cls)
        g = app.game
        while g.state != "run":
            yield
        while not g.scn.dummies:
            yield
        d = g.scn.dummies[0]
        yield from sleep(0.3)           # let it get moving
        g.ads_t = 1.0
        aim_at(g, d)
        return g, d

    def watch(g, d, secs, pad_row):
        """For `secs`, feed the controller `pad_row` (buttons, lt, rt, lx, ly, rx, ry) and touch nothing
        else. Returns (how far the view turned by itself, how far off the dummy the crosshair got)."""
        turned, worst = 0.0, 0.0
        end = time.perf_counter() + secs
        while time.perf_counter() < end:
            app.pad.inject(time.perf_counter(), *pad_row)
            y0 = g.yaw
            yield
            turned += abs((g.yaw - y0 + 180) % 360 - 180)
            worst = max(worst, off_target(g, d))
        return turned, worst

    ads_bit = padmod.BIT[cfg["binds"]["ads"]]
    # the same situation on a controller first, to show this test can see assist when it is there
    cfg["cod"]["aa_style"] = "classic"
    g, d = yield from face_to_face(pad_name)
    yield from watch(g, d, 0.05, (ads_bit, 0, 0, 32767, 0, 0, 0))               # sights up and strafing, then re-centre on it
    aim_at(g, d)
    turned, worst = yield from watch(g, d, 0.25, (ads_bit, 0, 0, 32767, 0, 0, 0))   # right stick untouched throughout
    expect(turned > 0.3 and g.kit.assist.info["strength"] > 0.5,
           f"on a controller the view follows the dummy by itself ({turned:.2f} deg in a quarter second, strength {g.kit.assist.info['strength']:.2f})")
    app.pad.inject(time.perf_counter(), 0, 0, 0, 0, 0, 0, 0)
    g.abort()
    yield
    g, d = yield from face_to_face(kbm)
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "M2", True)        # sights up
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "D", True)         # strafing
    yield from sleep(0.05)
    aim_at(g, d)
    y_start = g.yaw
    turned, worst = yield from watch(g, d, 0.25, (ads_bit, 0, 0, 32767, 0, 32767, 0))   # ...and a thumb on both of the pad's sticks
    expect(turned == 0.0 and g.yaw == y_start, "on a mouse it does not move a hair - strafing, sights up, dummy in the crosshair, pad in hand")
    expect(worst < 6.0, f"(the dummy was in the crosshair's reach all along: never more than {worst:.1f} deg off)")
    expect(g.kit.strafe > 0.9 and abs(g.kit.vx) > 1.0, f"(and it really was strafing: {g.kit.vx:.2f} m/s on the D key)")
    expect(g.kit.assist.info["strength"] == 0.0 and g.kit.assist.info["slow"] == 0.0, "strength 0, slowdown 0")
    app.pad.inject(time.perf_counter(), 0, 0, 0, 0, 0, 0, 0)
    # no slowdown either: counts turn the view by the same angle on the dummy as off it
    aim_at(g, d)
    yield from frames(2)
    k = g.kit.m_deg * g.kit.m_ads
    y0 = g.yaw
    app.mouse.inject(time.perf_counter(), mouse.MOTION, 50, 0)
    yield from frames(2)
    on_target = g.yaw - y0
    g.yaw += 60.0
    y0 = g.yaw
    app.mouse.inject(time.perf_counter(), mouse.MOTION, 50, 0)
    yield from frames(2)
    expect(near(on_target, 50 * k, 1e-9) and near(g.yaw - y0, 50 * k, 1e-9), f"50 counts = {50 * k:.4f} deg through the sights, on the dummy or off it")
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, "M2", False)
    yield from frames(3)
    y0 = g.yaw
    app.mouse.inject(time.perf_counter(), mouse.MOTION, 50, 0)
    yield from frames(2)
    expect(near(g.yaw - y0, 50 * 0.0066 * cfg["cod"]["mouse_sens"], 1e-9), "from the hip: 0.0066 degrees x sensitivity per count")
    # keys: eight directions at walking pace
    for held_keys, want in ((("W",), (0.0, -1.0)), (("W", "D"), (0.7071, -0.7071)), (("S", "A"), (-0.7071, 0.7071)),
                            (("A", "D"), None)):
        app.mouse.resync()
        yield from frames(2)
        g.yaw, g.kit.x, g.kit.z, g.kit.vx, g.kit.vz = 0.0, 0.0, 0.0, 0.0, 0.0
        for kname in ("W", "A", "S", "D"):
            app.mouse.inject(time.perf_counter(), mouse.BUTTON, kname, kname in held_keys)
        yield from sleep(0.3)
        v = math.hypot(g.kit.vx, g.kit.vz)
        if want is None:
            expect(v < 0.05, "A+D: opposite keys cancel")
        else:
            expect(v > 1.0 and near(g.kit.vx / v, want[0], 0.02) and near(g.kit.vz / v, want[1], 0.02)
                   and near(v, codkit.TUNE["walk"], 0.3), f"{'+'.join(held_keys)}: {v:.2f} m/s in the right direction")
    app.mouse.resync()
    g.abort()
    yield
    cfg["cod"]["aa_style"] = "bo7"
    # mouse movement must do nothing in a controller run
    g, d = yield from face_to_face(pad_name, codscn.StrafeSpray)
    y0 = g.yaw
    app.mouse.inject(time.perf_counter(), mouse.MOTION, 4000, 0)
    yield from frames(3)
    expect(abs(g.yaw - y0) < 1e-6, f"and in a controller run the mouse does not turn the view (4000 counts moved it {abs(g.yaw - y0):.1e} deg)")
    g.abort()
    yield
    app.set_profile(kbm)
    app.bot = Bot(app)
    yield from frames(2)
    ts = screens.TrackScreen(app, tracks.COD)
    app.push(ts)
    ts.sel = next(i for i, (k_, a_) in enumerate(ts.rows) if a_ is codscn.StickyTrack)
    ts.top = max(0, ts.sel - 8)
    yield from shot("mouse_cod_track")
    app.pop()
    for cls in (codscn.StickyTrack, codscn.MirrorStrafe, codscn.SnapShot, codscn.RecoilHold, codscn.StrafeDuel):
        app.start(cls)
        g = app.game
        if cls is codscn.StickyTrack:
            yield from wait(lambda: g.state == "run", what="countdown")
            yield from sleep(1.2)
            yield from shot("mouse_cod_run")
        rec = yield from finish_run()
        st = rec["stats"]
        expect((st.get("kills", 0) > 0 or cls is codscn.StrafeDuel) and not any(k_.startswith("aa_") for k_ in st) and "coach" not in st
               and rec["input"]["aim_assist"] is False and rec["fingerprint"] == config.fingerprint_cod(cfg),
               f"{cls.name}: {st.get('kills')} kills at {st.get('accuracy_pct')}%, no aim-assist numbers in the log")
    cod_fp = config.fingerprint_cod(cfg)
    app.set_profile(pad_name)
    expect(config.fingerprint_cod(cfg) != cod_fp, "its runs sit in their own fingerprint, apart from every controller's")
    app.set_profile(kbm)


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

# ---------------------------------------------------------- the real input thread
print("the real raw-input thread")
m = mouse.Mouse()
t0 = time.perf_counter()
while m.thread.ok is None and time.perf_counter() - t0 < 2.0:
    time.sleep(0.01)
expect(m.thread.ok is True, f"Raw Input registered on its own thread {m.thread.error}")
m.update(time.perf_counter(), listening=False)
time.sleep(0.2)
m.update(time.perf_counter(), listening=False)
expect(m.events == [] and not m.held, "while the window is not focused nothing is read")
if m.thread.ok and not os.environ.get("AIMTUNER_NO_NUDGE"):
    class MI(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("data", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                    ("extra", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("mi", MI)]

    send = ctypes.windll.user32.SendInput
    send.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    m.update(time.perf_counter())           # start listening
    time.sleep(0.05)
    m.update(time.perf_counter())
    N, sent, rows = 24, [], []
    for i in range(N):                      # 12 ms apart: Windows hands injected reports over late when they come closer
        inp = INPUT(0, MI(1 if i % 2 == 0 else -1, 0, 0, 0x0001, 0, 0))
        sent.append(time.perf_counter())
        send(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.012)
        m.update(time.perf_counter())
        rows += [e for e in m.events if e[1] == mouse.MOTION]
    time.sleep(0.05)
    m.update(time.perf_counter())
    rows += [e for e in m.events if e[1] == mouse.MOTION]
    # the user may be moving the real mouse too; ours are the +1 / -1 steps in the right order near the right time
    mine, lag = [], []
    for i, ts in enumerate(sent):
        want = 1 if i % 2 == 0 else -1
        hit = next((e for e in rows if e[2] == want and e[3] == 0 and 0.0 <= e[0] - ts < 0.010 and e not in mine), None)
        if hit:
            mine.append(hit)
            lag.append((hit[0] - ts) * 1000)
    lag.sort()
    expect(len(mine) >= N - 3, f"{len(mine)} of {N} one-count nudges arrived as one count each (raw, unaccelerated)")
    if lag:
        expect(lag[len(lag) // 2] < 1.0, f"sent -> stamped by the input thread: median {lag[len(lag) // 2]:.2f} ms, worst {lag[-1]:.2f} ms")
m.stop()
time.sleep(0.15)
expect(not m.thread.is_alive(), "the thread stops when asked")

print(f"{checks[0]} checks, {len(errors)} failed")
sys.exit(1 if errors else 0)
