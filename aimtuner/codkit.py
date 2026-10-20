"""Call of Duty-style aiming, for the scenarios on the Call of Duty track.

How much of this is the real game and how much is a model, so nobody mistakes one
for the other:

MEASURED in the player's own game (the spin test in Settings):
  * hip-fire turn speed at full stick, and the ADS-to-hip speed ratio.

TAKEN FROM THE GAME: the 0.10-4.00 sensitivity scale, the ADS multiplier, stick
  deadzones, FOV; the rifle's movement speeds, ADS time and damage ranges; that
  Black Ops 7 gives 75% of rotational aim assist without right-stick tracking,
  full with it, and takes longer than BO6 to reach full strength with distance.

TAKEN FROM OTHER PEOPLE'S MEASUREMENTS of the game (read off charts, older titles):
  * the shape of the three response curves (see `curve`);
  * aim assist's rules - a bubble of fixed size on screen, what counts as stick
    input, where it fades in and out with distance.

TUNED BY FEEL, because nothing is published: how strong slowdown and rotation
  are (then deliberately set a step BELOW the real game, as asked, so the mechanic
  is something you use rather than lean on), recoil, hip-fire spread, how enemies
  shoot back.

KNOWN ONLY FROM A BETA: the Modern Warfare 4 rule set. The game was unreleased when it
  was written; it follows what creators showed and the studio said about the August
  2026 beta, and will want revisiting once the game is out (see TUNE).

NOT MODELLED: the game ramps turn speed up over the first moments at full stick;
  its size and duration are not published, so here full stick is full speed at once.

Tuning lives in TUNE. Changing anything there changes how hard the scenarios
are, so bump config.COD_MODEL when you do.
"""
from __future__ import annotations

import math

from . import config
from .theme import ACCENT, BAD, DIM, FAINT, GOOD, WARN, WHITE, alpha

# Where each number comes from is marked: [game] = patch notes or published weapon
# stats, [measured] = a community measurement of the game, [player] = asked for by
# the player this was built for, [tuned] = chosen by feel because nothing is published.
TUNE = {
    # -- weapon: one rifle, modelled loosely on the M15 MOD 0
    "rpm": 600.0,              # [player] slower than the real 769, for a 400 ms body-shot kill instead of 312
    "hp": 100,                 # [game] multiplayer health
    "body": 20,                # [player] 5 body shots (the real rifle does 21 inside 30.5 m: also 5)
    "head_near": 34,           # [player] 3 headshots up close...
    "head_far": 25,            # [player] ...4 at range
    "head_range": 30.0,        # [game] the M15's first damage drop is at 30.5 m
    "ads_time": 0.25,          # [game] M15 ADS time, s
    "ads_zoom": 1.5,           # [measured] M15 iron sights, off the player's own screenshots
    "hip_spread": 3.2,         # [tuned] deg cone when not aiming; shrinks to 0 as the sights come up
    # [player] The shape is the player's own: an unheld full-magazine spray photographed on the game's
    # firing range - straight up, a kick to the right, a long lean to the left, then back across to
    # the right. Each entry is (sideways, climb) in degrees that the view moves after that bullet,
    # for bullets 1-29 of a 30-round magazine; hole positions were read off the photo and resampled.
    # The photo cannot give the scale (it does not say how far the wall was), so the size is set to
    # leave the opening kick where play-testing put it ("a bit heavier" than the first build): an
    # unheld 5-bullet spray climbs 1.3 degrees - chest to head at 20 m, over it at 35 m.
    "recoil": (
        (-0.11, 0.32), (0.00, 0.39), (0.03, 0.34), (-0.01, 0.29), (0.00, 0.31),
        (0.03, 0.33), (0.09, 0.38), (0.13, 0.43), (0.02, 0.38), (-0.13, 0.34),
        (-0.20, 0.38), (-0.21, 0.35), (-0.15, 0.30), (-0.04, 0.32), (-0.06, 0.32),
        (-0.15, 0.24), (-0.13, 0.22), (-0.03, 0.23), (0.10, 0.21), (0.15, 0.23),
        (0.07, 0.32), (0.05, 0.34), (0.15, 0.22), (0.21, 0.13), (0.14, 0.20),
        (0.02, 0.30), (0.00, 0.32), (0.13, 0.33), (0.30, 0.38),
    ),
    # There is no magazine here, so a spray can outlast the pattern: after it the gun keeps
    # climbing at its late-magazine rate and sways about where it ended. [tuned]
    "recoil_tail": ((-0.12, 0.28), (-0.12, 0.28), (0.12, 0.28), (0.12, 0.28)),
    "recoil_rand": 0.045,      # [tuned] each bullet also wanders this far either side, so it is never quite the same
    # -- movement, m/s
    "walk": 4.65, "ads_walk": 2.95,     # [game] M15 MOD 0 after Season 01
    "accel": 0.07,                      # [tuned]
    # -- aim assist. The real strengths are not published; these are set a step BELOW
    #    what measurements of the game suggest (about 0.6 rotation, about 0.5 slowdown).
    "slow_max": 0.38,          # [tuned] share of look speed removed inside the bubble
    "hip_slow": 0.6,           # [tuned] ...scaled by this when firing from the hip
    "rot_max": 0.45,           # [tuned] share of the target's on-screen motion the view follows by itself
    "rot_min_share": 0.75,     # [game] BO7: without right-stick tracking you get 75% of full ("penalty 25%")
    "track_need": 0.35,        # [tuned] BO7: right stick must supply this share of the needed turn rate for full
    "rot_pitch": 0.7,          # [tuned] vertical share of the rotation
    "strafe_min": 0.25,        # [measured] left stick must be this far over, sideways, to count as input
    "aim_min": 0.05,           # [measured] ...or the right stick this far in any direction
    # The bubble is a fixed area of the SCREEN around the crosshair, not a fixed size in
    # the world [measured: "constant on-screen size"; the size is the old engine's region].
    "bubble": (0.14, 0.19),             # half-width, half-height in screen units (-1..1)
    "zone": (0.75, 1.0),                # flat inside, with a soft edge so it does not snap on and off;
    #                                     [measured] rotation uses the same bubble as slowdown
    # strength against range in metres: (distance, gain) points, linear in between
    "range_slow": ((0.0, 0.0), (3.0, 1.0), (50.0, 1.0), (100.0, 0.0)),
    # [measured] BO6 ramps up from point blank, is slightly weaker at 5 m and matches older
    # games from 10 m; rotation is barely there past 50-60 m.
    "range_rot_classic": ((0.0, 0.0), (6.0, 1.0), (35.0, 1.0), (50.0, 0.3), (70.0, 0.05), (95.0, 0.0)),
    # [game] BO7 "needs more range to reach full strength" and has "slightly more at very long
    # range". [measured] a left-stick-only strafe past a target at 10 m turned the view 11
    # degrees in BO7 against 19 in BO6; the 10 m point is set so that this model gives the
    # same ratio (see the strafe-by check in the test notes of the README).
    "range_rot_bo7": ((0.0, 0.0), (5.0, 0.45), (10.0, 0.95), (12.0, 1.0), (35.0, 1.0), (50.0, 0.3),
                      (70.0, 0.08), (95.0, 0.0)),
    # -- Modern Warfare 4, as it stood at the end of its beta (September 2026; the game was
    #    not out when this was written, and Infinity Ward said the tuning was not final).
    #    Strength is the same reduced strength as the other styles: only the rules differ.
    # [measured, not under controlled conditions] the beta started with Modern Warfare II's
    # aim assist, which has no fade at point-blank range; [game] after the nerf the studio
    # still found controllers slightly ahead inside 5 m.
    "range_rot_mw4": ((0.0, 1.0), (35.0, 1.0), (50.0, 0.3), (70.0, 0.05), (95.0, 0.0)),
    "range_slow_mw4": ((0.0, 1.0), (50.0, 1.0), (100.0, 0.0)),
    # [observed in footage of the second beta weekend] with the right stick untouched, a
    # target running past turns the view by "a tiny bit ... realistically almost none".
    # That it then scales with how well the right stick follows, as in BO7, is an assumption.
    "mw4_min_share": 0.15,
    # [observed in footage of the first beta weekend] the view follows a jumping target up
    # and down as readily as sideways. Whether the nerf touched this is not known.
    "rot_pitch_mw4": 1.0,
    # -- "Coach": AimTuner's own rule set, no game's. Same reduced strength, but paid out
    #    in proportion to the three habits that every game's assist rewards, so the HUD can
    #    show which one is missing. Do all three and you are at full strength under each of
    #    the other rule sets as well; that is the point of it. [all designed, none measured]
    "coach_base": 0.15,        # what any stick input at all earns
    "coach_track": 0.50,       # ...plus this for the right stick following the target
    "coach_strafe": 0.35,      # ...plus this for strafing
    "coach_track_need": 0.50,  # right stick must supply this share of the needed turn rate (BO7 asks 0.35)
    # Following is a habit, not a reflex, so it is judged like one. For this long after a target is
    # picked up, sets off or changes direction, the stick is not judged at all (nobody's thumb has
    # reacted yet) and the assist carries on at what it was...
    "coach_grace": 0.20,
    # ...and the stick is averaged over about this long, so quick taps that keep up count as keeping up.
    "coach_smooth": 0.12,
    "coach_strafe_full": 0.60, # sideways left stick at which strafing counts in full (it starts at strafe_min)
    "zone_coach": (0.35, 1.0), # centring: all of it only in the middle third of the bubble, none at its edge
    "range_rot_coach": ((0.0, 0.0), (6.0, 1.0), (35.0, 1.0), (50.0, 0.3), (70.0, 0.05), (95.0, 0.0)),
    "smoke_cut": 0.25,         # [tuned] optical depth of smoke on the sight line that cuts assist
    # -- enemies that shoot back [tuned]
    "enemy_dmg": 20, "enemy_rpm": 480.0,
    "regen_delay": 3.0, "regen_rate": 45.0, "down_time": 1.4,
}

