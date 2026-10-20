"""The ghost: a strong human player who plays the very targets of one of your runs.

WHAT IT PLAYS
The run's capture (capture.py) holds every target: where it was, frame by frame, and when it
went. The ghost's world puts the same targets in, in the same order, moving the same way - but
at the ghost's pace: when it kills one sooner than you did, the next arrives sooner, after the
same gap you had (Release). If it outlasts the run's targets it starts on them again. A target
the ghost is slower on than you were keeps moving (its path is played back and forth).

Its body is yours: it stands, walks, leans and crouches exactly as you did (the kits' `body`
hook). Only its hands are its own - the aim stick (or the mouse), the trigger and the sights.
And it plays through the game's real code: the same sensitivity, stick curve and deadzones, the
same aim assist (on the Call of Duty track, on a controller; never on a mouse, never on Siege),
recoil, damage and dummies that shoot back. So every number it gets is earned the way yours are.

HOW IT AIMS (Ghost)
A human, not an aimbot: it sees with a delay, reacts after a reaction time, throws a fast
"ballistic" first movement that lands near the target but not on it (short, more often than
long, and less exactly the further it went), then makes corrections, each after a pause to see
where it landed. Moving targets it follows with a lagging pursuit that anticipates most - not
all - of their motion. Its thumb (or hand) has a top speed, takes time to move and trembles.
The numbers (PARAMS) are a strong player's, from published research on human aiming; see
README "The ghost" for the sources and what is assumed.
"""
from __future__ import annotations

import bisect
import math
import random
from collections import deque
from types import SimpleNamespace

import numpy as np

from . import capture as capmod
from . import codscn, config, scenarios, tracks
from .codkit import BODY_R, HEAD_R, Dummy, Mover, Shooter, recoil_step, TUNE as COD_TUNE
from .game import Game
from .r6kit import SiegeDummy, SiegeShooter
from . import r6kit

DT = 1.0 / 240.0
P_ = capmod.P
BACK_AND_FORTH = ("Strafe", "Runner", "Patrol", "LeanSpam")     # movers that keep going to and fro


# ============================================================ the run's targets
class TargetBook:
    """Every target of a captured run: its path, when it came and went, and the rhythm of the
    run (how many were up at once, and the gap after a kill before the next one)."""

    def __init__(self, cap):
        t = cap.t
        self.info = cap.head["keys"]
        n = self.n = len(self.info)
        self.times, self.poses = [], []
        for k in range(n):
            fr, ps = cap.track_of(k)
            self.times.append(t[fr].astype(np.float64))
            self.poses.append(ps.astype(np.float64))
        gone = {}
        for e in cap.head["events"]:
            if e["kind"] == "removed" and e.get("key", -1) >= 0:
                gone[e["key"]] = (float(e["t"]), e.get("why", "kill"))
        self.pingpong = []
        for k in range(n):
            info, ps = self.info[k], self.poses[k]
            if info["kind"] == "dummy":
                self.pingpong.append(info.get("mover") in BACK_AND_FORTH)
            else:                               # a sphere that drifted (Spray Transfer), not one that sat still
                self.pingpong.append(len(ps) > 1 and float(np.abs(ps[-1, :3] - ps[0, :3]).max()) > 0.05)
        self.spawn = [float(ts[0]) if len(ts) else 0.0 for ts in self.times]
        self.last = [float(ts[-1]) if len(ts) else 0.0 for ts in self.times]
        self.why = [gone[k][1] if k in gone else "end" for k in range(n)]
        self.gone = [gone[k][0] if k in gone else self.last[k] for k in range(n)]
        self.order = sorted(range(n), key=lambda k: (self.spawn[k], k))
        spawns = sorted(self.spawn)
        outs = sorted(self.gone[k] for k in range(n))
        removals = sorted(self.gone[k] for k in range(n) if self.why[k] != "end")
        self.cap, self.gap = {}, {}
        for k in self.order:
            s = self.spawn[k] + 1e-9
            # how many were up just after it came (itself included): the room the ghost's world must have
            self.cap[k] = bisect.bisect_right(spawns, s) - bisect.bisect_right(outs, s)
            i = bisect.bisect_right(removals, s)
            self.gap[k] = self.spawn[k] - removals[i - 1] if i else None
        gaps = [g for g in self.gap.values() if g is not None]
        self.gap_typ = float(np.median(gaps)) if gaps else 0.5
        # Strafe Track: when the target turned round, and which way (to time the ghost's reversals as
        # the drill times yours)
        self.flips = [(float(e["t"]), e.get("dir", 0.0)) for e in cap.head["events"] if e["kind"] == "flip"]

    def pose(self, k, rel, now):
        """Where target k is `now` in a world that let it in at time `rel`. Once its own path runs
        out (you were quicker than the ghost): one that kept moving back and forth (a strafer, a
        runner) is played back and forth; anything else - one that peeked out, ducked, ran at you,
        or stood - stays where you dealt with it."""
        ts, ps = self.times[k], self.poses[k]
        if len(ts) < 2:
            return ps[0]
        s, e = ts[0], ts[-1]
        tau = s + (now - rel)
        if tau > e:
            span = e - s
            if span < 0.5 or not self.pingpong[k]:
                return ps[-1]
            m = (tau - e) % (2.0 * span)
            tau = e - m if m <= span else s + (m - span)
        i = int(np.searchsorted(ts, tau))
        if i <= 0:
            return ps[0]
        if i >= len(ts):
            return ps[-1]
        a, b = ts[i - 1], ts[i]
        w = (tau - a) / (b - a) if b > a else 0.0
        return ps[i - 1] + (ps[i] - ps[i - 1]) * w


