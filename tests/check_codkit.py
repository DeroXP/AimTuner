"""Numeric checks of the Call of Duty kit: calibration, curves, geometry, damage, aim assist rules."""
import os
import math, random, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aimtuner import codkit, config
from aimtuner.codkit import TUNE, AimAssist, Dummy, World
from aimtuner.gfx import Camera

fails = []


def check(name, got, want, tol=1e-6):
    ok = got is not None and abs(got - want) <= tol
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got if not isinstance(got, float) else round(got, 4)} (want {round(want, 4)})")
    if not ok:
        fails.append(name)


def truth(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name} {detail}")
    if not cond:
        fails.append(name)


print("calibration from the player's spin test (10 spins / 5.59 s hip, 5 spins / 11 s ADS at 1.95 and x0.95)")
cfg = config.load(r"Z:\nope\missing.json")
per_unit, ratio = config.cod_rates(cfg)
check("hip deg/s at 1.95", per_unit * 1.95, 3600 / 5.59, 0.01)
check("ads deg/s at 1.95 x0.95", per_unit * 1.95 * 0.95 * ratio, 1800 / 11.0, 0.01)
check("deg/s per 1.00", per_unit, 330.26, 0.05)
truth("cod fingerprint differs from core", config.fingerprint_cod(cfg) != config.fingerprint(cfg))
fp = config.fingerprint_cod(cfg)
cfg["cod"]["aa_style"] = "classic"
truth("aim assist style is part of the fingerprint", config.fingerprint_cod(cfg) != fp)
cfg["cod"]["aa_style"] = "bo7"
cfg["cod"]["cal"]["hip_seconds"] = 5.70
truth("recalibrating changes the fingerprint", config.fingerprint_cod(cfg) != fp)
cfg["cod"]["cal"]["hip_seconds"] = 5.59

print("response curves against the community measurement (share of full speed at 25 / 50 / 75 % stick)")
MEASURED = {"linear": (0.055, 0.249, 0.567), "standard": (0.032, 0.179, 0.517), "dynamic": (0.054, 0.211, 0.467)}
for kind, want in MEASURED.items():
    prev, mono = -1.0, True
    for i in range(1001):
        v = codkit.curve(i / 1000, kind)
        mono &= v >= prev - 1e-12
        prev = v
    truth(f"{kind} is monotonic, 0 at rest, 1 at full", mono and codkit.curve(0, kind) == 0.0 and codkit.curve(1, kind) == 1.0)
    for u, w in zip((0.25, 0.5, 0.75), want):
        check(f"{kind} at {int(u * 100)}% stick", codkit.curve(u, kind), w, 0.012)
truth("dynamic is ahead of standard at low stick and behind it past ~60%",
      codkit.curve(0.3, "dynamic") > codkit.curve(0.3, "standard") and codkit.curve(0.75, "dynamic") < codkit.curve(0.75, "standard"))
hip, ads = per_unit * 1.95, per_unit * 1.95 * 0.95 * ratio
print("   the player's turn speed on Dynamic, deg/s (hip | ADS):",
      {f"{int(u * 100)}%": f"{hip * codkit.curve(u, 'dynamic'):.0f} | {ads * codkit.curve(u, 'dynamic'):.0f}" for u in (0.1, 0.25, 0.5, 0.75, 1.0)})
check("right stick reaches full at 99%", codkit.shape(0.99, 0.0, 0.0, 0.99, "dynamic")[2], 1.0)
check("left stick reaches full at 75%", codkit.shape(0.0, 0.75, 0.0, 0.75, None)[2], 1.0)
check("left stick half way to its max", codkit.shape(0.375, 0.0, 0.0, 0.75, None)[2], 0.5)
x, y, f = codkit.shape(0.3, -0.4, 0.0, 0.99, "dynamic")
check("direction preserved", math.atan2(y, x), math.atan2(-0.4, 0.3), 1e-9)

