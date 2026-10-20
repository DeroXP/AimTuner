"""The Siege track: 24 scenarios, three for each of eight skills a Siege gunfight is made of.

Every one of them uses the Siege kit (r6kit.py): Siege's sensitivity scales and field of
view, a real gun from the game with its own recoil, a head that is always one bullet,
soft walls that bullets go through, leaning, and close indoor distances (Ubisoft's own
figure for the average engagement is 10 m).

NO AIM ASSIST. Siege has none in its multiplayer, on a controller or a mouse, so no
scenario here has any and none can: `aa` is False on the base class, and a subclass that
set it would be refused the moment Python read its definition (__init_subclass__).

A scenario is mostly configuration on top of SiegeScenario: the walls, where dummies
appear, how they move, whether they shoot back, and which numbers matter. The `bot_*`
methods tell the self-test bots what a player would do with their feet and shoulders.
"""
from __future__ import annotations

import math

from .codkit import HEAD_R, Mover, Peek, Runner, Rush, Strafe
from .codscn import M, CodScenario
from .r6kit import SiegeDummy, SiegeKit, SiegeShooter, SiegeWorld
from .scenarios import _med, _r
from .theme import BAD, GOOD, WARN, alpha

M6 = dict(M, **{
    "hs_kill_pct": ("Kills by headshot", "%", 1, 1),
    "one_tap_pct": ("Kills with a single bullet", "%", 1, 1),
    "exposure_median_ms": ("Time in their sights per peek", "ms", -1, 0),
    "lean_pct": ("Fight time spent leaning", "%", 1, 1),
    "caught_pct": ("Crossings you hit", "%", 1, 1),
    "found_median_ms": ("Appeared to in your sights", "ms", -1, 0),
    "clear_median_ms": ("Time to clear a room", "ms", -1, 0),
    "rooms": ("Rooms cleared", "", 1, 0),
    "damage_taken": ("Damage taken", "", -1, 0),
    "reloads": ("Reloads", "", -1, 0),
})


def mt(*keys):
    return tuple((k,) + M6[k] for k in keys)


H = 3.0     # how tall a wall is, m


def wall(x0, x1, z, thick=0.4, y0=0.0, y1=H):
    """A wall running left-right at depth z."""
    return ((x0, y0, z - thick / 2), (x1, y1, z + thick / 2))


def side(x, z0, z1, thick=0.4, y0=0.0, y1=H):
    """A wall running front-back at sideways position x."""
    return ((x - thick / 2, y0, min(z0, z1)), (x + thick / 2, y1, max(z0, z1)))


class SiegeScenario(CodScenario):
    cod = False
    kit_cls = SiegeKit
    track = "r6"
    aa = False              # locked: see the notes at the top, and __init_subclass__ below
    show_meter = False
    metrics = mt("score", "kills", "accuracy_pct", "hs_kill_pct", "kill_median_ms", "bullets_per_kill")

    room = ((-14.0, 0.0, -32.0), (14.0, 9.0, 8.0))     # a tall hall; the 3 m walls in it make the rooms
    move_area = (-4.0, 4.0, -1.5, 3.0)
    dist = (7.0, 14.0)                  # most Siege fights are inside 15 m
    spread = 28.0
    speeds = (1, 2, 3)                  # the operator speeds dummies come in: 125 / 110 / 100 health
    xray = False                        # the scenario shows dummies through walls (a ping): they count as in view
    stances = (0.0,)                    # crouch amounts a dummy may spawn in (0 standing, 1 crouched)

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if cls.__dict__.get("aa"):
            raise TypeError(f"{cls.__name__}: the Siege track has no aim assist, and a scenario cannot add any")

    def __init__(self, g):
        super().__init__(g)
        self.hs_kills = self.one_taps = 0
        self.peeks: list = []           # how long the player was in a dummy's view, each time (ms)
        self._peek_t0 = None

    def build(self) -> SiegeWorld:
        return SiegeWorld(self.room)

    def make_dummy(self, x, z):
        sb = self.shoot_back
        d = SiegeDummy(x, z, self.mover(), SiegeShooter(*sb) if sb else None, self.rng.choice(self.speeds))
        d.crouch = self.rng.choice(self.stances)
        return d

    def update(self, dt, now):
        super().update(dt, now)
        if self.xray:               # pinged through a wall: as good as seen, for the bookkeeping and the bots
            for d in self.dummies:
                s = d.s
                if s["seen"] is None:
                    s["seen"] = now
                    s["pre"], s["level"] = self._offsets(d)
                s["vis"] = True
        # exposure: each unbroken stretch with a dummy able to see you is one peek
        seen = any(d.s["vis"] for d in self.dummies) and not self.xray
        if seen and self._peek_t0 is None:
            self._peek_t0 = now
        elif not seen and self._peek_t0 is not None:
            self.peeks.append((now - self._peek_t0) * 1000.0)
            self._peek_t0 = None

    def landed(self, d, t, zone):
        d.s["zone"] = zone

    def kill(self, d, t):
        if d.s.get("zone") == "head":
            self.hs_kills += 1
        if d.s["shots"] == 1:
            self.one_taps += 1
        super().kill(d, t)

    def summary(self):
        s = super().summary()
        k = self.kills
        s["hs_kill_pct"] = round(100.0 * self.hs_kills / k, 1) if k else None
        s["one_tap_pct"] = round(100.0 * self.one_taps / k, 1) if k else None
        s["exposure_median_ms"] = _r(_med(self.peeks))
        for key in list(s):                 # nothing on this track is assisted: no such numbers, not even empty ones
            if key.startswith(("aa_", "acc_assist", "acc_raw")):
                del s[key]
        return s

    # ---- what the self-test bots do with the rest of the controls ----------------
    def bot_lean(self, g) -> int:
        return 0

    def ping(self, ui, g, d, col=BAD):
        """Mark a dummy the player cannot see: a bracket where it stands, a dot at its head."""
        cam = g.cam
        top, feet = cam.project(d.head()), cam.project((d.x, d.y, d.z))
        if top is None or feet is None:
            return
        x, y0 = (top[0] * 0.5 + 0.5) * ui.W, (0.5 - top[1] * 0.5) * ui.H
        y1 = (0.5 - feet[1] * 0.5) * ui.H
        if not (-200.0 < x < ui.W + 200.0):
            return
        w = max(abs(y1 - y0) * 0.16, 5.0)
        c = alpha(col, 0.85)
        for sx in (-1.0, 1.0):
            ui.line(x + sx * w, y0, x + sx * w, y1, 2.0, c)
            ui.line(x + sx * w, y0, x + sx * w * 0.4, y0, 2.0, c)
            ui.line(x + sx * w, y1, x + sx * w * 0.4, y1, 2.0, c)
        ui.disc(x, y0, max(w * 0.35, 3.0), c)


