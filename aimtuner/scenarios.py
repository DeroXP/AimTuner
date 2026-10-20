"""The nine scenarios.

Pure logic - no GL - so analyze.py can import the metric tables. Each scenario
talks to the running Game (`g`) for the camera, clock, input and effects, and
ends by returning a flat `summary()` dict whose keys are described by `metrics`:
(key, label, unit, better, decimals) with better = +1 higher / -1 lower.

All times a scenario sees are game-clock seconds; shot and stick events arrive
with the input thread's timestamps, not the frame's.
"""
from __future__ import annotations

import math
import statistics

from .theme import ACCENT, BAD, DIM, GOOD, WARN, WHITE, alpha


def _clean(xs):
    return [x for x in xs if x is not None]


def _med(xs):
    xs = _clean(xs)
    return statistics.median(xs) if xs else None


def _avg(xs):
    xs = _clean(xs)
    return statistics.fmean(xs) if xs else None


def _sd(xs):
    xs = _clean(xs)
    return statistics.stdev(xs) if len(xs) > 1 else None


def _r(x, n=1):
    return None if x is None else round(x, n)


def _radius(ang_deg: float, dist: float) -> float:
    return dist * math.sin(math.radians(ang_deg))


def _mix(a, b, k):
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


class Scenario:
    id = name = tagline = how = measures = ""
    track = "core"
    cod = False             # True: a Call of Duty track scenario
    kit_cls = None          # the kit that drives movement, aim and the weapon (None on the Core track)
    timed = True            # runs for the configured round length (else: fixed trial count)
    lock_aim = False
    hold_fire = False       # shoot is held, not tapped
    needs_ads = False
    crosshair = True
    primary = "score"
    # The 0-100 "share on target" number the progress screen ranks scenarios by when it picks
    # what to train (stats.grade); None for drills that have no such thing.
    grade = "accuracy_pct"
    pad_only = False        # True: needs a controller (not offered to a mouse-and-keyboard profile)
    metrics: tuple = ()

    def __init__(self, g):
        self.g = g
        self.rng = g.rng
        self.events: list = []
        self.done = False
        self.shots = self.hits = self.kills = 0
        # played with no end (see Game): a timed drill just keeps going; one with a set number
        # of trials (Reflex, Stick Snap) keeps adding trials instead of stopping
        self.endless = bool(getattr(g, "endless", False))

    def begin(self, now): ...
    def on_shoot(self, t): ...
    def on_ads(self, t, down): ...
    def update(self, dt, now): ...
    def presented(self, t): ...
    def draw(self, ui, g): ...

    def spheres(self):
        return ()

    def live(self):
        """(pos, radius) of the targets currently worth aiming at."""
        return ()

    def hud(self):
        return ()

    def progress(self) -> str:
        return ""

    def summary(self) -> dict:
        return {}

    def trace(self):
        return None

    def capture_targets(self):
        """(key, kind, pose, visible, static or None) for every target there is this frame, for the
        replay and the ghost (capture.py). pose = (x, y, z, a, b, c, d, flag) by kind:
          "sphere"  a = radius, flag = hits it still takes
          "track"   a = radius, b / c = its yaw / pitch from the eye
          "dummy"   a = crouch, b = lean, c / d = the way a lean moves its head, flag = shield open"""
        return ()

    def removed(self, key, why: str):
        """A target went: "kill", or "retire" (it timed out). For the capture."""
        rec = getattr(self.g, "rec", None)
        if rec is not None:
            rec.event("removed", key=key, why=why)

    def shot(self, hit: bool):
        """A shot was fired (a click, or one bullet). For the capture."""
        rec = getattr(self.g, "rec", None)
        if rec is not None:
            rec.event("shot", hit=bool(hit))

    def _accuracy(self) -> float:
        return 100.0 * self.hits / self.shots if self.shots else 0.0

    def _score(self) -> int:
        # Kills are the headline, scaled so spraying misses cannot buy them cheaply.
        return round(self.kills * 100 * (0.4 + 0.6 * self._accuracy() / 100.0))