print("geometry and damage")
w = World(((-30, 0, -70), (30, 10, 10)), boxes=[((-1, 0, -6), (1, 3, -5))], smokes=[(8, 1.5, -10, 4)], smoke_density=0.15)
check("bullet stops at the box", w.ray((0, 1.6, 0), (0, 0, -1)), 5.0, 1e-9)
truth("line of sight blocked through the box", not w.clear((0, 1.6, 0), (0, 1.6, -12)))
truth("line of sight clear beside it", w.clear((3, 1.6, 0), (3, 1.6, -12)))
truth("no smoke on a clear line", w.tau((0, 1.6, 0), (3, 1.6, -12)) == 0.0)
truth("smoke on a line through the cloud", w.tau((8, 1.6, 0), (8, 1.6, -20)) > 0.25, f"(tau {w.tau((8, 1.6, 0), (8, 1.6, -20)):.2f})")
px, pz = w.push_out(0.2, -5.5, 0.35)
truth("player pushed out of the box", not (-1.35 < px < 1.35 and -6.35 < pz < -4.65), f"-> ({px:.2f}, {pz:.2f})")
px, pz = w.push_out(40.0, 0.0, 0.35)
check("player kept inside the room", px, 29.65, 1e-9)
d = Dummy(0.0, -15.0)
eye = (0.0, 1.62, 0.0)


def aim_at(pt):
    v = (pt[0] - eye[0], pt[1] - eye[1], pt[2] - eye[2])
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


truth("ray at the head is a headshot", d.hit(eye, aim_at(d.head()))[1] == "head")
truth("ray at the chest is a body shot", d.hit(eye, aim_at(d.chest()))[1] == "body")
truth("ray at the shins is a body shot", d.hit(eye, aim_at((0.0, 0.3, -15.0)))[1] == "body")
truth("ray over the head misses", d.hit(eye, aim_at((0.0, 1.95, -15.0))) is None)
truth("ray beside the body misses", d.hit(eye, aim_at((0.45, 1.2, -15.0))) is None)
d.crouch = 1.0
truth("crouched: old head height now misses", d.hit(eye, aim_at((0.0, 1.62, -15.0))) is None)
truth("crouched: head is 0.46 m lower", abs(d.head()[1] - 1.16) < 1e-9)
d.crouch = 0.0
dmg = lambda zone, dist: codkit.CodKit.damage(None, zone, dist)
check("body shots to kill", math.ceil(TUNE["hp"] / dmg("body", 10)), 5)
check("headshots to kill up close", math.ceil(TUNE["hp"] / dmg("head", 10)), 3)
check("headshots to kill at range", math.ceil(TUNE["hp"] / dmg("head", 40)), 4)
truth("2 head + 2 body kills up close", 2 * dmg("head", 10) + 2 * dmg("body", 10) >= TUNE["hp"])
truth("1 head + 3 body does not", dmg("head", 10) + 3 * dmg("body", 10) < TUNE["hp"])
check("body-shot TTK ms", 4 * 60000 / TUNE["rpm"], 400.0)

print("recoil: the shape of the player's photographed spray (up, a kick right, a lean left, back across to the right)")
holes = []
fake = types.SimpleNamespace(
    g=types.SimpleNamespace(yaw=0.0, pitch=0.0, ads_t=1.0, eye=eye, rng=random.Random(4), t=0.0),
    scn=types.SimpleNamespace(dummies=[], on_bullet=lambda t, best: holes.append((fake.g.yaw, fake.g.pitch))),
    world=w, spray=0, last_shot=-9.0, kick=0.0)
rand, TUNE["recoil_rand"] = TUNE["recoil_rand"], 0.0
for _ in range(40):
    codkit.CodKit._bullet(fake, 0.0)
TUNE["recoil_rand"] = rand
side, climb = [h[0] for h in holes], [h[1] for h in holes]
check("a 30-round magazine is 29 steps", len(TUNE["recoil"]), 29)
truth("the first bullet goes where you aimed", holes[0] == (0.0, 0.0))
truth("every bullet lands above the one before", all(b > a for a, b in zip(climb, climb[1:])))
check("an unheld 5-bullet spray climbs this many degrees (the weight play-testing settled on)", climb[4], 1.34, 0.05)
truth("a whole magazine climbs 8-9.5 degrees", 8.0 <= climb[29] <= 9.5, f"({climb[29]:.2f})")
truth("the first six bullets go nearly straight up", max(abs(s) for s in side[:6]) < 0.15)
kick = max(range(6, 12), key=lambda i: side[i])
lean = min(range(30), key=lambda i: side[i])
truth("then a kick to the right", side[kick] > 0.12, f"(bullet {kick + 1}: {side[kick]:+.2f})")
truth("then a long lean to the left, furthest around bullets 16-21", 15 <= lean <= 20 and side[lean] < -0.7,
      f"(bullet {lean + 1}: {side[lean]:+.2f})")