# ========================================================= crosshair placement
class HeadHeight(SiegeScenario):
    id, group, name = "r6_place_height", "Crosshair placement", "Head Height"
    tagline = "Standing or crouched, near or far. Is your crosshair already where the head is?"
    how = ("Dummies appear standing or crouched, 6 to 18 m out. Between kills, park your crosshair at standing "
           "head height. One bullet to the head kills, as always in Siege.")
    measures = ("Crosshair height discipline: how many degrees off head level you were the instant each one "
                "appeared. In Siege that gap is the whole fight - a head is one bullet, a body is three or four.")
    primary = "level_median_deg"
    metrics = mt("level_median_deg", "hs_kill_pct", "first_hit_median_ms", "kills", "accuracy_pct", "score")
    dist = (6.0, 18.0)
    spread = 24.0
    respawn = (0.5, 1.0)
    stances = (0.0, 0.0, 1.0)


class SliceThePie(SiegeScenario):
    id, group, name = "r6_place_slice", "Crosshair placement", "Slice the Pie"
    tagline = "Clear a corner an angle at a time. Someone is behind it, and they shoot."
    how = ("Start behind the wall. Step or lean out to the right until a dummy comes into view, kill it, then "
           "get back behind the wall for the next. They fire about a second after they see you.")
    measures = ("Pre-aiming a corner: how far your crosshair was from the dummy the instant it came into view, "
                "and how fast the first hit followed.")
    primary = "preaim_median_deg"
    metrics = mt("preaim_median_deg", "first_hit_median_ms", "kills", "hs_kill_pct", "deaths", "score")
    start = (-1.0, 0.0, 0.0)
    move_area = (-3.0, 5.0, -1.2, 2.0)
    respawn = (0.2, 0.4)
    shoot_back = ((0.85, 1.25), 0.45)
    stances = (0.0, 0.0, 1.0)
    SPOTS = ((1.6, -7.0), (-0.5, -9.0), (-2.5, -11.0), (-4.5, -13.0), (-7.0, -15.0), (0.8, -12.0))

    def build(self):
        return SiegeWorld(self.room, [wall(-14.0, 0.6, -3.0)])

    def can_spawn(self, now):
        return self.kit.x < -0.2 and abs(self.kit.lean_t) < 0.2      # only once you are back behind the wall

    def place(self, now):
        return self.rng.choice(self.SPOTS)

    def bot_move(self, g):
        if self.dummies:
            return (1.0, 0.0) if not self.dummies[0].s["vis"] and self.kit.x < 4.5 else (0.0, 0.0)
        return (-1.0, 0.0) if self.kit.x > -0.6 else (0.0, 0.0)

    def draw(self, ui, g):
        if not self.dummies and not self.can_spawn(g.t):
            ui.text(ui.W / 2, 640, "BACK BEHIND THE WALL", "m", WARN, 1)


class PingPreAim(SiegeScenario):
    id, group, name = "r6_place_ping", "Crosshair placement", "Pre-aim the Ping"
    tagline = "You know where they are. Put your crosshair where they will be."
    how = ("A red marker shows a dummy waiting beside one of two doorways. A moment later it steps into the "
           "doorway. Pre-aim that door at head height and take it as it appears.")
    measures = ("Using information: how close your crosshair already was when the dummy stepped out, and how "
                "many of them died to a single bullet.")
    primary = "preaim_median_deg"
    metrics = mt("preaim_median_deg", "one_tap_pct", "first_hit_median_ms", "kills", "hs_kill_pct", "score")
    DOORS = (-4.5, 4.5)
    WALL_Z = -11.0
    respawn = (0.5, 1.0)
    timeout = 7.0

    def build(self):
        edges = [-14.0] + [e for x in self.DOORS for e in (x - 0.7, x + 0.7)] + [14.0]
        return SiegeWorld(self.room, [wall(edges[i], edges[i + 1], self.WALL_Z) for i in range(0, len(edges), 2)])

    def place(self, now):
        self._door = self.rng.choice(self.DOORS)
        self._side = self.rng.choice((-1.0, 1.0))
        return self._door + self._side * 1.9, self.WALL_Z - 1.2

    def mover(self):
        return Peek(reach=1.9, out=(1.6, 2.4), hidden=(1.1, 2.0), speed=3.2)

    def spawn(self, now):
        d = super().spawn(now)
        m = d.mover
        m.sx, m.sz, m.dir = -self._side, 0.0, 1.0          # step toward the doorway it waits beside
        m.next = now + self.rng.uniform(1.1, 2.0)           # ...after you have had time to read the ping
        return d

    def puppet_spawned(self, d):
        self._door = min(self.DOORS, key=lambda x: abs(x - d.x))

    def live(self):
        """The bots hold the doorway the ping is beside, as a player would."""
        out = super().live()
        if not out and self.dummies:
            return (((self._door, 1.55, self.WALL_Z - 1.2), 0.0),)
        return out

    def draw(self, ui, g):
        for d in self.dummies:
            if not d.s["vis"]:
                self.ping(ui, g, d)