class Release:
    """When the run's targets come into the ghost's world: in the run's order, as soon as there is
    room for them (no more up at once than you had) and the gap you had after a kill has passed
    since the ghost's own last kill."""

    def __init__(self, book):
        self.book = book
        self.i = 0
        self.alive: dict = {}            # ghost id -> (target, time it came in)
        self.last_out = None             # time of the ghost world's latest kill or timeout
        # (ghost time, your time, ghost id, target) at each release: the ghost's body follows what yours
        # was doing relative to the latest target - where you stood and which way you leaned when it came
        self.warps: list = [(0.0, 0.0, None, None)]

    def _anchor(self, w, now):
        r, s, gid, k = w
        t = s + (now - r)
        if gid is not None and gid in self.alive:
            # still alive for the ghost: hold the body as you had it when you dealt with it, rather than
            # walking on into what you did afterwards (and out of its sight)
            t = min(t, self.book.gone[k])
        return t

    def body_time(self, now: float):
        """Your time to take the body from at ghost time `now`, and the one before it (for a short
        blend when a new target re-anchors it): (time, previous time or None, blend 0-1)."""
        w = self.warps[-1]
        cur = self._anchor(w, now)
        if len(self.warps) > 1 and now - w[0] < 0.2:
            return cur, self._anchor(self.warps[-2], now), (now - w[0]) / 0.2
        return cur, None, 1.0

    def due(self, now):
        b, out = self.book, []
        while b.n:
            k, lap = b.order[self.i % b.n], self.i // b.n
            if len(self.alive) + len(out) >= b.cap[k]:
                break
            gap = b.gap[k]
            if gap is None and lap == 0:             # one of the run's opening targets: when you got it
                if now + 1e-9 < b.spawn[k]:
                    break
            else:
                if self.last_out is None:
                    if lap == 0:
                        break
                elif now + 1e-9 < self.last_out + (b.gap_typ if gap is None else gap):
                    break
            out.append((k, (k, lap)))
            self.warps.append((now, b.spawn[k], (k, lap), k))
            self.i += 1
        return out

    def put(self, gid, k, now):
        self.alive[gid] = (k, now)

    def removed(self, gid, now):
        if self.alive.pop(gid, None) is not None:
            self.last_out = now

    def overdue(self, gid, now) -> bool:
        """Should the ghost's world take this one away? When it timed out on you, after the same
        time; when you killed it, only if the ghost is getting nowhere with it."""
        got = self.alive.get(gid)
        if got is None:
            return False
        k, rel = got
        b = self.book
        life = b.gone[k] - b.spawn[k]
        if b.why[k] == "retire":
            return now - rel >= life
        if b.why[k] == "end":
            return False
        return now - rel >= max(2.0 * life, life + 6.0)


# ============================================================ puppet scenarios
class _Puppet:
    """Mixed in ahead of a scenario class: the scenario runs as written, but its targets are the
    run's (book + release) instead of its own."""

    book: TargetBook = None
    release: Release = None

    def removed(self, key, why):
        super().removed(key, why)
        self.release.removed(key, self.g.t)


class PuppetMover(Mover):
    """Moves a dummy along the path it took in the run."""

    def __init__(self, book, k, rel):
        self.book, self.k, self.rel = book, k, rel
        self.open_until = -1.0              # Shield Push: when the shield is swung open

    def start(self, d, g, rng):
        pass

    def step(self, d, dt, now, g):
        p = self.book.pose(self.k, self.rel, now)
        d.x, d.y, d.z, d.crouch = p[0], p[1], p[2], p[3]
        if isinstance(d, SiegeDummy):
            d.lean, d.lx, d.lz = p[4], p[5], p[6]
        self.open_until = now + 1.0 if p[7] > 0.5 else -1.0


class _DummyPuppet(_Puppet):
    def begin(self, now):
        self.next_spawn = 1e18

    def update(self, dt, now):
        self.next_spawn = 1e18              # the scenario's own spawning stays off
        for k, gid in self.release.due(now):
            self._put(k, gid, now)
        for d in list(self.dummies):
            if self.release.overdue(d.id, now):
                self.retire(d, now)
        super().update(dt, now)
        self.next_spawn = 1e18

    def _put(self, k, gid, now):
        info = self.book.info[k]
        siege = self.track == "r6"
        sh = info.get("shooter")
        shooter = None
        if sh:
            shooter = (SiegeShooter if siege else Shooter)(tuple(sh[0]), sh[1])
        mover = PuppetMover(self.book, k, now)
        p = self.book.pose(k, now, now)
        d = SiegeDummy(p[0], p[2], mover, shooter, info.get("speed") or 2) if siege else Dummy(p[0], p[2], mover, shooter)
        d.hp = d.hp_max = info.get("hp") or d.hp_max
        d.id = gid
        mover.step(d, 0.0, now, self.g)
        d.s = {"t0": now, "seen": None, "vis": False, "pre": None, "level": None, "first_shot": None,
               "first_shot_hit": False, "first_hit": None, "shots": 0, "hits": 0, "heads": 0,
               "back_at": None, "ads_at": None, "open_dmg": 0,
               "dist0": math.hypot(d.x - self.kit.x, d.z - self.kit.z)}
        self.dummies.append(d)
        self.release.put(gid, k, now)
        self.puppet_spawned(d)
        if d.shooter:
            self.announce(d)


class _ClickPuppet(_Puppet):
    def begin(self, now):
        self.next_at = 1e18

    def update(self, dt, now):
        g = self.g
        if self.cur is None:
            for k, gid in self.release.due(now):
                p = self.book.pose(k, now, now)
                self.cur = scenarios._Approach(g, now, (p[0], p[1], p[2]), p[3])
                self.cur_key = gid
                self.release.put(gid, k, now)
                break
        else:
            self.cur.step(g, now)
            if self.release.overdue(self.cur_key, now):
                self.removed(self.cur_key, "retire")
                self.cur = None


class _GridPuppet(_Puppet):
    def begin(self, now):
        pass

    def _spawn(self, avoid=None):
        pass

    def update(self, dt, now):
        for k, gid in self.release.due(now):
            p = self.book.pose(k, now, now)
            self.cells[gid] = (p[0], p[1], p[2])
            self.keys[gid] = gid
            self.release.put(gid, k, now)
        for gid in list(self.cells):
            if self.release.overdue(gid, now):
                del self.cells[gid]
                self.keys.pop(gid, None)
                self.removed(gid, "retire")


