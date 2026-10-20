"""Rainbow Six Siege-style gunplay, for the Siege track.

How much of this is the real game and how much is a model, so nobody mistakes one for
the other. Every number below is marked where it is defined:

  [game]      Ubisoft's own patch notes, designer's notes or in-game values.
  [measured]  someone's measurement of the game (sensitivity calculators, weapon
              spreadsheets, frame counts).
  [tuned]     chosen by feel because nothing is published - recoil above all. Siege's
              recoil patterns are not published as numbers; what is known is each gun's
              character (how hard it climbs, which way it drifts, when it gets worse),
              and that is what the patterns here are built from.
  [trainer]   not Siege at all: a convenience for a drill that repeats.

AIM ASSIST: none. Siege gives a controller no aim assist in its multiplayer, so this
track has none for anyone, on any input device, in any scenario. It is not a setting:
the kit below is built with the same stand-in a mouse gets on the Call of Duty track
(codkit.NoAssist, whose compute() raises), it contains no slowdown or rotation code,
and a scenario that tried to declare aim assist is refused when its class is defined
(r6scn.SiegeScenario). tests/check_r6.py checks all of it on both devices.

Tuning lives in TUNE and GUNS. Changing anything there changes how hard the scenarios
are, so bump config.R6_MODEL when you do.
"""
from __future__ import annotations

import math

from . import config
from . import mouse as mousemod
from .codkit import (HEAD_R, Dummy, NoAssist, Shooter, World, ramp, ray_box, ray_sphere, ray_vcapsule,
                     shape as cod_shape)
from .theme import BAD, DIM, FAINT, GOOD, WARN, WHITE, alpha

TUNE = {
    # -- health and damage
    "hp": {1: 125, 2: 110, 3: 100},     # [game] by operator speed (1-speed is the 3-armor heavy), since Y6S3
    "limb": 0.75,                       # [game] arms and legs (the Siege X halving was reverted in Y10S4.1)
    "soft_pen": 0.70,                   # [measured, by the community, not Ubisoft] damage left after one soft wall
    # -- the player
    "eye": 1.62, "eye_crouch": 1.05,    # [tuned] eye height standing / crouched, m - Siege publishes neither
    "stance_time": 0.25,                # [tuned] standing <-> crouched, s
    "walk": {1: 2.85, 2: 3.00, 3: 3.10},  # [measured, loosely: community figures disagree] m/s, weapon up
    "ads_walk": 2.34,                   # [game] m/s while aiming down sights, every operator, since Y7S4
    "crouch_walk": 0.55,                # [tuned] share of speed while crouched
    "accel": 0.08,                      # [tuned]
    "lean_shift": 0.34,                 # [tuned] how far the eye moves sideways in a full lean, m (not published)
    "lean_roll": 12.0,                  # [tuned] view tilt in a full lean, degrees (not published)
    "lean_time": 0.28,                  # [tuned] upright to fully leaned, s (not published)
    "lean_cover": 0.55,                 # [trainer] enemy hit chance against a player who is leaning out of cover
    # -- aiming down sights, by weapon class, s [game: Y9S1, confirmed by measurement at Y11S3.1]
    "ads_time": {"ar": 0.52, "smg": 0.46, "mp": 0.38},
    "sight_1x": 1.05,                   # [game] a non-magnifying sight raises 5% faster
    "hip_spread": 5.0,                  # [tuned] degrees of cone from the hip; nothing here is meant to be hip-fired
    # -- recoil that settles: Siege re-centres the sight once you stop firing. Here that is whatever
    #    climb you did not pull down yourself, coming back over about a third of a second. [tuned]
    "recover_after": 0.12,              # s since the last bullet before it starts
    "recover_rate": 9.0,                # share of the outstanding climb recovered per second
    # -- attachments, as the game has them now
    "flash_hider": 0.80,                # [measured] vertical kick, every bullet
    "compensator": 0.65,                # [game] sideways kick (Y8S1: -35%)
    "muzzle_brake": 0.50,               # [game] the first bullet's kick (Y8S1: -50%)
    "vertical_grip": 0.80,              # [game] vertical kick (Y9S1: -20%)
    "angled_grip": 1.20,                # [game] reload speed (Y9S1: +20%; it no longer speeds up ADS)
    "horizontal_grip": 1.05,            # [game] movement speed (Y9S1: +5%)
    # -- enemies that shoot back [trainer]
    "enemy_dmg": 34, "enemy_rpm": 300.0,   # a real gun's damage at well under a real gun's rate: pressure, not instant death
    "calm_after": 3.0,                  # s without being shot at before health is restored (Siege has no regeneration)
    "down_time": 1.6,
}

# Sights: the share of your field-of-view setting each one leaves (so a 1x still zooms a little).
# [game: Ubisoft's Y5S3 ADS guide; 3.5x is the community's figure, Ubisoft has not published it]
SIGHTS = {"1x": 0.90, "2.5x": 0.42, "3.5x": 0.32}