# ============================================================== holding angles
class PixelAngle(SiegeScenario):
    id, group, name = "r6_hold_pixel", "Holding angles", "Pixel Angle"
    tagline = "A gap a metre wide. They cross it at a run."
    how = ("Hold your crosshair on the gap in the wall at head height. Dummies run past behind it - you get "
           "about a third of a second each time. Hit one and it counts; kill it and the next comes.")
    measures = ("Holding a tight angle: how many crossings you put a bullet into. It is reaction and "
                "placement, not tracking - the crosshair should already be there.")
    primary = "caught_pct"
    grade = "caught_pct"
    metrics = mt("caught_pct", "kills", "hs_kill_pct", "accuracy_pct", "first_hit_median_ms", "score")
    WALL_Z, GAP, LANE = -12.0, 1.0, -14.6
    respawn = (0.6, 1.2)

    def __init__(self, g):
        super().__init__(g)
        self.crossings = self.caught = 0

    def build(self):
        return SiegeWorld(self.room, [wall(-14.0, -self.GAP / 2, self.WALL_Z), wall(self.GAP / 2, 14.0, self.WALL_Z)])

    def place(self, now):
        return 0.0, self.LANE

    def mover(self):
        return Runner(self.rng.uniform(3.2, 4.4), 4.5, (0.5, 1.4))

    def spawn(self, now):
        d = super().spawn(now)
        m = d.mover                     # straight across behind the gap, starting out of sight at one end
        m.sx, m.sz, m.ox, m.oz = 1.0, 0.0, 0.0, self.LANE
        m.off = -m.dir * m.span
        d.x, d.z = m.ox + m.off, m.oz
        d.s["cross"] = False
        return d

    def puppet_spawned(self, d):
        d.s["cross"] = False

    def tick(self, d, dt, now):
        s = d.s
        if s["vis"] and not s["cross"]:
            s["cross"], s["cross_hit"] = True, False
            self.crossings += 1
        elif not s["vis"]:
            s["cross"] = False

    def landed(self, d, t, zone):
        super().landed(d, t, zone)
        if not d.s.get("cross_hit"):
            d.s["cross_hit"] = True
            self.caught += 1

    def live(self):
        out = super().live()
        return out or (((0.0, 1.45, self.LANE), 0.0),)        # the bots hold the gap

    def hud(self):
        return (("CAUGHT", f"{self.caught} / {self.crossings}"), ("KILLS", str(self.kills)))

    def _score(self):
        return round(1000.0 * self.caught / self.crossings) if self.crossings else 0

    def summary(self):
        s = super().summary()
        s["caught_pct"] = round(100.0 * self.caught / self.crossings, 1) if self.crossings else None
        return s


class DoorHold(SiegeScenario):
    id, group, name = "r6_hold_door", "Holding angles", "Door Hold"
    tagline = "One doorway, ten metres. They swing it, and they shoot."
    how = ("Hold the doorway. A dummy swings out from one edge or the other, fires, and ducks back. You hear "
           "each one coming. Be on the edge it comes from, at head height.")
    measures = "Winning the swing: time from the dummy showing to your first hit, and how many duels you win."
    primary = "win_pct"
    metrics = mt("win_pct", "first_hit_median_ms", "kills", "hs_kill_pct", "deaths", "preaim_median_deg")
    WALL_Z = -10.0
    shoot_back = ((0.45, 0.70), 0.55)
    respawn = (0.7, 1.4)
    timeout = 9.0
    move_area = (-2.0, 2.0, -1.0, 2.0)

    def build(self):
        return SiegeWorld(self.room, [wall(-14.0, -0.8, self.WALL_Z), wall(0.8, 14.0, self.WALL_Z)])

    def place(self, now):
        self._side = self.rng.choice((-1.0, 1.0))
        return self._side * 1.7, self.WALL_Z - 1.1

    def mover(self):
        return Peek(reach=1.25, out=(0.7, 1.2), hidden=(0.7, 1.5), speed=4.6)

    def spawn(self, now):
        d = super().spawn(now)
        d.mover.sx, d.mover.sz, d.mover.dir = -self._side, 0.0, 1.0
        return d

    def puppet_spawned(self, d):
        self._side = 1.0 if d.x > 0.0 else -1.0

    def live(self):
        out = super().live()
        if not out and self.dummies:
            return (((self._side * 0.5, 1.5, self.WALL_Z - 1.1), 0.0),)
        return out


class TwoDoors(SiegeScenario):
    id, group, name = "r6_hold_two", "Holding angles", "Two Doors"
    tagline = "Two doorways, and you can only watch one."
    how = ("A dummy steps into the left doorway or the right one, then back out. Some of them shoot - those "
           "you hear. Hold a sensible angle and be ready to snap to the other.")
    measures = "Covering two angles: the snap from the door you were holding to the one they took."
    primary = "first_hit_median_ms"
    metrics = mt("first_hit_median_ms", "kills", "hs_kill_pct", "preaim_median_deg", "deaths", "score")
    DOORS = (-4.0, 4.0)
    WALL_Z = -10.0
    shoot_back = ((0.8, 1.2), 0.45)
    respawn = (0.5, 1.1)
    timeout = 7.0

    def build(self):
        edges = [-14.0] + [e for x in self.DOORS for e in (x - 0.8, x + 0.8)] + [14.0]
        return SiegeWorld(self.room, [wall(edges[i], edges[i + 1], self.WALL_Z) for i in range(0, len(edges), 2)])

    def place(self, now):
        door = self.rng.choice(self.DOORS)
        self._side = self.rng.choice((-1.0, 1.0))
        return door + self._side * 1.9, self.WALL_Z - 1.2

    def mover(self):
        return Peek(reach=1.9, out=(1.0, 1.7), hidden=(0.7, 1.4), speed=4.2)

    def spawn(self, now):
        d = super().spawn(now)
        d.mover.sx, d.mover.sz, d.mover.dir = -self._side, 0.0, 1.0
        if self.rng.random() < 0.5:
            d.shooter = None                    # only some of them fire
        return d


# ===================================================================== peeking
class _Peek(SiegeScenario):
    """You are behind cover and they are not. What is measured is how little time you give them."""
    primary = "exposure_median_ms"
    grade = None
    metrics = mt("exposure_median_ms", "kills", "deaths", "hs_kill_pct", "lean_pct", "damage_taken")
    stances = (0.0, 0.0, 1.0)

    def _score(self):
        # kills are the headline; each time you were downed costs two of them
        return max(0, round((self.kills - 2 * self.kit.deaths) * 100 * (0.4 + 0.6 * self._accuracy() / 100.0)))


class LeanPeek(_Peek):
    id, group, name = "r6_peek_lean", "Peeking", "Lean Peek"
    tagline = "Your feet do not move. Lean out, take the shot, lean back."
    how = ("You are tucked behind the end of a wall. Dummies downrange can only be seen by leaning right. Lean "
           "out, kill, and come back - anything left in view of you for half a second starts shooting.")
    measures = ("The lean peek: how long you were in their sights each time you leaned out. Short peeks and "
                "one-bullet kills are the whole point.")
    can_move = False
    shoot_back = ((0.50, 0.75), 0.60)
    respawn = (0.5, 1.0)
    dist = (7.0, 14.0)
    SIDE = 1

    def build(self):
        s = self.SIDE
        return SiegeWorld(self.room, [((min(-3.0 * s, 0.42 * s), 0.0, -1.4), (max(-3.0 * s, 0.42 * s), H, -1.0))])

    def place(self, now):
        d = self.rng.uniform(*self.dist)
        x = self.rng.uniform(0.06 * d + 0.75, 0.40 * d - 0.5)      # hidden upright, in view from a full lean
        return self.SIDE * x, -d

    def bot_lean(self, g):
        return self.SIDE if self.dummies else 0

    def draw(self, ui, g):
        if self.dummies and abs(self.kit.lean_t) < 0.3 and not any(d.s["vis"] for d in self.dummies):
            ui.text(ui.W / 2, 640, "LEAN " + ("RIGHT" if self.SIDE > 0 else "LEFT"), "m", WARN, 1)