# Share of peak rotation that the meter and the stats call "full". It sits between what
# Black Ops 7 gives for strafing alone (0.75) and for tracking as well (1.0), so the
# meter only reads FULL when the right stick is doing its part.
FULL_AT = 0.85

# The aim-assist rule sets a scenario can run under: id -> (short label, full name)
STYLES = {
    "bo7": ("BO7", "Black Ops 7"),
    "classic": ("MW3 / BO6", "MW3 / Black Ops 6"),
    "mw4": ("MW4 beta", "Modern Warfare 4 (beta)"),
    "coach": ("Coach", "Coach (AimTuner's own)"),
}


def recoil_step(n: int) -> tuple:
    """(sideways, climb) in degrees that the view moves after bullet number n of a spray (0 = the first)."""
    pat = TUNE["recoil"]
    if n < len(pat):
        return pat[n]
    tail = TUNE["recoil_tail"]
    return tail[(n - len(pat)) % len(tail)]


def style_label(style: str, full: bool = False) -> str:
    return STYLES.get(style, STYLES["bo7"])[1 if full else 0]

EYE_H = 1.62
BODY_R = 0.25       # torso
LEG_R = 0.17
HEAD_R = 0.145


def ramp(x: float, pts) -> float:
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _zone(e: float, lim) -> float:
    full, none = lim
    return 1.0 if e <= full else (0.0 if e >= none else (none - e) / (none - full))


# Aim response curves as (stick deflection, exponent p): turn speed = deflection ** p,
# with p read between the points. Fitted to a community measurement of the game's three
# curves (u/AntonUncut's Warzone chart, as shown by TheXclusiveAce), which gives these
# shares of full speed at 25 / 50 / 75 % stick:
#     Linear   5.5 / 25 / 57      Standard   3.2 / 18 / 52      Dynamic   5.4 / 21 / 47
# The surprise in that data is that "Linear" is not linear: turn speed goes with roughly
# the SQUARE of stick travel, and the other two are steeper still. Dynamic starts out
# like Linear, falls behind through the middle, and catches up at the rim.
# Read off a chart of an older title, so treat mid-stick speeds as close, not exact.
_CURVES = {
    "linear": ((0.0, 2.00), (1.0, 2.00)),
    "standard": ((0.25, 2.48), (0.50, 2.48), (0.75, 2.29), (1.0, 2.29)),
    "dynamic": ((0.25, 2.10), (0.50, 2.25), (0.75, 2.65), (1.0, 3.05)),
}


def curve(u: float, kind: str) -> float:
    """Stick deflection (0-1, after deadzones) -> share of full turn speed.
    Full-stick speed itself is not modelled at all: it comes from the spin test."""
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    return u ** ramp(u, _CURVES.get(kind, _CURVES["dynamic"]))


def shape(x: float, y: float, dz_min: float, dz_max: float, kind: str | None):
    """Radial min/max deadzone, then the curve. kind None = no curve (movement stick)."""
    m = math.hypot(x, y)
    if m <= dz_min or m == 0.0:
        return 0.0, 0.0, 0.0
    u = min((m - dz_min) / max(dz_max - dz_min, 1e-6), 1.0)
    f = curve(u, kind) if kind else u
    return x / m * f, y / m * f, f


# ------------------------------------------------------------------ geometry
def ray_sphere(ro, rd, c, r):
    ox, oy, oz = ro[0] - c[0], ro[1] - c[1], ro[2] - c[2]
    b = ox * rd[0] + oy * rd[1] + oz * rd[2]
    h = b * b - (ox * ox + oy * oy + oz * oz - r * r)
    if h < 0.0:
        return None
    t = -b - math.sqrt(h)
    return t if t > 0.0 else None


def ray_vcapsule(ro, rd, x, z, y0, y1, r):
    """Vertical capsule: sphere centres at (x, y0, z) and (x, y1, z)."""
    ox, oz = ro[0] - x, ro[2] - z
    a = rd[0] * rd[0] + rd[2] * rd[2]
    best = None
    if a > 1e-9:
        b = ox * rd[0] + oz * rd[2]
        h = b * b - a * (ox * ox + oz * oz - r * r)
        if h < 0.0:
            return None                 # misses the infinite cylinder, so it misses the caps too
        t = (-b - math.sqrt(h)) / a
        if t > 0.0 and y0 <= ro[1] + t * rd[1] <= y1:
            return t
    for yc in (y0, y1):
        t = ray_sphere(ro, rd, (x, yc, z), r)
        if t is not None and (best is None or t < best):
            best = t
    return best