truth("and back across to finish right of where it started", side[29] > 0.25 and side[29] - side[lean] > 1.0, f"({side[29]:+.2f})")
truth("sideways travel stays small next to the climb, as in the photo", max(side[:30]) - min(side[:30]) < 0.2 * climb[29])
check("past the magazine it sways about where it ended instead of drifting off", side[39] - side[31], 0.0, 0.25)
truth("...and keeps climbing", climb[39] > climb[30] + 2.0)

print("aim assist rules")
DT = 1 / 240
FULL = TUNE["rot_max"]
LONE = FULL * TUNE["rot_min_share"]


def scene(style, dist, speed=3.6, boxes=(), smokes=(), off_deg=0.0, ads=1.0):
    cam = Camera()
    cam.set(0.0, 0.0, 100.0, 16 / 9, 1.0 + (TUNE["ads_zoom"] - 1.0) * ads)
    g = types.SimpleNamespace(eye=eye, yaw=off_deg, pitch=math.degrees(math.atan2(1.18 - 1.62, dist)), ads_t=ads, cam=cam)
    k = types.SimpleNamespace(g=g, world=World(((-40, 0, -110), (40, 10, 10)), boxes, smokes, 0.15))
    dm = Dummy(0.0, -dist)
    aa = AimAssist(style)
    aa.compute(k, [dm], DT, 0.0, 0.0, 0.0, 0.0)       # first frame establishes where the target was
    dm.x += speed * DT                                # it steps sideways
    omega = math.degrees(math.atan2(dm.x, dist)) / DT
    return aa, k, dm, omega


def follow(style, dist, want, strafe, rmag, **kw):
    aa, k, dm, om = scene(style, dist, **kw)
    mul, ry, rp = aa.compute(k, [dm], DT, want * om, 0.0, strafe, rmag)
    return mul, ry / om, aa


for style, lone in (("bo7", LONE), ("classic", FULL), ("mw4", FULL * TUNE["mw4_min_share"])):
    mul, f, _ = follow(style, 15.0, 0.0, 0.0, 0.0)
    check(f"{style}: no stick input, no rotation", f, 0.0)
    check(f"{style}: slowdown applies inside the bubble (ADS)", mul, 1 - TUNE["slow_max"], 1e-6)
    check(f"{style}: left stick only 20% over is not input", follow(style, 15.0, 0.0, 0.20, 0.0)[1], 0.0)
    check(f"{style}: strafing only follows this share of the target", follow(style, 15.0, 0.0, 0.8, 0.0)[1], lone, 1e-3)
    check(f"{style}: right stick tracking it", follow(style, 15.0, 1.0, 0.0, 0.5)[1], FULL, 1e-3)
    check(f"{style}: right stick going the wrong way", follow(style, 15.0, -1.0, 0.0, 0.5)[1], lone, 1e-3)
    check(f"{style}: hip-fire slowdown is weaker", follow(style, 15.0, 1.0, 0.0, 0.5, ads=0.0)[0],
          1 - TUNE["slow_max"] * TUNE["hip_slow"], 1e-6)
check("bo7: strafing alone is 75% of full, as the patch notes say", LONE / FULL, 0.75)
check("bo7: tracking at 20% of the needed rate gives part strength", follow("bo7", 15.0, 0.2, 0.0, 0.3)[1],
      FULL * (0.75 + 0.25 * 0.2 / TUNE["track_need"]), 2e-3)
for dist, want_bo7, want_cl in ((1.0, 0.09, 0.167), (3.0, 0.27, 0.5), (7.0, 0.65, 1.0), (10.0, 0.95, 1.0), (20.0, 1.0, 1.0),
                                (50.0, 0.3, 0.3), (62.5, 0.1625, 0.144), (100.0, 0.0, 0.0)):
    check(f"bo7 strength at {dist:g} m (share of full)", follow("bo7", dist, 1.0, 0.0, 0.5)[1] / FULL, want_bo7, 0.02)
    check(f"classic strength at {dist:g} m (share of full)", follow("classic", dist, 1.0, 0.0, 0.5)[1] / FULL, want_cl, 0.02)