class QuickPeek(_Peek):
    id, group, name = "r6_peek_quick", "Peeking", "Quick Peek"
    tagline = "Step out, shoot, step back. They are faster than a lean is."
    how = ("A pillar hides you from the dummies behind it. Sidestep out past it, kill, and get back in - they "
           "open fire in under half a second, and they do not miss much.")
    measures = ("The strafe peek: time in their sights per peek, and what it cost you in health. Come out "
                "already aimed at head height and it is one bullet.")
    shoot_back = ((0.32, 0.48), 0.70)
    respawn = (0.7, 1.3)
    move_area = (-2.3, 2.3, -0.4, 0.8)

    def build(self):
        return SiegeWorld(self.room, [((-0.8, 0.0, -1.7), (0.8, H, -1.1))])

    def can_spawn(self, now):
        return abs(self.kit.x) < 0.45               # only while you are tucked in behind the pillar

    def place(self, now):
        d = self.rng.uniform(8.0, 14.0)
        return self.rng.uniform(-0.09 * d, 0.09 * d), -d

    def bot_move(self, g):
        k = self.kit
        if self.dummies:
            d = self.dummies[0]
            if not d.s["vis"]:
                return (1.0 if d.x >= 0.0 else -1.0, 0.0)
            return (0.0, 0.0)
        return (-math.copysign(1.0, k.x), 0.0) if abs(k.x) > 0.25 else (0.0, 0.0)

    def draw(self, ui, g):
        if not self.dummies and not self.can_spawn(g.t):
            ui.text(ui.W / 2, 640, "BACK BEHIND THE PILLAR", "m", WARN, 1)


class BothShoulders(_Peek):
    id, group, name = "r6_peek_side", "Peeking", "Both Shoulders"
    tagline = "Left or right? The sound tells you. Lean the right way."
    how = ("A narrow pillar is in front of you. A dummy appears on one side, announced by a tone in that ear. "
           "Lean that way, kill it, come back. Lean the wrong way and you see nothing.")
    measures = ("Peeking off either shoulder: time from the dummy appearing to you having it in your sights, "
                "and how long each peek left you exposed.")
    primary = "found_median_ms"
    metrics = mt("found_median_ms", "exposure_median_ms", "kills", "deaths", "hs_kill_pct", "lean_pct")
    can_move = False
    shoot_back = ((0.60, 0.90), 0.55)
    respawn = (0.5, 1.0)
    dist = (7.0, 13.0)

    def build(self):
        return SiegeWorld(self.room, [((-0.42, 0.0, -1.4), (0.42, H, -1.0))])

    def place(self, now):
        d = self.rng.uniform(*self.dist)
        self._side = self.rng.choice((-1, 1))
        return self._side * self.rng.uniform(0.06 * d + 0.75, 0.40 * d - 0.5), -d

    def bot_lean(self, g):
        return self._side if self.dummies else 0

    def puppet_spawned(self, d):
        self._side = 1 if d.x > 0.0 else -1

    def announce(self, d):
        """Always from its own side (the usual rule calls anything within 12 degrees "ahead"), and
        said on screen as well, for anyone playing with the sound off."""
        self.g.sfx("threat_r" if self._side > 0 else "threat_l")
        self.g.note("RIGHT  ►" if self._side > 0 else "◄  LEFT", WARN, 0.7)

    def summary(self):
        s = super().summary()
        s["found_median_ms"] = _r(_med([e.get("found_ms") for e in self.events]))
        return s

    def kill(self, d, t):
        found = None if d.s["seen"] is None else (d.s["seen"] - d.s["t0"]) * 1000.0
        super().kill(d, t)
        self.events[-1]["found_ms"] = _r(found)


# ==================================================================== one-taps
class OneTap(SiegeScenario):
    id, group, name = "r6_tap_one", "One-taps", "One Tap"
    head_only = True
    hold_fire = False
    bot_head = True
    tagline = "Heads only. One bullet each."
    how = ("Dummies appear standing or crouched within 16 m. Only headshots count - and in Siege one is all it "
           "takes. Tap, do not spray.")
    measures = "First-bullet headshots: what share of your bullets were headshots, and how fast each kill came."
    primary = "head_acc_pct"
    grade = "head_acc_pct"
    metrics = mt("head_acc_pct", "kills", "one_tap_pct", "kill_median_ms", "level_median_deg", "score")
    dist = (6.0, 16.0)
    spread = 30.0
    respawn = (0.3, 0.6)
    stances = (0.0, 0.0, 1.0)


class HeadSwap(SiegeScenario):
    id, group, name = "r6_tap_swap", "One-taps", "Head Swap"
    head_only = True
    hold_fire = False
    bot_head = True
    tagline = "Three heads up. One bullet, next head."
    how = "Three dummies stand in front of you. Headshot one and snap to the next; each kill brings a replacement."
    measures = "Head-to-head transfers: the time from a kill to your first hit on the next head."
    primary = "swap_median_ms"
    grade = "head_acc_pct"
    metrics = mt("swap_median_ms", "kills", "head_acc_pct", "one_tap_pct", "score")
    alive_max = 3
    respawn = (0.15, 0.35)
    dist = (7.0, 13.0)
    spread = 30.0
    stances = (0.0, 0.0, 1.0)

    def place(self, now):
        for _ in range(40):
            x, z = super().place(now)
            if all(math.hypot(x - o.x, z - o.z) > 2.2 for o in self.dummies):
                break
        return x, z


class FlankCheck(SiegeScenario):
    id, group, name = "r6_tap_flank", "One-taps", "Flank Check"
    tagline = "Footsteps beside you. Turn, find the head, end it."
    how = ("A dummy appears well off to one side or behind you, with a tone in the ear on that side. Turn and "
           "kill it before it kills you - it fires about a second after it sees you.")
    measures = "The big turn into a precise stop: time to first hit after a flank, and how many you survive."
    primary = "first_hit_median_ms"
    metrics = mt("first_hit_median_ms", "win_pct", "kills", "hs_kill_pct", "deaths", "accuracy_pct")
    start = (0.0, -12.0, 0.0)
    move_area = (-3.0, 3.0, -15.0, -9.0)
    dist = (5.0, 10.0)
    shoot_back = ((0.90, 1.25), 0.50)
    respawn = (0.6, 1.2)

    def place(self, now):
        return self._polar(self.g.yaw + self.rng.choice((-1.0, 1.0)) * self.rng.uniform(70.0, 165.0),
                           self.rng.uniform(*self.dist))