def ray_box(ro, rd, lo, hi, tmax):
    """Distance at which the ray enters the box, or None (also None when starting inside)."""
    tn, tf = 0.0, tmax
    for i in range(3):
        d = rd[i]
        if abs(d) < 1e-9:
            if ro[i] < lo[i] or ro[i] > hi[i]:
                return None
            continue
        a, b = (lo[i] - ro[i]) / d, (hi[i] - ro[i]) / d
        if a > b:
            a, b = b, a
        if a > tn:
            tn = a
        if b < tf:
            tf = b
        if tn > tf:
            return None
    return tn if tn > 0.0 else None


class World:
    """The arena: an outer room, cover boxes and smoke clouds."""

    def __init__(self, room, boxes=(), smokes=(), smoke_density=0.17):
        self.room = room
        self.boxes = [(tuple(map(float, lo)), tuple(map(float, hi))) for lo, hi in boxes]
        self.smokes = [tuple(map(float, s)) for s in smokes]
        self.smoke_density = smoke_density

    def ray(self, ro, rd, tmax=1e9) -> float:
        """How far a bullet travels before hitting cover (tmax if it does not)."""
        best = tmax
        for lo, hi in self.boxes:
            t = ray_box(ro, rd, lo, hi, best)
            if t is not None and t < best:
                best = t
        return best

    def clear(self, a, b) -> bool:
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        ln = math.sqrt(dx * dx + dy * dy + dz * dz)
        if ln < 1e-6:
            return True
        rd = (dx / ln, dy / ln, dz / ln)
        return self.ray(a, rd, ln) >= ln

    def tau(self, a, b) -> float:
        """Smoke on the line a->b, the same measure the shader draws (minus its noise)."""
        if not self.smokes:
            return 0.0
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        ln = math.sqrt(dx * dx + dy * dy + dz * dz)
        if ln < 1e-6:
            return 0.0
        rd = (dx / ln, dy / ln, dz / ln)
        tau = 0.0
        for sx, sy, sz, R in self.smokes:
            ox, oy, oz = a[0] - sx, a[1] - sy, a[2] - sz
            bq = ox * rd[0] + oy * rd[1] + oz * rd[2]
            h = bq * bq - (ox * ox + oy * oy + oz * oz - R * R)
            if h <= 0.0:
                continue
            h = math.sqrt(h)
            t0, t1 = max(-bq - h, 0.0), min(-bq + h, ln)
            if t1 > t0:
                tm = 0.5 * (t0 + t1)
                px, py, pz = ox + rd[0] * tm, oy + rd[1] * tm, oz + rd[2] * tm
                core = max(0.0, 1.0 - (px * px + py * py + pz * pz) / (R * R))
                tau += self.smoke_density * (t1 - t0) * core
        return tau

    def push_out(self, x, z, rad, area=None):
        """Keep a standing body out of cover and inside the room / its allowed area."""
        for lo, hi in self.boxes:
            if lo[1] > 1.6 or hi[1] < 0.2:
                continue
            nx, nz = min(max(x, lo[0]), hi[0]), min(max(z, lo[2]), hi[2])
            dx, dz = x - nx, z - nz
            d2 = dx * dx + dz * dz
            if d2 >= rad * rad:
                continue
            if d2 > 1e-12:
                d = math.sqrt(d2)
                x, z = nx + dx / d * rad, nz + dz / d * rad
            else:           # centre is inside the box: leave by the nearest face
                gaps = ((x - lo[0], -1, 0), (hi[0] - x, 1, 0), (z - lo[2], 0, -1), (hi[2] - z, 0, 1))
                g, sx, sz = min(gaps)
                x, z = x + sx * (g + rad), z + sz * (g + rad)
        x0, x1, z0, z1 = area or (self.room[0][0], self.room[1][0], self.room[0][2], self.room[1][2])
        return min(max(x, x0 + rad), x1 - rad), min(max(z, z0 + rad), z1 - rad)


# -------------------------------------------------------------------- dummies
class Dummy:
    """A mannequin target: a capsule body with a separate head."""

    _next_id = 0

    def __init__(self, x, z, mover=None, shooter=None, hp=None):
        Dummy._next_id += 1
        self.id = Dummy._next_id
        self.x, self.z = float(x), float(z)
        self.y = 0.0               # feet above the floor (jumping)
        self.crouch = 0.0          # 0 standing .. 1 crouched
        self.hp = self.hp_max = hp or TUNE["hp"]
        self.alive = True
        self.mover = mover
        self.shooter = shooter
        self.flash = -1.0          # game time of the last hit, for the white flash
        self.ang = None            # last (yaw, pitch) to the chest, for aim assist
        self.s = {}                # per-target scratch data the scenario keeps

    def head(self):
        return (self.x, self.y + 1.62 - 0.46 * self.crouch, self.z)

    def chest_y(self) -> float:
        return self.y + 1.18 - 0.38 * self.crouch

    def chest(self):
        return (self.x, self.chest_y(), self.z)

    def parts(self):
        """Body capsules as (lower centre y, upper centre y, radius): torso, then legs.
        Standing it is 1.77 m tall; crouched, 1.31 m."""
        c = self.crouch
        return ((self.y + 0.95 - 0.33 * c, self.y + 1.30 - 0.44 * c, BODY_R),
                (self.y + LEG_R, self.y + 0.80 - 0.35 * c, LEG_R))

    def hit(self, ro, rd):
        """(distance, 'head' | 'body') for a ray, or None."""
        best = ray_sphere(ro, rd, self.head(), HEAD_R)
        zone = "head"
        for y0, y1, r in self.parts():
            t = ray_vcapsule(ro, rd, self.x, self.z, y0, y1, r)
            if t is not None and (best is None or t < best):
                best, zone = t, "body"
        return None if best is None else (best, zone)

    def instances(self, color, now):
        """Renderer instances: (pos, radius, rgb, alpha, light, half_height)."""
        k = max(0.0, 1.0 - (now - self.flash) / 0.09) if self.flash >= 0.0 else 0.0
        mix = lambda rgb: tuple(c + (1.0 - c) * k for c in rgb)        # flash white when hit
        (t0, t1, tr), (l0, l1, lr) = self.parts()
        return (((self.x, 0.5 * (l0 + l1), self.z), lr, mix(tuple(0.72 * c for c in color)), 1.0, 0.6, 0.5 * (l1 - l0)),
                ((self.x, 0.5 * (t0 + t1), self.z), tr, mix(color), 1.0, 0.0, 0.5 * (t1 - t0)),
                (self.head(), HEAD_R, mix(tuple(0.45 * c + 0.55 for c in color)), 1.0, 0.0))


class Mover:
    """Moves a dummy. Lateral motion is across the player's line of sight at spawn."""

    def start(self, d, g, rng):
        dx, dz = d.x - g.eye[0], d.z - g.eye[2]
        ln = math.hypot(dx, dz) or 1.0
        self.fx, self.fz = dx / ln, dz / ln         # away from the player
        self.sx, self.sz = -self.fz, self.fx        # across
        self.ox, self.oz = d.x, d.z
        self.rng = rng

    def step(self, d, dt, now, g): ...