# Guns: damage, fire rate, magazine and sights as of Y11S3.1 [game / measured in its shooting range].
# falloff: (metres, share of damage) [game: assault rifles 25-35 m, SMGs 18-28 m, both down to 60%].
# reload [tuned]. recoil [tuned to each gun's published character - Siege's recoil is not published
# as numbers anywhere] - degrees the view moves per bullet:
#   first   the opening bullet's climb
#   climb   each later bullet's climb, before the burst changes it
#   grow    (from bullet, to bullet, x): climb is multiplied up (or down) to x over those bullets -
#           "the longer the burst, the stronger the recoil" (Y7S3); Ubisoft has said where the long-burst
#           stage starts for a few guns (R4-C the 8th bullet, AK-12 the 10th) and it is 12 by default
#   pull    steady sideways drift per bullet (negative = left)
#   sway    sideways kick either way, at random, per bullet
#   wide    (from bullet, x): the random sideways part opens up by x from that bullet on
_AR = ((25.0, 1.0), (35.0, 0.60))
_SMG = ((18.0, 1.0), (28.0, 0.60))
GUNS = {
    "ump45": {"name": "UMP45", "who": "Castle / Pulse", "class": "smg", "dmg": 42, "rpm": 600, "mag": 25, "reload": 2.6,
              "falloff": _SMG, "sights": ("1x",), "grip": True,
              "recoil": {"first": 0.18, "climb": 0.14, "grow": (10, 16, 1.20), "pull": 0.0, "sway": 0.07, "wide": (12, 1.3)},
              "note": "Next to no climb, a little side-to-side wander. The easy one."},
    "556xi": {"name": "556xi", "who": "Thermite", "class": "ar", "dmg": 47, "rpm": 690, "mag": 30, "reload": 2.9,
              "falloff": _AR, "sights": ("1x", "2.5x"), "grip": True,
              "recoil": {"first": 0.30, "climb": 0.27, "grow": (10, 16, 1.25), "pull": -0.035, "sway": 0.04, "wide": (3, 2.2)},
              "note": "A gentle climb drifting up and to the left, with sideways bounces from the fourth shot."},
    "mp7": {"name": "MP7", "who": "Bandit", "class": "smg", "dmg": 32, "rpm": 900, "mag": 30, "reload": 2.5,
            "falloff": _SMG, "sights": ("1x",), "grip": False,
            "recoil": {"first": 0.34, "climb": 0.34, "grow": (2, 6, 0.55), "pull": 0.0, "sway": 0.12, "wide": (12, 1.2)},
            "note": "The first few shots bounce up, then it is mostly random sideways. Takes no grip."},
    "r4c": {"name": "R4-C", "who": "Ash", "class": "ar", "dmg": 39, "rpm": 860, "mag": 25, "reload": 2.6,
            "falloff": _AR, "sights": ("1x", "2.5x"), "grip": True,
            "recoil": {"first": 0.50, "climb": 0.44, "grow": (6, 10, 1.40), "pull": 0.045, "sway": 0.05, "wide": (8, 1.5)},
            "note": "A strong first kick, a fast vertical climb and a steady pull to the right; worse from the 8th bullet."},
    "ak12": {"name": "AK-12", "who": "Ace / Fuze", "class": "ar", "dmg": 40, "rpm": 850, "mag": 30, "reload": 2.9,
             "falloff": _AR, "sights": ("1x", "2.5x"), "grip": True,
             "recoil": {"first": 0.58, "climb": 0.38, "grow": (8, 13, 1.40), "pull": 0.05, "sway": 0.05, "wide": (10, 3.0)},
             "note": "A very strong first shot, steady up and right for six or seven, then all over the place from the 10th."},
    "vector": {"name": "Vector .45 ACP", "who": "Mira", "class": "smg", "dmg": 23, "rpm": 1200, "mag": 25, "reload": 2.4,
               "falloff": _SMG, "sights": ("1x",), "grip": True,
               "recoil": {"first": 0.10, "climb": 0.10, "grow": (2, 5, 2.40), "pull": 0.0, "sway": 0.035, "wide": (3, 4.0)},
               "note": "Twenty bullets a second. Almost nothing for three shots, then it bounces left and right."},
    "f2": {"name": "F2", "who": "Twitch", "class": "ar", "dmg": 37, "rpm": 980, "mag": 25, "reload": 2.8,
           "falloff": _AR, "sights": ("1x", "2.5x"), "grip": True,
           "recoil": {"first": 0.48, "climb": 0.50, "grow": (9, 14, 1.35), "pull": 0.0, "sway": 0.07, "wide": (12, 1.6)},
           "note": "The fastest-firing rifle here, with a harsh vertical kick from the first bullets."},
    "c8": {"name": "C8-SFW", "who": "Buck", "class": "ar", "dmg": 40, "rpm": 837, "mag": 30, "reload": 2.7,
           "falloff": _AR, "sights": ("1x", "2.5x"), "grip": False,
           "recoil": {"first": 0.52, "climb": 0.52, "grow": (9, 14, 1.40), "pull": 0.0, "sway": 0.03, "wide": (12, 1.3)},
           "note": "The hardest straight-up climb of the rifles, hardly any sideways - and it takes no grip."},
    "smg11": {"name": "SMG-11", "who": "Sledge / Smoke", "class": "mp", "dmg": 32, "rpm": 1270, "mag": 16, "reload": 2.2,
              "falloff": _SMG, "sights": ("1x",), "grip": True,
              "recoil": {"first": 0.45, "climb": 0.30, "grow": (4, 7, 2.20), "pull": 0.0, "sway": 0.10, "wide": (5, 2.2)},
              "note": "A machine pistol: sixteen rounds in three quarters of a second, and wild from the fifth."},
}
GUN_ORDER = ("ump45", "556xi", "mp7", "r4c", "ak12", "vector", "f2", "c8", "smg11")     # easiest recoil first
BARRELS = ("flash_hider", "compensator", "muzzle_brake", "none")
GRIPS = ("vertical", "angled", "horizontal", "none")
LABELS = {"flash_hider": "Flash hider", "compensator": "Compensator", "muzzle_brake": "Muzzle brake", "none": "None",
          "vertical": "Vertical grip", "angled": "Angled grip", "horizontal": "Horizontal grip"}