# ============================================================== recoil control
class MagDump(SiegeScenario):
    id, group, name = "r6_recoil_dump", "Recoil control", "Mag Dump"
    tagline = "An armoured dummy and a full magazine. Keep every bullet on it."
    how = ("This dummy takes most of a magazine to the body, and its head counts as body. Hold the trigger and "
           "hold the spray on it. The gun, barrel and grip are the ones chosen in Settings.")
    measures = ("Raw recoil control for the gun you picked: what share of a sustained spray stays on a "
                "man-sized target. Change the gun in Settings and this number changes with it.")
    primary = "accuracy_pct"
    metrics = mt("accuracy_pct", "kills", "bullets_per_kill", "reloads", "score")
    dist = (9.0, 16.0)
    spread = 8.0
    respawn = (0.3, 0.6)

    def make_dummy(self, x, z):
        d = super().make_dummy(x, z)
        gun = self.kit.gun
        d.hp = d.hp_max = round(0.6 * gun["mag"] * gun["dmg"])      # six bullets in ten have to land
        return d

    def on_bullet(self, t, hit):
        if hit and hit[1] == "head":
            hit = (hit[0], "body", hit[2])
        super().on_bullet(t, hit)

    def summary(self):
        s = super().summary()
        s["reloads"] = self.kit.reloads
        return s


class SprayTransfer(SiegeScenario):
    id, group, name = "r6_recoil_transfer", "Recoil control", "Spray Transfer"
    tagline = "Three through the door, shoulder to shoulder. One spray."
    how = ("Three dummies stand close together. Kill one and drag the spray onto the next without letting go "
           "of the trigger - the recoil comes with you.")
    measures = ("Moving a spray that is already climbing: time from a kill to the first hit on the next, and "
                "bullets per kill.")
    primary = "swap_median_ms"
    metrics = mt("swap_median_ms", "kills", "bullets_per_kill", "accuracy_pct", "hs_kill_pct", "score")
    alive_max = 3
    respawn = (0.9, 1.3)
    dist = (8.0, 12.0)

    def can_spawn(self, now):
        return not self.dummies or getattr(self, "_filling", False)

    def place(self, now):
        if not self.dummies:
            self._filling = True
            self._c = (math.radians(self.rng.uniform(-18.0, 18.0)), self.rng.uniform(*self.dist))
            self._slot = 0
        b, d = self._c
        off = (self._slot - 1) * self.rng.uniform(1.2, 1.7)
        self._slot += 1
        if self._slot >= self.alive_max:
            self._filling = False
        return self.kit.x + d * math.sin(b) + off * math.cos(b), self.kit.z - d * math.cos(b) + off * math.sin(b)


class LongHallway(SiegeScenario):
    id, group, name = "r6_recoil_burst", "Recoil control", "Long Hallway"
    tagline = "Thirty metres. A full spray will not hold; bursts will."
    how = ("Dummies lean out from the far end of a long hallway, 26 to 34 m away. At that range the gun's climb "
           "is bigger than they are and its damage is falling off: fire short bursts.")
    measures = "Burst discipline at range: accuracy, and how many bullets each kill cost you."
    primary = "accuracy_pct"
    metrics = mt("accuracy_pct", "bullets_per_kill", "kills", "hs_kill_pct", "kill_median_ms", "score")
    start = (0.0, 6.0, 0.0)
    move_area = (-2.0, 2.0, 4.5, 7.5)
    respawn = (0.5, 1.0)
    SPOTS = ((-3.4, -22.0), (3.4, -22.0), (-3.4, -27.0), (3.4, -27.0))

    def build(self):
        hard = [side(-4.2, -30.0, 8.0), side(4.2, -30.0, 8.0)]
        hard += [((sx * 4.0 - 0.9, 0.0, z + 0.5), (sx * 4.0 + 0.9, H, z + 1.1)) for sx in (-1, 1) for z in (-22.0, -27.0)]
        return SiegeWorld(self.room, hard)

    def place(self, now):
        free = [p for p in self.SPOTS if all(abs(d.mover.ox - p[0]) > 0.5 or abs(d.mover.oz - p[1]) > 0.5
                                             for d in self.dummies)]
        self._spot = self.rng.choice(free or self.SPOTS)
        return self._spot

    def mover(self):
        return Peek(reach=1.5, out=(1.4, 2.4), hidden=(0.5, 1.1), speed=3.4)

    def spawn(self, now):
        d = super().spawn(now)
        d.mover.sx, d.mover.sz = -math.copysign(1.0, self._spot[0]), 0.0     # lean in toward the middle of the hall
        d.mover.dir = 1.0
        return d


# ==================================================================== tracking
class LeanSpam(Mover):
    """Shuffles on the spot and throws its head from side to side, the way players dodge headshots."""

    def __init__(self, speed=1.6, hold=(0.22, 0.48), span=0.8):
        self.speed, self.hold, self.span = speed, hold, span

    def start(self, d, g, rng):
        super().start(d, g, rng)
        d.lx, d.lz = self.sx, self.sz
        self.dir = rng.choice((-1.0, 1.0))
        self.want = rng.choice((-1.0, 1.0))
        self.off = self.v = 0.0
        self.flip = self.swap = 0.0

    def step(self, d, dt, now, g):
        if now >= self.flip or self.off * self.dir > self.span:
            if self.flip:
                self.dir = -self.dir
            self.flip = now + self.rng.uniform(0.4, 1.0)
        if now >= self.swap:
            self.want = -self.want if self.rng.random() < 0.8 else 0.0 if self.want else self.rng.choice((-1.0, 1.0))
            self.swap = now + self.rng.uniform(*self.hold)
        self.v += (self.dir * self.speed - self.v) * min(dt / 0.08, 1.0)
        self.off += self.v * dt
        d.x, d.z = self.ox + self.sx * self.off, self.oz + self.sz * self.off
        step = dt / 0.16
        d.lean += max(-step, min(step, self.want - d.lean))