class _TrackPuppet(_Puppet):
    _k = None

    def move(self, dt, now):
        if self._k is None:
            for k, gid in self.release.due(now):
                self._k, self._rel = k, now
                self.release.put(gid, k, now)
                break
            if self._k is None:
                return
        p = self.book.pose(self._k, self._rel, now)
        self.tyaw, self.tpitch = float(p[4]), float(p[5])
        if hasattr(self, "reversals"):                  # Strafe Track times your reversal after each of its own
            fl = self.book.flips
            i = getattr(self, "_fi", 0)
            while i < len(fl) and fl[i][0] - self.book.spawn[self._k] + self._rel <= now:
                d = fl[i][1] or -self.dir
                self.dir = d
                self.flips += 1
                self.pending = now if self.g.yaw_rate * d < -3.0 else None
                i += 1
            self._fi = i
            if self.pending is not None:
                if self.g.yaw_rate * self.dir > 3.0:
                    self.reversals.append((now - self.pending) * 1000.0)
                    self.events.append({"t": round(self.pending, 3), "reversal_ms": round(self.reversals[-1], 1)})
                    self.pending = None
                elif now - self.pending > 0.8:
                    self.pending = None


class _SprayPuppet(_Puppet):
    def begin(self, now):
        pass

    def _new(self):
        pass

    def update(self, dt, now):
        g = self.g
        for k, gid in self.release.due(now):
            p = self.book.pose(k, now, now)
            self.targets.append({"id": gid, "k": k, "rel": now, "yaw": 0.0, "pitch": 0.0, "vy": 0.0, "vp": 0.0,
                                 "hp": int(self.book.info[k].get("hp") or self.hp), "pos": (p[0], p[1], p[2])})
            self.release.put(gid, k, now)
        for tg in list(self.targets):
            if self.release.overdue(tg["id"], now):
                self.targets.remove(tg)
                self.removed(tg["id"], "retire")
                continue
            p = self.book.pose(tg["k"], tg["rel"], now)
            tg["pos"] = (p[0], p[1], p[2])
        if g.fire_held:
            self.cool -= dt
            while self.cool <= 0.0:
                self._fire(now)
                self.cool += 1.0 / self.rate


def puppet_class(orig, book, release):
    if issubclass(orig, codscn.CodScenario):
        mix = _DummyPuppet
    elif issubclass(orig, scenarios._Click):
        mix = _ClickPuppet
    elif issubclass(orig, scenarios.Grid):
        mix = _GridPuppet
    elif issubclass(orig, scenarios._Tracking):
        mix = _TrackPuppet
    elif issubclass(orig, scenarios.Spray):
        mix = _SprayPuppet
    else:
        raise ValueError(f"no ghost for {orig.id}")
    return type("Ghost" + orig.__name__, (mix, orig), {"book": book, "release": release})


def playable(scn_id: str) -> bool:
    cls = tracks.BY_ID.get(scn_id)
    return cls is not None and scn_id not in capmod.NOT_CAPTURED and issubclass(
        cls, (codscn.CodScenario, scenarios._Click, scenarios.Grid, scenarios._Tracking, scenarios.Spray))


# ============================================================ the ghost's hands
class _Pad:
    """What the game reads as a controller: the ghost's aim stick and buttons."""

    def __init__(self, aim_stick):
        self.events: list = []
        self.samples: list = []
        self.held: set = set()
        self.aim_stick = aim_stick
        self.aim = (0.0, 0.0)
        self.lost = False
        self.active = 0

    def stick(self, name):
        return self.aim if name == self.aim_stick else (0.0, 0.0)

    def down(self, name):
        return name in self.held


class _Mouse:
    """...or as a mouse: the ghost's hand, as counts, and its buttons."""

    def __init__(self):
        self.events: list = []
        self.held: set = set()
        self.reports = self.paired = 0
        self.virtual = True
        self.hz = 0.0
        self.age_ms = 0.0
        self.dx = self.dy = 0

    def down(self, name):
        return name in self.held