class Strafe(Mover):
    """Left-right strafing with random reversals; optional jumps and crouch spam."""

    def __init__(self, speed=3.6, hold=(0.35, 0.95), span=3.5, jump=0.0, crouch=0.0):
        self.speed, self.hold, self.span = speed, hold, span
        self.jump, self.crouch = jump, crouch       # chances per second

    def start(self, d, g, rng):
        super().start(d, g, rng)
        self.dir = rng.choice((-1.0, 1.0))
        self.v = self.off = 0.0
        self.flip = 0.0
        self.vy = 0.0
        self.duck_until = 0.0

    def step(self, d, dt, now, g):
        if now >= self.flip or self.off * self.dir > self.span:
            if self.flip:
                self.dir = -self.dir
            self.flip = now + self.rng.uniform(*self.hold)
        self.v += (self.dir * self.speed - self.v) * min(dt / 0.06, 1.0)
        self.off += self.v * dt
        d.x, d.z = self.ox + self.sx * self.off, self.oz + self.sz * self.off
        if d.y > 0.0 or self.vy > 0.0:              # in the air
            self.vy -= 9.8 * 1.6 * dt
            d.y = max(0.0, d.y + self.vy * dt)
            if d.y == 0.0:
                self.vy = 0.0
        elif self.jump and self.rng.random() < self.jump * dt:
            self.vy = 5.2                           # ~0.85 m apex, ~0.65 s in the air
        if self.crouch:
            if now < self.duck_until:
                d.crouch = min(1.0, d.crouch + dt / 0.12)
            else:
                d.crouch = max(0.0, d.crouch - dt / 0.12)
                if self.rng.random() < self.crouch * dt:
                    self.duck_until = now + self.rng.uniform(0.25, 0.55)


class Runner(Mover):
    """Runs across the lane between two points, turning round at each end."""

    def __init__(self, speed=6.0, span=14.0, pause=(0.0, 0.0)):
        self.speed, self.span, self.pause = speed, span, pause

    def start(self, d, g, rng):
        super().start(d, g, rng)
        self.dir = rng.choice((-1.0, 1.0))
        self.off = -self.dir * self.span
        self.wait = 0.0
        d.x, d.z = self.ox + self.sx * self.off, self.oz + self.sz * self.off

    def step(self, d, dt, now, g):
        if now < self.wait:
            return
        self.off += self.dir * self.speed * dt
        if self.off * self.dir >= self.span:
            self.off = self.dir * self.span
            self.dir = -self.dir
            self.wait = now + self.rng.uniform(*self.pause)
        d.x, d.z = self.ox + self.sx * self.off, self.oz + self.sz * self.off


class Peek(Mover):
    """Steps out from behind cover, stays a moment, steps back."""

    def __init__(self, reach=1.3, out=(0.7, 1.3), hidden=(0.6, 1.3), speed=4.0, side=None):
        self.reach, self.out_t, self.hid_t, self.speed, self.side = reach, out, hidden, speed, side

    def start(self, d, g, rng):
        super().start(d, g, rng)
        self.dir = self.side if self.side is not None else rng.choice((-1.0, 1.0))
        self.off = 0.0
        self.target = 0.0
        self.next = rng.uniform(0.2, 0.7)

    def step(self, d, dt, now, g):
        if now >= self.next:
            if self.target == 0.0:
                self.target, self.next = self.reach, now + self.rng.uniform(*self.out_t)
            else:
                self.target, self.next = 0.0, now + self.rng.uniform(*self.hid_t)
        step = self.speed * dt
        self.off += max(-step, min(step, self.target - self.off))
        d.x, d.z = self.ox + self.sx * self.off * self.dir, self.oz + self.sz * self.off * self.dir


class Duck(Mover):
    """Stands, then drops into a crouch (out of sight behind low cover), then stands again."""

    def __init__(self, up=(0.9, 1.7), down=(0.5, 1.1)):
        self.up, self.down = up, down

    def start(self, d, g, rng):
        super().start(d, g, rng)
        self.target = 0.0
        self.next = rng.uniform(*self.up)

    def step(self, d, dt, now, g):
        if now >= self.next:
            self.target = 1.0 - self.target
            self.next = now + self.rng.uniform(*(self.down if self.target else self.up))
        step = dt / 0.12
        d.crouch += max(-step, min(step, self.target - d.crouch))


class Rush(Mover):
    """Runs at the player in a zig-zag."""

    def __init__(self, speed=5.4, weave=2.2, period=0.9):
        self.speed, self.weave, self.period = speed, weave, period

    def start(self, d, g, rng):
        super().start(d, g, rng)
        self.phase = rng.uniform(0.0, 6.28)

    def step(self, d, dt, now, g):
        dx, dz = g.eye[0] - d.x, g.eye[2] - d.z
        ln = math.hypot(dx, dz)
        if ln < 1.2:
            return
        fx, fz = dx / ln, dz / ln
        w = math.cos(now * 2 * math.pi / self.period + self.phase) * self.weave
        d.x += (fx * self.speed + -fz * w) * dt
        d.z += (fz * self.speed + fx * w) * dt


class Shooter:
    """Makes a dummy fire back once it has seen the player for a moment. Its
    accuracy drops the faster the player moves across its line of fire, which is
    the reason to keep strafing."""

    def __init__(self, delay=(0.50, 0.80), accuracy=0.50):
        self.delay, self.accuracy = delay, accuracy
        self.seen = None
        self.cool = 0.0
        self.react = None

    def step(self, d, dt, now, kit, visible, rng):
        if not visible or kit.down_until > now:
            self.seen = None
            return
        if self.seen is None:
            self.seen, self.react, self.cool = now, rng.uniform(*self.delay), 0.0
        if now - self.seen < self.react:
            return
        self.cool -= dt
        while self.cool <= 0.0:
            self.cool += 60.0 / TUNE["enemy_rpm"]
            dist = math.hypot(d.x - kit.x, d.z - kit.z)
            p = self.accuracy * max(0.35, 1.0 - dist / 90.0) * (1.0 - 0.6 * kit.lateral(d))
            kit.shots_at_me += 1
            if rng.random() < p:
                kit.take_damage(TUNE["enemy_dmg"], d, now)


