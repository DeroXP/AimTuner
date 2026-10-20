"""The Call of Duty track: 27 scenarios, three for each of nine mechanics.

Every one of these uses the Call of Duty kit (codkit.py): the game's sensitivity
scale, a rifle that kills in 5 body shots or 3-4 headshots, recoil, and
movement on the left stick. Whether aim assist is on is a property of the
scenario, never a setting - several of them exist to show what it does and what
takes it away.

A scenario here is mostly configuration on top of CodScenario: where dummies
appear, how they move, whether they shoot back, and which numbers matter.
"""
from __future__ import annotations

import math

from .codkit import (BODY_R, HEAD_R, TUNE, CodKit, Duck, Dummy, Peek, Runner, Rush, Shooter, Strafe, World)
from .scenarios import Scenario, _med, _r
from .theme import BAD, WARN

M = {   # metric definitions shared across scenarios: key -> (label, unit, better, decimals)
    "score": ("Score", "", 1, 0),
    "kills": ("Kills", "", 1, 0),
    "accuracy_pct": ("Accuracy", "%", 1, 1),
    "headshot_pct": ("Hits that were headshots", "%", 1, 1),
    "head_acc_pct": ("Shots that were headshots", "%", 1, 1),
    "ttk_median_ms": ("First shot to kill", "ms", -1, 0),
    "first_hit_median_ms": ("Seen to first hit", "ms", -1, 0),
    "kill_median_ms": ("Seen to dead", "ms", -1, 0),
    "bullets_per_kill": ("Bullets per kill", "", -1, 2),
    "swap_median_ms": ("Kill to first hit on the next", "ms", -1, 0),
    "aa_full_pct": ("Fight time at full assist", "%", 1, 1),
    "aa_avg_pct": ("Average assist strength", "%", 1, 1),
    "strafe_pct": ("Fight time spent strafing", "%", 1, 1),
    "deaths": ("Times downed", "", -1, 0),
    "win_pct": ("Duels won", "%", 1, 1),
    "preaim_median_deg": ("Crosshair off target at first sight", "°", -1, 2),
    "level_median_deg": ("Off head height at first sight", "°", -1, 2),
    "first_shot_hit_pct": ("First bullet on target", "%", 1, 1),
    "first_shot_median_ms": ("Seen to first bullet", "ms", -1, 0),
    "ads_shot_median_ms": ("Aim pressed to first bullet", "ms", -1, 0),
    "opening_dmg": ("Damage in the first 0.4 s", "", 1, 1),
    "acc_assist_pct": ("Accuracy with assist", "%", 1, 1),
    "acc_raw_pct": ("Accuracy without assist", "%", 1, 1),
    "reacquire_median_ms": ("Back in view to first hit", "ms", -1, 0),
    "acc_close_pct": ("Accuracy inside 10 m", "%", 1, 1),
    "acc_mid_pct": ("Accuracy 10-30 m", "%", 1, 1),
    "acc_far_pct": ("Accuracy past 30 m", "%", 1, 1),
    "still_shots": ("Shots wasted standing still", "", -1, 0),
}


def mt(*keys):
    return tuple((k,) + M[k] for k in keys)


AHEAD = 12.0    # degrees either side of the crosshair that still count as "in front of you" for the warning tone