class LeanSpammer(SiegeScenario):
    id, group, name = "r6_track_lean", "Tracking", "Lean Spam"
    head_only = True
    bot_head = True
    tagline = "It will not hold its head still. Only the head counts."
    how = ("The dummy shuffles and throws its head left and right. Only headshots count. Do not chase every "
           "lean - find the rhythm and meet the head where it is going.")
    measures = "Hitting a head that is being moved on purpose: the share of your bullets that were headshots."
    primary = "head_acc_pct"
    grade = "head_acc_pct"
    metrics = mt("head_acc_pct", "kills", "kill_median_ms", "one_tap_pct", "score")
    dist = (7.0, 12.0)
    spread = 14.0
    respawn = (0.4, 0.8)

    def mover(self):
        return LeanSpam()


class CrouchSpam(SiegeScenario):
    id, group, name = "r6_track_crouch", "Tracking", "Crouch Spam"
    tagline = "Strafing, and dropping its head out of your spray."
    how = ("The dummy strafes and keeps dropping into a crouch and standing again. Follow it up and down as "
           "well as side to side.")
    measures = "Vertical tracking under recoil: accuracy, and how many of your kills were still headshots."
    metrics = mt("accuracy_pct", "kills", "hs_kill_pct", "ttk_median_ms", "bullets_per_kill", "score")
    primary = "accuracy_pct"
    dist = (7.0, 12.0)
    spread = 12.0

    def mover(self):
        return Strafe(3.0, (0.35, 0.9), 2.8, crouch=2.6)


class HallwayRunner(SiegeScenario):
    id, group, name = "r6_track_run", "Tracking", "Hallway Runner"
    tagline = "A three-speed sprinting across the doorways."
    how = "A dummy sprints across your lane at 9 to 14 m, behind a row of pillars. Pick it up in the gaps and stay on it."
    measures = "Fast tracking in short windows: accuracy, and time to kill once you start shooting."
    metrics = mt("accuracy_pct", "kills", "ttk_median_ms", "reacquire_median_ms", "hs_kill_pct", "score")
    primary = "accuracy_pct"
    speeds = (3,)
    dist = (12.4, 12.6)
    spread = 2.0

    def build(self):
        return SiegeWorld(self.room, [((x - 0.6, 0.0, -10.4), (x + 0.6, H, -9.8)) for x in (-6.0, -2.0, 2.0, 6.0)])

    def place(self, now):
        return 0.0, -12.5

    def mover(self):
        return Runner(5.0, 8.5, (0.0, 0.3))

    def spawn(self, now):
        d = super().spawn(now)
        m = d.mover
        m.sx, m.sz, m.ox, m.oz = 1.0, 0.0, 0.0, -12.5
        m.off = -m.dir * m.span
        d.x, d.z = m.ox + m.off, m.oz
        return d


# =================================================================== wallbangs
class Patrol(Mover):
    """Walks between random points inside a rectangle, at a fixed height (a floor above, say)."""

    def __init__(self, area, speed=2.2, y=0.0, pause=(0.0, 0.6)):
        self.area, self.speed, self.y, self.pause = area, speed, y, pause

    def start(self, d, g, rng):
        super().start(d, g, rng)
        self.wait = 0.0
        self._pick(d)
        d.y = self.y

    def _pick(self, d):
        x0, x1, z0, z1 = self.area
        for _ in range(12):
            self.tx, self.tz = self.rng.uniform(x0, x1), self.rng.uniform(z0, z1)
            if math.hypot(self.tx - d.x, self.tz - d.z) > 2.0:
                break

    def step(self, d, dt, now, g):
        d.y = self.y
        if now < self.wait:
            return
        dx, dz = self.tx - d.x, self.tz - d.z
        ln = math.hypot(dx, dz)
        if ln < self.speed * dt + 0.05:
            self.wait = now + self.rng.uniform(*self.pause)
            self._pick(d)
            return
        d.x += dx / ln * self.speed * dt
        d.z += dz / ln * self.speed * dt


class Wallbang(SiegeScenario):
    id, group, name = "r6_bang_wall", "Wallbangs", "Wallbang"
    xray = True
    tagline = "A soft wall, and someone walking behind it."
    how = ("The boarded wall stops your eyes, not your bullets. A red marker shows the dummy behind it. Shoot "
           "through. Damage is cut by a third going through, but a headshot still kills.")
    measures = ("Tracking a target you cannot see: accuracy through the wall. The marker's dot is the head - "
                "put the crosshair there, not on the middle of the bracket.")
    primary = "accuracy_pct"
    metrics = mt("accuracy_pct", "kills", "hs_kill_pct", "bullets_per_kill", "ttk_median_ms", "score")
    WALL_Z = -8.0
    respawn = (0.4, 0.8)
    stances = (0.0, 0.0, 1.0)

    def build(self):
        return SiegeWorld(self.room, soft=[wall(-9.0, 9.0, self.WALL_Z, 0.25)])

    def place(self, now):
        return self.rng.uniform(-5.0, 5.0), self.rng.uniform(self.WALL_Z - 5.0, self.WALL_Z - 1.5)

    def mover(self):
        return Patrol((-6.0, 6.0, self.WALL_Z - 5.5, self.WALL_Z - 1.2), self.rng.uniform(1.6, 2.6))

    def draw(self, ui, g):
        for d in self.dummies:
            self.ping(ui, g, d)


class FromBelow(SiegeScenario):
    id, group, name = "r6_bang_floor", "Wallbangs", "From Below"
    xray = True
    tagline = "They are on the floor above. The floor is wood."
    how = ("The ceiling over you is a soft floor, and dummies are walking on it. Markers show where. Aim up and "
           "shoot through the boards.")
    measures = ("Vertical play: accuracy at steep angles through a floor, where your crosshair moves across "
                "the target much faster than your hand is used to.")
    primary = "accuracy_pct"
    metrics = mt("accuracy_pct", "kills", "bullets_per_kill", "ttk_median_ms", "hs_kill_pct", "score")
    FLOOR = 3.0
    AREA = (-5.0, 5.0, -9.0, -2.5)
    respawn = (0.4, 0.8)
    alive_max = 2

    def build(self):
        return SiegeWorld(self.room, soft=[((-8.0, self.FLOOR - 0.2, -13.0), (8.0, self.FLOOR, 4.0))])

    def place(self, now):
        x0, x1, z0, z1 = self.AREA
        return self.rng.uniform(x0, x1), self.rng.uniform(z0, z1)

    def mover(self):
        return Patrol(self.AREA, self.rng.uniform(1.4, 2.4), self.FLOOR)

    def draw(self, ui, g):
        for d in self.dummies:
            self.ping(ui, g, d)