# ----------------------------------------------------------------- aim assist
class AimAssist:
    """Slowdown + rotational aim assist, as measurements and patch notes describe it.

    slowdown   - while a target is inside the bubble (a fixed area of the screen
                 around the crosshair) the look speed your stick asks for is cut.
    rotational - the view turns by itself to follow a share of the target's motion
                 across your screen, whether the target moved or you strafed. It
                 needs stick input: left stick at least a quarter over sideways, or
                 any right stick.

    Both need a clear line of sight and are cut by smoke, and fade out at distance
    (rotation long before slowdown).

    The styles share one strength and differ in how rotation is earned and where:
      MW3 / BO6   - any qualifying input gives the full share; fades in from point blank.
      Black Ops 7 - input alone gives 75% of it; the last quarter comes from the right
                    stick actually following the target. It also takes a little more
                    distance to reach full strength.
      MW4 (beta)  - strafing alone gives next to nothing: the right stick has to follow
                    the target. No fade at point-blank range, and it follows a target up
                    and down as readily as sideways.
      Coach       - no game's rules: rotation is paid out for three habits at once (right
                    stick following, strafing, staying centred), each reported separately
                    in info["parts"] so the HUD and the results can say which is missing.
                    Following is not judged while your thumb could not have reacted yet.
    """

    # share of full rotation earned by stick input that is not tracking the target
    FLOOR = {"classic": 1.0, "bo7": TUNE["rot_min_share"], "mw4": TUNE["mw4_min_share"], "coach": TUNE["coach_base"]}

    def __init__(self, style: str):
        self.style = style if style in STYLES else "bo7"
        self.floor = self.FLOOR[self.style]
        self.range_rot = TUNE["range_rot_" + self.style]
        self.range_slow = TUNE["range_slow_mw4" if self.style == "mw4" else "range_slow"]
        self.pitch = TUNE["rot_pitch_mw4" if self.style == "mw4" else "rot_pitch"]
        self.track_need = TUNE["coach_track_need" if self.style == "coach" else "track_need"]
        self.peak = TUNE["rot_max"]
        # "parts" are Coach's three habits (0-1 each, None = not asked for); "unscored" names the
        # ones that are shown but must not count towards the run's habit averages this frame
        self.info = {"strength": 0.0, "slow": 0.0, "reason": "", "target": None, "parts": None, "unscored": ()}
        self.clock = 0.0
        self._ft = {"d": None}      # Coach's memory of how the current target has been moving

    def _follow(self, d, wx, wv, sx, sv, dt):
        """Coach's reading of "is the right stick following it?" -> (0-1, does this moment count).

        (wx, wv) is how the target is sliding across the view and (sx, sv) what the right
        stick is asking for, both in deg/s. Moments that say nothing about the habit do not
        count: when there is nothing to follow, and the first `coach_grace` seconds after a
        target is picked up, sets off or changes direction."""
        now, st = self.clock, self._ft
        if st["d"] is not d:
            st = self._ft = {"d": d, "dir": None, "rest": 0.0, "until": now + TUNE["coach_grace"],
                             "hold": 1.0, "q": 1.0, "sx": sx, "sv": sv}
        wmag = math.hypot(wx, wv)
        if wmag < 1.0:
            st["rest"] += dt
            return 1.0, False
        u = (wx / wmag, wv / wmag)
        turned = st["dir"] is not None and u[0] * st["dir"][0] + u[1] * st["dir"][1] < 0.5
        if turned or st["rest"] >= 0.10:
            st["until"], st["hold"] = now + TUNE["coach_grace"], st["q"]
        st["dir"], st["rest"] = u, 0.0
        if now < st["until"]:
            st["sx"], st["sv"] = sx, sv         # the average starts from wherever the stick is when the grace ends
            return st["hold"], False
        k = min(dt / TUNE["coach_smooth"], 1.0)
        st["sx"] += (sx - st["sx"]) * k
        st["sv"] += (sv - st["sv"]) * k
        st["q"] = max(0.0, min(1.0, (st["sx"] * u[0] + st["sv"] * u[1]) / (self.track_need * wmag)))
        return st["q"], True

    def _coach(self, kit, e, q, scored, strafe):
        """Coach rules -> (share of the target's motion followed, centring 0-1, what each habit is costing)."""
        centre = _zone(e, TUNE["zone_coach"])
        w_track, w_strafe = TUNE["coach_track"], TUNE["coach_strafe"]
        if getattr(getattr(kit, "scn", None), "can_move", True):
            s = max(0.0, min(1.0, (strafe - TUNE["strafe_min"]) / (TUNE["coach_strafe_full"] - TUNE["strafe_min"])))
        else:           # a drill that plants your feet: following the target carries the strafing share too
            s, w_track, w_strafe = None, w_track + w_strafe, 0.0
        self.info["parts"] = {"track": q, "strafe": s, "centre": centre}
        self.info["unscored"] = () if scored else ("track",)
        costs = [(1.0 - w_track * (1.0 - q), "follow it with the right stick")]
        if s is not None:
            costs.append((1.0 - w_strafe * (1.0 - s), "keep strafing"))
        return TUNE["rot_max"] * (TUNE["coach_base"] + w_track * q + w_strafe * (s or 0.0)), centre, costs

    def compute(self, kit, dummies, dt, want_yaw, want_pitch, strafe, rmag):
        """-> (stick scale, added yaw rate, added pitch rate). Rates in deg/s.
        strafe: sideways left-stick deflection 0-1; rmag: right-stick deflection 0-1."""
        g, world, info = kit.g, kit.world, self.info
        info.update(strength=0.0, slow=0.0, reason="no target near the crosshair", target=None, parts=None, unscored=())
        self.clock += dt
        eye, cam = g.eye, g.cam
        bx, by = TUNE["bubble"]
        cosp = math.cos(math.radians(g.pitch))
        best = None
        for d in dummies:
            if not d.alive:
                continue
            cy = d.chest_y()
            dx, dy, dz = d.x - eye[0], cy - eye[1], d.z - eye[2]
            flat = math.hypot(dx, dz)
            dist = math.hypot(flat, dy)
            if dist < 0.05:
                continue
            yaw_t = math.degrees(math.atan2(dx, -dz))
            pitch_t = math.degrees(math.atan2(dy, flat))
            prev, d.ang = d.ang, (yaw_t, pitch_t)
            # where the target sits on screen relative to the crosshair: sideways to its
            # centre line, and vertically zero anywhere between its feet and the top of its head
            ex = abs((yaw_t - g.yaw + 180.0) % 360.0 - 180.0) * cosp
            p_lo = math.degrees(math.atan2(d.y + 0.25 - eye[1], flat))
            p_hi = math.degrees(math.atan2(d.head()[1] + HEAD_R - eye[1], flat))
            ev = 0.0 if p_lo <= g.pitch <= p_hi else min(abs(g.pitch - p_lo), abs(g.pitch - p_hi))
            if ex > 60.0 or ev > 60.0:
                continue
            e = math.hypot(math.tan(math.radians(ex)) / cam.tanx / bx, math.tan(math.radians(ev)) / cam.tany / by)
            if e <= 1.5 and (best is None or e < best[0]):
                best = (e, d, dist, yaw_t, pitch_t, prev)
        if best is None:
            self._ft = {"d": None}              # lose a target and finding it again is picking it up afresh
            return 1.0, 0.0, 0.0
        e, d, dist, yaw_t, pitch_t, prev = best
        info["target"] = d
        if not (world.clear(eye, d.chest()) or world.clear(eye, d.head())):
            info["reason"] = "no line of sight"
            self._ft = {"d": None}
            return 1.0, 0.0, 0.0
        if world.tau(eye, d.chest()) > TUNE["smoke_cut"]:
            info["reason"] = "smoke"
            self._ft = {"d": None}
            return 1.0, 0.0, 0.0

        zone = _zone(e, TUNE["zone"])
        slow = TUNE["slow_max"] * zone * ramp(dist, self.range_slow)
        if g.ads_t < 0.5:
            slow *= TUNE["hip_slow"]
        info["slow"] = slow / TUNE["slow_max"]

        rng_gain = ramp(dist, self.range_rot)
        moving = strafe >= TUNE["strafe_min"] or rmag >= TUNE["aim_min"]
        wy = wp = 0.0
        if prev is not None and dt > 0.0:
            wy = ((yaw_t - prev[0] + 180.0) % 360.0 - 180.0) / dt      # how fast the target slides across the view
            wp = (pitch_t - prev[1]) / dt
            if abs(wy) > 500.0 or abs(wp) > 500.0:                     # it teleported (respawn), not moved
                wy = wp = 0.0
        q = 1.0
        if self.style == "coach":               # its own, more forgiving reading of "following" - see _follow
            q, scored = self._follow(d, wy * cosp, wp, want_yaw * cosp, want_pitch, dt)
        elif self.floor < 1.0:                  # how well is the right stick following it? (0-1)
            wmag = math.hypot(wy * cosp, wp)
            if wmag < 1.0:                      # nothing to follow: any right-stick input counts as tracking
                q = 1.0 if rmag >= TUNE["aim_min"] else 0.0
            else:                               # the stick's turn rate along the target's motion, against what is needed
                along = (want_yaw * cosp * wy * cosp + want_pitch * wp) / wmag
                q = max(0.0, min(1.0, along / max(self.track_need * wmag, 3.0)))
        if self.style == "coach":               # rotation is weighed by how centred you are, not just "in the bubble"
            share, zone, costs = self._coach(kit, e, q, scored, strafe)
            off = "stay centred on it"
        else:
            share = TUNE["rot_max"] * (self.floor + (1.0 - self.floor) * q)
            costs, off = [(share / TUNE["rot_max"], "follow it with the right stick")], "off the target"
        strength = share * zone * rng_gain if moving else 0.0
        info["strength"] = strength / self.peak
        if not moving:
            info["reason"] = "no stick input"
        elif info["strength"] >= FULL_AT:
            info["reason"] = ""
        else:                                   # name whichever one is costing the most
            info["reason"] = min([(rng_gain, "too close" if dist < 25.0 else "too far"), (zone, off)] + costs)[1]
        return 1.0 - slow, strength * wy, strength * wp * self.pitch