class CodScenario(Scenario):
    cod = True
    kit_cls = CodKit        # what drives movement, aim and the weapon in a run of it (game.Game)
    track = "cod"
    group = ""
    aa = True               # aim assist on in this scenario
    show_meter = True
    hold_fire = True
    needs_ads = True
    can_move = True
    primary = "score"
    metrics = mt("score", "kills", "accuracy_pct", "ttk_median_ms", "first_hit_median_ms", "headshot_pct")

    room = ((-34.0, 0.0, -78.0), (34.0, 11.0, 10.0))
    start = (0.0, 0.0, 0.0)             # x, z, yaw (0 = looking down -z)
    move_area = (-9.0, 9.0, -3.0, 4.0)  # where the player may walk
    alive_max = 1
    respawn = (0.35, 0.75)              # seconds after a kill
    dist = (12.0, 22.0)                 # metres from the player
    spread = 26.0                       # degrees either side of straight ahead
    head_only = False
    bot_head = False                    # self-test bots aim at the head (it is all that shows)
    strafe_required = False
    shoot_back = None                   # None, or (reaction delay range, accuracy)
    timeout = None                      # seconds before an unkilled dummy is replaced

    def __init__(self, g):
        super().__init__(g)
        self.kit = g.kit
        self.world = self.build()
        self.dummies: list = []
        self.next_spawn = 0.0
        self.heads = self.dmg = 0
        self.shots_aa = self.hits_aa = self.shots_raw = self.hits_raw = 0
        self.first_shots = self.first_hits = 0
        self.still_shots = self.body_only = 0
        self.last_kill = None
        self.swaps: list = []
        self.reacq: list = []
        self.by_range = {"close": [0, 0], "mid": [0, 0], "far": [0, 0]}   # [shots, hits]
        self.duels = 0

    # ---- things a scenario overrides ---------------------------------------
    def build(self) -> World:
        return World(self.room)

    def mover(self):
        return None

    def place(self, now):
        """(x, z) for the next dummy: somewhere in front, within `spread` and `dist`."""
        b = math.radians(self.rng.uniform(-self.spread, self.spread))
        d = self.rng.uniform(*self.dist)
        return self.kit.x + d * math.sin(b), self.kit.z - d * math.cos(b)

    def can_spawn(self, now) -> bool:
        return True

    def tick(self, d, dt, now): ...

    def on_down(self, d, now):
        self.g.note("DOWN", BAD, 1.0)
        if d.shooter:
            d.shooter.seen = None

    # ---- shared machinery ---------------------------------------------------
    def begin(self, now):
        self.next_spawn = now + 0.4

    def _clamp(self, x, z):
        (x0, _, z0), (x1, _, z1) = self.room
        return min(max(x, x0 + 1.5), x1 - 1.5), min(max(z, z0 + 1.5), z1 - 1.5)

    def _polar(self, bearing_deg, dist):
        b = math.radians(bearing_deg)
        return self._clamp(self.kit.x + dist * math.sin(b), self.kit.z - dist * math.cos(b))

    def make_dummy(self, x, z):
        sb = self.shoot_back
        return Dummy(x, z, self.mover(), Shooter(*sb) if sb else None)

    def sees(self, d) -> bool:
        """Is this dummy in the player's view (and the player in its)?"""
        eye = self.g.eye
        return self.world.clear(eye, d.head()) or self.world.clear(eye, d.chest())

    def spawn(self, now):
        x, z = self._clamp(*self.place(now))
        d = self.make_dummy(x, z)
        d.s = {"t0": now, "seen": None, "vis": False, "pre": None, "level": None, "first_shot": None,
               "first_shot_hit": False, "first_hit": None, "shots": 0, "hits": 0, "heads": 0,
               "back_at": None, "ads_at": None, "open_dmg": 0,
               "dist0": math.hypot(x - self.kit.x, z - self.kit.z)}
        if d.mover:
            d.mover.start(d, self.g, self.rng)
        self.dummies.append(d)
        return d

    def announce(self, d):
        """A dummy that shoots back gives itself away as it appears: a two-note warning, played
        from the side you would have to turn to (or from both when it is ahead of you)."""
        rel = (math.degrees(math.atan2(d.x - self.kit.x, -(d.z - self.kit.z))) - self.g.yaw + 180.0) % 360.0 - 180.0
        self.g.sfx("threat_l" if rel < -AHEAD else ("threat_r" if rel > AHEAD else "threat_c"))

    def _offsets(self, d):
        """(degrees from the crosshair to the nearest part of the dummy, degrees off its head height)."""
        g, eye = self.g, self.g.eye
        dx, dz = d.x - eye[0], d.z - eye[2]
        flat = math.hypot(dx, dz) or 1e-6
        ex = ((math.degrees(math.atan2(dx, -dz)) - g.yaw + 180.0) % 360.0 - 180.0) * math.cos(math.radians(g.pitch))
        p_lo = math.degrees(math.atan2(d.y + 0.2 - eye[1], flat))
        hy = d.head()[1]
        p_hi = math.degrees(math.atan2(hy + HEAD_R - eye[1], flat))
        ev = 0.0 if p_lo <= g.pitch <= p_hi else min(abs(g.pitch - p_lo), abs(g.pitch - p_hi))
        half = math.degrees(math.atan2(BODY_R, flat))
        return (math.hypot(max(abs(ex) - half, 0.0), ev),
                abs(g.pitch - math.degrees(math.atan2(hy - eye[1], flat))))

    def _engaged(self):
        """The visible dummy nearest the crosshair - the one a missed bullet was meant for."""
        best, best_e = None, 1e9
        for d in self.dummies:
            if d.s["vis"]:
                e = self._offsets(d)[0]
                if e < best_e:
                    best, best_e = d, e
        return best

    def fighting(self) -> bool:
        return any(d.s["vis"] for d in self.dummies)

    def update(self, dt, now):
        g, world, eye = self.g, self.world, self.g.eye
        if len(self.dummies) < self.alive_max and now >= self.next_spawn and self.can_spawn(now):
            d = self.spawn(now)
            if d.shooter:
                self.announce(d)
            self.next_spawn = now + 0.12
        for d in list(self.dummies):
            if d.mover:
                d.mover.step(d, dt, now, g)
            vis = self.sees(d)
            s = d.s
            if vis and s["seen"] is None:
                s["seen"] = now
                s["pre"], s["level"] = self._offsets(d)
            if vis and not s["vis"] and s["first_hit"] is not None:
                s["back_at"] = now                  # came back into view mid-fight: time the re-acquire
            elif not vis:
                s["back_at"] = None
            s["vis"] = vis
            if d.shooter:
                d.shooter.step(d, dt, now, self.kit, vis, self.rng)
            self.tick(d, dt, now)
            if self.timeout and d.alive and now - s["t0"] > self.timeout:
                self.retire(d, now)

    def on_ads(self, t, down):
        if down:
            for d in self.dummies:
                if d.s["seen"] is not None and d.s["ads_at"] is None:
                    d.s["ads_at"] = t

    def retire(self, d, now, delay=None):
        """Remove a dummy that was not killed."""
        d.alive = False
        if d in self.dummies:
            self.dummies.remove(d)
            self.removed(d.id, "retire")
        self.next_spawn = now + (self.rng.uniform(*self.respawn) if delay is None else delay)

    def capture_targets(self):
        for d in self.dummies:
            sh = d.shooter
            yield (d.id, "dummy", (d.x, d.y, d.z, d.crouch, getattr(d, "lean", 0.0), getattr(d, "lx", 1.0),
                                   getattr(d, "lz", 0.0), self.capture_flag(d)), d.s["vis"],
                   {"hp": d.hp_max, "speed": getattr(d, "speed", None),
                    "shooter": [list(sh.delay), sh.accuracy] if sh is not None else None,
                    "mover": type(d.mover).__name__ if d.mover is not None else None})

    def capture_flag(self, d) -> float:
        """Anything else about a dummy the ghost needs to see it as the player did (Siege's shield)."""
        return 0.0

    def puppet_spawned(self, d):
        """A dummy has been put in by the ghost's replay of a run (ghost.py) rather than by spawn():
        set up whatever this scenario's own spawn() or place() would have."""

    def _band(self, dist):
        return "close" if dist < 10.0 else ("mid" if dist <= 30.0 else "far")

    def on_bullet(self, t, hit):
        g, kit = self.g, self.kit
        self.shots += 1
        self.shot(hit is not None)
        assisted = self.aa and kit.assist.info["strength"] >= 0.3
        tgt = hit[2] if hit else self._engaged()
        opener = False                      # is this the first bullet fired at that dummy?
        band = None                         # one distance band per bullet, for the shot and (if it lands) the hit
        if tgt is not None:
            s = tgt.s
            s["shots"] += 1
            band = self._band(math.hypot(tgt.x - kit.x, tgt.z - kit.z))
            self.by_range[band][0] += 1
            if s["first_shot"] is None and s["seen"] is not None:
                s["first_shot"], opener = t, True
                self.first_shots += 1
        if assisted:
            self.shots_aa += 1
        else:
            self.shots_raw += 1
        if self.strafe_required and kit.strafe < TUNE["strafe_min"]:    # the same test aim assist applies
            self.still_shots += 1
            g.note("KEEP MOVING", WARN, 0.4)
            return
        if not hit:
            return
        dist, zone, d = hit
        s = d.s
        if self.head_only and zone != "head":
            self.body_only += 1
            g.sfx("miss")
            return
        self.hits += 1
        self.by_range[band][1] += 1
        if assisted:
            self.hits_aa += 1
        else:
            self.hits_raw += 1
        if zone == "head":
            self.heads += 1
            s["heads"] += 1
        s["hits"] += 1
        if opener:
            s["first_shot_hit"] = True
            self.first_hits += 1
        if s["first_hit"] is None:
            s["first_hit"] = t
            if self.last_kill is not None:
                self.swaps.append((t - self.last_kill) * 1000.0)
                self.last_kill = None
        if s["back_at"] is not None:
            self.reacq.append((t - s["back_at"]) * 1000.0)
            s["back_at"] = None
        dmg = kit.damage(zone, dist)
        if s["first_shot"] is not None and t - s["first_shot"] <= 0.4:
            s["open_dmg"] += min(dmg, max(d.hp, 0))
        d.hp -= dmg
        d.flash = g.t
        self.dmg += dmg
        self.landed(d, t, zone)
        if d.hp <= 0 and d.alive:
            self.kill(d, t)
        else:
            g.fx_hit(False, zone == "head")

    def landed(self, d, t, zone): ...

    def kill(self, d, t):
        g, s = self.g, d.s
        d.alive = False
        self.kills += 1
        self.last_kill = t
        if d.shooter:
            self.duels += 1
        seen = s["seen"] if s["seen"] is not None else s["t0"]
        self.events.append({
            "t": round(s["t0"], 3), "dist_m": round(s["dist0"], 1),
            "preaim_deg": _r(s["pre"], 2), "level_deg": _r(s["level"], 2),
            "first_shot_ms": _r(None if s["first_shot"] is None else (s["first_shot"] - seen) * 1000.0),
            "first_shot_hit": s["first_shot_hit"],
            "first_hit_ms": _r(None if s["first_hit"] is None else (s["first_hit"] - seen) * 1000.0),
            "ttk_ms": _r(None if s["first_shot"] is None else (t - s["first_shot"]) * 1000.0),
            "kill_ms": _r((t - seen) * 1000.0),
            "ads_shot_ms": _r(None if s["ads_at"] is None or s["first_shot"] is None
                              else (s["first_shot"] - s["ads_at"]) * 1000.0),
            "open_dmg": s["open_dmg"], "shots": s["shots"], "hits": s["hits"], "heads": s["heads"], "killed": True,
        })
        g.fx_hit(True)
        g.pop(d.chest(), 0.42)
        self.dummies.remove(d)
        self.removed(d.id, "kill")
        self.next_spawn = t + self.rng.uniform(*self.respawn)

    # ---- drawing / bots -----------------------------------------------------
    def spheres(self):
        out, now, col = [], self.g.t, self.g.color
        threat = tuple(0.45 * c + 0.55 * k for c, k in zip(col, (1.0, 0.85, 0.2)))
        for d in self.dummies:
            out.extend(d.instances(threat if d.shooter else col, now))
        return out

    def live(self):
        head = self.head_only or self.bot_head
        return tuple((d.head() if head else d.chest(), HEAD_R if head else BODY_R)
                     for d in self.dummies if d.s["vis"])

    def bot_move(self, g):
        """Optional left-stick input (x, y) a self-test bot should hold."""
        return None

    def hud(self):
        return (("KILLS", str(self.kills)), ("ACCURACY", f"{self._accuracy():.0f}%"))

    def summary(self):
        ev = [e for e in self.events if e.get("killed")]

        def pct(h, n):
            return round(100.0 * h / n, 1) if n else None

        s = {
            "score": self._score(), "kills": self.kills, "shots": self.shots, "hits": self.hits,
            "accuracy_pct": _r(self._accuracy()),
            "headshot_pct": pct(self.heads, self.hits), "head_acc_pct": pct(self.heads, self.shots),
            "bullets_per_kill": round(self.shots / self.kills, 2) if self.kills else None,
            "ttk_median_ms": _r(_med([e["ttk_ms"] for e in ev])),
            "first_hit_median_ms": _r(_med([e["first_hit_ms"] for e in ev])),
            "kill_median_ms": _r(_med([e["kill_ms"] for e in ev])),
            "first_shot_median_ms": _r(_med([e["first_shot_ms"] for e in self.events])),
            "first_shot_hit_pct": pct(self.first_hits, self.first_shots),
            "ads_shot_median_ms": _r(_med([e.get("ads_shot_ms") for e in self.events])),
            "preaim_median_deg": _r(_med([e["preaim_deg"] for e in self.events]), 2),
            "level_median_deg": _r(_med([e["level_deg"] for e in self.events]), 2),
            "swap_median_ms": _r(_med(self.swaps)),
            "reacquire_median_ms": _r(_med(self.reacq)),
            "acc_assist_pct": pct(self.hits_aa, self.shots_aa), "acc_raw_pct": pct(self.hits_raw, self.shots_raw),
            "acc_close_pct": pct(self.by_range["close"][1], self.by_range["close"][0]),
            "acc_mid_pct": pct(self.by_range["mid"][1], self.by_range["mid"][0]),
            "acc_far_pct": pct(self.by_range["far"][1], self.by_range["far"][0]),
            "still_shots": self.still_shots, "damage": self.dmg,
            "opening_dmg": _r(_med([e["open_dmg"] for e in self.events])),
        }
        if self.shoot_back:
            n = self.duels + self.kit.deaths
            s["win_pct"] = pct(self.duels, n)
        return s