# Numbers for a strong human player. (s = seconds; shares are of the movement's size or of the
# target's angular radius.) Sources and caveats: README "The ghost".
PARAMS = {
    "pad": dict(
        # [R] = from research (README "The ghost" lists the sources); [A] = assumed, nothing published
        rt=0.185, rt_sd=0.025,          # [R] notice a new target -> start moving: a top-quartile visual reaction
                                        #     (~185 ms), a little helped by the drill's steady rhythm
        switch=0.120, switch_sd=0.022,  # [A] the next target already in view: seen and chosen while killing this one
        tau=0.085,                      # [R] how old what it sees is when it steers (pursuit latency 100-130 ms)
        pred=0.93,                      # [R/A] how much of a target's steady motion it anticipates (eye
                                        #     pursuit gain is ~0.9-1.0 for steady motion; the hand's share assumed)
        vwin=0.05,                      # [A] judging a target's speed takes this long a look
        flip=0.07,                      # [R] ...and believing it has turned round takes this much longer: a
                                        #     sudden change of direction is answered in ~190-200 ms (Kelkkanen)
        mt_a=0.06, mt_b=0.070,          # [R] first movement's duration: a + b * log2(D / W + 1); a stick is about
                                        #     half as fast as a mouse at this (ISO 9241-9 throughput)
        under=0.06, k_par=0.13, k_perp=0.056,   # [R] the first movement tends to land short, its spread along the
                                        #     way 2.3 times its spread across (Burkitt; Warburton) - sometimes past
        bow=0.12,                       # [A] a thumb swings in an arc: how much a movement bows to one side
        finger=0.04,                    # [A] the trigger finger acts this long after the decision to fire
        ramp=0.07, brake=0.10,          # [A] a turn faster than the stick allows: full stick, then brake
        perc=0.06, perc_rel=0.15,       # [A] judging where the target is: off by this (deg) + share of its size
        corr_delay=0.04,                # [R] then a correction ~110-120 ms after landing (tau is part of it)
        tol=0.55,                       # [A] "on it": within this share of the target's radius
        kp=4.3,                         # [R] following: crossover ~4.3 rad/s for rate (stick) control (McRuer)
        sdn=0.06,                       # [A] speed noise, share of the speed (signal-dependent noise)
        tremor=0.010,                   # [A] thumb tremor, stick deflection, at ~9 Hz [R]
        lag=0.025, slew=14.0,           # [A] the stick trails the thumb's intent; fastest change, full sweeps / s
        dwell=0.0, click_frac=0.80, refractory=0.11,     # [R] skilled players fire early, as the crosshair arrives
        fire_on=1.2, fire_off=2.6,      # [A] start a burst within / give it up beyond these shares of the target...
        burst=0.20,                     # [A] ...and once started, hold it at least this long
        ff=0.55, ff_sd=0.20, ff_delay=0.12,   # [A] recoil: share of the climb pulled down (varies burst to
                                        #     burst) once it has been firing this long - nothing published
        ads_within=35.0,                # [A] aim down sights as it starts on a target closer than this (deg);
                                        #     further, it turns from the hip and raises them on arrival
        hold_gain=3.0, hold_rate=90.0,  # [A] drifting back to where the next one is likely to show
        assist_adapt=0.6,               # [A] how far it has learned to lean on aim assist (0 = not at all)
    ),
    "kbm": dict(
        rt=0.180, rt_sd=0.025,
        switch=0.110, switch_sd=0.020,
        tau=0.080,
        pred=0.95,
        vwin=0.05, flip=0.06,
        mt_a=0.04, mt_b=0.060,          # [R] a mouse: about twice a stick's throughput
        under=0.05, k_par=0.11, k_perp=0.048,
        bow=0.06,
        finger=0.03,
        ramp=0.05, brake=0.08,
        perc=0.05, perc_rel=0.15,
        corr_delay=0.035,
        tol=0.55,
        kp=4.8,                         # [R] crossover ~4.8 rad/s for position (mouse) control
        sdn=0.05,
        tremor=0.35,                    # [A] hand tremor, cm/s, at ~9 Hz [R]
        lag=0.015, hand=150.0,          # [R] fastest hand speed ~0.9-1.7 m/s in fast aimed movements
        dwell=0.0, click_frac=0.80, refractory=0.10,
        fire_on=1.2, fire_off=2.6, burst=0.20,
        ff=0.60, ff_sd=0.20, ff_delay=0.10,
        ads_within=35.0,
        hold_gain=3.0, hold_rate=120.0,
    ),
}


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