class MurderHole(SiegeScenario):
    id, group, name = "r6_bang_hole", "Wallbangs", "Murder Hole"
    tagline = "A hole in the wall the size of a letterbox. Find the angle."
    how = ("There is one small hole in the wall, at head height. A marker shows the dummy standing in the room "
           "behind it. Sidestep until it lines up through the hole, and shoot. If you can see it, it can see you.")
    measures = "Working an angle: time from a dummy appearing to your first hit, through an opening you have to line up."
    primary = "kill_median_ms"
    grade = None
    metrics = mt("kill_median_ms", "kills", "hs_kill_pct", "accuracy_pct", "deaths", "score")
    WALL_Z = -6.0
    HOLE = (0.38, 1.18, 1.78)           # half-width, bottom, top
    shoot_back = ((0.9, 1.3), 0.45)
    respawn = (0.5, 1.0)
    move_area = (-4.0, 4.0, -1.0, 1.5)

    def build(self):
        hw, y0, y1 = self.HOLE
        z = self.WALL_Z
        return SiegeWorld(self.room, [wall(-14.0, -hw, z), wall(hw, 14.0, z), wall(-hw, hw, z, 0.4, 0.0, y0),
                                      wall(-hw, hw, z, 0.4, y1, H)])

    def place(self, now):
        dz = self.rng.uniform(3.0, 7.0)                             # metres behind the wall
        reach = 3.6 * dz / (-self.WALL_Z)                           # as far out as the player can still line up
        return self.rng.uniform(-reach, reach), self.WALL_Z - dz

    def _line(self, d):
        """Where the player has to stand (x) for this dummy to show through the hole."""
        k = (self.kit.z - self.WALL_Z) / (self.WALL_Z - d.z)
        return -k * d.x

    def bot_move(self, g):
        if not self.dummies:
            return (0.0, 0.0)
        dx = self._line(self.dummies[0]) - self.kit.x
        return (math.copysign(1.0, dx), 0.0) if abs(dx) > 0.12 else (0.0, 0.0)

    def draw(self, ui, g):
        for d in self.dummies:          # you know where it is (a drone told you); the work is the angle
            if not d.s["vis"]:
                self.ping(ui, g, d)


# ============================================================== under pressure
class Advance(Mover):
    """Walks straight at the player's line, stopping now and then to drop its shield and shoot."""

    def __init__(self, speed=1.5):
        self.speed = speed

    def start(self, d, g, rng):
        super().start(d, g, rng)
        self.open_until = 0.0
        self.next_open = rng.uniform(2.2, 3.4)

    def step(self, d, dt, now, g):
        if now >= self.next_open:
            self.open_until = now + self.rng.uniform(0.9, 1.3)
            self.next_open = self.open_until + self.rng.uniform(2.0, 3.2)
        if now >= self.open_until and d.z < g.eye[2] - 1.8:
            d.z += self.speed * dt


class ShieldPush(SiegeScenario):
    id, group, name = "r6_press_shield", "Under pressure", "Shield Push"
    tagline = "A ballistic shield walking at you. Feet, or wait for the head."
    how = ("The shield stops everything. Shoot the legs under it - limbs take three quarters damage - or wait: "
           "every few seconds it lowers the shield to fire, and its head is one bullet. Do not let it reach you.")
    measures = ("Small targets under pressure: accuracy on what the shield leaves showing, and whether you "
                "took the head when it was offered.")
    primary = "accuracy_pct"
    metrics = mt("accuracy_pct", "kills", "hs_kill_pct", "bullets_per_kill", "deaths", "score")
    speeds = (1,)
    respawn = (0.8, 1.4)
    shoot_back = ((0.25, 0.40), 0.55)
    move_area = (-2.5, 2.5, -1.0, 2.5)

    def build(self):
        return SiegeWorld(self.room, [((0.0, -5.0, 0.0), (0.1, -4.9, 0.1))])     # the shield; tick() puts it in place

    def place(self, now):
        return self.rng.uniform(-2.0, 2.0), self.rng.uniform(-17.0, -14.0)

    def mover(self):
        return Advance(self.rng.uniform(1.3, 1.8))

    def _open(self, d):
        return self.g.t < d.mover.open_until

    def capture_flag(self, d) -> float:
        return 1.0 if d.mover is not None and self._open(d) else 0.0

    def sees(self, d):
        eye = self.g.eye
        return (self.world.clear(eye, d.head()) or self.world.clear(eye, d.chest())
                or self.world.clear(eye, (d.x, 0.16, d.z)))

    def update(self, dt, now):
        if not self.dummies:
            self.world.boxes[0] = ((0.0, -5.0, 0.0), (0.1, -4.9, 0.1))
        super().update(dt, now)

    def tick(self, d, dt, now):
        if not d.alive:
            return
        off = 0.95 if self._open(d) else 0.0            # swung aside while it shoots
        self.world.boxes[0] = ((d.x - 0.48 + off, 0.45, d.z + 0.42), (d.x + 0.48 + off, 1.86, d.z + 0.50))
        if d.shooter and not self._open(d):
            d.shooter.seen = None                       # it only fires with the shield down
        if math.hypot(d.x - self.kit.x, d.z - self.kit.z) < 1.9:
            self.kit.take_damage(self.kit.hp_max, d, now)
            self.retire(d, now)

    def kill(self, d, t):
        super().kill(d, t)
        self.world.boxes[0] = ((0.0, -5.0, 0.0), (0.1, -4.9, 0.1))

    def retire(self, d, now, delay=None):
        super().retire(d, now, delay)
        self.world.boxes[0] = ((0.0, -5.0, 0.0), (0.1, -4.9, 0.1))

    def live(self):
        out = []
        for d in self.dummies:
            if d.s["vis"]:
                out.append((d.head(), HEAD_R) if self._open(d) else ((d.x, 0.17, d.z), 0.15))
        return tuple(out)


class StopTheRush(SiegeScenario):
    id, group, name = "r6_press_rush", "Under pressure", "Stop the Rush"
    tagline = "Three-speeds through the door, one after another, shooting."
    how = ("Dummies sprint at you from a doorway 18 m away, weaving and firing as they come. Being touched "
           "puts you down. Heads end it fastest.")
    measures = "Staying accurate while being run at: kills against times downed, and bullets per kill."
    primary = "score"
    metrics = mt("score", "kills", "deaths", "hs_kill_pct", "bullets_per_kill", "accuracy_pct")
    speeds = (3,)
    alive_max = 2
    respawn = (0.5, 1.1)
    shoot_back = ((0.6, 0.9), 0.30)
    move_area = (-3.0, 3.0, -1.0, 3.0)

    def build(self):
        return SiegeWorld(self.room, [wall(-14.0, -1.0, -18.0), wall(1.0, 14.0, -18.0)])

    def place(self, now):
        return self.rng.uniform(-0.6, 0.6), self.rng.uniform(-21.0, -19.5)

    def mover(self):
        return Rush(4.8, 1.8, 0.8)

    def tick(self, d, dt, now):
        if d.alive and math.hypot(d.x - self.kit.x, d.z - self.kit.z) < 1.4:
            self.kit.take_damage(self.kit.hp_max, d, now)
            self.retire(d, now)