def _wall(x0, x1, z, h=3.2, thick=0.6):
    return ((x0, 0.0, z - thick / 2), (x1, h, z + thick / 2))


# =================================================================== flicking
class SnapShot(CodScenario):
    id, group, name = "cod_flick_snap", "Flicking", "Snap Shot"
    aa = False
    tagline = "One dummy, 15-45 degrees off your aim. No aim assist."
    how = "A dummy appears to one side. Snap onto it and kill it: 5 body shots, or 3-4 to the head."
    measures = ("Raw flicking at your real sensitivity: how long from seeing it to the first hit, and how "
                "cleanly you finish. Compare with Slowdown Stop, the same drill with aim assist.")
    metrics = mt("score", "kills", "accuracy_pct", "first_hit_median_ms", "kill_median_ms", "headshot_pct")
    dist = (12.0, 28.0)
    flick = (15.0, 45.0)

    def place(self, now):
        bearing = self.g.yaw + self.rng.choice((-1.0, 1.0)) * self.rng.uniform(*self.flick)
        bearing = max(-70.0, min(70.0, bearing))
        return self._polar(bearing, self.rng.uniform(*self.dist))


class TargetSwap(CodScenario):
    id, group, name = "cod_flick_swap", "Flicking", "Target Swap"
    tagline = "Three dummies up. Kill one, snap to the next."
    how = "Three dummies stand in front of you. Each kill brings a replacement somewhere else."
    measures = ("Transfers under aim assist: the time from a kill to your first hit on the next target, "
                "with slowdown catching the end of each flick.")
    metrics = mt("score", "kills", "accuracy_pct", "swap_median_ms", "ttk_median_ms", "bullets_per_kill")
    alive_max = 3
    respawn = (0.15, 0.35)
    dist = (14.0, 24.0)
    spread = 32.0

    def place(self, now):
        for _ in range(40):
            x, z = super().place(now)
            if all(math.hypot(x - o.x, z - o.z) > 3.5 for o in self.dummies):
                break
        return x, z