def gun_of(c: dict) -> dict:
    return GUNS.get(c["weapon"], GUNS["r4c"])


def sight_of(c: dict) -> str:
    """The sight in use: the chosen one if this gun can mount it, else its first."""
    g = gun_of(c)
    return c["sight"] if c["sight"] in g["sights"] else g["sights"][0]


def grip_of(c: dict) -> str:
    """The grip in use - none on the guns that cannot take one (C8-SFW, MP7). [game]"""
    return c["grip"] if gun_of(c)["grip"] else "none"


# ---------------------------------------------------------------- field of view
def hfov(c: dict) -> float:
    """Siege's FOV setting is VERTICAL (60-90). The renderer wants horizontal at 16:9."""
    return math.degrees(2.0 * math.atan(math.tan(math.radians(c["fov"]) * 0.5) * 16.0 / 9.0))


def zoom_of(c: dict, sight: str | None = None) -> float:
    """How much the sight magnifies at this FOV setting. Through a sight the vertical field of view is
    the setting times the sight's share (Ubisoft's own model), which makes the magnification depend on
    the setting: a "2.5x" is 2.4x at FOV 60 and 2.9x at 90."""
    half = math.radians(c["fov"]) * 0.5
    return math.tan(half) / math.tan(half * SIGHTS[sight or sight_of(c)])


# ------------------------------------------------------------ sensitivity, mouse
# One count turns the view (sensitivity x multiplier unit / 200) radians [measured: the
# community's sensitivity calculators; the game's default unit is 0.02].
MOUSE_K = math.degrees(1.0) / 200.0
UNITS = (0.02, 0.01, 0.005, 0.002, 0.001)       # multiplier units set_mouse_cm360 may pick


def mouse_deg(c: dict) -> float:
    return c["mouse_sens"] * c["mouse_unit"] * MOUSE_K


def mouse_ads_ratio(slider: float, zoom: float) -> float:
    """Share of the hip sensitivity through the sights, for the ADS slider and a sight that
    magnifies `zoom` times: slider x 0.02 x tan(sight FOV / 2) / tan(FOV / 2), which is
    (slider / 50) / zoom. At the default 50 a hand movement covers the same distance on screen
    as it does from the hip. [game: Ubisoft's Y5S3 ADS guide. One slider here; the game also
    offers one per magnification.]"""
    return slider * 0.02 / zoom


def mouse_response(cfg: dict, ads=None):
    c = cfg["r6"]
    slider = c["mouse_ads"] if ads is None else ads
    return mouse_deg(c), lambda fov, zoom: mouse_ads_ratio(slider, zoom)


def set_mouse_cm360(cfg: dict, cm: float) -> None:
    """Set Siege's mouse sensitivity to `cm` centimetres per turn at this DPI. The slider only
    moves in whole numbers, which is coarse at low values, so - as players do in the game's
    GameSettings.ini - the multiplier unit is made smaller when the slider alone cannot land
    within 1%."""
    c = cfg["r6"]
    want = 360.0 / (cm / 2.54 * cfg["mouse"]["dpi"])            # degrees per count
    best = None
    for unit in (c["mouse_unit"],) + UNITS:
        s = min(max(round(want / (unit * MOUSE_K)), 1), 100)
        err = abs(math.log(s * unit * MOUSE_K / want))
        if best is None or err < best[0] - 1e-9:
            best = (err, s, unit)
        if err <= 0.01:
            break
    c["mouse_sens"], c["mouse_unit"] = best[1], best[2]


