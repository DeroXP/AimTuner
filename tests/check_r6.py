"""The Siege track: its maths against the game's published numbers, that there is no aim assist in it
for anyone, and its controls and guns in the real loop on a controller and on a mouse.

    python tests/check_r6.py [folder for screenshots]
"""
import copy, inspect, math, os, random, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import codkit, config, mouse, r6kit, r6scn, sens, tracks
from aimtuner import pad as padmod

errors = []


def expect(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


def near(a, b, tol):
    return abs(a - b) <= tol


cfg = copy.deepcopy(config.DEFAULTS)
r6 = cfg["r6"]

print("no aim assist, by construction")
expect(len(tracks.R6.scenarios) == 24 and len(tracks.R6.groups) == 8 and all(len(g) == 3 for _, g in tracks.R6.groups),
       "24 scenarios: three for each of eight skills")
expect(all(s.aa is False and s.kit_cls is r6kit.SiegeKit and s.track == "r6" for s in tracks.R6.scenarios),
       "every one of them is built on the Siege kit with aim assist off")
try:
    type("Cheat", (r6scn.SiegeScenario,), {"aa": True, "id": "x"})
    expect(False, "a Siege scenario that declares aim assist must be refused")
except TypeError:
    expect(True, "a Siege scenario that declared aim assist is refused when Python reads its definition")
src = inspect.getsource(r6kit)
code = "\n".join(ln.split("#")[0] for ln in src.split('"""')[2::2] for ln in ln.splitlines())    # code, not the notes
expect("AimAssist" not in code and ".compute(" not in code and "slow" not in code.lower(),
       "the Siege kit's code never builds an aim assist, never asks one for anything, and has no slowdown in it")
expect(not any(k.startswith("r6.aa") or "assist" in k for k in config.comparable_r6(cfg)) and "aa_style" not in r6,
       "there is no aim-assist setting for the Siege track to have")

print("mouse sensitivity, against the game's numbers")
r6.update(mouse_sens=10, mouse_unit=0.02)
cfg["mouse"]["dpi"] = 800
expect(near(sens.own_mouse_cm(cfg, "r6"), 19.95, 0.01), f"sens 10, unit 0.02, 800 DPI: {sens.own_mouse_cm(cfg, 'r6'):.2f} cm per turn (19.95)")
r6.update(mouse_sens=33, mouse_unit=0.002)
expect(near(sens.own_mouse_cm(cfg, "r6"), 60.5, 0.1), f"sens 33, unit 0.002, 800 DPI: {sens.own_mouse_cm(cfg, 'r6'):.1f} cm per turn (60.5)")
r6.update(mouse_sens=10, mouse_unit=0.02, mouse_ads=50, fov=90, weapon="r4c", sight="1x")
ms = sens.mouse(cfg, "r6")
z1 = r6kit.zoom_of(r6)
expect(near(1.0 / z1, 0.8541, 0.0005) and near(19.95 / ms.ads(0.0, z1), 23.36, 0.02),
       f"1x at FOV 90, ADS 50: x{1 / z1:.4f}, {19.95 / ms.ads(0.0, z1):.2f} cm through the sight (Ubisoft's formula: 0.8541, 23.36)")
r6["sight"] = "2.5x"
z25 = r6kit.zoom_of(r6)
expect(near(1.0 / z25, 0.3424, 0.0005) and near(19.95 / ms.ads(0.0, z25), 58.27, 0.05),
       f"2.5x at FOV 90: x{1 / z25:.4f}, {19.95 / ms.ads(0.0, z25):.2f} cm (0.3424, 58.27)")
r6["mouse_ads"] = 100
expect(near(sens.mouse(cfg, "r6").ads(0.0, z25) * z25, 2.0, 1e-9), "the ADS slider is linear: 100 is twice 50")
r6.update(mouse_ads=50, sight="1x")
for v, h in ((60, 91.49), (75, 107.51), (90, 121.28)):
    r6["fov"] = v
    expect(near(r6kit.hfov(r6), h, 0.01), f"FOV {v} (vertical, as in the game) is {r6kit.hfov(r6):.2f} across a 16:9 screen ({h})")
r6["fov"] = 84
r6["weapon"] = "ump45"
r6["sight"] = "2.5x"
expect(r6kit.sight_of(r6) == "1x", "a defender's gun takes 1x only, whatever the setting says")
r6.update(weapon="c8", grip="vertical")
expect(r6kit.grip_of(r6) == "none", "the C8-SFW takes no grip, whatever the setting says")
for cm in (50.0, 41.7, 30.0, 18.0, 12.0, 90.0):
    r6kit.set_mouse_cm360(cfg, cm)
    got = sens.own_mouse_cm(cfg, "r6")
    if abs(got / cm - 1.0) > 0.011:
        expect(False, f"{cm} cm per turn comes out as {got:.2f} on Siege's scale")
expect(True, "any distance per turn can be set on Siege's scale to within 1% (lowering the multiplier unit where the slider is too coarse)")

print("damage, health and guns")
r6.update(weapon="r4c", sight="1x", barrel="none", grip="none", speed=2)
G = types.SimpleNamespace(cfg=cfg, over={}, kbm=False, fov=0.0, zoom_max=1.0)
kit = r6kit.SiegeKit(G)
expect(kit.damage("body", 10.0) == 39 and kit.damage("body", 25.0) == 39 and kit.damage("body", 35.0) == round(39 * 0.6)
       and kit.damage("body", 60.0) == round(39 * 0.6), "R4-C: 39 to the body out to 25 m, 60% of that from 35 m")
expect(kit.damage("limb", 10.0) == round(39 * 0.75), "a limb takes three quarters")
kit._pen = r6kit.TUNE["soft_pen"]
expect(kit.damage("body", 10.0) == round(39 * 0.7), "through a soft wall: 70%")
tgt = r6kit.SiegeDummy(0.0, -10.0, speed=1)
kit._tgt = tgt
expect(tgt.hp == 125 and kit.damage("head", 50.0) == 125, "a headshot is whatever the target has left - at any range, through a wall too")
kit._tgt, kit._pen = None, 1.0
expect([r6kit.SiegeDummy(0, 0, speed=s).hp for s in (3, 2, 1)] == [100, 110, 125], "health 100 / 110 / 125 for 3- / 2- / 1-speed")
ak = r6kit.GUNS["ak12"]
expect([math.ceil(h / ak["dmg"]) for h in (100, 110, 125)] == [3, 3, 4], "AK-12 (40): 3 / 3 / 4 body shots")
want = {"r4c": (39, 860, 25), "f2": (37, 980, 25), "ak12": (40, 850, 30), "c8": (40, 837, 30), "556xi": (47, 690, 30),
        "mp7": (32, 900, 30), "vector": (23, 1200, 25), "smg11": (32, 1270, 16), "ump45": (42, 600, 25)}
expect(set(r6kit.GUN_ORDER) == set(want) and all((r6kit.GUNS[k]["dmg"], r6kit.GUNS[k]["rpm"], r6kit.GUNS[k]["mag"]) == v
                                                 for k, v in want.items()),
       "nine guns, with the game's damage, fire rate and magazine as of Y11S3.1")
expect(kit.mag == 26 and near(kit.ads_time, 0.52 / 1.05, 1e-9), "a full magazine plus one in the chamber; a rifle's sights take 0.52 s (5% faster on a 1x)")
r6["weapon"] = "smg11"
expect(near(r6kit.SiegeKit(G).ads_time, 0.38 / 1.05, 1e-9), "a machine pistol's take 0.38 s")
r6["weapon"] = "r4c"

print("recoil: each gun's character, and what the attachments do")


def spray(name, barrel="none", grip="none", seed=0, n=None):
    g, rng = r6kit.GUNS[name], random.Random(seed)
    steps = [r6kit.recoil_step(g, i, rng, barrel, grip) for i in range(n or g["mag"])]
    return sum(s for s, _ in steps), sum(u for _, u in steps), steps


climb = {k: spray(k)[1] for k in r6kit.GUN_ORDER}
expect(min(climb, key=climb.get) == "ump45" and max(climb, key=climb.get) == "c8",
       f"an unheld magazine: the UMP45 climbs least ({climb['ump45']:.1f} deg) and the C8-SFW most ({climb['c8']:.1f})")
drift = lambda k: sum(spray(k, seed=s)[0] for s in range(200)) / 200.0
expect(drift("r4c") > 0.5 and drift("ak12") > 0.5 and drift("556xi") < -0.5 and abs(drift("c8")) < 0.3,
       f"R4-C and AK-12 pull right ({drift('r4c'):+.1f}, {drift('ak12'):+.1f}), the 556xi left ({drift('556xi'):+.1f}), the C8 hardly at all")
s8 = spray("r4c")[2]
expect(s8[12][1] > s8[4][1] * 1.3, "the R4-C's climb is harder late in the burst than early (long-burst recoil from the 8th bullet)")
late = [abs(spray("ak12", seed=s)[2][20][0] - 0.05) for s in range(300)]
early = [abs(spray("ak12", seed=s)[2][4][0] - 0.05) for s in range(300)]
expect(sum(late) > 2.5 * sum(early), "the AK-12 goes sideways at random from the 10th bullet")
_, up0, st0 = spray("r4c")
_, up_fh, _ = spray("r4c", barrel="flash_hider")
_, up_vg, _ = spray("r4c", grip="vertical")
expect(near(up_fh / up0, 0.80, 1e-9) and near(up_vg / up0, 0.80, 1e-9), "flash hider: 20% less climb; vertical grip: 20% less climb")
_, _, st_mb = spray("r4c", barrel="muzzle_brake")
expect(near(st_mb[0][1], st0[0][1] * 0.5, 1e-9) and st_mb[1:] == st0[1:], "muzzle brake: half the first bullet's kick, nothing else")
side0 = [s for s, _ in spray("ak12")[2]]
side_c = [s for s, _ in spray("ak12", barrel="compensator")[2]]
expect(all(near(b, a * 0.65, 1e-12) for a, b in zip(side0, side_c)), "compensator: 35% less sideways kick")

print("walls")
w = r6kit.SiegeWorld(((-10, 0, -20), (10, 6, 5)), [((-1, 0, -6), (1, 3, -5.6))], [((-5, 0, -9), (5, 3, -8.75))])
eye, fwd = (0.0, 1.6, 0.0), (0.0, 0.0, -1.0)
expect(near(w.ray(eye, fwd), 5.6, 1e-9) and w.ray((3.0, 1.6, 0.0), fwd) > 100.0, "hard cover stops a bullet; a soft wall does not")
expect(w.soft_before((3.0, 1.6, 0.0), fwd, 12.0) == 1 and w.soft_before((3.0, 1.6, 0.0), fwd, 8.0) == 0, "...but the bullet knows it went through one")
expect(not w.clear((3.0, 1.6, 0.0), (3.0, 1.6, -12.0)) and w.clear((3.0, 1.6, 0.0), (3.0, 1.6, -8.0)), "and nobody sees through either kind")
expect(w.soft_from == 1 and len(w.boxes) == 2, "the renderer is told which boxes are the soft ones")


def seeable(cls, spots):
    """Which of a drill's fixed spots can NOT be seen from anywhere the player is allowed to stand?"""
    world = cls.build(types.SimpleNamespace(room=cls.room, COVER=getattr(cls, "COVER", ())))
    x0, x1, z0, z1 = cls.move_area
    stand = [(x0 + 0.35 + (x1 - x0 - 0.7) * i / 24.0, z0 + 0.35 + (z1 - z0 - 0.7) * j / 6.0) for i in range(25) for j in range(7)]
    hidden = []
    for spot in spots:
        d = r6kit.SiegeDummy(spot[0], spot[1])
        d.crouch = spot[2] if len(spot) > 2 else 0.0
        if not any(world.clear((px, r6kit.TUNE["eye"], pz), d.head()) for px, pz in stand):
            hidden.append(spot)
    return hidden


for cls in (r6scn.RoomClear, r6scn.SliceThePie):
    expect(not seeable(cls, cls.SPOTS), f"{cls.name}: every place a dummy can stand is in view from somewhere you can stand "
           f"{seeable(cls, cls.SPOTS) or ''}")

# ---------------------------------------------------------------- the real loop
from aimtuner.app import App
from aimtuner.bot import Bot

scratch = tempfile.mkdtemp(prefix="aimtuner-r6-")
shots = sys.argv[1] if len(sys.argv) > 1 else None
opts = types.SimpleNamespace(windowed=False, offscreen=(1920, 1080) if shots else (1280, 720), bench=False, selftest=True,
                             only=None, repeats=1, shots=None, duration=4.0, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
app.pad.use_virtual("bot")
app.mouse.use_virtual()


def frames(n=3):
    for _ in range(n):
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
        yield


def shot(name):
    if shots:
        yield from frames(3)
        app.pending_shot = os.path.join(shots, name + ".png")
        yield from frames(2)


def pad_in(btn=0, lt=0, rt=0, lx=0, ly=0, rx=0, ry=0):
    app.pad.inject(time.perf_counter(), btn, lt, rt, lx, ly, rx, ry)


def key_in(name, down):
    app.mouse.inject(time.perf_counter(), mouse.BUTTON, name, down)


def script():
    cfg = app.cfg
    c = cfg["r6"]
    c.update(weapon="r4c", sight="1x", barrel="none", grip="none", speed=2)
    pad_name = cfg["active_profile"]
    kbm = next(p["name"] for p in cfg["profiles"] if p.get("kind") == "kbm")
    B = padmod.BIT
    shoot_bit, ads_bit = B[cfg["binds"]["shoot"]], B[cfg["binds"]["ads"]]
    yield from frames(5)

    print("every scenario, on both devices, is built without aim assist")
    bad = []
    for prof in (pad_name, kbm):
        app.set_profile(prof)
        for cls in tracks.R6.scenarios:
            app.start(cls)
            g = app.game
            if not (type(g.kit) is r6kit.SiegeKit and isinstance(g.kit.assist, codkit.NoAssist) and g.scn.aa is False):
                bad.append((prof, cls.name))
            g.abort()
            yield
    expect(not bad, f"48 starts (24 scenarios x controller, mouse): each has the stand-in that refuses, never an aim assist {bad}")

    def start(cls, prof):
        app.set_profile(prof)
        opts.duration = 40.0            # long enough that no round ends in the middle of a check
        app.start(cls)
        g = app.game
        while g.state != "run":
            yield
        return g

    def face(cls, prof):
        g = yield from start(cls, prof)
        while not g.scn.dummies:
            yield
        d = g.scn.dummies[0]
        yield from sleep(0.3)
        return g, d

    def aim_at(g, pos):
        e = g.eye
        dx, dy, dz = pos[0] - e[0], pos[1] - e[1], pos[2] - e[2]
        g.yaw, g.pitch = math.degrees(math.atan2(dx, -dz)), math.degrees(math.atan2(dy, math.hypot(dx, dz)))

    print("a controller gets nothing: no rotation, no slowdown")
    g, d = yield from face(r6scn.CrouchSpam, pad_name)      # a strafing dummy, 7 to 12 m
    pad_in(ads_bit, lx=32767)
    yield from sleep(0.45)                                  # sights up, strafing
    aim_at(g, d.chest())
    y0, p0 = g.yaw, g.pitch
    worst = 0.0
    end = time.perf_counter() + 0.3
    while time.perf_counter() < end:
        pad_in(ads_bit, lx=32767)                           # left stick over, right stick untouched
        yield
        worst = max(worst, g.err_deg(d.chest()))
    expect(g.yaw == y0 and g.pitch == p0, "sights up, strafing, a moving dummy in the crosshair, right stick untouched: the view does not move a hair")
    expect(worst > 0.5 and g.kit.strafe > 0.9, f"(the dummy did slide off the crosshair - {worst:.1f} deg - and nothing followed it)")
    expect(g.kit.assist.info["strength"] == 0.0 and g.kit.assist.info["slow"] == 0.0, "strength 0, slowdown 0")
    full = g.kit.rate_h * g.kit.ads_scale
    rates = []
    for off in (0.0, 70.0):                                 # the same full-stick turn on the dummy and far from it
        aim_at(g, d.chest())
        g.yaw += off
        pad_in(ads_bit, rx=32767)
        yield from frames(3)
        rates.append(g.yaw_rate)
    expect(near(rates[0], full, 1e-6) and near(rates[1], full, 1e-6),
           f"full stick turns at {full:.1f} deg/s through the sights with the crosshair on the dummy and with it nowhere near")
    pad_in()
    g.abort()
    yield

    print("...and neither does a mouse")
    g, d = yield from face(r6scn.CrouchSpam, kbm)
    key_in("M2", True)
    key_in("D", True)
    yield from sleep(0.45)
    aim_at(g, d.chest())
    y0 = g.yaw
    end = time.perf_counter() + 0.3
    while time.perf_counter() < end:
        pad_in(ads_bit, lx=32767, rx=32767)                 # a thumb on both of a pad's sticks changes nothing either
        yield
    expect(g.yaw == y0, "sights up, strafing on D, dummy in the crosshair, a controller in the other hand: the view does not move")
    pad_in()
    k = g.kit.m_deg * g.kit.m_ads
    moved = []
    for off in (0.0, 70.0):
        aim_at(g, d.chest())
        g.yaw += off
        yield from frames(2)
        y0 = g.yaw
        app.mouse.inject(time.perf_counter(), mouse.MOTION, 60, 0)
        yield from frames(2)
        moved.append(g.yaw - y0)
    expect(near(moved[0], 60 * k, 1e-9) and near(moved[1], 60 * k, 1e-9), f"60 counts = {60 * k:.4f} deg through the sights, on the dummy or off it")
    expect(near(g.kit.m_deg, c["mouse_sens"] * c["mouse_unit"] * r6kit.MOUSE_K, 1e-12)
           and near(g.kit.m_ads, c["mouse_ads"] * 0.02 / r6kit.zoom_of(c), 1e-12), "the counts are Siege's: sensitivity x unit x 0.2865, and its ADS formula")
    key_in("M2", False)
    key_in("D", False)

    print("leaning, crouching and reloading on mouse and keyboard")
    yield from frames(3)
    g.yaw = 0.0
    e0 = g.eye
    key_in("E", True)
    yield from sleep(0.45)
    expect(near(g.kit.lean_t, 1.0, 1e-9) and near(g.eye[0] - g.kit.x, r6kit.TUNE["lean_shift"], 1e-6)
           and g.roll == r6kit.TUNE["lean_roll"] and g.eye[1] < e0[1],
           f"hold E: the eye moves {g.eye[0] - g.kit.x:.2f} m to the right and the view tilts")
    yield from shot("r6_lean")
    key_in("E", False)
    key_in("Q", True)
    yield from sleep(0.7)
    expect(near(g.kit.lean_t, -1.0, 1e-9) and g.eye[0] < g.kit.x, "Q leans the other way")
    key_in("Q", False)
    yield from sleep(0.45)
    expect(g.kit.lean_t == 0.0 and g.roll == 0.0, "let go and you stand straight again")
    key_in("C", True)
    key_in("C", False)
    yield from sleep(0.4)
    expect(near(g.eye[1], r6kit.TUNE["eye_crouch"], 1e-6), f"C crouches: the eye drops to {g.eye[1]:.2f} m")
    key_in("C", True)
    key_in("C", False)
    yield from sleep(0.4)
    expect(near(g.eye[1], r6kit.TUNE["eye"], 1e-6), "C again stands")
    g.kit.mag = 3
    key_in("R", True)
    key_in("R", False)
    yield from frames(3)
    expect(g.kit.reload_t > 0.0, "R reloads")
    key_in("M1", True)
    yield from sleep(0.3)
    expect(g.kit.mag == 3 and g.scn.shots == 0, "nothing fires while the magazine is out")
    key_in("M1", False)
    yield from wait(lambda: g.kit.reload_t <= 0.0, 6.0, "the reload")
    expect(g.kit.mag == 26, "reloaded with a round still in the chamber: a magazine and one")
    g.abort()
    yield

    print("the same on a controller: stick clicks, B, X")
    g = yield from start(r6scn.HeadHeight, pad_name)
    g.yaw = 0.0
    pad_in(B["RS"])
    yield from frames(3)
    pad_in()
    yield from sleep(0.3)
    expect(g.kit.lean_want == 0, "R3 without the sights up does nothing: on a controller you lean while aiming")
    pad_in(ads_bit)
    yield from frames(3)
    pad_in(ads_bit | B["RS"])
    yield from frames(3)
    pad_in(ads_bit)
    yield from sleep(0.45)
    expect(g.kit.lean_want == 1 and near(g.kit.lean_t, 1.0, 1e-9), "sights up, click R3: lean right")
    pad_in(ads_bit | B["RS"])
    yield from frames(3)
    pad_in(ads_bit)
    yield from sleep(0.45)
    expect(g.kit.lean_want == 0 and g.kit.lean_t == 0.0, "click it again: upright")
    pad_in(ads_bit | B["LS"])
    yield from frames(3)
    pad_in(ads_bit)
    yield from sleep(0.45)
    expect(g.kit.lean_t == -1.0, "L3 leans left")
    pad_in()
    yield from sleep(0.45)
    expect(g.kit.lean_want == 0 and g.kit.lean_t == 0.0, "letting go of Aim stands you up")
    pad_in(B["B"])
    yield from frames(3)
    pad_in()
    yield from sleep(0.4)
    expect(g.kit.crouch_t == 1.0, "B crouches")
    pad_in(B["B"])
    yield from frames(3)
    pad_in()
    g.kit.mag = 5
    pad_in(B["X"])
    yield from frames(3)
    pad_in()
    expect(g.kit.reload_t > 0.0 and g.kit.reloads == 1, "X reloads")
    g.abort()
    yield

    print("one bullet to the head; three or four to the body")
    for speed, body in ((3, 3), (2, 3), (1, 4)):
        r6scn.HeadHeight.speeds = (speed,)
        g, d = yield from face(r6scn.HeadHeight, kbm)
        key_in("M2", True)
        yield from sleep(0.7)
        hp = d.hp
        n = 0
        while d.alive and n < 8:
            aim_at(g, d.chest())
            yield from frames(2)
            key_in("M1", True)
            yield from frames(2)
            key_in("M1", False)
            yield from sleep(0.12)
            n += 1
        expect(n == body and hp == r6kit.TUNE["hp"][speed], f"a {speed}-speed ({hp} health): {n} R4-C bullets to the body")
        yield from wait(lambda: g.scn.dummies, 5.0, "the next dummy")
        d = g.scn.dummies[0]
        yield from sleep(0.3)
        aim_at(g, d.head())
        yield from frames(2)
        key_in("M1", True)
        yield from frames(2)
        key_in("M1", False)
        yield from frames(3)
        expect(not d.alive and g.scn.hs_kills == 1 and g.scn.one_taps == 1, "...and one to the head")
        key_in("M2", False)
        g.abort()
        yield
    r6scn.HeadHeight.speeds = (1, 2, 3)

    print("soft walls: seen through a ping, shot through at 70%")
    r6scn.Wallbang.speeds = (1,)
    g, d = yield from face(r6scn.Wallbang, kbm)
    expect(not g.scn.sees(d) and d.s["vis"], "the dummy is behind a wall you cannot see through, and marked")
    yield from shot("r6_wallbang")
    key_in("M2", True)
    yield from sleep(0.7)
    aim_at(g, d.chest())
    yield from frames(2)
    key_in("M1", True)
    yield from frames(2)
    key_in("M1", False)
    yield from frames(3)
    expect(d.hp == 125 - round(39 * 0.7), f"a body shot through the boards does {125 - d.hp} (39 x 0.7)")
    yield from sleep(0.4)
    aim_at(g, d.head())
    yield from frames(2)
    key_in("M1", True)
    yield from frames(2)
    key_in("M1", False)
    yield from frames(3)
    expect(not d.alive, "a headshot through the boards still kills")
    key_in("M2", False)
    g.abort()
    yield
    r6scn.Wallbang.speeds = (1, 2, 3)
    g = yield from start(r6scn.DoorHold, kbm)
    while not g.scn.dummies:
        yield
    d = g.scn.dummies[0]
    d.mover = None
    d.x, d.z = 4.0, g.scn.WALL_Z - 1.5                      # parked behind the hard wall
    yield from frames(3)
    key_in("M2", True)
    yield from sleep(0.7)
    aim_at(g, d.chest())
    yield from frames(2)
    key_in("M1", True)
    yield from sleep(0.3)
    key_in("M1", False)
    yield from frames(3)
    expect(d.hp == d.hp_max and g.scn.shots >= 3 and g.scn.hits == 0, f"a hard wall stops every bullet ({g.scn.shots} fired, none through)")
    key_in("M2", False)
    g.abort()
    yield

    print("recoil in the loop: it climbs by the pattern, and settles when you stop")
    c["weapon"] = "556xi"
    g = yield from start(r6scn.MagDump, kbm)
    key_in("M2", True)
    yield from sleep(0.7)
    g.yaw, g.pitch = 120.0, 0.0                             # at nothing in particular
    yield from frames(2)
    p0 = g.pitch
    key_in("M1", True)
    yield from wait(lambda: g.kit.mag == 0 or g.kit.reload_t > 0.0, 8.0, "the magazine to empty")
    top = g.pitch
    key_in("M1", False)
    gun = r6kit.GUNS["556xi"]
    lo = sum(r6kit.recoil_step(gun, n, random.Random(1))[1] for n in range(31))
    expect(near(top - p0, lo, 0.02), f"31 unheld bullets from the 556xi climb {top - p0:.2f} deg (the pattern says {lo:.2f})")
    yield from sleep(0.9)
    expect(abs(g.pitch - p0) < 0.05 and g.kit.rec_y < 0.01, f"let go and the sight settles back to where it was aimed (off by {g.pitch - p0:+.3f} deg)")
    yield from wait(lambda: g.kit.reload_t <= 0.0, 6.0, "the reload")
    expect(g.kit.mag == 30 and g.kit.reloads == 1, "an empty magazine reloads itself - to 30, with nothing left in the chamber")
    # pull down exactly as much as it climbs, and nothing is left to settle: it must not dive afterwards
    g.pitch = base = 0.0
    yield from frames(2)
    key_in("M1", True)
    fired = g.scn.shots
    kdeg = g.kit.m_deg * g.kit.m_ads
    end = time.perf_counter() + 0.6
    skip = 0
    while time.perf_counter() < end:
        yield
        if skip:                                            # (a pull lands two frames after it is sent)
            skip -= 1
        elif g.pitch - base > 0.0:
            app.mouse.inject(time.perf_counter(), mouse.MOTION, 0, round((g.pitch - base) / kdeg))   # toward you = down
            skip = 2
    key_in("M1", False)
    yield from frames(4)
    held = g.pitch
    yield from sleep(0.8)
    expect(g.scn.shots - fired >= 5 and abs(held - base) < 0.5 and abs(g.pitch - held) < 0.1,
           f"a spray you held down does not dive when you let go (moved {g.pitch - held:+.3f} deg after {g.scn.shots - fired} bullets)")
    key_in("M2", False)
    g.abort()
    yield
    c["weapon"] = "r4c"

    print("whole runs, played by the bot, on a controller and on a mouse")
    app.bot = Bot(app)
    opts.duration = 4.0
    app.set_profile(pad_name)
    fp0 = config.fingerprints(cfg)
    for prof in (pad_name, kbm):
        app.set_profile(prof)
        for cls in (r6scn.OneTap, r6scn.LeanPeek, r6scn.BothShoulders, r6scn.Wallbang, r6scn.CrouchSpam, r6scn.DoorHold):
            app.start(cls)
            g = app.game
            yield from wait(lambda: app.game is None, 30.0, cls.name)
            yield from sleep(0.3)
            rec = app.log.runs[-1]
            st = rec["stats"]
            leaned = st.get("lean_pct") or 0.0
            ok = (rec["track"] == "r6" and rec["scenario"] == cls.id and rec["fingerprint"] == config.fingerprint_r6(cfg)
                  and st.get("kills", 0) > 0 and not any(k.startswith("aa_") or k == "coach" for k in st)
                  and st["weapon"] == "R4-C" and rec["settings"]["r6.weapon"] == "r4c"
                  and rec["input"]["kind"] == ("kbm" if prof == kbm else "pad"))
            if cls in (r6scn.LeanPeek, r6scn.BothShoulders):
                ok = ok and leaned > 50.0
            expect(ok, f"{prof}: {cls.name} - {st.get('kills')} kills, no aim-assist numbers, logged as a Siege run"
                   + (f", {leaned:.0f}% of the fight leaning" if cls in (r6scn.LeanPeek, r6scn.BothShoulders) else ""))
            if len(app.screens) > 1:
                app.pop()
    print("the sensitivity finder, on Siege's drills")
    from aimtuner import sensfinder
    app.set_profile(pad_name)
    S = sensfinder.SensFinder(app)
    expect(S.track == "cod" and S.phases is sensfinder.PHASES, "by default it plays the Call of Duty track's drills, as it always did")
    app.push(S)
    app.keys.add("e")                                   # RB: the other game's drills
    yield from frames(2)
    S = app.screens[-1]
    expect(S is not None and S.track == "r6" and S.dialect == "r6" and S.phases["hip"]["key"] == "r6.sens_h"
           and S.phases["hip"]["drill"] is r6scn.SensTurn and S.phases["ads"]["drill"] is r6scn.SensStrafe,
           "RB switches to Siege's drills, searching Siege's own 1 - 100 stick sensitivity")
    if shots:
        yield from shot("r6_finder")
    opts.duration = 2.0
    per_unit = config.r6_rates(cfg)[0]
    app.keys.add("enter")
    yield from wait(lambda: app.game is not None, 20.0, "a Siege finder block")
    g = app.game
    v = S.queue[0]
    expect(type(g.scn) is r6scn.SensTurn and type(g.kit) is r6kit.SiegeKit and isinstance(g.kit.assist, codkit.NoAssist),
           "a block is a Siege drill, with no aim assist")
    expect(near(g.kit.rate_h, v * per_unit, 1e-9) and v == int(v) and 1 <= v <= 100,
           f"played at a planned slider value ({v:.0f}), which is {g.kit.rate_h:.0f} deg/s by the spin test")

    def finder_wait(pred, what):
        end = time.perf_counter() + 240.0
        while not pred():
            if time.perf_counter() > end:
                expect(False, "timeout waiting for " + what)
                return
            if S.state == "rest" and app.game is None:
                app.keys.add("enter")
            yield

    yield from finder_wait(lambda: S.state == "result", "the Siege hip round")
    expect(len(S.blocks["hip"]) == 10 and all(b["context"].get("drills") == "r6" for b in S.blocks["hip"])
           and sum(b["kills"] or 0 for b in S.blocks["hip"]) > 0, "ten blocks, logged as Siege drills, and the bot scored")
    S.fits["hip"] = dict(S.fits["hip"] or {}, best=63.4)
    cod_sens = cfg["cod"]["sens_h"]
    app.keys.add("x")
    yield from frames(2)
    expect(c["sens_h"] == 63 and c["sens_v"] == 63 and cfg["cod"]["sens_h"] == cod_sens and S.phase == "ads",
           f"applied to Siege's own setting ({c['sens_h']}); the Call of Duty track's is untouched")
    ads_speed = sens.own_pad(cfg, "r6")
    expect(near(ads_speed.rate_h * ads_speed.ads, 120.0, 2.5), f"with the ADS slider moved to keep the ADS speed ({c['ads']}: "
           f"{ads_speed.rate_h * ads_speed.ads:.0f} deg/s, it was 120)")
    c.update(sens_h=50, sens_v=50, ads=50)
    P = sensfinder.SensFinder(app)
    expect(not P.blocks["hip"] and not P.blocks["ads"], "a Call of Duty finder does not count the Siege session")
    cfg["sens"]["pad"] = "own"
    R = sensfinder.SensFinder(app, "r6")
    expect(len(R.blocks["hip"]) == 10 and R.rounds["hip"] == 1, "a Siege finder opened later does: its ten blocks, one round")
    app.pop()
    cfg["sens"]["pad"] = "r6"
    expect(sensfinder.SensFinder(app).track == "r6", "and when every track follows Siege, Siege's drills are the default")
    cfg["sens"]["pad"] = "own"
    opts.duration = 4.0

    app.bot = None
    app.set_profile(pad_name)
    c["weapon"] = "ak12"
    fp = config.fingerprints(cfg)
    expect(fp["r6"] != fp0["r6"] and fp["core"] == fp0["core"] and fp["cod"] == fp0["cod"], "another gun is another Siege comparison, and only that")
    c["weapon"] = "r4c"
    cfg["cod"]["sens_h"] += 0.1
    cfg["aim"]["sens_h"] += 10
    expect(config.fingerprint_r6(cfg) == fp0["r6"], "the other tracks' settings do not touch the Siege comparison")
    expect(len({fp0["core"], fp0["cod"], fp0["r6"]}) == 3, "the three tracks never share a fingerprint")


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
print("siege:", "all passed" if not errors else f"FAILED {errors}")
sys.exit(1 if errors else 0)