class CheckSix(CodScenario):
    id, group, name = "cod_flick_180", "Flicking", "Check Your Six"
    tagline = "They spawn beside and behind you - and they shoot."
    how = ("A dummy appears well off to one side or behind, with a warning tone in the ear on that side. "
           "Turn, find it, kill it before it kills you.")
    measures = ("The top end of your sensitivity: big turns into a controlled stop, under pressure. "
                "Orange dummies fire back about a second after they see you.")
    metrics = mt("score", "kills", "first_hit_median_ms", "accuracy_pct", "deaths", "win_pct")
    start = (0.0, -34.0, 0.0)
    move_area = (-6.0, 6.0, -40.0, -28.0)
    dist = (8.0, 16.0)
    shoot_back = ((0.95, 1.35), 0.45)

    def place(self, now):
        return self._polar(self.g.yaw + self.rng.choice((-1.0, 1.0)) * self.rng.uniform(95.0, 170.0),
                           self.rng.uniform(*self.dist))


# =================================================================== tracking
class StrafeSpray(CodScenario):
    id, group, name = "cod_track_strafe", "Tracking", "Strafe Spray"
    aa = False
    tagline = "A strafing dummy at mid range. No aim assist."
    how = "The dummy strafes left and right and reverses at random. Hold fire and stay on it."
    measures = ("Raw tracking with recoil. Compare with Sticky Track, the same drill with aim assist, to "
                "see how much of your tracking the game is doing for you.")
    metrics = mt("score", "kills", "accuracy_pct", "ttk_median_ms", "bullets_per_kill", "headshot_pct")
    dist = (14.0, 20.0)
    spread = 10.0

    def mover(self):
        return Strafe(3.6, (0.35, 0.95), 4.0)


class CrossingRunner(CodScenario):
    id, group, name = "cod_track_cross", "Tracking", "Crossing Runner"
    tagline = "Dummies sprint across your lane."
    how = "A dummy runs from one side to the other at 22-34 m. Pick it up and track it down."
    measures = "Fast, steady tracking of a target crossing at speed, the way a player runs across a doorway."
    metrics = mt("score", "kills", "accuracy_pct", "ttk_median_ms", "aa_full_pct", "bullets_per_kill")
    dist = (22.0, 34.0)
    spread = 4.0

    def mover(self):
        return Runner(6.0, 13.0)


class JumpShot(CodScenario):
    id, group, name = "cod_track_jump", "Tracking", "Jump Shot"
    tagline = "Strafing, jumping and crouching at close range."
    how = "The dummy strafes, jumps and drops into a crouch. Follow it up and down as well as side to side."
    measures = "Vertical tracking on top of horizontal - the part aim assist helps with least."
    metrics = mt("score", "kills", "accuracy_pct", "ttk_median_ms", "headshot_pct", "aa_full_pct")
    dist = (10.0, 16.0)
    spread = 10.0

    def mover(self):
        return Strafe(3.8, (0.35, 0.9), 3.5, jump=0.9, crouch=0.8)