truth("bo7 is weaker than classic inside 10 m", follow("bo7", 7.0, 1.0, 0.0, 0.5)[1] < follow("classic", 7.0, 1.0, 0.0, 0.5)[1])
tanx = math.tan(math.radians(50.0)) / TUNE["ads_zoom"]
half_deg = math.degrees(math.atan(TUNE["bubble"][0] * tanx))
print(f"   bubble half-width while aiming: {half_deg:.1f} deg ({math.tan(math.radians(half_deg)) * 15:.2f} m at 15 m)")
for style in ("bo7", "classic"):
    check(f"{style}: 60% of the way to the bubble edge is still full",
          follow(style, 15.0, 1.0, 0.0, 0.5, off_deg=-math.degrees(math.atan(0.6 * TUNE["bubble"][0] * tanx)))[1] / FULL, 1.0, 0.02)
    check(f"{style}: half strength in the middle of the soft edge",
          follow(style, 15.0, 1.0, 0.0, 0.5, off_deg=-math.degrees(math.atan(0.875 * TUNE["bubble"][0] * tanx)))[1] / FULL, 0.5, 0.06)
check("outside the bubble: nothing", follow("classic", 15.0, 1.0, 0.0, 0.5, off_deg=-half_deg * 1.2)[1], 0.0)
check("outside the bubble: no slowdown either", follow("classic", 15.0, 1.0, 0.0, 0.5, off_deg=-half_deg * 1.2)[0], 1.0)
mul, f, aa = follow("bo7", 15.0, 1.0, 0.8, 0.5, boxes=[((-3, 0, -8), (3, 3, -7))])
truth("behind a wall: nothing, and it says why", (mul, f) == (1.0, 0.0) and aa.info["reason"] == "no line of sight")
mul, f, aa = follow("bo7", 15.0, 1.0, 0.8, 0.5, smokes=[(0, 1.5, -9, 4)])
truth("through smoke: nothing, and it says why", (mul, f) == (1.0, 0.0) and aa.info["reason"] == "smoke")
_, _, aa = follow("bo7", 15.0, 0.0, 0.8, 0.0)
truth("bo7 strafing only: meter explains what is missing", aa.info["reason"] == "follow it with the right stick" and abs(aa.info["strength"] - 0.75) < 0.01)
truth("weaker than the game's measured strengths (~0.6 rotation, ~0.5 slowdown)", TUNE["rot_max"] < 0.6 and TUNE["slow_max"] < 0.5)

print("the test the game itself was measured with: strafe past a still target, sights up, right stick untouched")


def strafe_by(style, dist=10.0, speed=TUNE["ads_walk"]):
    """Walk sideways past a still dummy; return how many degrees aim assist alone turned the view."""
    cam = Camera()
    cam.set(0.0, 0.0, 100.0, 16 / 9, TUNE["ads_zoom"])
    x0 = -dist * math.tan(math.radians(half_deg * 1.6))            # start with it well outside the bubble
    g = types.SimpleNamespace(eye=(x0, 1.62, 0.0), yaw=0.0, pitch=math.degrees(math.atan2(1.18 - 1.62, dist)), ads_t=1.0, cam=cam)
    k = types.SimpleNamespace(g=g, world=World(((-60, 0, -110), (60, 10, 10))))
    dm, aa, x, turned = Dummy(0.0, -dist), AimAssist(style), x0, 0.0
    while x < -x0 + 6.0:
        x += speed * DT
        g.eye = (x, 1.62, 0.0)
        _, ry, _ = aa.compute(k, [dm], DT, 0.0, 0.0, 1.0, 0.0)
        g.yaw += ry * DT
        turned += abs(ry) * DT
    return turned


sweep = math.degrees(2 * math.atan(TUNE["bubble"][0] * tanx))
cl, b7 = strafe_by("classic"), strafe_by("bo7")
print(f"   at 10 m the view turns {cl:.1f} deg (MW3 / BO6 style) and {b7:.1f} deg (BO7 style); measured in the games: 19 and 11")
check("BO7 / BO6 ratio as measured (11 / 19)", b7 / cl, 11 / 19, 0.04)
truth("weaker than the real thing, as asked, but not 'super weak' (at least 40% of it)", 0.4 * 19 <= cl < 19 and 0.4 * 11 <= b7 < 11,
      f"({cl / 19:.0%} and {b7 / 11:.0%} of the game on this test; {TUNE['rot_max'] / 0.6:.0%} of its moment-to-moment pull)")
# the same test for slowdown: how much longer a full turn takes because of one target at 10 m
slow_extra = sweep * (0.875) * (1 / (1 - TUNE["slow_max"]) - 1) / 360.0
print(f"   a 360 at 10 m past one target takes {100 * slow_extra:.1f}% longer (measured in the games: 2.5-3%)")
truth("slowdown is weaker than the real thing too, but not by more than half", 0.5 * 0.025 <= slow_extra < 0.03)