class NoAssist:
    """What mouse and keyboard get on this track: nothing, under any rule set, in any scenario.

    A run on a mouse is built with this instead of an AimAssist, so there is no strength, no
    slowdown and no rotation to switch on by accident - and anything that tried to ask for
    some would fail loudly here rather than quietly help. (The player's input device is fixed
    for the whole run, and the other device is not read, so a controller's assist cannot be
    borrowed by keeping a thumb on its stick either.)
    """

    style = "none"

    def __init__(self):
        self.info = {"strength": 0.0, "slow": 0.0, "reason": "", "target": None, "parts": None, "unscored": ()}

    def compute(self, *args, **kwargs):
        raise RuntimeError("aim assist was requested for mouse input - it must never be")


def sight_zoom(c: dict) -> float:
    """How much the rifle's sights magnify under Call of Duty settings `c` (its FOV and ADS field of view)."""
    zoom = TUNE["ads_zoom"]
    if c["ads_fov"] != "affected":      # independent: sights keep the view they have at FOV 80
        zoom *= math.tan(math.radians(c["fov"] / 2.0)) / math.tan(math.radians(40.0))
    return zoom


def mouse_ads_ratio(fov: float, zoom: float, coeff: float) -> float:
    """Call of Duty's "Relative" ADS sensitivity: how much slower the view turns per mouse count
    with the sights up, for a hip field of view `fov` (horizontal, at 16:9) and a sight that
    magnifies `zoom` times.

    The monitor distance coefficient picks the point on the screen where a hand movement covers
    the same distance at both zooms, as a multiple of the screen's half-height: the game's
    default 1.33 is three quarters of the way to the side edge of a 16:9 screen, 1.78 is the
    edge itself, and 0 matches right at the crosshair (1 / zoom). [formula as given by the
    community's sensitivity calculators; the game does not publish it]
    """
    t = math.tan(math.radians(fov) / 2.0) / (16.0 / 9.0)        # tan of half the vertical field of view
    if coeff <= 1e-6:
        return 1.0 / zoom
    return math.atan(coeff * t / zoom) / math.atan(coeff * t)