# ============================================================= micro-adjusting
class RecoilHold(CodScenario):
    id, group, name = "cod_micro_recoil", "Micro-adjusting", "Recoil Hold"
    aa = False
    tagline = "Still dummies at 30-40 m. Hold the spray on them."
    how = "The dummy does not move; your gun does. Keep the spray on its chest. The next one appears close by."
    measures = "Small, continuous corrections against recoil - bullets per kill is the number to drive down."
    metrics = mt("score", "kills", "accuracy_pct", "bullets_per_kill", "ttk_median_ms", "headshot_pct")
    dist = (30.0, 40.0)
    respawn = (0.2, 0.4)

    def place(self, now):
        off = self.rng.choice((-1.0, 1.0)) * self.rng.uniform(2.0, 5.0)
        return self._polar(max(-25.0, min(25.0, self.g.yaw + off)), self.rng.uniform(*self.dist))


class Jiggle(CodScenario):
    id, group, name = "cod_micro_jiggle", "Micro-adjusting", "Jiggle"
    aa = False
    tagline = "A dummy jiggling on the spot at long range."
    how = "The dummy makes short, quick side-steps. Stay on it with the smallest stick movements you can."
    measures = "Fine control right around stick centre, where deadzone and response curve matter most."
    metrics = mt("score", "kills", "accuracy_pct", "bullets_per_kill", "ttk_median_ms", "headshot_pct")
    dist = (26.0, 36.0)
    spread = 12.0

    def mover(self):
        return Strafe(2.6, (0.12, 0.30), 0.6)


class ClosePair(CodScenario):
    id, group, name = "cod_micro_pair", "Micro-adjusting", "Close Pair"
    tagline = "Two dummies a few degrees apart."
    how = "Two dummies stand close together at range. Kill one and shift to its neighbour."
    measures = ("Tiny transfers with aim assist on. Their slowdown bubbles overlap, so the assist can hold "
                "you on the wrong one - you have to push through it.")
    metrics = mt("score", "kills", "accuracy_pct", "swap_median_ms", "bullets_per_kill", "first_shot_hit_pct")
    alive_max = 2
    respawn = (0.15, 0.3)
    dist = (26.0, 34.0)
    spread = 14.0

    def place(self, now):
        if not self.dummies:
            return super().place(now)
        o = self.dummies[0]
        dist = math.hypot(o.x - self.kit.x, o.z - self.kit.z) + self.rng.uniform(-2.0, 2.0)
        bearing = math.degrees(math.atan2(o.x - self.kit.x, -(o.z - self.kit.z)))
        return self._polar(bearing + self.rng.choice((-1.0, 1.0)) * self.rng.uniform(2.0, 4.5), dist)


# ================================================================== precision
class Headhunter(CodScenario):
    id, group, name = "cod_prec_head", "Precision", "Headhunter"
    aa = False
    head_only = True
    tagline = "Only headshots count."
    how = "Body shots do nothing here. Three headshots kill at these distances (10-26 m)."
    measures = "Deliberate head-level accuracy: what share of your bullets are headshots."
    primary = "head_acc_pct"
    metrics = mt("head_acc_pct", "kills", "score", "kill_median_ms", "first_hit_median_ms")
    dist = (10.0, 26.0)
    spread = 24.0


class LongShot(CodScenario):
    id, group, name = "cod_prec_long", "Precision", "Long Shot"
    tagline = "Small, distant, drifting targets."
    how = ("Dummies at 45-58 m, moving slowly. Rotational assist is down to half or less out here, and "
           "headshots take four.")
    measures = "Long-range precision, where a degree of error is a metre of miss."
    metrics = mt("score", "kills", "accuracy_pct", "bullets_per_kill", "headshot_pct", "aa_avg_pct")
    dist = (45.0, 58.0)
    spread = 10.0

    def mover(self):
        return Strafe(1.6, (0.8, 1.6), 2.5)


class HeadGlitch(CodScenario):
    id, group, name = "cod_prec_glitch", "Precision", "Head Glitch"
    aa = False
    tagline = "Heads over cover, ducking in and out."
    how = "Dummies stand behind low walls with only head and shoulders showing, and duck out of sight."
    measures = "Hitting the small part of a target that cover leaves you - the classic head-glitch fight."
    metrics = mt("score", "kills", "accuracy_pct", "headshot_pct", "kill_median_ms", "first_hit_median_ms")
    bot_head = True
    alive_max = 2
    respawn = (0.4, 0.9)
    SPOTS = ((-9.0, -15.0), (-3.0, -21.0), (3.5, -17.0), (9.5, -24.0), (0.0, -29.0), (-12.0, -27.0))

    def build(self):
        return World(self.room, [((x - 1.5, 0.0, z + 0.45), (x + 1.5, 1.38, z + 0.95)) for x, z in self.SPOTS])

    def place(self, now):
        free = [p for p in self.SPOTS if all(abs(d.x - p[0]) > 0.5 or abs(d.z - p[1]) > 0.5 for d in self.dummies)]
        return self.rng.choice(free)

    def mover(self):
        return Duck()


# ================================================================= aim assist
class SlowdownStop(SnapShot):
    id, group, name = "cod_aa_slow", "Aim assist", "Slowdown Stop"
    aa = True
    tagline = "Snap Shot again - with aim assist."
    how = "Same drill as Snap Shot. This time your look speed drops as the crosshair reaches the dummy."
    measures = ("How to use slowdown: throw the flick and let the slow zone catch it, instead of braking "
                "by hand. The meter shows SLOW when you are inside it.")
    metrics = mt("score", "kills", "accuracy_pct", "first_hit_median_ms", "kill_median_ms", "first_shot_hit_pct")