print("Modern Warfare 4 rules (from its beta): same strength, different behaviour")
check("mw4: same peak rotation as the other styles", follow("mw4", 15.0, 1.0, 0.0, 0.5)[1], follow("classic", 15.0, 1.0, 0.0, 0.5)[1], 1e-6)
check("mw4: same slowdown as the other styles", follow("mw4", 15.0, 1.0, 0.0, 0.5)[0], follow("bo7", 15.0, 1.0, 0.0, 0.5)[0], 1e-9)
_, _, aa = follow("mw4", 15.0, 0.0, 0.8, 0.0)
truth("mw4 strafing only: the meter reads WEAK and says to use the right stick",
      aa.info["reason"] == "follow it with the right stick" and 0.02 < aa.info["strength"] < 0.3, f"(strength {aa.info['strength']:.2f})")
check("mw4: tracking at 20% of the needed rate gives part strength", follow("mw4", 15.0, 0.2, 0.0, 0.3)[1],
      FULL * (0.15 + 0.85 * 0.2 / TUNE["track_need"]), 3e-3)
for dist, want in ((1.0, 1.0), (3.0, 1.0), (7.0, 1.0), (20.0, 1.0), (50.0, 0.3), (100.0, 0.0)):
    check(f"mw4 strength at {dist:g} m (share of full)", follow("mw4", dist, 1.0, 0.0, 0.5)[1] / FULL, want, 0.02)
check("mw4: slowdown is whole at 1.5 m", follow("mw4", 1.5, 1.0, 0.0, 0.5)[0], 1 - TUNE["slow_max"], 1e-6)
near = math.hypot(1.5, 1.62 - 1.18) / 3.0          # the Black Ops fade-in is over the first 3 m to the chest
check("bo7: slowdown is only half there at 1.5 m", follow("bo7", 1.5, 1.0, 0.0, 0.5)[0], 1 - TUNE["slow_max"] * near, 1e-3)


def follow_up(style, dist=15.0, climb=2.5):
    """A target rising (a jump) while the right stick follows it up: share of its rise the assist supplies."""
    aa, k, dm, _ = scene(style, dist, speed=0.0)
    aa.compute(k, [dm], DT, 0.0, 0.0, 0.0, 0.0)
    y0 = math.degrees(math.atan2(dm.chest_y() - 1.62, dist))
    dm.y += climb * DT
    wp = (math.degrees(math.atan2(dm.chest_y() - 1.62, dist)) - y0) / DT
    _, _, rp = aa.compute(k, [dm], DT, 0.0, wp, 0.0, 0.5)
    return rp / wp


check("mw4: follows a jump as readily as a side-step", follow_up("mw4"), FULL, 2e-3)
check("bo7: follows a jump less readily than a side-step", follow_up("bo7"), FULL * TUNE["rot_pitch"], 2e-3)
m4 = strafe_by("mw4")
print(f"   strafe past a still target at 10 m, right stick untouched: {m4:.1f} deg under MW4 rules "
      f"(BO7 {b7:.1f}, MW3 / BO6 {cl:.1f}); footage of the beta's second weekend shows 'almost none'")
truth("mw4: left stick alone turns the view only a trace", 0.2 < m4 < 0.25 * b7)
cfg["cod"]["aa_style"] = "mw4"
fp_mw4 = config.fingerprint_cod(cfg)
cfg["cod"]["aa_style"] = "classic"
truth("mw4 runs are kept apart from the other styles' runs", fp_mw4 not in (fp, config.fingerprint_cod(cfg)))
cfg["cod"]["aa_style"] = "bo7"
truth("an unknown style falls back to BO7 rules", AimAssist("nonsense").style == "bo7")

print("Coach rules (AimTuner's own): the same strength, paid out for three habits")
B, WT, WS = TUNE["coach_base"], TUNE["coach_track"], TUNE["coach_strafe"]
GRACE = TUNE["coach_grace"]