# ------------------------------------------------------- sensitivity, controller
def pad_response(cfg: dict, hip=None, ads=None):
    """Siege does not publish its controller turn speeds, so - like the Call of Duty track - they
    come from a spin test: full-stick turns timed in the game (config.r6_rates)."""
    c = cfg["r6"]
    per_unit, ads_ratio = config.r6_rates(cfg)
    h = c["sens_h"] if hip is None else hip
    dz = c["dz_r"] / 100.0
    # [tuned] deadzone, then a squared response to a full-range stick: Siege's own curve is not published
    return (h * per_unit, c["sens_v"] * h / c["sens_h"] * per_unit, (c["ads"] if ads is None else ads) * ads_ratio,
            lambda x, y: cod_shape(x, y, dz, 0.99, "linear"))


# ------------------------------------------------------------------------ recoil
def recoil_step(gun: dict, n: int, rng, barrel: str = "none", grip: str = "none") -> tuple:
    """(sideways, climb) in degrees that the view moves after bullet number n of a burst (0 = the first)."""
    r = gun["recoil"]
    if n == 0:
        up = r["first"] * (TUNE["muzzle_brake"] if barrel == "muzzle_brake" else 1.0)
    else:
        a, b, x = r["grow"]
        k = 1.0 if n <= a else (x if n >= b else 1.0 + (x - 1.0) * (n - a) / (b - a))
        up = r["climb"] * k
    if barrel == "flash_hider":
        up *= TUNE["flash_hider"]
    if grip == "vertical":
        up *= TUNE["vertical_grip"]
    w_from, w_x = r["wide"]
    side = (r["pull"] + rng.uniform(-r["sway"], r["sway"])) * (w_x if n >= w_from else 1.0)
    if barrel == "compensator":
        side *= TUNE["compensator"]
    return side, up