class RangeLadder(CodScenario):
    id, group, name = "cod_aa_range", "Aim assist", "Range Ladder"
    tagline = "The same fight at 4, 7, 10, 18, 30, 45 and 60 metres."
    how = "Strafing dummies come at fixed distances, nearest first, then round again. Watch the assist meter."
    measures = ("Where rotational assist works and where it does not. Black Ops 7: next to nothing at point "
                "blank, full from about 12 m. MW3 / BO6: full from 6 m. Modern Warfare 4: full from point "
                "blank. All fade past 35 m. Your accuracy by distance shows what that costs you.")
    metrics = mt("score", "kills", "acc_close_pct", "acc_mid_pct", "acc_far_pct", "aa_avg_pct")
    RUNGS = (4.0, 7.0, 10.0, 18.0, 30.0, 45.0, 60.0)
    timeout = 6.0
    respawn = (0.3, 0.5)

    def __init__(self, g):
        super().__init__(g)
        self.rung = 0

    def place(self, now):
        d = self.RUNGS[self.rung % len(self.RUNGS)]
        self.rung += 1
        return self._polar(self.rng.uniform(-12.0, 12.0), d)

    def mover(self):
        return Strafe(3.0, (0.4, 1.0), 2.5)


class StickyTrack(StrafeSpray):
    id, group, name = "cod_aa_track", "Aim assist", "Sticky Track"
    aa = True
    tagline = "Strafe Spray again - with aim assist."
    how = "Same strafing dummy. Rotational assist follows part of its movement, if you earn it."
    measures = ("The meter only reads FULL while your right stick is following the target. Without that you "
                "keep three quarters of the assist under Black Ops 7 rules and almost none under Modern "
                "Warfare 4 rules. Let go of both sticks and it switches off.")
    metrics = mt("score", "kills", "accuracy_pct", "aa_full_pct", "aa_avg_pct", "ttk_median_ms")


# ======================================================= rotational aim assist
class StrafeToStick(CodScenario):
    id, group, name = "cod_raa_strafe", "Rotational aim assist", "Strafe to Stick"
    strafe_required = True
    tagline = "Still dummies. Your bullets only count while you are moving."
    how = "Keep strafing with the left stick while you shoot. Shots fired standing still do no damage."
    measures = ("The habit rotational assist rewards: your own strafe moves the target across your screen, "
                "and the assist turns you with it - fully only while the right stick goes with it, and "
                "under Modern Warfare 4 rules hardly at all without it.")
    metrics = mt("score", "kills", "accuracy_pct", "aa_full_pct", "strafe_pct", "still_shots")
    dist = (10.0, 18.0)
    spread = 14.0
    move_area = (-10.0, 10.0, -2.0, 3.0)


class MirrorStrafe(CodScenario):
    id, group, name = "cod_raa_mirror", "Rotational aim assist", "Mirror Strafe"
    strafe_required = True
    tagline = "You strafe, it strafes."
    how = "The dummy strafes and reverses. Keep moving yourself and keep the right stick following it."
    measures = "Both sticks at once: left to move, right to track - the full strength of rotational assist."
    metrics = mt("score", "kills", "accuracy_pct", "aa_full_pct", "aa_avg_pct", "strafe_pct")
    dist = (11.0, 17.0)
    spread = 10.0
    move_area = (-10.0, 10.0, -2.0, 3.0)

    def mover(self):
        return Strafe(3.6, (0.35, 0.95), 4.0)


class StrafeDuel(CodScenario):
    id, group, name = "cod_raa_duel", "Rotational aim assist", "Strafe Duel"
    tagline = "A one-on-one where standing still gets you killed."
    how = ("The orange dummy shoots back, and a warning tone tells you when a new one is up. It hits far "
           "less often when you are moving across its aim.")
    measures = "A real gunfight: strafe to survive, track to keep your assist, finish before it finishes you."
    primary = "win_pct"
    metrics = mt("win_pct", "kills", "deaths", "accuracy_pct", "aa_full_pct", "strafe_pct")
    dist = (12.0, 18.0)
    spread = 12.0
    shoot_back = ((0.35, 0.60), 0.62)
    move_area = (-10.0, 10.0, -2.0, 3.0)
    respawn = (0.8, 1.3)

    def mover(self):
        return Strafe(3.4, (0.4, 1.0), 4.0)


# ================================================================== centering
class HeadLevel(CodScenario):
    id, group, name = "cod_center_level", "Centering", "Head Level"
    aa = False
    tagline = "Dummies pop up at any distance. Is your crosshair already at head height?"
    how = "Between kills, park your crosshair where a head would be. Distances vary from 8 to 40 m."
    measures = "Crosshair height discipline: how many degrees off head level you were when each one appeared."
    primary = "level_median_deg"
    metrics = mt("level_median_deg", "first_hit_median_ms", "kills", "accuracy_pct", "headshot_pct", "score")
    dist = (8.0, 40.0)
    spread = 22.0
    respawn = (0.5, 1.0)


class CornerSlice(CodScenario):
    id, group, name = "cod_center_corner", "Centering", "Corner Slice"
    tagline = "Strafe out past a wall onto a dummy you cannot see yet."
    how = ("Start behind the wall. Strafe right until the dummy comes into view, kill it, then step back "
           "behind the wall for the next.")
    measures = ("Pre-aiming a corner: how far your crosshair was from the dummy at the instant it came into "
                "view, and how fast the first hit followed.")
    primary = "preaim_median_deg"
    metrics = mt("preaim_median_deg", "first_hit_median_ms", "kills", "accuracy_pct", "first_shot_hit_pct", "score")
    start = (-1.2, 0.0, 0.0)
    move_area = (-3.0, 7.0, -1.5, 2.0)
    respawn = (0.2, 0.4)
    SPOTS = ((0.5, -12.0), (-1.5, -14.0), (1.0, -18.0), (-3.0, -20.0), (-5.0, -26.0), (-6.0, -32.0))

    def build(self):
        return World(self.room, [((-34.0, 0.0, -7.0), (1.2, 3.4, -6.2))])

    def can_spawn(self, now):
        return self.kit.x < 0.2                 # only once the player is back behind the wall

    def place(self, now):
        return self.rng.choice(self.SPOTS)

    def bot_move(self, g):
        if self.dummies:
            return (1.0, 0.0) if not self.dummies[0].s["vis"] and self.kit.x < 6.0 else (0.0, 0.0)
        return (-1.0, 0.0)

    def draw(self, ui, g):
        if not self.dummies and self.kit.x >= 0.2:
            ui.text(ui.W / 2, 640, "BACK BEHIND THE WALL", "m", WARN, 1)