# ------------------------------------------------------------------------ kit
class CodKit:
    """Per-run state for a Call of Duty track scenario: movement, aim, weapon, health."""

    def __init__(self, game):
        self.g = g = game
        self.scn = None
        cfg = game.cfg
        # the sensitivity finder plays blocks at other values without touching the saved settings
        from . import sens          # (here, not at the top: sens builds on this module)

        c = self.c = dict(cfg["cod"], **game.over.get("cod", {}))
        # How the view turns: this track's own settings (the game's scales, the spin test), or the
        # track's that every track has been told to follow - see sens.py. The finder's trial values
        # arrive in over["sens"].
        self.aim = sens.pad(cfg, "cod", game.over.get("sens"))
        self.rate_h, self.rate_v = self.aim.rate_h, self.aim.rate_v     # deg/s at full stick, hip
        self.ads_scale = self.aim.ads                                   # ADS speed as a share of hip speed
        self.aim_stick = cfg["aim"]["stick"]
        self.move_stick = "left" if self.aim_stick == "right" else "right"
        self.invert = cfg["aim"]["invert_y"]
        self.dz_l = (c["dz_l_min"] / 100.0, c["dz_l_max"] / 100.0)
        zoom = sight_zoom(c)
        g.fov, g.zoom_max = float(c["fov"]), zoom
        # Mouse and keyboard: the game's own mouse scale (or the followed track's), and no aim
        # assist of any kind.
        self.kbm = bool(getattr(g, "kbm", False))
        ms = sens.mouse(cfg, "cod", game.over.get("sens"))
        self.m_deg = ms.deg                             # degrees per count from the hip
        self.m_ads = ms.ads(g.fov, zoom)                # ...times this with the sights up
        self.assist = NoAssist() if self.kbm else AimAssist(c["aa_style"])
        self.interval = 60.0 / TUNE["rpm"]
        self.world = None
        self.x = self.z = 0.0
        self.vx = self.vz = 0.0
        self.area = None
        self.lmag = self.rmag = self.strafe = 0.0
        self.cool = 0.0
        self.spray = 0             # shots in the current burst (drives the recoil pattern)
        self.last_shot = -9.0
        self.kick = 0.0
        self.hp = float(TUNE["hp"])
        self.hurt_t = -9.0
        self.hurt_from = 0.0
        self.down_until = -1.0
        self.deaths = self.shots_at_me = 0
        self.dmg_taken = 0.0
        # aim-assist bookkeeping while actually fighting (firing at something in view)
        self.aa_t = self.aa_sum = self.aa_full = 0.0
        self.aa_why: dict = {}
        self.habit_t = 0.0          # Coach rules: time with a target in the bubble...
        self.habits: dict = {}      # ...how much of each habit there was...
        self.habit_w: dict = {}     # ...over the time that habit could be judged
        self.move_t = self.fight_t = 0.0
        # The ghost (ghost.py) moves exactly as the player it is replaying did: then this gives the
        # body for a moment of the run - (x, z, vx, vz, strafe, lean, crouch) - instead of the sticks.
        self.body = None

    def attach(self, scn):
        self.scn = scn
        self.world = scn.world
        self.x, self.z, self.g.yaw = scn.start
        self.area = scn.move_area
        self.g.eye = (self.x, EYE_H, self.z)

    # ---- helpers the scenarios and bots use ---------------------------------
    def lateral(self, d) -> float:
        """0-1: how fast the player is moving across this dummy's line of fire."""
        dx, dz = self.x - d.x, self.z - d.z
        ln = math.hypot(dx, dz) or 1.0
        return min(abs(self.vx * -dz / ln + self.vz * dx / ln) / TUNE["walk"], 1.0)

    def damage(self, zone: str, dist: float) -> int:
        if zone == "head":
            return TUNE["head_near"] if dist <= TUNE["head_range"] else TUNE["head_far"]
        return TUNE["body"]

    def stick_for(self, rate: float, vertical: bool = False) -> float:
        """Stick deflection that asks for `rate` deg/s (inverts deadzone + curve; for bots)."""
        return self.aim.stick_for(rate, vertical, self.ads_scale if self.g.ads_held else 1.0)

    def take_damage(self, dmg, d, now):
        if now < self.down_until:
            return
        self.hp -= dmg
        self.dmg_taken += dmg
        self.hurt_t = now
        self.hurt_from = math.degrees(math.atan2(d.x - self.x, -(d.z - self.z)))
        self.g.sfx("miss")
        if self.hp <= 0.0:
            self.deaths += 1
            self.hp = float(TUNE["hp"])
            self.down_until = now + TUNE["down_time"]
            self.scn.on_down(d, now)

    # ---- per frame ----------------------------------------------------------
    def _read_mouse(self, dt, events, off, down):
        """Mouse and keyboard: turn the view by this frame's counts, in the order they arrived, and
        return (when Shoot was pressed or None, the view at that instant or None).

        One count is `m_deg` degrees from the hip and `m_deg * m_ads` through the sights, switching
        the instant Aim goes down or up. Nothing else touches the view: no acceleration, no
        smoothing, and no aim assist (there is none to call - see NoAssist).
        """
        g, scn, m, binds = self.g, self.scn, self.g.mouse, self.g.binds
        shoot, ads = binds["shoot"], binds["ads"]
        aiming = g.ads_held
        up = 1.0 if self.invert else -1.0       # pushing the mouse away looks up, unless inverted
        yaw0, pitch0 = g.yaw, g.pitch
        turned = 0.0
        pressed_at = view = None
        for t, kind, a, b in events:
            if kind == 0:                       # mouse.MOTION
                if not down:
                    k = self.m_deg * (self.m_ads if aiming else 1.0)
                    g.yaw += a * k
                    g.pitch = max(-85.0, min(85.0, g.pitch + b * k * up))
                    turned += math.hypot(a * k * math.cos(math.radians(g.pitch)), b * k)
            elif kind == 1:                     # mouse.BUTTON: a button or a key
                if a == shoot:
                    if b and pressed_at is None:
                        pressed_at, view = t - off, (g.yaw, g.pitch)
                elif a == ads:
                    aiming = bool(b)
                    scn.on_ads(t - off, aiming)
        if not -180.0 <= g.yaw < 180.0:         # (only when it has to: a view nothing moved stays bit-for-bit where it was)
            g.yaw = (g.yaw + 180.0) % 360.0 - 180.0
        g.fire_held = m.down(shoot) and not down
        g.ads_held = m.down(ads)
        g.aim_raw = (0.0, 0.0)
        self.rmag = 1.0 if turned else 0.0
        if dt > 0.0:
            g.yaw_rate = ((g.yaw - yaw0 + 180.0) % 360.0 - 180.0) / dt
            g.pitch_rate = (g.pitch - pitch0) / dt
            g.turn_rate = turned / dt
        g.frame_turn = turned
        return pressed_at, view

    def step(self, dt, events, off):
        g, pad, scn = self.g, self.g.pad, self.scn
        now = g.t
        down = now < self.down_until
        view = None
        if self.kbm:
            pressed_at, view = self._read_mouse(dt, events, off, down)
        else:
            binds = g.cfg["binds"]
            shoot, ads = binds["shoot"], binds["ads"]
            pressed_at = None
            for t, name, is_down in events:
                if name == shoot and is_down:
                    pressed_at = t - off
                elif name == ads:
                    scn.on_ads(t - off, is_down)
            g.fire_held = pad.down(shoot) and not down
            g.ads_held = pad.down(ads)

        # 1. bullets, judged against the view that was on screen (a mouse click: against the view
        #    at the click itself, which _read_mouse kept)
        self.cool -= dt
        if (g.fire_held or pressed_at is not None) and not down:
            first = True
            while self.cool <= 0.0:
                self._bullet(pressed_at if (first and pressed_at is not None) else now, view if first else None)
                self.cool += self.interval
                first = False
        elif self.cool < 0.0:
            self.cool = 0.0
        if now - self.last_shot > 0.25:
            self.spray = 0
        self.kick = max(0.0, self.kick - dt * 9.0)

        # 2. sights
        step = dt / TUNE["ads_time"]
        g.ads_t += max(-step, min(step, (1.0 if g.ads_held else 0.0) - g.ads_t))

        # 3. movement (left stick; full speed at the max-deadzone setting - or the movement keys,
        #    which are all or nothing, at full speed in whichever of the eight directions is held)
        if self.body is not None:
            self.x, self.z, self.vx, self.vz, self.strafe = self.body(now)[:5]
            self.lmag = self.strafe
        else:
            if self.kbm:
                m, b = g.mouse, g.binds
                mx = (1.0 if m.down(b["right"]) else 0.0) - (1.0 if m.down(b["left"]) else 0.0)
                my = (1.0 if m.down(b["forward"]) else 0.0) - (1.0 if m.down(b["back"]) else 0.0)
                if mx and my:
                    mx, my = mx * 0.7071, my * 0.7071
                self.lmag = 1.0 if (mx or my) else 0.0
            else:
                mx, my = pad.stick(self.move_stick)
                mx, my, self.lmag = shape(mx, my, self.dz_l[0], self.dz_l[1], None)
            if down or not scn.can_move:
                mx = my = self.lmag = 0.0
            self.strafe = abs(mx)               # sideways only: walking forward or back is not strafing
            yaw = math.radians(g.yaw)
            sy, cy = math.sin(yaw), math.cos(yaw)
            vmax = TUNE["walk"] + (TUNE["ads_walk"] - TUNE["walk"]) * g.ads_t
            tx = (cy * mx + sy * my) * vmax
            tz = (sy * mx - cy * my) * vmax
            k = min(dt / TUNE["accel"], 1.0)
            self.vx += (tx - self.vx) * k
            self.vz += (tz - self.vz) * k
            nx, nz = self.world.push_out(self.x + self.vx * dt, self.z + self.vz * dt, 0.35, self.area)
            if dt > 0.0:
                self.vx, self.vz = (nx - self.x) / dt, (nz - self.z) / dt
            self.x, self.z = nx, nz
        g.eye = (self.x, EYE_H, self.z)

        # 4. aim. A mouse has already turned the view, count for count, in _read_mouse - and that is
        #    all that ever turns it: no slowdown, no rotation, whatever the scenario says.
        if self.kbm:
            g._update_cam()
            if g.fire_held and scn.fighting():
                self.fight_t += dt
                if self.strafe >= TUNE["strafe_min"]:
                    self.move_t += dt
            if self.hp < TUNE["hp"] and now - self.hurt_t > TUNE["regen_delay"]:
                self.hp = min(float(TUNE["hp"]), self.hp + TUNE["regen_rate"] * dt)
            return
        #    A controller: stick -> curve -> sensitivity, then aim assist on top
        rx, ry = pad.stick(self.aim_stick)
        g.aim_raw = (rx, ry)
        ax, ay, self.rmag = self.aim.shape(rx, ry)
        scale = self.ads_scale if g.ads_held else 1.0     # as the game's default: applies the instant Aim is pressed
        want_yaw = ax * self.rate_h * scale
        want_pitch = (-ay if self.invert else ay) * self.rate_v * scale
        if down:
            want_yaw = want_pitch = 0.0
        if scn.aa and not down:
            mul, ry_add, rp_add = self.assist.compute(self, scn.dummies, dt, want_yaw, want_pitch, self.strafe, self.rmag)
        else:
            mul, ry_add, rp_add = 1.0, 0.0, 0.0
            self.assist.info.update(strength=0.0, slow=0.0, target=None, reason="", parts=None, unscored=())
        g.yaw_rate = want_yaw * mul + ry_add
        g.pitch_rate = want_pitch * mul + rp_add
        g.yaw = (g.yaw + g.yaw_rate * dt + 180.0) % 360.0 - 180.0
        g.pitch = max(-85.0, min(85.0, g.pitch + g.pitch_rate * dt))
        g.turn_rate = math.hypot(want_yaw * math.cos(math.radians(g.pitch)), want_pitch)
        g.frame_turn = g.turn_rate * dt
        g._update_cam()

        # 5. bookkeeping: how much assist there was while actually fighting
        if g.fire_held and scn.fighting():
            self.fight_t += dt
            if self.strafe >= TUNE["strafe_min"]:
                self.move_t += dt
            if scn.aa:
                s = self.assist.info["strength"]
                self.aa_t += dt
                self.aa_sum += s * dt
                if s >= FULL_AT:
                    self.aa_full += dt
                else:
                    why = self.assist.info["reason"] or "partial"
                    self.aa_why[why] = self.aa_why.get(why, 0.0) + dt
                parts = self.assist.info["parts"]
                if parts:
                    self.habit_t += dt
                    skip = self.assist.info["unscored"]
                    for name, v in parts.items():
                        if v is not None and name not in skip:
                            self.habits[name] = self.habits.get(name, 0.0) + v * dt
                            self.habit_w[name] = self.habit_w.get(name, 0.0) + dt

        # 6. health comes back after a few quiet seconds
        if self.hp < TUNE["hp"] and now - self.hurt_t > TUNE["regen_delay"]:
            self.hp = min(float(TUNE["hp"]), self.hp + TUNE["regen_rate"] * dt)

    def _bullet(self, t, view=None):
        g, scn = self.g, self.scn
        yaw, pitch = view or (g.yaw, g.pitch)
        spread = TUNE["hip_spread"] * (1.0 - g.ads_t)
        if spread > 0.01:
            r, a = spread * math.sqrt(g.rng.random()), g.rng.uniform(0.0, 2 * math.pi)
            yaw += r * math.cos(a) / max(math.cos(math.radians(pitch)), 0.2)
            pitch += r * math.sin(a)
        y, p = math.radians(yaw), math.radians(pitch)
        cp = math.cos(p)
        rd = (math.sin(y) * cp, math.sin(p), -math.cos(y) * cp)
        wall = self.world.ray(g.eye, rd)
        best = None
        for d in scn.dummies:
            if d.alive:
                h = d.hit(g.eye, rd)
                if h and h[0] < wall and (best is None or h[0] < best[0]):
                    best = (h[0], h[1], d)
        scn.on_bullet(t, best)
        # recoil: the view follows the weapon's pattern; pulling it back is the player's job
        n = self.spray
        side, climb = recoil_step(n)
        g.pitch = min(85.0, g.pitch + climb)
        g.yaw += side + g.rng.uniform(-TUNE["recoil_rand"], TUNE["recoil_rand"])
        self.spray = n + 1
        self.last_shot = g.t
        self.kick = 1.0

    # ---- HUD ----------------------------------------------------------------
    def draw(self, ui):
        g, scn = self.g, self.scn
        W, cx = ui.W, ui.W / 2
        now = g.t
        # health
        hp = max(self.hp, 0.0) / TUNE["hp"]
        ui.rect(56, 1000, 260, 12, (0.0, 0.0, 0.0, 0.5))
        ui.rect(56, 1000, 260 * hp, 12, GOOD if hp > 0.5 else (WARN if hp > 0.25 else BAD))
        ui.text(56, 974, "HEALTH", "s", DIM)
        age = now - self.hurt_t
        if 0.0 <= age < 0.5:                 # red arc on the side the shots came from
            a = 1.0 - age / 0.5
            rel = math.radians((self.hurt_from - g.yaw + 180.0) % 360.0 - 180.0) - math.pi / 2
            ui.ring(cx, 540, 150, 10, alpha(BAD, 0.8 * a), rel - 0.35, rel + 0.35, 48)
            ui.rect(0, 0, W, ui.H, (0.6, 0.0, 0.0, 0.10 * a))
        if now < self.down_until:
            ui.rect(0, 0, W, ui.H, (0.25, 0.0, 0.0, 0.45))
            ui.text(cx, 430, "DOWN", "xl", BAD, 1)
        # aim assist meter (a controller's; a mouse has no assist to show)
        if self.kbm:
            ui.text(W - 56, 82, "aim assist none · mouse", "s", FAINT, 2)
            return
        if scn.aa and scn.show_meter:
            info = self.assist.info
            s = max(0.0, min(1.0, info["strength"]))
            bx, by, bw = cx - 150, 760, 300
            ui.rect(bx, by, bw, 8, (0.0, 0.0, 0.0, 0.5))
            ui.rect(bx, by, bw * s, 8, GOOD if s >= FULL_AT else (WARN if s >= 0.3 else FAINT))
            ui.rect(bx + bw * FULL_AT - 1, by - 3, 2, 14, alpha(WHITE, 0.5))
            label = "AIM ASSIST  " + ("FULL" if s >= FULL_AT else ("PARTIAL" if s >= 0.3 else ("WEAK" if s > 0.02 else "OFF")))
            ui.text(cx, by - 26, label, "s", WHITE if s >= 0.3 else DIM, 1)
            if self.assist.style == "coach":
                # the three habits the assist is being paid out for, so the missing one is obvious
                parts = info["parts"] or {}
                w = (bw - 24) / 3.0
                for i, (key, name) in enumerate((("track", "FOLLOW"), ("strafe", "STRAFE"), ("centre", "CENTRE"))):
                    v, x = parts.get(key), bx + i * (w + 12)
                    ui.rect(x, by + 22, w, 5, (0.0, 0.0, 0.0, 0.5))
                    if v is not None:
                        ui.rect(x, by + 22, w * v, 5, GOOD if v >= 0.8 else (WARN if v >= 0.3 else BAD))
                    ui.text(x + w / 2, by + 32, name, "s", FAINT if v is None else (WHITE if v >= 0.8 else WARN), 1)
            elif info["target"] is not None and s < FULL_AT and info["reason"]:
                ui.text(cx, by + 16, info["reason"], "s", WARN, 1)
            if info["slow"] > 0.3:
                ui.text(bx - 14, by - 8, "SLOW", "s", ACCENT, 2)
        ui.text(W - 56, 82, "aim assist " + (style_label(self.assist.style) if scn.aa else "off"), "s", FAINT, 2)

    def summary(self) -> dict:
        """Kit-level stats merged into the scenario's own."""
        out = {
            "strafe_pct": round(100.0 * self.move_t / self.fight_t, 1) if self.fight_t > 0 else None,
            "deaths": self.deaths, "damage_taken": round(self.dmg_taken),
        }
        if self.scn.aa and not self.kbm:
            out["aa_avg_pct"] = round(100.0 * self.aa_sum / self.aa_t, 1) if self.aa_t > 0 else None
            out["aa_full_pct"] = round(100.0 * self.aa_full / self.aa_t, 1) if self.aa_t > 0 else None
            out["aa_lost_to"] = {k: round(v, 2) for k, v in sorted(self.aa_why.items(), key=lambda kv: -kv[1])}
            if self.habit_t > 0.5:          # Coach rules: how much of each habit was there, and which to work on
                habits = {k: round(100.0 * v / self.habit_w[k], 1) for k, v in self.habits.items() if self.habit_w[k] > 0.25}
                if habits:
                    out["coach"] = dict(habits, focus=min(habits, key=habits.get))
        return out