class RoomClear(SiegeScenario):
    id, group, name = "r6_press_clear", "Under pressure", "Room Clear"
    tagline = "Three in the room, and each one opens fire a little after the last."
    how = ("You are at the doorway of a room with three dummies in it: behind cover, in the corners, in the "
           "open. They start shooting one after another. Clear the room; the next one fills straight after.")
    measures = "Target priority and speed: how long each room took you, and what it cost."
    primary = "clear_median_ms"
    grade = "accuracy_pct"
    metrics = mt("clear_median_ms", "rooms", "kills", "hs_kill_pct", "deaths", "accuracy_pct")
    alive_max = 3
    respawn = (0.9, 1.3)
    timeout = 14.0
    move_area = (-2.2, 2.2, -0.6, 2.0)
    # x, z, crouched. Every one of these can be seen from somewhere you can stand (tests/check_r6.py
    # checks it): the two nearest need a step across the doorway.
    SPOTS = ((-5.0, -6.0, 0.0), (5.0, -6.5, 0.0), (-2.5, -10.0, 1.0), (3.0, -11.0, 1.0), (-6.0, -12.5, 0.0),
             (6.0, -13.0, 0.0), (0.0, -13.5, 0.0), (-4.0, -8.0, 0.0), (4.5, -9.0, 0.0))
    COVER = ((-2.5, -10.0), (3.0, -11.0))       # the low walls the crouched ones sit behind

    def __init__(self, g):
        super().__init__(g)
        self.rooms: list = []
        self._wave_t = None
        self._order = 0

    def build(self):
        hard = [wall(-14.0, -1.5, -2.5), wall(1.5, 14.0, -2.5), side(-7.5, -16.0, -2.5), side(7.5, -16.0, -2.5),
                wall(-7.5, 7.5, -16.0)]
        hard += [((x - 1.1, 0.0, z + 0.55), (x + 1.1, 1.05, z + 0.85)) for x, z in self.COVER]
        return SiegeWorld(self.room, hard)

    def can_spawn(self, now):
        return not self.dummies or getattr(self, "_filling", False)

    def place(self, now):
        if not self.dummies:
            self._filling = True
            self._picks = self.rng.sample(self.SPOTS, self.alive_max)
            self._order = 0
            self._wave_t = now
        self._spot = self._picks[self._order]
        self._order += 1
        if self._order >= self.alive_max:
            self._filling = False
        return self._spot[0], self._spot[1]

    def make_dummy(self, x, z):
        d = SiegeDummy(x, z, None, SiegeShooter((0.7 + 0.55 * (self._order - 1), 1.0 + 0.55 * (self._order - 1)), 0.50),
                       self.rng.choice(self.speeds))
        d.crouch = self._spot[2]
        return d

    def puppet_spawned(self, d):
        if len(self.dummies) <= 1:          # the first of a new room
            self._wave_t = d.s["t0"]

    def kill(self, d, t):
        super().kill(d, t)
        if not self.dummies and self._wave_t is not None:
            self.rooms.append((t - self._wave_t) * 1000.0)
            self.g.note("ROOM CLEAR", GOOD, 0.8)

    def bot_move(self, g):
        """Step across the doorway to open up whichever side still hides someone."""
        hidden = [d for d in self.dummies if not d.s["vis"]]
        if hidden and len(hidden) == len(self.dummies):
            return (1.0 if hidden[0].x < 0.0 else -1.0, 0.0)
        return (0.0, 0.0)

    def live(self):
        """(for the bots) a crouched one shows only its head over the cover"""
        return tuple((d.head(), HEAD_R) if d.crouch > 0.5 else (d.chest(), 0.25) for d in self.dummies if d.s["vis"])

    def hud(self):
        return (("ROOMS", str(len(self.rooms))), ("KILLS", str(self.kills)))

    def summary(self):
        s = super().summary()
        s["rooms"] = len(self.rooms)
        s["clear_median_ms"] = _r(_med(self.rooms))
        return s


# ======================================================= sensitivity finder
# Drills for the sensitivity finder when it is searching for Siege: a Siege gun, Siege's distances,
# and - the reason they exist - no aim assist, which changes which sensitivity a thumb can control.
# Not part of the track and never logged as runs.
class SensTurn(SiegeScenario):
    id, name = "r6_sens_turn", "Turn and Burn"
    how = "Dummies appear beside and behind you. Turn onto each one and kill it. Stay planted - no need to move."
    can_move = False
    start = (0.0, -12.0, 0.0)
    dist = (5.0, 10.0)
    respawn = (0.2, 0.4)

    def place(self, now):
        return self._polar(self.g.yaw + self.rng.choice((-1.0, 1.0)) * self.rng.uniform(50.0, 150.0),
                           self.rng.uniform(*self.dist))


class SensStrafe(SiegeScenario):
    id, name = "r6_sens_strafe", "Strafe Kill"
    how = "A strafing dummy appears a little off your aim. Get on it and stay on it until it drops."
    can_move = False
    dist = (7.0, 13.0)
    respawn = (0.2, 0.4)

    def place(self, now):
        bearing = self.g.yaw + self.rng.choice((-1.0, 1.0)) * self.rng.uniform(6.0, 22.0)
        return self._polar(max(-60.0, min(60.0, bearing)), self.rng.uniform(*self.dist))

    def mover(self):
        return Strafe(3.0, (0.4, 1.0), 3.0)


GROUPS = (
    ("Crosshair placement", (HeadHeight, SliceThePie, PingPreAim)),
    ("Holding angles", (PixelAngle, DoorHold, TwoDoors)),
    ("Peeking", (LeanPeek, QuickPeek, BothShoulders)),
    ("One-taps", (OneTap, HeadSwap, FlankCheck)),
    ("Recoil control", (MagDump, SprayTransfer, LongHallway)),
    ("Tracking", (LeanSpammer, CrouchSpam, HallwayRunner)),
    ("Wallbangs", (Wallbang, FromBelow, MurderHole)),
    ("Under pressure", (ShieldPush, StopTheRush, RoomClear)),
)
ALL = tuple(s for _, group in GROUPS for s in group)