# ------------------------------------------------------------------ reaction
class Reaction(Scenario):
    id = "reaction"
    name = "Reflex"
    tagline = "Fire the instant the orb turns green."
    how = "No aiming. Wait for green, then press Shoot. Pressing early voids the attempt."
    measures = ("Pure reaction on your Shoot bind: your reflexes plus the button or trigger's "
                "travel and the pad's latency. The cleanest RB-versus-RT test.")
    timed = False
    lock_aim = True
    crosshair = False
    primary = "rt_median_ms"
    grade = None
    trials = 10
    metrics = (
        ("rt_median_ms", "Median reaction", "ms", -1, 1),
        ("rt_avg_ms", "Average reaction", "ms", -1, 1),
        ("rt_best_ms", "Fastest", "ms", -1, 1),
        ("rt_sd_ms", "Spread (SD)", "ms", -1, 1),
        ("false_starts", "False starts", "", -1, 0),
    )

    def __init__(self, g):
        super().__init__(g)
        self.rts: list = []
        self.false_starts = self.lapses = 0
        self.state = "wait"
        self.go_at = self.t_go = self.until = 0.0
        self.pending = False
        self.msg, self.msg_col = "", WHITE
        self.pos = g.at(0.0, 0.0, 14.0)

    def begin(self, now):
        self._arm(now)

    def _arm(self, now):
        self.state = "wait"
        self.go_at = now + self.rng.uniform(1.4, 3.6)

    def _show(self, now, msg, col, hold=0.85):
        self.state, self.until, self.msg, self.msg_col = "show", now + hold, msg, col

    def update(self, dt, now):
        if self.state == "wait" and now >= self.go_at:
            # The clock starts when the green frame is actually handed to the display
            # (see presented), not when we decided to draw it.
            self.state, self.t_go, self.pending = "go", now, True
        elif self.state == "go" and now - self.t_go > 2.0:
            self.lapses += 1
            self._show(now, "TOO SLOW", WARN)
        elif self.state == "show" and now >= self.until:
            if len(self.rts) >= self.trials and not self.endless:
                self.done = True
            else:
                self._arm(now)

    def presented(self, t):
        if self.pending:
            self.t_go, self.pending = t, False

    def on_shoot(self, t):
        if self.state == "wait":
            self.false_starts += 1
            self.g.sfx("miss")
            self._show(t, "TOO EARLY", BAD)
        elif self.state == "go":
            rt = (t - self.t_go) * 1000.0
            if rt < 90.0:       # nobody reacts to light this fast: it was a guess
                self.false_starts += 1
                self.g.sfx("miss")
                self._show(t, "TOO EARLY", BAD)
                return
            self.rts.append(rt)
            self.events.append({"trial": len(self.rts), "rt_ms": round(rt, 2)})
            self.g.sfx("hit")
            self._show(t, f"{rt:.0f} ms", GOOD)

    def spheres(self):
        col = {"wait": (0.86, 0.14, 0.12), "go": (0.20, 1.00, 0.36)}.get(self.state, (0.34, 0.37, 0.42))
        return ((self.pos, 1.5, col, 1.0, 1.4),)

    def progress(self):
        return f"{len(self.rts)}" if self.endless else f"{len(self.rts)} / {self.trials}"

    def draw(self, ui, g):
        cx = ui.W / 2
        if self.state == "show":
            ui.text(cx, 690, self.msg, "xl", self.msg_col, 1)
        elif self.state == "wait":
            ui.text(cx, 700, "WAIT FOR GREEN", "l", DIM, 1)

    def summary(self):
        med = _med(self.rts)
        return {
            "score": round(100000 / med) if med else 0,
            "rt_median_ms": _r(med), "rt_avg_ms": _r(_avg(self.rts)),
            "rt_best_ms": _r(min(self.rts)) if self.rts else None, "rt_sd_ms": _r(_sd(self.rts)),
            "false_starts": self.false_starts, "lapses": self.lapses, "trials": len(self.rts),
        }