class DoorWatch(CodScenario):
    id, group, name = "cod_center_doors", "Centering", "Door Watch"
    tagline = "Three doorways. One of them is about to have someone in it."
    how = ("A dummy steps into one of three doorways, then back out. Some of them shoot - those you hear "
           "coming. Hold a sensible angle.")
    measures = "Holding an angle: how close your crosshair already was when the dummy stepped out."
    primary = "preaim_median_deg"
    metrics = mt("preaim_median_deg", "first_hit_median_ms", "kills", "accuracy_pct", "deaths", "score")
    DOORS = (-10.0, 0.0, 10.0)
    WALL_Z = -26.0
    shoot_back = ((0.9, 1.4), 0.40)
    respawn = (0.5, 1.1)
    timeout = 7.0

    def build(self):
        edges = [-34.0] + [e for x in self.DOORS for e in (x - 1.2, x + 1.2)] + [34.0]
        return World(self.room, [_wall(edges[i], edges[i + 1], self.WALL_Z) for i in range(0, len(edges), 2)])

    def place(self, now):
        door = self.rng.choice(self.DOORS)
        self._side = self.rng.choice((-1.0, 1.0))
        return door + self._side * 2.1, self.WALL_Z - 1.3

    def mover(self):
        return Peek(reach=2.1, out=(1.1, 1.8), hidden=(0.7, 1.4), speed=4.5)

    def spawn(self, now):
        d = super().spawn(now)
        # step toward the doorway it was placed beside, whichever way the player happens to be standing
        d.mover.sx, d.mover.sz, d.mover.dir = -self._side, 0.0, 1.0
        if self.rng.random() < 0.5:
            d.shooter = None                    # only some of them fire
        return d


# ================================================================= first shot
class FirstBullet(CodScenario):
    id, group, name = "cod_first_bullet", "First-shot", "First Bullet"
    aa = False
    hold_fire = False
    tagline = "One bullet each. Make it land."
    how = "A dummy appears a few degrees off. Your first bullet decides it: hit and it drops, miss and it leaves."
    measures = "First-shot accuracy and how long you take to fire it - the opening of every gunfight."
    primary = "first_shot_hit_pct"
    metrics = mt("first_shot_hit_pct", "first_shot_median_ms", "kills", "level_median_deg", "score")
    dist = (14.0, 34.0)
    respawn = (0.45, 0.85)
    flick = (5.0, 18.0)

    def __init__(self, g):
        super().__init__(g)
        self.ignore_until = 0.0

    def place(self, now):
        bearing = self.g.yaw + self.rng.choice((-1.0, 1.0)) * self.rng.uniform(*self.flick)
        return self._polar(max(-60.0, min(60.0, bearing)), self.rng.uniform(*self.dist))

    def on_bullet(self, t, hit):
        if t < self.ignore_until or not self.dummies:
            return                              # the rest of the burst, or nothing to shoot at: not counted
        d = self.dummies[0]
        self.ignore_until = t + 0.35
        if not self._valid(d):
            return
        self.shots += 1
        self.shot(bool(hit and hit[2] is d))
        self.first_shots += 1
        d.s["first_shot"], d.s["shots"] = t, 1
        seen = d.s["seen"] if d.s["seen"] is not None else d.s["t0"]
        if hit and hit[2] is d:
            self.hits += 1
            self.first_hits += 1
            d.s["first_shot_hit"] = True
            d.s["hits"] = 1
            if hit[1] == "head":
                self.heads += 1
            self.kill(d, t)
        else:
            self.events.append({"t": round(d.s["t0"], 3), "dist_m": round(d.s["dist0"], 1), "killed": False,
                                "preaim_deg": _r(d.s["pre"], 2), "level_deg": _r(d.s["level"], 2),
                                "first_shot_ms": _r((t - seen) * 1000.0), "first_shot_hit": False,
                                "ads_shot_ms": _r(None if d.s["ads_at"] is None else (t - d.s["ads_at"]) * 1000.0),
                                "open_dmg": 0})
            self.g.fx_miss()
            self.retire(d, t)

    def _valid(self, d) -> bool:
        return True

    def _score(self):
        return round(1000.0 * self.first_hits / self.first_shots) if self.first_shots else 0

    def hud(self):
        pct = 100.0 * self.first_hits / self.first_shots if self.first_shots else 0.0
        return (("HITS", str(self.first_hits)), ("FIRST BULLET", f"{pct:.0f}%"))


class SnapAds(FirstBullet):
    id, group, name = "cod_first_ads", "First-shot", "Snap ADS"
    aa = True
    tagline = "From the hip: sights up, one bullet."
    how = "Let go of Aim between targets. When one appears, raise your sights and fire once. Hip-fire misses."
    measures = "The whole opening move: time from pressing Aim to the bullet leaving, and whether it lands."
    metrics = mt("first_shot_hit_pct", "ads_shot_median_ms", "first_shot_median_ms", "kills", "score")

    def can_spawn(self, now):
        return not self.g.ads_held

    def _valid(self, d):
        return True

    def draw(self, ui, g):
        if not self.dummies and g.ads_held:
            ui.text(ui.W / 2, 640, "RELEASE AIM", "m", WARN, 1)