class Ghost:
    """The ghost's eyes and hands for one run (see the notes at the top)."""

    def __init__(self, g, kbm: bool, seed: int = 7):
        self.g = g
        self.kbm = kbm
        self.p = dict(PARAMS["kbm" if kbm else "pad"])
        self.rng = random.Random(seed)
        scn = g.scn
        self.click = not scn.hold_fire                  # a shot per press (else hold the trigger)
        self.ads_needed = bool(scn.needs_ads)
        self.always_fire = isinstance(scn, scenarios._Tracking)
        steps = max(1, round(self.p["tau"] / DT))
        self.hist: deque = deque(maxlen=steps + 40)      # what it saw, newest last
        self.noticed: dict = {}                         # target -> when it noticed it
        self.first_seen: dict = {}
        self.mode = "idle"
        self.key = None
        self.free_at = 0.0
        self.plan = None
        self.t_mode = 0.0
        self.tol = self.p["click_frac"] if self.click else self.p["tol"]
        self.dwell_t = None
        self.last_click = -9.0
        self.fire = False
        self.fire_want, self.fire_due = False, None
        self.press_due: list = []
        self.fire_t = None
        self.ads = False
        self.ads_off_at = None
        self.cmd = [0.0, 0.0]                           # commanded turn rate (deg/s): what it intends
        self.out = [0.0, 0.0]                           # the stick (or the hand's speed), as it actually is
        self.noise = [0.0, 0.0]
        self.trem = [0.0, 0.0]
        self.tremv = [0.0, 0.0]
        self.pn = [0.0, 0.0]                            # how far off its idea of where the target is (in sd)
        self.v_used: dict = {}                          # target -> (the speed it goes by, since when it doubts it)
        self.eff = [0.0, 0.0]                           # how far it has meant to turn so far (yaw, pitch)
        self.cmd_prev = (0.0, 0.0)
        self.view = (g.yaw, g.pitch)                    # where it believes it is aiming
        self.ff_gain = self.p["ff"]
        self.rem = [0.0, 0.0]                           # mouse: fractions of a count not sent yet
        self.holds: list = []                           # where targets showed up so far (yaw, pitch)
        self.engaged: set = set()
        self.v_cur = (0.0, 0.0)                         # how fast it sees its target slide across the view
        self.corrections = 0
        self.climb = self._climb_rate()

    # ---- what the game is like to it ---------------------------------------------
    def _climb_rate(self) -> float:
        """How fast, in deg/s, the gun climbs while held - what a practised player pulls against."""
        kit = self.g.kit
        if kit is None:
            return 0.0
        if self.g.scn.track == "r6":
            rng = random.Random(1)
            ups = [r6kit.recoil_step(kit.gun, n, rng, kit.barrel, kit.grip)[1] for n in range(12)]
        else:
            ups = [recoil_step(n)[1] for n in range(12)]
        return sum(ups) / len(ups) / kit.interval

    def _rate_cap(self) -> float:
        """The fastest the view can turn right now, deg/s."""
        g, kit = self.g, self.g.kit
        if self.kbm:
            deg = kit.m_deg * (kit.m_ads if g.ads_held else 1.0) if kit else g.m_deg
            return self.p["hand"] / 2.54 * g.cfg["mouse"]["dpi"] * deg
        if kit is not None:
            return kit.rate_h * (kit.ads_scale if g.ads_held else 1.0)
        a = g.aim
        return a.rate_h * (1.0 + (a.ads - 1.0) * g.ads_t)

    def _see(self):
        """Targets as angles from the eye: {key: (yaw, pitch, radius)}, plus hold points."""
        g, scn = self.g, self.g.scn
        e = g.eye
        live = scn.live()
        idx = list(scn.capture_targets())
        seen, holds = {}, []
        for pos, r in live:
            dx, dy, dz = pos[0] - e[0], pos[1] - e[1], pos[2] - e[2]
            flat = math.hypot(dx, dz)
            dist = math.hypot(flat, dy)
            if dist < 1e-6:
                continue
            ang = (math.degrees(math.atan2(dx, -dz)), math.degrees(math.atan2(dy, flat)))
            if r <= 0.0:
                holds.append(ang)
                continue
            key, best = None, 1e9
            for k, kind, pose, vis, _ in idx:
                if kind == "dummy":
                    pts = ((pose[0], pose[1] + 1.2, pose[2]), (pose[0], pose[1] + 1.6, pose[2]), (pose[0], 0.17, pose[2]))
                else:
                    pts = ((pose[0], pose[1], pose[2]),)
                for q in pts:
                    d2 = (q[0] - pos[0]) ** 2 + (q[1] - pos[1]) ** 2 + (q[2] - pos[2]) ** 2
                    if d2 < best:
                        key, best = k, d2
            seen[key] = (ang[0], ang[1], math.degrees(math.asin(min(r / dist, 1.0))))
        return seen, holds

    # ---- one step -----------------------------------------------------------------
    def step(self, t: float, dt: float):
        g, p, rng = self.g, self.p, self.rng
        seen, holds = self._see()
        self.hist.append((t, seen, g.yaw, g.pitch, self.eff[0], self.eff[1]))
        lag = max(1, round(p["tau"] / dt))
        win = max(2, round(p["vwin"] / dt))             # it judges a target's speed over this long
        old = self.hist[-1 - lag] if len(self.hist) > lag else self.hist[0]
        prev = self.hist[-1 - lag - win] if len(self.hist) > lag + win else self.hist[0]
        vis = old[1]
        # Where it believes it is aiming: what it saw tau ago, plus what its own hand has done since (it
        # knows that at once). Anything else that moved the view - recoil, aim assist, its own shake -
        # it only sees tau later, as a player would.
        self.view = (old[2] + self.eff[0] - old[4], old[3] + self.eff[1] - old[5])
        k_pn = min(dt / 0.12, 1.0)                      # its misjudgement of where things are drifts slowly
        for i in (0, 1):
            self.pn[i] += (rng.gauss(0.0, 1.0) - self.pn[i]) * k_pn
        for k, a in vis.items():
            if k not in self.first_seen:                # a new one: noticed a reaction time after it showed
                self.first_seen[k] = (t, a[0], a[1])
                self.noticed[k] = t + max(0.04, rng.gauss(p["rt"], p["rt_sd"]) - p["tau"])

        # the target it is on: gone (killed, or out of sight)?
        if self.key is not None and self.key not in seen:
            self._drop(t)
        # pick one: the nearest it has noticed, once it is free to move on
        if self.key is None and t >= self.free_at:
            ready = [k for k in vis if self.noticed.get(k, 9e9) <= t]
            if ready:
                k = min(ready, key=lambda k: math.hypot(_wrap(vis[k][0] - self.view[0]), vis[k][1] - self.view[1]))
                self.key = k
                if k not in self.engaged:               # where they show up: it learns the room as it plays
                    self.engaged.add(k)
                    self.holds.append(self.first_seen[k][1:])
                self._plan_move(t, vis[k], self._vel(old, prev, k), first=True)

        # where it thinks the target is now (seen tau ago, carried forward by its speed)
        cmd = [0.0, 0.0]
        err = None
        self.v_cur = (0.0, 0.0)
        if self.key is not None and self.key in vis:
            a = vis[self.key]
            v = self.v_cur = self._vel(old, prev, self.key)
            sd = p["perc"] + p["perc_rel"] * a[2]       # (no eye is exact about where something is)
            ty = a[0] + v[0] * p["tau"] * p["pred"] + sd * self.pn[0]
            tp = a[1] + v[1] * p["tau"] * p["pred"] + sd * self.pn[1]
            ex, ey = _wrap(ty - self.view[0]), tp - self.view[1]
            err = (ex, ey, a[2], v)
            moving = math.hypot(*v) > 4.0
            if self.mode == "move":
                pl = self.plan
                s = (t - pl["t0"]) / pl["T"]
                if s >= 1.0:
                    if pl.get("ads_at") is not None and not self.ads:
                        self._set_ads(t, True)          # a big turn done: sights up now
                    self.mode, self.t_mode = "settle", t
                else:
                    k = _profile(pl, t - pl["t0"])
                    # the arc: out to one side and back, so it still ends where it was aimed
                    side = pl["bow"] * math.pi * math.cos(math.pi * s) / pl["T"]
                    cmd = [pl["uy"] * k - pl["up"] * side + v[0] * pl["follow"],
                           pl["up"] * k + pl["uy"] * side + v[1] * pl["follow"]]
            if self.mode == "settle":
                cmd = [v[0] * p["pred"], v[1] * p["pred"]]
                if t - self.t_mode >= p["corr_delay"]:
                    off = math.hypot(ex, ey)
                    if off <= max(a[2] * (2.5 if moving else self.tol), 0.05):
                        self.mode, self.t_mode = ("track" if moving else "hold"), t
                    else:
                        self.corrections += 1
                        self._plan_move(t, a, v, first=False)
            if self.mode in ("track", "hold"):
                gain = p["kp"] if self.mode == "track" else p["kp"] * 0.8
                cmd = [v[0] * p["pred"] + gain * ex, v[1] * p["pred"] + gain * ey]
                if math.hypot(ex, ey) > a[2] * 4.0 + 3.0:          # lost it: a fresh movement
                    self._plan_move(t, a, v, first=False)
        elif self.key is None:
            # Nothing to shoot: hold where the next one will come - the spot the drill gives (a door, a
            # gap, a ping) or, on the Call of Duty and Siege tracks, where they have tended to show up.
            # Sights up while holding, as a player holding an angle would.
            target = holds[0] if holds else (self._typical() if self.holds and g.kit is not None else None)
            if target is not None:
                ex, ey = _wrap(target[0] - self.view[0]), target[1] - self.view[1]
                if math.hypot(ex, ey) > 1.0:
                    cap = p["hold_rate"]
                    cmd = [max(-cap, min(cap, p["hold_gain"] * ex)), max(-cap, min(cap, p["hold_gain"] * ey))]
                if holds and self.ads_needed and not self.ads:
                    self._set_ads(t, True)

        # the trigger and the sights
        self._triggers(t, err)
        climb = self.climb if (self.fire and g.kit is not None) else 0.0
        if self.fire and self.ff_on(t):
            cmd[1] -= self.ff_gain * climb
        # what it expects its view to do: its own intent, and the climb it knows the gun has
        self.eff[0] += cmd[0] * dt
        self.eff[1] += (cmd[1] + climb) * dt
        self.cmd_prev = (cmd[0], cmd[1])
        self._drive(t, dt, cmd)

    def ff_on(self, t):
        return self.fire_t is not None and t - self.fire_t >= self.p["ff_delay"] and self.g.kit is not None

    def _typical(self):
        """Where targets have tended to show up (it learns the room as it plays)."""
        hs = self.holds[-8:]
        return (sum(h[0] for h in hs) / len(hs), sum(h[1] for h in hs) / len(hs))

    def _vel(self, old, prev, k):
        """How fast it sees target k sliding across the view (deg/s). A reversal is believed only once
        it has gone on for `flip` seconds; until then it carries on with the old direction."""
        a, b = old[1].get(k), prev[1].get(k)
        dt = old[0] - prev[0]
        if a is None or b is None or dt <= 0.0:
            return (0.0, 0.0)
        vy, vp = _wrap(a[0] - b[0]) / dt, (a[1] - b[1]) / dt
        if abs(vy) > 400.0 or abs(vp) > 400.0:
            return (0.0, 0.0)
        t = old[0]
        used, since = self.v_used.get(k, ((vy, vp), None))
        out = []
        for i, v in enumerate((vy, vp)):
            u = used[i]
            if u * v < 0.0 and abs(v) > 4.0:            # it has turned round - or has it?
                s = since[i] if since and since[i] is not None else t
                if t - s < self.p["flip"]:
                    out.append((u, s))
                    continue
            out.append((v, None))
        self.v_used[k] = (tuple(o[0] for o in out), tuple(o[1] for o in out))
        return self.v_used[k][0]

    def _plan_move(self, t, a, v, first):
        """A ballistic movement onto the target: where it is expected to be, plus the error a human
        hand makes over that distance. Too far for the stick's top speed: full stick, then brake."""
        g, p, rng = self.g, self.p, self.rng
        ey0, ep0 = _wrap(a[0] - self.view[0]), a[1] - self.view[1]
        D0 = math.hypot(ey0, ep0)
        W = max(2.0 * a[2], 0.2)
        T = max(p["mt_a"] + p["mt_b"] * math.log2(D0 / W + 1.0), 0.05)
        ads_at = None
        if self.ads_needed and not self.ads:
            if D0 <= p["ads_within"] or g.kit is None:
                self._set_ads(t, True)                  # sights up as it moves: they are up by the time it arrives
            else:
                ads_at = -1.0                           # a big turn: from the hip, sights up as it brakes
        cap = self._rate_cap() * 0.95
        # aim where it will be when the movement ends (anticipated in part)
        ty = ey0 + v[0] * (p["tau"] + T) * p["pred"]
        tp = ep0 + v[1] * (p["tau"] + T) * p["pred"]
        D = math.hypot(ty, tp)
        if D > 1e-6:
            ux, uy = ty / D, tp / D
            along = D * (-p["under"] + rng.gauss(0.0, p["k_par"]))
            across = D * rng.gauss(0.0, p["k_perp"])
            ty += ux * along - uy * across
            tp += uy * along + ux * across
        dy, dp = ty - v[0] * T * p["pred"], tp - v[1] * T * p["pred"]
        dist = math.hypot(dy, dp)
        pl = {"t0": t, "T": T, "follow": p["pred"], "dist": dist, "uy": 0.0, "up": 0.0, "bow": 0.0}
        if dist > 1e-6:
            pl["uy"], pl["up"] = dy / dist, dp / dist
            pl["bow"] = rng.gauss(0.0, p["bow"]) * dist   # sideways at the middle of the movement, deg
        if dist * 2.07 / T > cap:                       # the bell would need more than full stick
            pl.update(cap=cap, ramp=p["ramp"], brake=p["brake"])
            pl["T"] = dist / cap + 0.5 * (p["ramp"] + p["brake"])
        if ads_at is not None:              # (pressing Aim changes the turn speed at once: not mid-turn)
            pl["ads_at"] = t + pl["T"]
        self.plan = pl
        self.mode, self.t_mode = "move", t

    def _drop(self, t):
        """The target is gone. Moving on to another takes a moment (it has to see that it went)."""
        self.key = None
        self.mode, self.plan = "idle", None
        self.dwell_t = None
        self.free_at = t + max(0.06, self.rng.gauss(self.p["switch"], self.p["switch_sd"]))
        if self.ads_needed:
            self.ads_off_at = t + 0.35

    def _set_ads(self, t, on):
        self.ads = on
        self.ads_off_at = None

    def _triggers(self, t, err):
        g, p = self.g, self.p
        if self.ads_off_at is not None and t >= self.ads_off_at and self.key is None:
            self.ads, self.ads_off_at = False, None
            self.held_at = None
        press = False
        if self.always_fire:
            fire = True
        elif err is None:
            fire = False
        else:
            ex, ey, rad, v = err
            off_now = math.hypot(ex, ey) / max(rad, 1e-3)
            # where the crosshair will be against the target when the finger acts: it fires on arrival
            rel = (self.cmd_prev[0] - v[0], self.cmd_prev[1] - v[1])
            off = math.hypot(ex - rel[0] * p["finger"], ey - rel[1] * p["finger"]) / max(rad, 1e-3)
            ready = not self.ads_needed or g.ads_t >= 0.8
            if self.click:
                fire = False
                # (never again before it has seen where the last shot left the sights: recoil moves them)
                gap = max(p["refractory"], p["tau"] + 0.03 if g.kit is not None else p["refractory"])
                if off_now <= 1.0 and off <= p["click_frac"] and ready:
                    if self.dwell_t is None:
                        self.dwell_t = t
                    if t - self.dwell_t >= p["dwell"] and t - self.last_click >= gap:
                        press, self.last_click = True, t
                else:
                    self.dwell_t = None
            else:
                committed = self.fire_t is not None and t - self.fire_t < p["burst"]
                fire = ready and (off <= p["fire_on"] or (self.fire_want and (committed or off_now <= p["fire_off"])))
        # the finger: what it decides happens `finger` seconds later (the sights keep moving meanwhile)
        if fire != self.fire_want:
            self.fire_want, self.fire_due = fire, t + p["finger"]
        if press:
            self.press_due.append(t + p["finger"])
        now_fire = self.fire
        if self.fire_due is not None and t >= self.fire_due:
            now_fire, self.fire_due = self.fire_want, None
        now_press = False
        while self.press_due and self.press_due[0] <= t:
            self.press_due.pop(0)
            now_press = True
        if now_fire and not self.fire:
            self.fire_t = t
            self.ff_gain = max(0.0, self.rng.gauss(p["ff"], p["ff_sd"]))     # how well this burst is held down
        if not now_fire:
            self.fire_t = None
        changed = now_fire != self.fire
        self.fire = now_fire
        self._buttons(t, now_press, changed)

    def _buttons(self, t, press, changed):
        g = self.g
        shoot, ads = g.binds["shoot"], g.binds["ads"]
        dev = g.mouse if self.kbm else g.pad
        ev = []
        want = set()
        if self.fire:
            want.add(shoot)
        if self.ads:
            want.add(ads)
        for name in (shoot, ads):
            if (name in want) != (name in dev.held):
                ev.append((t, name, name in want))
        if press:
            ev.append((t, shoot, True))
            ev.append((t, shoot, False))
        dev.held = want
        if self.kbm:
            dev.events = [(e[0], 1, e[1], e[2]) for e in ev]
        else:
            dev.events = ev

    def _drive(self, t, dt, cmd):
        """Intent -> what the thumb (or hand) actually does: noise, lag, limits, tremor."""
        g, p, rng = self.g, self.p, self.rng
        k = min(dt / 0.08, 1.0)
        for i in (0, 1):                                 # speed noise, smooth and proportional to the speed
            self.noise[i] += (rng.gauss(0.0, 1.0) - self.noise[i]) * k
        cmd = [cmd[0] * (1.0 + p["sdn"] * self.noise[0]), cmd[1] * (1.0 + p["sdn"] * self.noise[1])]
        # tremor: a lightly damped ~9 Hz oscillation kept going by noise, about unit size
        w, z = 2.0 * math.pi * 9.0, 0.25
        for i in (0, 1):
            x, v = self.trem[i], self.tremv[i]
            acc = -w * w * x - 2.0 * z * w * v + w * math.sqrt(4.0 * z * w / dt) * rng.gauss(0.0, 1.0)
            v += acc * dt
            self.tremv[i], self.trem[i] = v, x + v * dt
        if self.kbm:
            self._drive_mouse(t, dt, cmd)
        else:
            self._drive_pad(t, dt, cmd)

    def _drive_pad(self, t, dt, cmd):
        g, p = self.g, self.p
        kit = g.kit
        if kit is not None:
            resp = kit.aim
            scale = kit.ads_scale if g.ads_held else 1.0
            rate_h, rate_v = kit.rate_h * scale, kit.rate_v * scale
        else:
            resp = g.aim
            k = 1.0 + (resp.ads - 1.0) * g.ads_t
            rate_h, rate_v = resp.rate_h * k, resp.rate_v * k
        if kit is not None and g.scn.aa and getattr(kit.assist, "peak", None):
            # A practised player has learned their aim assist: the view slows near a target, and turns
            # with it by itself. They lean on it - in part (assist_adapt) - rather than fight it.
            info, c = kit.assist.info, p["assist_adapt"]
            slow = info["slow"] * COD_TUNE["slow_max"]
            rot = info["strength"] * kit.assist.peak
            v = self.v_cur
            cmd = [(cmd[0] - c * rot * v[0]) / max(1.0 - c * slow, 0.3),
                   (cmd[1] - c * rot * v[1] * kit.assist.pitch) / max(1.0 - c * slow, 0.3)]
        inv = g.cfg["aim"]["invert_y"]
        nx = cmd[0] / max(rate_h, 1e-6)
        ny = (-cmd[1] if inv else cmd[1]) / max(rate_v, 1e-6)
        m = math.hypot(nx, ny)
        target = [0.0, 0.0]
        if m > 1e-4:
            ux, uy = nx / m, ny / m
            m = min(m, 1.0)
            lo, hi = 0.0, 1.0
            for _ in range(16):                          # the deflection whose response is that fast
                mid = 0.5 * (lo + hi)
                if resp.shape(mid * ux, mid * uy)[2] < m:
                    lo = mid
                else:
                    hi = mid
            target = [hi * ux, hi * uy]
        a = min(dt / p["lag"], 1.0)
        lim = p["slew"] * dt
        for i in (0, 1):
            step = (target[i] - self.out[i]) * a
            self.out[i] += max(-lim, min(lim, step))
        sx = self.out[0] + p["tremor"] * self.trem[0]
        sy = self.out[1] + p["tremor"] * self.trem[1]
        mag = math.hypot(sx, sy)
        if mag > 1.0:
            sx, sy = sx / mag, sy / mag
        g.pad.aim = (sx, sy)

    def _drive_mouse(self, t, dt, cmd):
        """The hand works in centimetres: intent (deg/s) -> hand speed (cm/s), which lags, tops out and
        trembles -> counts."""
        g, p = self.g, self.p
        kit = g.kit
        if kit is not None:
            deg = kit.m_deg * (kit.m_ads if g.ads_held else 1.0)
        else:
            deg = g.m_deg * (1.0 + (g.msens.ads(g.fov, g.zoom_max) - 1.0) * g.ads_t)
        counts_per_cm = g.cfg["mouse"]["dpi"] / 2.54
        cm = [c / deg / counts_per_cm for c in cmd]          # cm/s the hand is asked for
        sp = math.hypot(*cm)
        if sp > p["hand"]:
            cm = [c * p["hand"] / sp for c in cm]
        a = min(dt / p["lag"], 1.0)
        for i in (0, 1):
            self.out[i] += (cm[i] - self.out[i]) * a
        up = 1.0 if g.cfg["aim"]["invert_y"] else -1.0
        dx = (self.out[0] + p["tremor"] * self.trem[0]) * dt * counts_per_cm
        dy = (self.out[1] + p["tremor"] * self.trem[1]) * dt * counts_per_cm * up
        self.rem[0] += dx
        self.rem[1] += dy
        cx, cy = int(self.rem[0]), int(self.rem[1])
        self.rem[0] -= cx
        self.rem[1] -= cy
        if cx or cy:
            g.mouse.events = [(t, 0, cx, cy)] + g.mouse.events