def drive(style, plan, secs, dist=15.0, off_deg=0.0, planted=False):
    """Play `secs` of a target walking sideways while the view stays `off_deg` from it.
    plan(t) -> (target speed m/s, stick as a multiple of the turn rate that keeps up with it at 3.6 m/s,
    left stick sideways, right stick deflection). Returns one row per frame:
    (t, share of peak rotation, stick slowdown, was following judged, the assist object, following 0-1)."""
    cam = Camera()
    cam.set(0.0, 0.0, 100.0, 16 / 9, TUNE["ads_zoom"])
    bearing = lambda: math.degrees(math.atan2(dm.x, dist))
    g = types.SimpleNamespace(eye=eye, yaw=off_deg, pitch=math.degrees(math.atan2(1.18 - 1.62, dist)), ads_t=1.0, cam=cam)
    k = types.SimpleNamespace(g=g, world=World(((-40, 0, -110), (40, 10, 10))))
    if planted:
        k.scn = types.SimpleNamespace(can_move=False)
    dm, aa, rows, t = Dummy(0.0, -dist), AimAssist(style), [], 0.0
    pace = math.degrees(3.6 / dist)                    # deg/s the view must turn to stay on a 3.6 m/s walker
    aa.compute(k, [dm], DT, 0.0, 0.0, 0.0, 0.0)        # first frame establishes where the target was
    while t < secs - 1e-9:
        speed, want, strafe, rmag = plan(t)
        dm.x += speed * DT
        g.yaw = bearing() + off_deg
        mul, _, _ = aa.compute(k, [dm], DT, want * pace, 0.0, strafe, rmag)
        t += DT
        rows.append((t, aa.info["strength"], mul, not aa.info["unscored"], aa, (aa.info["parts"] or {}).get("track")))
    return rows


def settled(style, want, strafe, rmag, **kw):
    """The same hands held for long enough that nothing is still being given the benefit of the doubt."""
    return drive(style, lambda t: (3.6, want, strafe, rmag), 0.8, **kw)[-1]


check("coach: the three shares add up to the whole", B + WT + WS, 1.0, 1e-9)
check("coach: no stick input, no rotation", settled("coach", 0.0, 0.0, 0.0)[1], 0.0)
check("coach: right stick following and strafing gives all of it", settled("coach", 1.0, 0.8, 0.5)[1], 1.0, 1e-3)
check("coach: ...and no more than any other style's all", settled("coach", 1.0, 0.8, 0.5)[1], settled("classic", 1.0, 0.8, 0.5)[1], 1e-3)
check("coach: following without strafing", settled("coach", 1.0, 0.0, 0.5)[1], B + WT, 1e-3)
check("coach: strafing without following", settled("coach", 0.0, 0.8, 0.0)[1], B + WS, 1e-3)
check("coach: a half-hearted strafe earns part of the strafing share", settled("coach", 1.0, 0.425, 0.5)[1], B + WT + WS * 0.5, 2e-3)
check("coach: following at 35% of the needed rate is not enough for all of that share (BO7 would call it full)",
      settled("coach", 0.35, 0.8, 0.5)[1], B + WT * 0.35 / TUNE["coach_track_need"] + WS, 0.02)
check("coach: same slowdown as the other styles", settled("coach", 1.0, 0.8, 0.5)[2], settled("bo7", 1.0, 0.8, 0.5)[2], 1e-9)
edge = lambda frac: -math.degrees(math.atan(frac * TUNE["bubble"][0] * tanx))
check("coach: in the middle third of the bubble, centring is whole", settled("coach", 1.0, 0.8, 0.5, off_deg=edge(0.25))[1], 1.0, 0.02)
check("coach: two thirds of the way out, about half is left", settled("coach", 1.0, 0.8, 0.5, off_deg=edge(0.675))[1], 0.5, 0.06)
truth("coach: where another style is still at full, it has started to pay less",
      settled("coach", 1.0, 0.8, 0.5, off_deg=edge(0.6))[1] < 0.75 * settled("classic", 1.0, 0.8, 0.5, off_deg=edge(0.6))[1])
aa = settled("coach", 1.0, 0.0, 0.5)[4]
truth("coach reports each habit: following yes, strafing no, centred yes",
      aa.info["parts"] is not None and aa.info["parts"]["track"] > 0.99 and aa.info["parts"]["strafe"] == 0.0
      and aa.info["parts"]["centre"] > 0.99 and aa.info["reason"] == "keep strafing", f"{aa.info['parts']} / {aa.info['reason']!r}")