# ------------------------------------------------------------------------- world
class SiegeWorld(World):
    """The arena, with Siege's two kinds of surface: hard cover stops sight and bullets; soft walls
    and floors stop sight only - a bullet goes through and arrives weaker."""

    def __init__(self, room, hard=(), soft=(), smokes=(), smoke_density=0.17):
        super().__init__(room, list(hard) + list(soft), smokes, smoke_density)
        self.soft_from = len(hard)      # boxes from this index on are soft (the renderer draws them as boarded wood)

    def ray(self, ro, rd, tmax=1e9) -> float:
        """How far a bullet travels before something hard stops it."""
        best = tmax
        for lo, hi in self.boxes[:self.soft_from]:
            t = ray_box(ro, rd, lo, hi, best)
            if t is not None and t < best:
                best = t
        return best

    def soft_before(self, ro, rd, tmax) -> int:
        """How many soft surfaces a bullet passes through in its first `tmax` metres."""
        n = 0
        for lo, hi in self.boxes[self.soft_from:]:
            t = ray_box(ro, rd, lo, hi, tmax)
            if t is not None and t < tmax:
                n += 1
        return n

    def clear(self, a, b) -> bool:
        """Can one point be seen from the other? Soft or hard, a wall is a wall to the eye."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        ln = math.sqrt(dx * dx + dy * dy + dz * dz)
        if ln < 1e-6:
            return True
        rd = (dx / ln, dy / ln, dz / ln)
        for lo, hi in self.boxes:
            t = ray_box(a, rd, lo, hi, ln)
            if t is not None and t < ln:
                return False
        return True


class SiegeDummy(Dummy):
    """A dummy with Siege's damage zones: a head that is always one bullet, a body, and limbs
    that take less. `lean` slides the head sideways (a lean-spammer); `sx, sz` is which way."""

    def __init__(self, x, z, mover=None, shooter=None, speed=2):
        super().__init__(x, z, mover, shooter, TUNE["hp"][speed])
        self.speed = speed
        self.lean = 0.0             # -1 .. 1
        self.lx, self.lz = 1.0, 0.0     # the sideways direction a lean moves the head in

    def head(self):
        k = 0.26 * self.lean
        return (self.x + self.lx * k, self.y + 1.62 - 0.46 * self.crouch - 0.04 * abs(self.lean), self.z + self.lz * k)

    def hit(self, ro, rd):
        """(distance, 'head' | 'body' | 'limb') for a ray, or None."""
        best = ray_sphere(ro, rd, self.head(), HEAD_R)
        zone = "head"
        (t0, t1, tr), (l0, l1, lr) = self.parts()
        t = ray_vcapsule(ro, rd, self.x, self.z, t0, t1, tr)
        if t is not None and (best is None or t < best):
            best, zone = t, "body"
        t = ray_vcapsule(ro, rd, self.x, self.z, l0, l1, lr)
        if t is not None and (best is None or t < best):
            best, zone = t, "limb"
        return None if best is None else (best, zone)


class SiegeShooter(Shooter):
    """A dummy that fires back. As on the Call of Duty track its aim suffers when you move across
    its line of fire - and, here, when all it can see of you is what a lean puts round a corner."""

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
            p = self.accuracy * max(0.4, 1.0 - dist / 60.0) * (1.0 - 0.5 * kit.lateral(d))
            if abs(kit.lean_t) > 0.5:
                p *= TUNE["lean_cover"]
            kit.shots_at_me += 1
            if rng.random() < p:
                kit.take_damage(TUNE["enemy_dmg"], d, now)


# --------------------------------------------------------------------------- kit
PAD_LEAN_L, PAD_LEAN_R, PAD_CROUCH, PAD_RELOAD = "LS", "RS", "B", "X"     # [game] the console defaults


class SiegeKit:
    """Per-run state for a Siege track scenario: movement, leaning, aim, the gun, health.

    There is no aim assist in here for any input device - see the notes at the top of this file."""

    assist_note = "NO AIM ASSIST  ·  SIEGE HAS NONE, ON A CONTROLLER OR A MOUSE"

    def __init__(self, game):
        from . import sens          # (here, not at the top: sens builds on this module)

        self.g = g = game
        self.scn = None
        cfg = game.cfg
        c = self.c = dict(cfg["r6"], **game.over.get("r6", {}))
        self.gun = gun_of(c)
        self.barrel, self.grip = c["barrel"], grip_of(c)
        self.speed = c["speed"] if c["speed"] in (1, 2, 3) else 2
        self.sight = sight_of(c)
        zoom = zoom_of(c, self.sight)
        g.fov, g.zoom_max = hfov(c), zoom
        # How the view turns: Siege's own settings, or the track's that every track has been told to follow.
        self.aim = sens.pad(cfg, "r6", game.over.get("sens"))
        self.rate_h, self.rate_v, self.ads_scale = self.aim.rate_h, self.aim.rate_v, self.aim.ads
        self.kbm = bool(getattr(g, "kbm", False))
        ms = sens.mouse(cfg, "r6", game.over.get("sens"))
        self.m_deg = ms.deg
        self.m_ads = ms.ads(g.fov, zoom)
        self.assist = NoAssist()            # the only kind there is on this track
        self.aim_stick = cfg["aim"]["stick"]
        self.move_stick = "left" if self.aim_stick == "right" else "right"
        self.invert = cfg["aim"]["invert_y"]
        self.dz_l = c["dz_l"] / 100.0
        self.interval = 60.0 / self.gun["rpm"]
        self.ads_time = TUNE["ads_time"][self.gun["class"]] / (TUNE["sight_1x"] if self.sight == "1x" else 1.0)
        self.reload_time = self.gun["reload"] / (TUNE["angled_grip"] if self.grip == "angled" else 1.0)
        self.walk = TUNE["walk"][self.speed] * (TUNE["horizontal_grip"] if self.grip == "horizontal" else 1.0)
        self.world = None
        self.x = self.z = 0.0
        self.vx = self.vz = 0.0
        self.area = None
        self.lmag = self.rmag = self.strafe = 0.0
        # the gun
        self.mag = self.gun["mag"] + 1  # [game] a full magazine and one in the chamber
        self._chambered = True
        self.reload_t = 0.0             # seconds of reload left (0 = ready)
        self.reloads = 0
        self.cool = 0.0
        self.spray = 0                  # bullets in the current burst (drives the recoil)
        self.last_shot = -9.0
        self.kick = 0.0
        self.rec_x = self.rec_y = 0.0   # climb and drift the sight has not been pulled back from yet
        self._tgt = None                # the dummy a bullet being scored struck (see damage)
        self._pen = 1.0                 # ...and what share of its damage got through any soft wall
        # stance
        self.lean_want = 0              # -1 left, 0 upright, +1 right
        self.lean_t = 0.0
        self.crouch_want = False
        self.crouch_t = 0.0
        self.lean_time = self.fight_t = self.move_t = 0.0
        # health
        self.hp_max = float(TUNE["hp"][self.speed])
        self.hp = self.hp_max
        self.hurt_t = -9.0
        self.hurt_from = 0.0
        self.down_until = -1.0
        self.deaths = self.shots_at_me = 0
        self.dmg_taken = 0.0
        # the ghost (ghost.py) moves, leans and crouches exactly as the player it is replaying did:
        # then this gives (x, z, vx, vz, strafe, lean, crouch) for a moment of the run
        self.body = None

    def attach(self, scn):
        self.scn = scn
        self.world = scn.world
        self.x, self.z, self.g.yaw = scn.start
        self.area = scn.move_area
        self._place_eye()

    def controls(self) -> str:
        """The Siege-only controls, in words, for the countdown screen."""
        if self.kbm:
            b, lab = self.g.binds, mousemod.label
            return (f"LEAN  {lab(b['lean_l'])} / {lab(b['lean_r'])}        CROUCH  {lab(b['crouch'])}        "
                    f"RELOAD  {lab(b['reload'])}")
        return "LEAN  L3 / R3 while aiming        CROUCH  B        RELOAD  X"

    # ---- helpers the scenarios and bots use ---------------------------------
    def lateral(self, d) -> float:
        """0-1: how fast the player is moving across this dummy's line of fire."""
        dx, dz = self.x - d.x, self.z - d.z
        ln = math.hypot(dx, dz) or 1.0
        return min(abs(self.vx * -dz / ln + self.vz * dx / ln) / TUNE["ads_walk"], 1.0)

    def damage(self, zone: str, dist: float) -> float:
        """What one bullet does: the gun's damage at this range, less for a limb, less again
        through a soft wall - and whatever is left of the target if it struck the head. In Siege
        a headshot is a kill with every gun, through walls too. [game]"""
        if zone == "head" and self._tgt is not None:
            return max(self._tgt.hp, 1)
        pts = self.gun["falloff"]
        dmg = self.gun["dmg"] * ramp(dist, ((0.0, 1.0),) + pts)
        if zone == "limb":
            dmg *= TUNE["limb"]
        return round(dmg * self._pen)

    def stick_for(self, rate: float, vertical: bool = False) -> float:
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
            self.hp = self.hp_max
            self.down_until = now + TUNE["down_time"]
            self.scn.on_down(d, now)

    def reload(self):
        if self.reload_t <= 0.0 and self.mag <= self.gun["mag"]:
            self._chambered = self.mag > 0      # [game] reloading with a round left keeps it in the chamber
            self.reload_t = self.reload_time
            self.reloads += 1
            self.spray = 0

    def _place_eye(self):
        """Eye position from where the feet are, the stance and the lean."""
        g = self.g
        yaw = math.radians(g.yaw)
        k = TUNE["lean_shift"] * self.lean_t
        h = TUNE["eye"] - (TUNE["eye"] - TUNE["eye_crouch"]) * self.crouch_t - 0.05 * abs(self.lean_t)
        ex, ez = self.x + math.cos(yaw) * k, self.z + math.sin(yaw) * k
        if k and self.world:                # a lean never puts the eye inside the wall you are leaning round
            ex, ez = self.world.push_out(ex, ez, 0.06)
        g.eye = (ex, h, ez)
        g.roll = TUNE["lean_roll"] * self.lean_t

    # ---- per frame ----------------------------------------------------------
    def _keys(self, events, off, down):
        """Mouse and keyboard: turn the view by this frame's counts in the order they arrived, and
        read the buttons. -> (when Shoot was pressed or None, the view at that instant or None).

        One count is `m_deg` degrees from the hip and `m_deg * m_ads` through the sights. Nothing
        else touches the view: no acceleration, no smoothing, no assist."""
        g, scn, m, b = self.g, self.scn, self.g.mouse, self.g.binds
        shoot, ads = b["shoot"], b["ads"]
        aiming = g.ads_held
        up = 1.0 if self.invert else -1.0
        turned = pulled = 0.0
        pressed_at = view = None
        for t, kind, a, v in events:
            if kind == 0:                       # mouse.MOTION
                if not down:
                    k = self.m_deg * (self.m_ads if aiming else 1.0)
                    g.yaw += a * k
                    dp = v * k * up
                    g.pitch = max(-85.0, min(85.0, g.pitch + dp))
                    pulled += dp
                    turned += math.hypot(a * k * math.cos(math.radians(g.pitch)), v * k)
            elif kind == 1:                     # mouse.BUTTON: a button or a key
                if a == shoot:
                    if v and pressed_at is None:
                        pressed_at, view = t - off, (g.yaw, g.pitch)
                elif a == ads:
                    aiming = bool(v)
                    scn.on_ads(t - off, aiming)
                elif v and a == b["crouch"]:
                    self.crouch_want = not self.crouch_want
                elif v and a == b["reload"]:
                    self.reload()
        if not -180.0 <= g.yaw < 180.0:
            g.yaw = (g.yaw + 180.0) % 360.0 - 180.0
        g.fire_held = m.down(shoot) and not down
        g.ads_held = m.down(ads)
        self.lean_want = (1 if m.down(b["lean_r"]) else 0) - (1 if m.down(b["lean_l"]) else 0)     # held, as on PC
        g.aim_raw = (0.0, 0.0)
        self.rmag = 1.0 if turned else 0.0
        g.frame_turn = turned
        return pressed_at, view, pulled

    def _pad(self, events, off, down):
        """A controller: Shoot and Aim as bound; lean on the stick clicks while aiming (a click
        leans, the same click or letting go of Aim stands you back up), B crouches, X reloads -
        unless one of those is what Shoot or Aim is bound to."""
        g, pad, scn = self.g, self.g.pad, self.scn
        binds = g.cfg["binds"]
        shoot, ads = binds["shoot"], binds["ads"]
        taken = {shoot, ads}
        pressed_at = None
        for t, name, is_down in events:
            if name == shoot:
                if is_down:
                    pressed_at = t - off
            elif name == ads:
                scn.on_ads(t - off, is_down)
            elif is_down and name not in taken:
                if name == PAD_LEAN_L:
                    self.lean_want = 0 if self.lean_want == -1 else -1
                elif name == PAD_LEAN_R:
                    self.lean_want = 0 if self.lean_want == 1 else 1
                elif name == PAD_CROUCH:
                    self.crouch_want = not self.crouch_want
                elif name == PAD_RELOAD:
                    self.reload()
        g.fire_held = pad.down(shoot) and not down
        g.ads_held = pad.down(ads)
        if not g.ads_held:
            self.lean_want = 0              # [game] on a controller you lean only while aiming
        return pressed_at

    def step(self, dt, events, off):
        g, scn = self.g, self.scn
        now = g.t
        down = now < self.down_until
        view = None
        pulled = 0.0
        pitch0, yaw0 = g.pitch, g.yaw
        if self.kbm:
            pressed_at, view, pulled = self._keys(events, off, down)
        else:
            pressed_at = self._pad(events, off, down)

        # 1. the gun: bullets are judged against the view that was on screen (a click: the view at the click)
        if self.reload_t > 0.0:
            self.reload_t -= dt
            if self.reload_t <= 0.0:
                self.reload_t, self.mag = 0.0, self.gun["mag"] + (1 if self._chambered else 0)
        self.cool -= dt
        fired = False
        if (g.fire_held or pressed_at is not None) and not down and self.reload_t <= 0.0:
            first = True
            while self.cool <= 0.0 and self.mag > 0:
                self._bullet(pressed_at if (first and pressed_at is not None) else now, view if first else None)
                self.cool += self.interval
                first, fired = False, True
        if self.mag <= 0:
            self.reload()                   # [trainer] an empty magazine reloads itself
        if self.cool < 0.0 and not fired:
            self.cool = 0.0
        if now - self.last_shot > 0.25:
            self.spray = 0
        self.kick = max(0.0, self.kick - dt * 9.0)

        # 2. sights, stance, lean
        step = dt / self.ads_time
        g.ads_t += max(-step, min(step, (1.0 if g.ads_held else 0.0) - g.ads_t))
        if self.body is not None:
            self.x, self.z, self.vx, self.vz, self.strafe, self.lean_t, self.crouch_t = self.body(now)
            self.lmag = self.strafe
        else:
            step = dt / TUNE["stance_time"]
            self.crouch_t += max(-step, min(step, (1.0 if self.crouch_want else 0.0) - self.crouch_t))
            step = dt / TUNE["lean_time"]
            self.lean_t += max(-step, min(step, float(self.lean_want if not down else 0) - self.lean_t))

        # 3. movement: the left stick, or the movement keys (all or nothing, eight directions)
        if self.body is None:
            if self.kbm:
                m, b = g.mouse, g.binds
                mx = (1.0 if m.down(b["right"]) else 0.0) - (1.0 if m.down(b["left"]) else 0.0)
                my = (1.0 if m.down(b["forward"]) else 0.0) - (1.0 if m.down(b["back"]) else 0.0)
                if mx and my:
                    mx, my = mx * 0.7071, my * 0.7071
                self.lmag = 1.0 if (mx or my) else 0.0
            else:
                mx, my = g.pad.stick(self.move_stick)
                mx, my, self.lmag = cod_shape(mx, my, self.dz_l, 0.95, None)
            if down or not scn.can_move:
                mx = my = self.lmag = 0.0
            self.strafe = abs(mx)
            yaw = math.radians(g.yaw)
            sy, cy = math.sin(yaw), math.cos(yaw)
            vmax = self.walk + (min(TUNE["ads_walk"], self.walk) - self.walk) * g.ads_t
            vmax *= 1.0 + (TUNE["crouch_walk"] - 1.0) * self.crouch_t
            tx = (cy * mx + sy * my) * vmax
            tz = (sy * mx - cy * my) * vmax
            k = min(dt / TUNE["accel"], 1.0)
            self.vx += (tx - self.vx) * k
            self.vz += (tz - self.vz) * k
            nx, nz = self.world.push_out(self.x + self.vx * dt, self.z + self.vz * dt, 0.35, self.area)
            if dt > 0.0:
                self.vx, self.vz = (nx - self.x) / dt, (nz - self.z) / dt
            self.x, self.z = nx, nz

        # 4. aim. A mouse has already turned the view, count for count. A controller: stick ->
        #    deadzone and curve -> sensitivity. That is all there is; nothing here helps.
        if not self.kbm:
            rx, ry = g.pad.stick(self.aim_stick)
            g.aim_raw = (rx, ry)
            ax, ay, self.rmag = self.aim.shape(rx, ry)
            scale = self.ads_scale if g.ads_held else 1.0
            wy = ax * self.rate_h * scale
            wp = (-ay if self.invert else ay) * self.rate_v * scale
            if down:
                wy = wp = 0.0
            if wy or wp:        # (an untouched stick leaves the view bit-for-bit where it was)
                g.yaw += wy * dt
                if not -180.0 <= g.yaw < 180.0:
                    g.yaw = (g.yaw + 180.0) % 360.0 - 180.0
                g.pitch = max(-85.0, min(85.0, g.pitch + wp * dt))
            pulled = wp * dt
            g.frame_turn = math.hypot(wy * math.cos(math.radians(g.pitch)), wp) * dt

        # 5. recoil settles. Pulling down against the climb uses the climb up, so what recovers
        #    afterwards is only the part you left - a tap comes back to where it was aimed, a
        #    spray you held down does not dive.
        if pulled < 0.0 and self.rec_y > 0.0:
            self.rec_y = max(0.0, self.rec_y + pulled)
        if now - self.last_shot > TUNE["recover_after"] and (self.rec_y > 1e-4 or abs(self.rec_x) > 1e-4):
            k = min(TUNE["recover_rate"] * dt, 1.0)
            g.pitch -= self.rec_y * k
            g.yaw -= self.rec_x * k
            self.rec_y -= self.rec_y * k
            self.rec_x -= self.rec_x * k
        if dt > 0.0:
            g.yaw_rate = ((g.yaw - yaw0 + 180.0) % 360.0 - 180.0) / dt
            g.pitch_rate = (g.pitch - pitch0) / dt
            g.turn_rate = g.frame_turn / dt
        self._place_eye()
        g._update_cam()

        # 6. bookkeeping, and health: no regeneration in Siege - it comes back between fights
        if g.fire_held and scn.fighting():
            self.fight_t += dt
            if self.strafe >= 0.25:
                self.move_t += dt
            if abs(self.lean_t) > 0.5:
                self.lean_time += dt
        if self.hp < self.hp_max and now - self.hurt_t > TUNE["calm_after"]:
            self.hp = self.hp_max

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
        self._tgt, self._pen = None, 1.0
        if best is not None:
            self._tgt = best[2]
            self._pen = TUNE["soft_pen"] ** self.world.soft_before(g.eye, rd, best[0])
        self.mag -= 1
        scn.on_bullet(t, best)
        self._tgt = None
        # recoil: the view kicks; pulling it back is the player's job
        side, up = recoil_step(self.gun, self.spray, g.rng, self.barrel, self.grip)
        g.pitch = min(85.0, g.pitch + up)
        g.yaw += side
        self.rec_y += up
        self.rec_x += side
        self.spray += 1
        self.last_shot = g.t
        self.kick = 1.0

    # ---- HUD ----------------------------------------------------------------
    def draw(self, ui):
        g = self.g
        W, cx = ui.W, ui.W / 2
        now = g.t
        hp = max(self.hp, 0.0) / self.hp_max
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
        # the magazine
        full = self.gun["mag"]
        ui.text(W - 56, 968, self.gun["name"] + "  ·  " + self.sight, "s", DIM, 2)
        if self.reload_t > 0.0:
            k = 1.0 - self.reload_t / self.reload_time
            ui.text(W - 56, 990, "RELOADING", "l", WARN, 2)
            ui.rect(cx - 110, 620, 220, 6, (0.0, 0.0, 0.0, 0.5))
            ui.rect(cx - 110, 620, 220 * k, 6, WARN)
        else:
            ui.text(W - 56, 990, f"{self.mag}  /  {full} + 1", "l", WHITE if self.mag > full * 0.25 else WARN, 2)
        if abs(self.lean_t) > 0.05 or self.crouch_t > 0.05:
            bits = (["LEAN " + ("RIGHT" if self.lean_t > 0 else "LEFT")] if abs(self.lean_t) > 0.05 else []) \
                + (["CROUCHED"] if self.crouch_t > 0.5 else [])
            ui.text(330, 996, "  ·  ".join(bits), "s", DIM)
        ui.text(W - 56, 82, "no aim assist · Siege", "s", FAINT, 2)

    def summary(self) -> dict:
        """Kit-level stats merged into the scenario's own. No aim-assist numbers: there is none to measure."""
        ft = self.fight_t
        return {
            "strafe_pct": round(100.0 * self.move_t / ft, 1) if ft > 0 else None,
            "lean_pct": round(100.0 * self.lean_time / ft, 1) if ft > 0 else None,
            "deaths": self.deaths, "damage_taken": round(self.dmg_taken), "reloads": self.reloads,
            "weapon": self.gun["name"],
        }