def _mjv(s: float) -> float:
    """Speed profile of an aimed movement, as a share of the distance per unit of normalised time:
    bell-shaped like the minimum-jerk model, but peaking at 40% of the movement and braking for
    longer, as measured aimed movements do (Burkitt et al.: peak speed at ~40% of the time)."""
    return 60.0 * s * s * (1.0 - s) ** 3


def _smooth(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def _profile(pl, tau: float) -> float:
    """Speed (deg/s) a planned movement asks for, tau seconds in: the bell above, or - for a turn the
    stick cannot make that fast - a ramp up to full stick, a hold, and a brake at the end."""
    T = pl["T"]
    if "cap" not in pl:
        return pl["dist"] * _mjv(tau / T) / T
    return pl["cap"] * min(_smooth(tau / pl["ramp"]), _smooth((T - tau) / pl["brake"]))


# ============================================================ a whole run
class GhostRun:
    """The ghost playing one captured run, headless, as fast as the computer allows."""

    def __init__(self, cap, seed: int = 7):
        self.cap = cap
        head = cap.head
        self.book = TargetBook(cap)
        self.release = Release(self.book)
        cls = puppet_class(tracks.BY_ID[head["scenario"]], self.book, self.release)
        cfg = head["cfg"]
        self.kbm = bool(head["kbm"])
        app = SimpleNamespace(cfg=cfg, pad=_Pad(cfg["aim"]["stick"]), mouse=_Mouse(),
                              opts=SimpleNamespace(duration=None, fast=True), gfx=SimpleNamespace(aspect=head.get("aspect", 16 / 9)),
                              sfx=SimpleNamespace(play=lambda *a: None), session_id="ghost")
        over = dict(head.get("over") or {}, endless=True)
        g = self.g = Game(app, cls, over=over)
        g.rng.seed(seed)
        g.state, g.started, g.t, g.offset = "run", True, 0.0, 0.0
        if g.kit is not None:
            v = cap.view
            ts = cap.t
            cols = [v[:, capmod.V[c]] for c in ("bx", "bz", "bvx", "bvz", "strafe", "lean", "crouch")]
            rel = self.release

            def at(tu):
                i = min(max(int(np.searchsorted(ts, tu)), 0), len(ts) - 1)
                return [float(c[i]) for c in cols]

            def body(now):
                tu, t_prev, w = rel.body_time(now)
                b = at(tu)
                if t_prev is not None:
                    a = at(t_prev)
                    b = [x + (y - x) * w for x, y in zip(a, b)]
                return tuple(b)
            g.kit.body = body
            g.kit.x, g.kit.z = float(cols[0][0]), float(cols[1][0])
        y0, p0 = float(cap.view[0, capmod.V["yaw"]]), float(cap.view[0, capmod.V["pitch"]])
        g.yaw, g.pitch = y0, p0
        g._update_cam()
        g.scn.begin(0.0)
        self.ghost = Ghost(g, self.kbm, seed)
        self.end = cap.duration
        self.done = False

    def run(self, until: float | None = None, cancel=None, progress=None):
        """Play up to game time `until` (default: the whole run)."""
        g, gh = self.g, self.ghost
        end = self.end if until is None else min(until, self.end)
        n = 0
        while g.t < end:
            if cancel is not None and cancel():
                return False
            g.pad.events, g.mouse.events = [], []
            gh.step(g.t, DT)
            g.update(DT, g.t + DT, set(), DT)
            n += 1
            if progress is not None and n % 240 == 0:
                progress(g.t / max(self.end, 1e-6))
        self.done = g.t >= self.end
        return True

    def result(self) -> dict:
        g = self.g
        stats = g.scn.summary()
        if g.kit is not None:
            stats.update(g.kit.summary())
        cap = g.rec.finish({"run_id": self.cap.head.get("run_id", "") + "-ghost", "ghost": True,
                            "duration": round(g.t, 3), "stats": stats,
                            "metrics": self.cap.head.get("metrics"), "primary": self.cap.head.get("primary")})
        return {"stats": stats, "capture": cap, "corrections": self.ghost.corrections}