# ---------------------------------------------------------------- stick snap
class StickSnap(Scenario):
    id = "sticksnap"
    name = "Stick Snap"
    tagline = "Throw the aim stick toward the arrow."
    how = "Let the stick centre, wait for the arrow, then slam the aim stick that way and let go."
    measures = ("The stick itself: how soon it leaves centre after the cue, and how long the throw "
                "from 20% to 90% deflection takes. Stick tension, throw length and latency show up here.")
    timed = False
    lock_aim = True
    crosshair = False
    primary = "stick_rt_median_ms"
    grade = None
    pad_only = True         # it times a thumbstick leaving centre; there is nothing to time on a mouse
    trials = 16
    metrics = (
        ("stick_rt_median_ms", "Median stick reaction", "ms", -1, 1),
        ("throw_median_ms", "Median throw (20% to 90%)", "ms", -1, 1),
        ("stick_rt_sd_ms", "Spread (SD)", "ms", -1, 1),
        ("wrong_dir", "Wrong direction", "", -1, 0),
        ("false_starts", "False starts", "", -1, 0),
    )
    DIRS = {"right": (1.0, 0.0), "left": (-1.0, 0.0), "up": (0.0, 1.0), "down": (0.0, -1.0)}

    def __init__(self, g):
        super().__init__(g)
        self.queue = list(self.DIRS) * (self.trials // 4)
        self.rng.shuffle(self.queue)
        self.n = 0
        self.state = "center"
        self.center_since = None
        self.go_at = self.t_go = self.until = 0.0
        self.pending = False
        self.cue = None
        self.t_react = self.t_full = self.dir_ok = None
        self.wrong = self.false_starts = self.missed = 0
        self.msg, self.msg_col = "", WHITE

    def _show(self, now, msg, col):
        self.state, self.until, self.msg, self.msg_col, self.cue = "show", now + 0.7, msg, col, None

    def update(self, dt, now):
        g = self.g
        mag = math.hypot(*g.aim_raw)
        st = self.state
        if st == "center":
            if mag < 0.12:
                if self.center_since is None:
                    self.center_since = now
                elif now - self.center_since >= 0.25:
                    self.state, self.go_at = "wait", now + self.rng.uniform(0.9, 2.2)
            else:
                self.center_since = None
        elif st == "wait":
            if mag > 0.3:
                self.false_starts += 1
                self._show(now, "TOO EARLY", BAD)
            elif now >= self.go_at:
                if self.n >= len(self.queue):       # endless: another shuffled set of the four directions
                    more = list(self.DIRS) * (self.trials // 4)
                    self.rng.shuffle(more)
                    self.queue += more
                self.state, self.cue = "go", self.queue[self.n]
                self.t_go, self.pending = now, True
                self.t_react = self.t_full = self.dir_ok = None
        elif st == "go":
            cx, cy = self.DIRS[self.cue]
            for t, sx, sy in g.stick_samples():
                if t < self.t_go:
                    continue
                m = math.hypot(sx, sy)
                if self.t_react is None and m >= 0.2:
                    self.t_react = t
                if self.dir_ok is None and m >= 0.6:
                    self.dir_ok = (sx * cx + sy * cy) / m > 0.7071
                if self.t_full is None and m >= 0.9:
                    self.t_full = t
            if (self.t_full is not None or now - self.t_go > 1.5
                    or (self.t_react is not None and now - self.t_react > 0.5)):
                self._finish(now)
        elif st == "show" and now >= self.until:
            if self.n >= self.trials and not self.endless:
                self.done = True
            else:
                self.state, self.center_since = "center", None

    def presented(self, t):
        if self.pending:
            self.t_go, self.pending = t, False

    def _finish(self, now):
        if self.t_react is None:
            self.missed += 1
            return self._show(now, "MISSED", WARN)
        rt = (self.t_react - self.t_go) * 1000.0
        if rt < 70.0:
            self.false_starts += 1
            return self._show(now, "TOO EARLY", BAD)
        throw = (self.t_full - self.t_react) * 1000.0 if self.t_full is not None else None
        ok = bool(self.dir_ok)
        self.events.append({"trial": self.n + 1, "dir": self.cue, "ok": ok,
                            "rt_ms": round(rt, 2), "throw_ms": _r(throw, 2)})
        self.n += 1
        if ok:
            self.g.sfx("hit")
            self._show(now, f"{rt:.0f} ms", GOOD)
        else:
            self.wrong += 1
            self.g.sfx("miss")
            self._show(now, "WRONG WAY", BAD)

    def progress(self):
        return f"{self.n}" if self.endless else f"{self.n} / {self.trials}"

    def draw(self, ui, g):
        cx, cy, R = ui.W / 2, 500.0, 120.0
        ui.ring(cx, cy, R, 3, alpha(WHITE, 0.25))
        sx, sy = g.aim_raw
        ui.disc(cx + sx * R, cy - sy * R, 9, ACCENT)
        if self.state == "go" and self.cue:
            dx, dy = self.DIRS[self.cue]
            dy = -dy
            ox, oy = cx + dx * (R + 50), cy + dy * (R + 50)
            px, py = -dy, dx
            tip = (ox + dx * 95, oy + dy * 95)
            ui.tri(tip, (ox + px * 62, oy + py * 62), (ox - px * 62, oy - py * 62), GOOD)
        elif self.state == "show":
            ui.text(cx, cy + R + 60, self.msg, "xl", self.msg_col, 1)
        elif self.state == "center":
            ui.text(cx, cy + R + 70, "LET THE STICK CENTRE", "l", DIM, 1)
        else:
            ui.text(cx, cy + R + 70, "WAIT FOR THE ARROW", "l", DIM, 1)

    def summary(self):
        good = [e for e in self.events if e["ok"]]
        rts = [e["rt_ms"] for e in good]
        med = _med(rts)
        return {
            "score": round(100000 / med) if med else 0,
            "stick_rt_median_ms": _r(med), "stick_rt_avg_ms": _r(_avg(rts)),
            "stick_rt_sd_ms": _r(_sd(rts)),
            "throw_median_ms": _r(_med([e["throw_ms"] for e in good])),
            "wrong_dir": self.wrong, "false_starts": self.false_starts, "missed": self.missed,
            "trials": self.n,
        }


# --------------------------------------------------------- click-to-kill set
class _Approach:
    """Everything measured about one target between spawning and dying."""

    __slots__ = ("t0", "pos", "r", "ang_r", "d0", "path", "min_err", "overshoots", "react",
                 "first_on", "last_enter", "on", "shots", "busy", "ads_at")

    def __init__(self, g, now, pos, r):
        self.t0, self.pos, self.r = now, pos, r
        dist = math.dist(pos, g.eye)
        self.ang_r = math.degrees(math.asin(min(r / dist, 1.0)))
        self.d0 = self.min_err = g.err_deg(pos)
        self.path = 0.0
        self.overshoots = self.shots = 0
        self.react = self.first_on = self.last_enter = self.ads_at = None
        self.on = False
        # Stick reaction is only timed when the stick was at rest as the target
        # appeared; if it was still moving there is no clean "first movement".
        self.busy = g.turn_rate > 2.0

    def step(self, g, now):
        err = g.err_deg(self.pos)
        self.path += g.frame_turn
        if self.react is None and not self.busy and g.turn_rate > 12.0:
            self.react = now - self.t0
        on = err <= self.ang_r
        if on and not self.on:
            self.last_enter = now
            if self.first_on is None:
                self.first_on = now - self.t0
        self.on = on
        # An overshoot is the error growing again by a target-radius after a real approach.
        if err < self.min_err:
            self.min_err = err
        elif self.min_err < self.d0 * 0.5 and err > self.min_err + max(self.ang_r, 0.7):
            self.overshoots += 1
            self.min_err = err

    def record(self, t_kill) -> dict:
        need = max(self.d0 - self.ang_r, 0.0)
        eff = min(need / self.path, 1.0) if self.path > 1e-6 and need > 0 else 1.0
        click = None if self.last_enter is None else max((t_kill - self.last_enter) * 1000.0, 0.0)
        return {
            "t": round(self.t0, 3), "dist_deg": round(self.d0, 2),
            "ttk_ms": round((t_kill - self.t0) * 1000.0, 1),
            "react_ms": _r(None if self.react is None else self.react * 1000.0),
            "on_ms": _r(None if self.first_on is None else self.first_on * 1000.0),
            "click_ms": _r(click), "ads_ms": _r(None if self.ads_at is None else self.ads_at * 1000.0),
            "shots": self.shots, "overshoots": self.overshoots, "path_eff": round(eff, 3),
        }


REACT_FLOOR_MS = 100.0
REACT_MIN_SAMPLES = 5


def react_median(events):
    """(median stick reaction, samples used) for a click run.

    A "reaction" faster than 100 ms is the thumb already on its way before the
    target could have been seen - the first real runs logged several of 8-40 ms -
    so those are dropped, and with fewer than five clean samples there is no
    median worth reporting (Flick respawns quickly, so most targets arrive while
    the stick is still busy).
    """
    ok = [e["react_ms"] for e in events if e.get("react_ms") is not None and e["react_ms"] >= REACT_FLOOR_MS]
    return (_r(_med(ok)) if len(ok) >= REACT_MIN_SAMPLES else None), len(ok)


class _Click(Scenario):
    """One target at a time: reach it and fire."""

    ang = 2.0                 # target angular radius, degrees
    dist = 16.0
    sep = (12.0, 40.0)        # how far from the crosshair it spawns, degrees
    delay = (0.25, 0.55)      # pause after a kill; lets the stick re-centre
    yaw_lim = 40.0
    pitch_lim = (-13.0, 15.0)
    metrics = (
        ("score", "Score", "", 1, 0),
        ("kills", "Kills", "", 1, 0),
        ("accuracy_pct", "Accuracy", "%", 1, 1),
        ("ttk_median_ms", "Median time to kill", "ms", -1, 0),
        ("react_median_ms", "Median stick reaction", "ms", -1, 0),
        ("click_median_ms", "On target to shot", "ms", -1, 0),
        ("overshoot_per_kill", "Overshoots per kill", "", -1, 2),
        ("path_eff_pct", "Path efficiency", "%", 1, 1),
    )

    def __init__(self, g):
        super().__init__(g)
        self.cur: _Approach | None = None
        self.cur_key = 0            # which target this is, counting from 1 (for the capture)
        self.next_at = 0.0
        self.stray = self.hipfire = 0
        self.r = _radius(self.ang * g.size, self.dist)

    def begin(self, now):
        self.next_at = now + 0.4

    def _spawn(self, now):
        g, rng = self.g, self.rng
        lo, hi = self.pitch_lim
        for _ in range(40):
            d, th = rng.uniform(*self.sep), rng.uniform(0.0, 2 * math.pi)
            ty, tp = g.yaw + d * math.cos(th), g.pitch + d * math.sin(th) * 0.6
            if abs(ty) <= self.yaw_lim and lo <= tp <= hi:
                break
        else:   # the player is looking somewhere odd; just put it back in the arena
            ty, tp = rng.uniform(-self.yaw_lim, self.yaw_lim) * 0.8, rng.uniform(lo, hi)
        self.cur = _Approach(g, now, g.at(ty, tp, self.dist), self.r)
        self.cur_key += 1

    def update(self, dt, now):
        g = self.g
        if self.cur is None:
            if now >= self.next_at:
                if self.needs_ads and g.ads_held:
                    if now - self.next_at > 0.8:
                        g.note("RELEASE AIM", WARN, 0.2)
                else:
                    self._spawn(now)
        else:
            self.cur.step(g, now)

    def on_ads(self, t, down):
        c = self.cur
        if down and c is not None and c.ads_at is None:
            c.ads_at = t - c.t0

    def on_shoot(self, t):
        g, c = self.g, self.cur
        self.shots += 1
        if c is None:
            self.stray += 1
            return g.fx_miss()
        c.shots += 1
        if self.needs_ads and g.ads_t < 0.8:
            self.hipfire += 1
            g.note("AIM FIRST", BAD, 0.5)
            self.shot(False)
            return g.fx_miss()
        self.shot(g.hit(c.pos, c.r))
        if g.hit(c.pos, c.r):
            self.hits += 1
            self.kills += 1
            self.events.append(c.record(t))
            g.fx_hit(True)
            g.pop(c.pos, c.r)
            self.removed(self.cur_key, "kill")
            self.cur = None
            self.next_at = t + self.rng.uniform(*self.delay)
        else:
            g.fx_miss()

    def spheres(self):
        c = self.cur
        return ((c.pos, c.r, self.g.color, 1.0, 1.0),) if c else ()

    def capture_targets(self):
        c = self.cur
        if c is not None:
            yield self.cur_key, "sphere", (c.pos[0], c.pos[1], c.pos[2], c.r, 0.0, 0.0, 0.0, 1.0), True, None

    def live(self):
        c = self.cur
        return ((c.pos, c.r),) if c else ()

    def hud(self):
        return (("KILLS", str(self.kills)), ("ACCURACY", f"{self._accuracy():.0f}%"))

    def summary(self):
        ev = self.events
        react, react_n = react_median(ev)
        s = {
            "score": self._score(), "kills": self.kills, "shots": self.shots, "hits": self.hits,
            "accuracy_pct": _r(self._accuracy()),
            "ttk_median_ms": _r(_med([e["ttk_ms"] for e in ev])),
            "ttk_avg_ms": _r(_avg([e["ttk_ms"] for e in ev])),
            "react_median_ms": react, "react_samples": react_n,
            "click_median_ms": _r(_med([e["click_ms"] for e in ev])),
            "overshoot_per_kill": _r(sum(e["overshoots"] for e in ev) / len(ev), 2) if ev else None,
            "path_eff_pct": _r(None if not ev else 100.0 * _avg([e["path_eff"] for e in ev])),
            "stray_shots": self.stray,
        }
        if self.needs_ads:
            s["ads_median_ms"] = _r(_med([e["ads_ms"] for e in ev]))
            s["hipfire_shots"] = self.hipfire
        return s


class Flick(_Click):
    id = "flick"
    name = "Flick"
    tagline = "One target, 12-40 degrees away. Snap to it and fire."
    how = "Targets appear one at a time. Get the crosshair on and shoot. Misses cost score."
    measures = ("Big stick throws and stopping on a point: time to kill, first stick movement, "
                "overshoots, how direct the path was, and the gap between arriving and firing.")


class Micro(_Click):
    id = "micro"
    name = "Micro Adjust"
    tagline = "Small targets a few degrees off the crosshair."
    how = "Tiny corrections only. Each target spawns 2.5-9 degrees from where you are aiming."
    measures = ("Fine control around the stick's centre - where deadzone, tension and sensor "
                "resolution matter most. Watch overshoots per kill.")
    ang = 1.1
    sep = (2.5, 9.0)
    delay = (0.12, 0.30)


class AdsSnap(_Click):
    id = "ads"
    name = "ADS Snap"
    tagline = "Aim down sights, then fire. Hip-fire does not count."
    how = "Release Aim between targets. When one appears: hold Aim, line up, Shoot."
    measures = ("Your Aim + Shoot binds working together (LB+RB against LT+RT): time until Aim is "
                "pressed, time to kill while zoomed, and shots wasted from the hip.")
    needs_ads = True
    ang = 1.3
    dist = 20.0
    sep = (8.0, 28.0)
    delay = (0.35, 0.80)
    metrics = _Click.metrics + (
        ("ads_median_ms", "Median time to Aim", "ms", -1, 0),
        ("hipfire_shots", "Hip-fire shots", "", -1, 0),
    )


# ------------------------------------------------------------------ gridshot
class Grid(Scenario):
    id = "grid"
    name = "Gridshot"
    tagline = "Three targets on a grid. Clear them as fast as you can."
    how = "Three targets are always up. Each kill spawns another. Speed and accuracy both count."
    measures = "Rhythm and target-to-target speed with the next target already visible."
    cols, rows = 5, 3
    ang, dist = 2.6, 16.0
    metrics = (
        ("score", "Score", "", 1, 0),
        ("kills", "Kills", "", 1, 0),
        ("accuracy_pct", "Accuracy", "%", 1, 1),
        ("kps", "Kills per second", "", 1, 2),
        ("gap_median_ms", "Median time between kills", "ms", -1, 0),
    )

    def __init__(self, g):
        super().__init__(g)
        self.r = _radius(self.ang * g.size, self.dist)
        self.cells: dict = {}
        self.keys: dict = {}        # cell -> which target that is, counting from 1 (for the capture)
        self._n = 0
        self.last_kill = None
        self.gaps: list = []

    def _spawn(self, avoid=None):
        free = [(c, r) for c in range(self.cols) for r in range(self.rows)
                if (c, r) not in self.cells and (c, r) != avoid]
        c = self.rng.choice(free)
        self.cells[c] = self.g.at((c[0] - 2) * 10.0, (c[1] - 1) * 8.0 + 1.0, self.dist)
        self._n += 1
        self.keys[c] = self._n

    def begin(self, now):
        for _ in range(3):
            self._spawn()

    def on_shoot(self, t):
        g = self.g
        self.shots += 1
        best, best_err = None, 1e9
        for cell, pos in self.cells.items():
            if g.hit(pos, self.r):
                e = g.err_deg(pos)
                if e < best_err:
                    best, best_err = cell, e
        self.shot(best is not None)
        if best is None:
            return g.fx_miss()
        self.hits += 1
        self.kills += 1
        gap = None if self.last_kill is None else (t - self.last_kill) * 1000.0
        if gap is not None:
            self.gaps.append(gap)
        self.last_kill = t
        self.events.append({"t": round(t, 3), "cell": list(best), "gap_ms": _r(gap)})
        g.fx_hit(True)
        self.removed(self.keys.pop(best, None), "kill")
        g.pop(self.cells.pop(best), self.r)
        self._spawn(avoid=best)

    def spheres(self):
        col = self.g.color
        return tuple((pos, self.r, col, 1.0, 1.0) for pos in self.cells.values())

    def capture_targets(self):
        for cell, p in self.cells.items():
            yield self.keys.get(cell, cell), "sphere", (p[0], p[1], p[2], self.r, 0.0, 0.0, 0.0, 1.0), True, None

    def live(self):
        return tuple((pos, self.r) for pos in self.cells.values())

    def hud(self):
        return (("KILLS", str(self.kills)), ("ACCURACY", f"{self._accuracy():.0f}%"))

    def summary(self):
        t = max(self.g.t, 1e-6)
        return {
            "score": self._score(), "kills": self.kills, "shots": self.shots, "hits": self.hits,
            "accuracy_pct": _r(self._accuracy()), "kps": round(self.kills / t, 3),
            "gap_median_ms": _r(_med(self.gaps)),
        }


# ------------------------------------------------------------------ tracking
class _Tracking(Scenario):
    hold_fire = True
    primary = "on_target_pct"
    grade = "on_target_pct"
    ang, dist = 2.3, 16.0
    metrics = (
        ("on_target_pct", "On target", "%", 1, 1),
        ("dmg_pct", "On target while firing", "%", 1, 1),
        ("err_avg_deg", "Average error", "°", -1, 2),
        ("err_p90_deg", "Error, worst 10%", "°", -1, 2),
        ("streak_max_s", "Longest streak", "s", 1, 2),
        ("score", "Score", "", 1, 0),
    )

    def __init__(self, g):
        super().__init__(g)
        self.ang_r = self.ang * g.size
        self.r = _radius(self.ang_r, self.dist)
        self.tyaw, self.tpitch = 0.0, 1.5
        self.pos = g.at(self.tyaw, self.tpitch, self.dist)
        self.t_all = self.t_on = self.t_dmg = self.t_fire = self.err_sum = 0.0
        self.errs: list = []
        self.streak = self.streak_max = 0.0
        self.on = self.dmg = False
        self._tick = 0.0
        self._trace: list = []
        self._trace_next = 0.0

    def move(self, dt, now):
        raise NotImplementedError

    def update(self, dt, now):
        g = self.g
        self.move(dt, now)
        self.pos = g.at(self.tyaw, self.tpitch, self.dist)
        err = g.err_deg(self.pos)
        on, fire = err <= self.ang_r, g.fire_held
        self.on, self.dmg = on, on and fire
        self.t_all += dt
        self.err_sum += err * dt
        self.errs.append(err)
        if fire:
            self.t_fire += dt
        if on:
            self.t_on += dt
            self.streak += dt
            if self.streak > self.streak_max:
                self.streak_max = self.streak
            if fire:
                self.t_dmg += dt
                self._tick -= dt
                if self._tick <= 0.0:
                    g.sfx("tick")
                    self._tick = 0.09
        else:
            self.streak = 0.0
        if now >= self._trace_next:
            self._trace.append([round(now, 2), round(err, 2), int(on), int(fire)])
            self._trace_next = now + 0.05

    def spheres(self):
        base = self.g.color
        col = _mix(base, (1.0, 1.0, 1.0), 0.75) if self.dmg else (_mix(base, (1.0, 1.0, 1.0), 0.3) if self.on else base)
        return ((self.pos, self.r, col, 1.0, 1.0),)

    def live(self):
        return ((self.pos, self.r),)

    def capture_targets(self):
        p = self.pos
        yield 0, "track", (p[0], p[1], p[2], self.r, self.tyaw, self.tpitch, 0.0, 1.0), True, None

    def hud(self):
        pct = 100.0 * self.t_on / self.t_all if self.t_all else 0.0
        return (("ON TARGET", f"{pct:.0f}%"),)

    def draw(self, ui, g):
        if not g.fire_held:
            ui.text(ui.W / 2, 640, "HOLD SHOOT", "m", alpha(WARN, 0.9), 1)

    def summary(self):
        t = max(self.t_all, 1e-6)
        errs = sorted(self.errs)
        p90 = errs[min(int(len(errs) * 0.9), len(errs) - 1)] if errs else None
        return {
            "score": round(1000 * self.t_dmg / t),
            "on_target_pct": _r(100 * self.t_on / t), "dmg_pct": _r(100 * self.t_dmg / t),
            "fire_held_pct": _r(100 * self.t_fire / t),
            "err_avg_deg": _r(self.err_sum / t, 3), "err_median_deg": _r(_med(errs), 3),
            "err_p90_deg": _r(p90, 3), "streak_max_s": _r(self.streak_max, 2),
        }

    def trace(self):
        return {"columns": ["t", "err_deg", "on_target", "firing"], "rows": self._trace}


class SmoothTrack(_Tracking):
    id = "track_smooth"
    name = "Smooth Track"
    tagline = "Stay on a gliding target while holding fire."
    how = "Hold Shoot and keep the crosshair on the target as it drifts. Time on target is the score."
    measures = ("Steady low-deflection control: how well the stick holds a slow, changing turn "
                "rate. Stick smoothness and linearity live here.")

    def __init__(self, g):
        super().__init__(g)
        rng = self.rng
        j = lambda: rng.uniform(0.85, 1.15)
        s = lambda: rng.choice((0.0, math.pi))     # start at centre, heading either way
        self.k = (0.55 * j(), s(), 1.70 * j(), s(), 0.80 * j(), s(), 1.90 * j(), s())

    def move(self, dt, now):
        w1, p1, w2, p2, w3, p3, w4, p4 = self.k
        self.tyaw = 22.0 * math.sin(w1 * now + p1) + 7.0 * math.sin(w2 * now + p2)
        self.tpitch = 1.5 + 5.0 * math.sin(w3 * now + p3) + 2.0 * math.sin(w4 * now + p4)


class StrafeTrack(_Tracking):
    id = "track_strafe"
    name = "Strafe Track"
    tagline = "The target strafes and reverses without warning."
    how = "Hold Shoot and stay on the target. It changes direction at random - reverse with it."
    measures = ("Reactive tracking: on-target time plus how long your aim takes to reverse after "
                "the target does. Stick return speed and centring matter here.")
    metrics = _Tracking.metrics[:4] + (
        ("reversal_median_ms", "Median reversal reaction", "ms", -1, 0),
    ) + _Tracking.metrics[4:]
    speed = 22.0     # deg/s
    limit = 28.0

    def __init__(self, g):
        super().__init__(g)
        self.dir = self.rng.choice((-1.0, 1.0))
        self.vel = 0.0
        self.flip_at = 0.6
        self.pending = None
        self.reversals: list = []
        self.flips = 0

    def move(self, dt, now):
        g = self.g
        if now >= self.flip_at or (self.tyaw * self.dir > self.limit):
            self.dir = -self.dir
            self.flips += 1
            self.flip_at = now + self.rng.uniform(0.40, 1.30)
            rec = getattr(g, "rec", None)
            if rec is not None:                 # the moment it turned, for the ghost's replay of this run
                rec.event("flip", dir=self.dir)
            # Only time the reversal if the player was actually following the old direction.
            self.pending = now if g.yaw_rate * self.dir < -3.0 else None
        self.vel += (self.dir * self.speed - self.vel) * min(dt / 0.05, 1.0)
        self.tyaw += self.vel * dt
        if self.pending is not None:
            if g.yaw_rate * self.dir > 3.0:
                self.reversals.append((now - self.pending) * 1000.0)
                self.events.append({"t": round(self.pending, 3), "reversal_ms": round(self.reversals[-1], 1)})
                self.pending = None
            elif now - self.pending > 0.8:
                self.pending = None

    def summary(self):
        s = super().summary()
        s["reversal_median_ms"] = _r(_med(self.reversals))
        s["reversals_timed"] = len(self.reversals)
        s["target_flips"] = self.flips
        return s


# ------------------------------------------------------------ spray transfer
class Spray(Scenario):
    id = "spray"
    name = "Spray Transfer"
    tagline = "Full-auto. Burn a target down, switch to the next."
    how = "Hold Shoot for automatic fire. Each target takes 5 hits. Three drift around the arena."
    measures = ("Tracking under sustained fire and the snap between targets - the closest thing "
                "here to an actual gunfight. Switch time is kill to first hit on the next target.")
    hold_fire = True
    ang, dist = 2.5, 16.0
    rate = 12.0      # rounds per second
    hp = 5
    metrics = (
        ("score", "Score", "", 1, 0),
        ("kills", "Kills", "", 1, 0),
        ("accuracy_pct", "Accuracy", "%", 1, 1),
        ("switch_median_ms", "Median switch time", "ms", -1, 0),
        ("hits_per_s", "Hits per second", "", 1, 2),
    )

    def __init__(self, g):
        super().__init__(g)
        self.r = _radius(self.ang * g.size, self.dist)
        self.targets: list = []
        self.cool = 0.0
        self.last_kill = None
        self.switches: list = []
        self._id = 0

    def _new(self):
        rng, g = self.rng, self.g
        for _ in range(60):
            yaw, pitch = rng.uniform(-32, 32), rng.uniform(-8, 12)
            if all(math.hypot(yaw - o["yaw"], pitch - o["pitch"]) > 13 for o in self.targets) \
                    and math.hypot(yaw - g.yaw, pitch - g.pitch) > 9:
                break
        self._id += 1
        tg = {"id": self._id, "yaw": yaw, "pitch": pitch, "hp": self.hp,
              "vy": rng.choice((-1, 1)) * rng.uniform(3, 7), "vp": rng.choice((-1, 1)) * rng.uniform(0, 2)}
        tg["pos"] = g.at(yaw, pitch, self.dist)
        self.targets.append(tg)

    def begin(self, now):
        for _ in range(3):
            self._new()

    def _fire(self, t):
        g = self.g
        self.shots += 1
        best, best_err = None, 1e9
        for tg in self.targets:
            if g.hit(tg["pos"], self.r):
                e = g.err_deg(tg["pos"])
                if e < best_err:
                    best, best_err = tg, e
        self.shot(best is not None)
        if best is None:
            return
        self.hits += 1
        best["hp"] -= 1
        if self.last_kill is not None:
            self.switches.append((t - self.last_kill) * 1000.0)
            self.last_kill = None
        if best["hp"] > 0:
            return g.fx_hit(False)
        self.kills += 1
        self.events.append({"t": round(t, 3), "switch_ms": _r(self.switches[-1]) if self.switches else None})
        self.last_kill = t
        g.fx_hit(True)
        g.pop(best["pos"], self.r)
        self.removed(best["id"], "kill")
        self.targets.remove(best)
        self._new()

    def on_shoot(self, t):
        self._fire(t)
        self.cool = 1.0 / self.rate

    def update(self, dt, now):
        g = self.g
        for tg in self.targets:
            tg["yaw"] += tg["vy"] * dt
            tg["pitch"] += tg["vp"] * dt
            if abs(tg["yaw"]) > 34:
                tg["vy"] = -math.copysign(tg["vy"], tg["yaw"])
            if tg["pitch"] > 13 or tg["pitch"] < -9:
                tg["vp"] = -math.copysign(tg["vp"], tg["pitch"] - 2)
            tg["pos"] = g.at(tg["yaw"], tg["pitch"], self.dist)
        if g.fire_held:
            self.cool -= dt
            while self.cool <= 0.0:
                self._fire(now)
                self.cool += 1.0 / self.rate

    def spheres(self):
        base = self.g.color
        return tuple((tg["pos"], self.r, _mix((1.0, 1.0, 1.0), base, 0.35 + 0.65 * tg["hp"] / self.hp), 1.0, 1.0)
                     for tg in self.targets)

    def live(self):
        return tuple((tg["pos"], self.r) for tg in self.targets)

    def capture_targets(self):
        for tg in self.targets:
            p = tg["pos"]
            yield tg["id"], "sphere", (p[0], p[1], p[2], self.r, 0.0, 0.0, 0.0, float(tg["hp"])), True, {"hp": self.hp}

    def hud(self):
        return (("KILLS", str(self.kills)), ("ACCURACY", f"{self._accuracy():.0f}%"))

    def draw(self, ui, g):
        cam = g.cam
        for tg in self.targets:
            p = cam.project(tg["pos"])
            if p is None:
                continue
            x, y = (p[0] * 0.5 + 0.5) * ui.W, (0.5 - p[1] * 0.5) * ui.H
            rad = self.r / self.dist / cam.tany * ui.H * 0.5
            w = 64.0
            ui.rect(x - w / 2, y - rad - 16, w, 5, (0.0, 0.0, 0.0, 0.55))
            ui.rect(x - w / 2, y - rad - 16, w * tg["hp"] / self.hp, 5, WHITE)

    def summary(self):
        t = max(self.g.t, 1e-6)
        return {
            "score": self._score(), "kills": self.kills, "shots": self.shots, "hits": self.hits,
            "accuracy_pct": _r(self._accuracy()), "switch_median_ms": _r(_med(self.switches)),
            "hits_per_s": round(self.hits / t, 3),
        }


ALL = (Reaction, StickSnap, Flick, Micro, Grid, SmoothTrack, StrafeTrack, Spray, AdsSnap)
BY_ID = {s.id: s for s in ALL}