aa = settled("coach", 0.0, 0.8, 0.0)[4]
truth("coach: strafing only -> it asks for the right stick", aa.info["reason"] == "follow it with the right stick")
aa = settled("coach", 1.0, 0.8, 0.5, off_deg=edge(0.8))[4]
truth("coach: riding the edge of the bubble -> it asks you to centre", aa.info["reason"] == "stay centred on it", repr(aa.info["reason"]))
aa = settled("bo7", 1.0, 0.8, 0.5)[4]
truth("the other styles report no habits", aa.info["parts"] is None)
# a drill that plants your feet cannot ask for strafing: following carries that share
row = settled("coach", 1.0, 0.0, 0.5, planted=True)
check("coach, feet planted: following alone gives all of it", row[1], 1.0, 1e-3)
truth("coach, feet planted: strafing is not asked for", row[4].info["parts"]["strafe"] is None)

print("Coach judges the habit of following, not reaction time")
at = lambda rows, t: min(rows, key=lambda r: abs(r[0] - t))
rows = drive("coach", lambda t: (3.6, 0.0, 0.8, 0.0), 0.6)
truth("a target just picked up: following is not judged for the first moments",
      not at(rows, GRACE * 0.5)[3] and at(rows, GRACE + 0.05)[3])
check("...and the assist gives the benefit of the doubt meanwhile", at(rows, GRACE * 0.5)[1], 1.0, 1e-3)
check("...then pays for what the hands are actually doing", at(rows, GRACE + 0.1)[1], B + WS, 1e-3)
# following well, the target reverses, and the stick is still going the old way for a while
late = lambda react: (lambda t: (3.6 if t < 0.6 else -3.6, 1.0 if t < 0.6 + react else -1.0, 0.8, 0.5))
rows = drive("coach", late(0.15), 1.2)
judged = [r for r in rows if 0.6 + 2 * DT < r[0] < 0.6 + GRACE - DT]
truth("the target reverses: nothing is judged while a thumb could not have reacted", not any(r[3] for r in judged))
check("...and a player who was following keeps the whole assist through it", min(r[1] for r in judged), 1.0, 1e-3)
check("a 150 ms reaction costs nothing at all", min(r[1] for r in rows if r[0] > GRACE + 0.05), 1.0, 1e-3)
rows = drive("coach", late(0.45), 1.6)
check("a 450 ms reaction does: once the grace is over the stick is going the wrong way", at(rows, 0.6 + GRACE + 0.1)[1], B + WS, 1e-3)
check("...until it has caught up", rows[-1][1], 1.0, 0.02)
rows = drive("coach", lambda t: (3.6 if t < 0.6 else -3.6, 0.0, 0.8, 0.0), 1.2)
check("a reversal earns nothing for a player who was not following anyway", max(r[1] for r in rows if r[0] > 0.6), B + WS, 1e-3)
rows = drive("coach", lambda t: (0.0 if t < 0.5 else 3.6, 0.0 if t < 0.5 + 0.15 else 1.0, 0.8, 0.5), 1.2)
truth("a still target sets off: the same grace", not at(rows, 0.5 + GRACE * 0.5)[3] and at(rows, 0.5 + GRACE + 0.05)[3])
check("...so starting to follow 150 ms later costs nothing", min(r[1] for r in rows if r[0] > 0.5), 1.0, 1e-3)
rows = drive("coach", lambda t: (0.0, 0.0, 0.0, 0.5), 0.6)
truth("nothing to follow (neither of you moving): following is not judged", not any(r[3] for r in rows))
# quick taps that keep up on average: full stick-rate for 50 ms, nothing for 50 ms
taps = drive("coach", lambda t: (3.6, 1.3 if int(t / 0.05) % 2 == 0 else 0.0, 0.8, 0.5), 1.6)
tap_q = [r[5] for r in taps if r[3]]
truth("tapping the stick in time with a target counts as following it", min(tap_q[60:]) > 0.9, f"(lowest {min(tap_q[60:]):.2f})")
co = strafe_by("coach")
print(f"   strafe past a still target at 10 m, right stick untouched: {co:.1f} deg under Coach rules "
      f"(BO7 {b7:.1f}, MW3 / BO6 {cl:.1f}, MW4 {m4:.1f})")
truth("coach: a lone strafe earns less than under BO7 rules, more than under MW4 rules", m4 < co < b7)
cfg["cod"]["aa_style"] = "coach"
truth("coach runs are kept apart too", config.fingerprint_cod(cfg) not in (fp, fp_mw4))
cfg["cod"]["aa_style"] = "bo7"

print("\ncod kit checks:", "all passed" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