class OpeningBurst(CodScenario):
    id, group, name = "cod_first_burst", "First-shot", "Opening Burst"
    tagline = "It shoots almost as soon as it sees you. Win the first 0.4 seconds."
    how = ("The orange dummy fires back quickly; the warning tone is your cue. What matters is the damage "
           "you land right at the start.")
    measures = "Opening damage: how much of the kill you have done 0.4 seconds after your first bullet."
    primary = "opening_dmg"
    metrics = mt("opening_dmg", "win_pct", "kills", "deaths", "first_shot_hit_pct", "accuracy_pct")
    dist = (12.0, 22.0)
    spread = 18.0
    shoot_back = ((0.30, 0.45), 0.55)
    respawn = (0.8, 1.4)

    def mover(self):
        return Strafe(3.2, (0.4, 1.0), 3.0)


# ======================================================= losing aim assist
class SmokeLine(CodScenario):
    id, group, name = "cod_loss_smoke", "Losing aim assist", "Smoke Line"
    tagline = "A dummy strafing in and out of smoke."
    how = "You can still make it out inside the smoke, but aim assist cannot. Keep tracking when it lets go."
    measures = "Your accuracy with assist against your accuracy the moment smoke takes it away."
    metrics = mt("score", "kills", "acc_assist_pct", "acc_raw_pct", "accuracy_pct", "aa_full_pct")
    Z = -17.0

    def build(self):
        return World(self.room, smokes=[(0.0, 1.8, self.Z, 4.6)], smoke_density=0.15)

    def place(self, now):
        return self.rng.uniform(-8.0, 8.0), self.Z + self.rng.uniform(-0.5, 0.8)

    def spawn(self, now):
        d = super().spawn(now)
        m = d.mover                     # strafe straight across, centred on the cloud, from wherever it started
        m.sx, m.sz, m.ox, m.oz, m.off = 1.0, 0.0, 0.0, d.z, d.x
        return d

    def mover(self):
        return Strafe(3.6, (1.6, 3.2), 10.0)        # long legs, so it really does leave the smoke


class CoverBreak(CodScenario):
    id, group, name = "cod_loss_cover", "Losing aim assist", "Cover Break"
    tagline = "A dummy running behind a row of pillars."
    how = "It disappears behind each pillar and reappears in the gap. Assist drops every time it is hidden."
    measures = "Re-acquiring: how quickly you are hitting again each time it comes back into view."
    metrics = mt("score", "kills", "reacquire_median_ms", "accuracy_pct", "acc_raw_pct", "aa_full_pct")
    dist = (19.0, 19.5)
    spread = 2.0

    def build(self):
        return World(self.room, [((x - 0.7, 0.0, -16.6), (x + 0.7, 3.2, -15.4)) for x in (-10, -5, 0, 5, 10)])

    def place(self, now):
        return 0.0, -19.2

    def mover(self):
        return Runner(4.4, 12.5)


class PointBlankPush(CodScenario):
    id, group, name = "cod_loss_push", "Losing aim assist", "Point Blank Push"
    tagline = "They run straight at you - and under Black Ops rules, assist fades as they close."
    how = "Dummies charge from 30 m, weaving. Kill them before they reach you - being touched puts you down."
    measures = ("Close-range fights: accuracy inside 10 m against accuracy further out. Black Ops 7 gives "
                "you the least help here; Modern Warfare 4 rules keep assisting right up to contact.")
    metrics = mt("score", "kills", "deaths", "acc_close_pct", "acc_mid_pct", "aa_avg_pct")
    alive_max = 2
    dist = (26.0, 34.0)
    spread = 24.0
    respawn = (0.9, 1.6)

    def mover(self):
        return Rush(5.4, 2.2, 0.9)

    def tick(self, d, dt, now):
        if d.alive and math.hypot(d.x - self.kit.x, d.z - self.kit.z) < 1.4:
            self.kit.take_damage(TUNE["hp"], d, now)
            self.retire(d, now)


# ======================================================= sensitivity finder
# Drills for the sensitivity finder. Not part of the track and never logged as runs:
# they are played in short blocks at different sensitivities and only their scores kept.
class SensTurn(CodScenario):
    id, name = "sens_turn", "Turn and Burn"
    how = "Dummies appear beside and behind you. Turn onto each one and kill it. Stay planted - no need to move."
    can_move = False
    show_meter = False
    start = (0.0, -34.0, 0.0)
    dist = (8.0, 16.0)
    respawn = (0.2, 0.4)

    def place(self, now):
        return self._polar(self.g.yaw + self.rng.choice((-1.0, 1.0)) * self.rng.uniform(50.0, 150.0),
                           self.rng.uniform(*self.dist))


class SensStrafe(CodScenario):
    id, name = "sens_strafe", "Strafe Kill"
    how = "A strafing dummy appears a little off your aim. Get on it and stay on it until it drops."
    can_move = False
    show_meter = False
    dist = (12.0, 24.0)
    respawn = (0.2, 0.4)

    def place(self, now):
        bearing = self.g.yaw + self.rng.choice((-1.0, 1.0)) * self.rng.uniform(6.0, 22.0)
        return self._polar(max(-60.0, min(60.0, bearing)), self.rng.uniform(*self.dist))

    def mover(self):
        return Strafe(3.4, (0.4, 1.0), 3.5)


GROUPS = (
    ("Flicking", (SnapShot, TargetSwap, CheckSix)),
    ("Tracking", (StrafeSpray, CrossingRunner, JumpShot)),
    ("Micro-adjusting", (RecoilHold, Jiggle, ClosePair)),
    ("Precision", (Headhunter, LongShot, HeadGlitch)),
    ("Aim assist", (SlowdownStop, RangeLadder, StickyTrack)),
    ("Rotational aim assist", (StrafeToStick, MirrorStrafe, StrafeDuel)),
    ("Centering", (HeadLevel, CornerSlice, DoorWatch)),
    ("First-shot", (FirstBullet, SnapAds, OpeningBurst)),
    ("Losing aim assist", (SmokeLine, CoverBreak, PointBlankPush)),
)
ALL = tuple(s for _, group in GROUPS for s in group)
