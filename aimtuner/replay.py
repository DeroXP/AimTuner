"""After a run: the ghost plays its targets (GhostJob), then you can watch the run again with the
ghost's aim drawn over yours (ReplayScreen), or go through it target by target (RouteScreen).

The ghost's crosshair in the replay is placed relative to the target, not the room: on each
target, τ seconds after it showed, it is where the ghost's crosshair was against that same
target τ seconds after it showed in the ghost's run. (The ghost's targets come at its own pace,
so in the room they are at other times - see ghost.py.) On a target the ghost had already
killed, its crosshair waits and the target is marked with the ghost's time.
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np

from . import capture as capmod
from . import engage, ghost
from .capture import V
from .gfx import Camera
from .theme import ACCENT, BAD, DIM, FAINT, GOOD, WARN, WHITE, alpha
from .uikit import ROW, Screen, _footer, _header, _row

GHOST_COL = ACCENT
YOU_COL = WHITE
SPEEDS = (0.25, 0.5, 1.0, 2.0)


# ---------------------------------------------------------------- the ghost, in the background
class GhostJob:
    """Plays the ghost on a capture in a thread, then compares the two. Holds off while a run is on
    (a run has the computer to itself), and the thread never touches the window or the GPU."""

    def __init__(self, app, cap):
        self.app, self.cap = app, cap
        self.progress = 0.0
        self.result = None          # {"stats", "capture", "compare"} when done
        self.error = None
        self._stop = False
        self.thread = threading.Thread(target=self._work, name="ghost", daemon=True)
        self.thread.start()

    @property
    def done(self) -> bool:
        return self.result is not None or self.error is not None

    def cancel(self):
        self._stop = True

    def _wait_turn(self) -> bool:
        while self.app.game is not None and not self._stop:     # a run is on: keep out of its way
            time.sleep(0.05)
        return self._stop

    def _work(self):
        try:
            run = ghost.GhostRun(self.cap)
            while not run.done:
                if self._wait_turn():
                    return
                run.run(until=run.g.t + 2.0)
                self.progress = run.g.t / max(run.end, 1e-6)
                time.sleep(0)               # let the window have the interpreter for a moment
            res = run.result()
            res["compare"] = engage.compare(self.cap, res["capture"])
            rid = self.cap.head.get("run_id")
            if rid:
                try:
                    res["capture"].save(capmod.path_for(self.app.log.dir, rid, ghost=True))
                except OSError as e:
                    print(f"ghost: could not save {rid}: {e}")
            self.result = res
        except Exception as e:      # the ghost is an extra: never let it take the results screen down
            import traceback

            traceback.print_exc()
            self.error = f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- shared drawing helpers
def _cam_at(cap, t, cam=None, aspect=16 / 9):
    """The camera of a capture at game time t (view interpolated between frames)."""
    cam = cam or Camera()
    i = cap.frame_at(t)
    j = min(i + 1, cap.frames - 1)
    v0, v1 = cap.view[i], cap.view[j]
    t0, t1 = v0[V["t"]], v1[V["t"]]
    w = 0.0 if t1 <= t0 else min(max((t - t0) / (t1 - t0), 0.0), 1.0)
    lerp = lambda k: v0[V[k]] + (v1[V[k]] - v0[V[k]]) * w
    dyaw = ((v1[V["yaw"]] - v0[V["yaw"]] + 180.0) % 360.0) - 180.0
    yaw = v0[V["yaw"]] + dyaw * w
    ads = lerp("ads_t")
    z = ads * ads * (3.0 - 2.0 * ads)
    zoom = 1.0 + (cap.head["zoom_max"] - 1.0) * z
    cam.eye = (lerp("ex"), lerp("ey"), lerp("ez"))
    cam.set(yaw, lerp("pitch"), cap.head["fov"], aspect, zoom, lerp("roll"))
    return cam, yaw, lerp("pitch")


def _dir(yaw, pitch):
    y, p = math.radians(yaw), math.radians(pitch)
    cp = math.cos(p)
    return (math.sin(y) * cp, math.sin(p), -math.cos(y) * cp)


def _screen(ui, cam, yaw, pitch):
    """Where a direction from the eye lands on screen (UI units), or None if behind."""
    d = _dir(yaw, pitch)
    e = cam.eye
    p = cam.project((e[0] + d[0] * 50.0, e[1] + d[1] * 50.0, e[2] + d[2] * 50.0))
    if p is None:
        return None
    return (p[0] * 0.5 + 0.5) * ui.W, (0.5 - p[1] * 0.5) * ui.H


def _ms(x):
    return "-" if x is None else f"{x * 1000:.0f} ms"


def _tup(x):
    return tuple(_tup(v) for v in x) if isinstance(x, (list, tuple)) else x


def world_of(cap, f, t):
    """The arena as it was at frame f: the room, its boxes (Shield Push moves one), smoke."""
    w = dict(cap.head.get("world") or {})
    if not w:
        return {}
    boxes = w.get("boxes") or []
    for fb, bx in cap.head.get("boxes") or []:
        if fb <= f:
            boxes = bx
        else:
            break
    return {"room": _tup(w["room"]) if w.get("room") else None, "boxes": list(_tup(boxes)),
            "smokes": list(_tup(w.get("smokes") or [])), "smoke_density": w.get("smoke_density") or 0.0,
            "soft_from": w.get("soft_from"), "t": t}


# ---------------------------------------------------------------- the replay
class ReplayScreen(Screen):
    decor = False

    def __init__(self, app, cap, job, start=0.0, scn_name=""):
        super().__init__(app)
        self.cap, self.job = cap, job
        self.t = max(start, 0.0)
        self.speed = 2                  # index into SPEEDS (1x)
        self.playing = True
        self.ghost_view = False
        self.name = scn_name
        self.cam = Camera()
        self.last = None
        self.cmp = job.result["compare"] if job and job.result else None
        self.gcap = job.result["capture"] if job and job.result else None
        pairs = self.cmp["pairs"] if self.cmp else []
        self.pairs = sorted(pairs, key=lambda p: p[0]["t0"])
        self.starts = np.array([p[0]["t0"] for p in self.pairs]) if self.pairs else np.zeros(0)
        # tracking drills: one target for the whole run, at the same moment in both runs
        self.same_clock = bool(self.gcap is not None and not self.pairs
                               and self.cap.head["scenario"] in ("track_smooth", "track_strafe"))

    def _pair_at(self, t):
        if not len(self.starts):
            return None
        i = int(np.searchsorted(self.starts, t, side="right")) - 1
        if i < 0:
            return None
        y, g = self.pairs[i]
        return (y, g) if t <= y["t_kill"] + 0.25 else None

    def _ghost_aim(self, t):
        """The ghost's crosshair at your time t: (yaw, pitch) in your view, or None; plus a note."""
        if self.gcap is None:
            return None, None
        if self.same_clock:
            gc = self.gcap
            i = gc.frame_at(t)
            return (float(gc.view[i, V["yaw"]]), float(gc.view[i, V["pitch"]])), None
        pr = self._pair_at(t)
        if pr is None:
            return None, None
        y, g = pr
        tau = t - y["t0"]
        ts, ey, ep, size, ty, tp = g["path"]
        yts, yey, yep, ysize, yty, ytp = y["path"]
        j = min(max(int(np.searchsorted(yts, t)), 0), len(yts) - 1)
        tgt = (float(yty[j]), float(ytp[j]))                    # where the target is now, from your eye
        if tau > g["total"]:                                    # the ghost had it already
            return (tgt[0], tgt[1]), f"ghost killed it in {g['total'] * 1000:.0f} ms"
        k = min(max(int(np.searchsorted(ts, g["t0"] + tau)), 0), len(ts) - 1)
        return (tgt[0] - float(ey[k]), tgt[1] - float(ep[k])), None

    def update(self, nav, now):
        if self.last is None:
            self.last = now
        dt, self.last = now - self.last, now
        dur = self.cap.duration
        if self.playing:
            self.t = min(self.t + dt * SPEEDS[self.speed], dur)
            if self.t >= dur:
                self.playing = False
        if "back" in nav:
            self.app.pop()
        elif "accept" in nav:
            if self.t >= dur:
                self.t = 0.0
            self.playing = not self.playing
        if "left" in nav:
            self.t = max(self.t - 2.0, 0.0)
        if "right" in nav:
            self.t = min(self.t + 2.0, dur)
        if "up" in nav:
            self.speed = min(self.speed + 1, len(SPEEDS) - 1)
        if "down" in nav:
            self.speed = max(self.speed - 1, 0)
        if nav & {"lb", "rb"} and len(self.starts):
            i = int(np.searchsorted(self.starts, self.t - 0.05, side="right")) - 1
            i = i - 1 if "lb" in nav else i + 1
            i = min(max(i, 0), len(self.starts) - 1)
            self.t = max(float(self.starts[i]) - 0.3, 0.0)
        if "y" in nav and self.pairs:
            # the next target (after this one) where you lost the most time to the ghost, of the worst five
            worst = sorted(self.pairs, key=lambda p: p[1]["total"] - p[0]["total"])[:5]
            later = [p for p in worst if p[0]["t0"] > self.t + 0.5] or worst
            self.t = max(later[0][0]["t0"] - 0.3, 0.0)
            self.speed = 1
            self.playing = True
        if "x" in nav and self.gcap is not None:
            self.ghost_view = not self.ghost_view

    def scene(self):
        """What the window draws behind this screen: the run at the replay's time."""
        cap = self.gcap if self.ghost_view else self.cap
        t = self._ghost_time(self.t) if self.ghost_view else self.t
        cam, yaw, pitch = _cam_at(cap, t, self.cam, self.app.gfx.aspect)
        f = cap.frame_at(t)
        return cam, cap.spheres(f), world_of(cap, f, t)

    def _ghost_time(self, t):
        pr = self._pair_at(t)
        if pr is None or self.same_clock:
            return t
        y, g = pr
        return g["t0"] + min(t - y["t0"], g["total"] + 0.2)

    def _trail(self, ui, cam, pts, col):
        prev = None
        n = len(pts)
        for i, (yw, pt) in enumerate(pts):
            s = _screen(ui, cam, yw, pt)
            if s is not None and prev is not None:
                ui.line(prev[0], prev[1], s[0], s[1], 3.0, alpha(col, 0.15 + 0.75 * i / max(n, 1)))
            prev = s

    def draw(self, ui):
        cap, W, H = self.cap, ui.W, ui.H
        cam = self.cam
        t = self.t
        cx, cy = W / 2, H / 2
        ui.rect(0, 0, W, 96, (0.0, 0.0, 0.0, 0.6))
        ui.text(56, 26, ("GHOST'S VIEW  ·  " if self.ghost_view else "REPLAY  ·  ") + self.name.upper(), "l", WHITE)
        m, s = divmod(t, 60)
        dm, ds = divmod(cap.duration, 60)
        ui.text(W - 56, 26, f"{int(m)}:{s:04.1f} / {int(dm)}:{ds:04.1f}   ·   {SPEEDS[self.speed]:g}x"
                + ("" if self.playing else "   ·   paused"), "l", DIM, 2)
        if self.ghost_view:
            ui.text(56, 62, "The ghost's own run: its targets, at its own pace. X goes back to yours.", "s", GHOST_COL)
        else:
            # trails: the last half second of each crosshair
            i = cap.frame_at(t)
            j0 = cap.frame_at(t - 0.5)
            step = max(1, (i - j0) // 40)
            you = [(float(cap.view[k, V["yaw"]]), float(cap.view[k, V["pitch"]])) for k in range(j0, i + 1, step)]
            self._trail(ui, cam, you, YOU_COL)
            gh, note = self._ghost_aim(t)
            if gh is not None:
                pts = []
                for k in range(12, -1, -1):
                    a, _ = self._ghost_aim(t - k * 0.04)
                    if a is not None:
                        pts.append(a)
                self._trail(ui, cam, pts, GHOST_COL)
                sp = _screen(ui, cam, *gh)
                if sp is not None:
                    x, y = sp
                    if note:
                        ui.ring(x, y, 22, 3, alpha(GOOD, 0.9))
                        ui.text(x, y + 30, note, "s", GOOD, 1)
                    else:
                        ui.ring(x, y, 9, 3, GHOST_COL)
                        ui.disc(x, y, 2.5, GHOST_COL)
                else:
                    ui.text(cx, cy + 80, "ghost: off screen", "s", GHOST_COL, 1)
            # your crosshair
            ui.rect(cx - 12, cy - 1.5, 24, 3, YOU_COL)
            ui.rect(cx - 1.5, cy - 12, 3, 24, YOU_COL)
            pr = self._pair_at(t)
            if pr:
                y, g = pr
                n = self.pairs.index(pr) + 1
                diff = y["total"] - g["total"]
                ui.text(cx, 120, f"TARGET {n} of {len(self.pairs)}   ·   you {_ms(y['total'])}   ·   ghost {_ms(g['total'])}",
                        "m", WHITE, 1)
                ui.text(cx, 152, ("you lost " if diff > 0 else "you gained ") + f"{abs(diff) * 1000:.0f} ms here", "s",
                        BAD if diff > 0.05 else (WARN if diff > 0 else GOOD), 1)
            ui.text(56, 62, "white  you        teal  the ghost (strong human, same target)", "s", FAINT)
        self._timeline(ui)
        hints = "A  play / pause        Left / Right  2 s        LB / RB  target        Up / Down  speed"
        if self.pairs:
            hints += "        Y  next worst"
        if self.gcap is not None:
            hints += "        X  " + ("your view" if self.ghost_view else "ghost's view")
        _footer(ui, hints + "        B  back")

    def _timeline(self, ui):
        W = ui.W
        x0, x1, y = 96.0, W - 96.0, 980.0
        dur = max(self.cap.duration, 1e-6)
        ui.rect(x0, y, x1 - x0, 6, (0.0, 0.0, 0.0, 0.6))
        for yv, g in self.pairs:
            diff = yv["total"] - g["total"]
            col = BAD if diff > 0.15 else (WARN if diff > 0.05 else GOOD)
            a = x0 + (x1 - x0) * yv["t0"] / dur
            b = x0 + (x1 - x0) * yv["t_kill"] / dur
            ui.rect(a, y - 4, max(b - a, 2.0), 14, alpha(col, 0.55))
        px = x0 + (x1 - x0) * self.t / dur
        ui.rect(px - 1.5, y - 10, 3, 26, WHITE)


# ---------------------------------------------------------------- target by target
class RouteScreen(Screen):
    """Every target you and the ghost both killed: your path and its path, and where the time went."""

    decor = False

    def __init__(self, app, cap, job, scn_name=""):
        super().__init__(app)
        self.cap, self.job = cap, job
        self.cmp = job.result["compare"]
        self.pairs = self.cmp["pairs"]
        self.sel = 0
        self.top = 0
        self.name = scn_name

    def update(self, nav, now):
        n = len(self.pairs)
        if "back" in nav:
            self.app.pop()
            return
        if not n:
            return
        if "up" in nav:
            self.sel = (self.sel - 1) % n
        if "down" in nav:
            self.sel = (self.sel + 1) % n
        if "y" in nav:
            self.sel = max(range(n), key=lambda i: self.pairs[i][0]["total"] - self.pairs[i][1]["total"])
        rows = 15
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + rows:
            self.top = self.sel - rows + 1
        if "accept" in nav:
            y, _ = self.pairs[self.sel]
            scr = ReplayScreen(self.app, self.cap, self.job, start=y["t0"] - 0.3, scn_name=self.name)
            scr.speed = 1
            self.app.push(scr)

    def draw(self, ui):
        W = ui.W
        _header(ui, "EVERY TARGET  ·  " + self.name.upper(), "you against the ghost, a strong human, on the same targets")
        if not self.pairs:
            self._tracking(ui)
            _footer(ui, "B  back")
            return
        x, y0, w = 96.0, 190.0, 520.0
        ui.text(x, y0 - 8, "TARGET", "s", FAINT)
        ui.text(x + 250, y0 - 8, "YOU", "s", FAINT, 2)
        ui.text(x + 370, y0 - 8, "GHOST", "s", FAINT, 2)
        ui.text(x + w - 20, y0 - 8, "LOST", "s", FAINT, 2)
        y = y0 + 22
        for i in range(self.top, min(self.top + 15, len(self.pairs))):
            yv, g = self.pairs[i]
            sel = i == self.sel
            diff = yv["total"] - g["total"]
            col = BAD if diff > 0.15 else (WARN if diff > 0.05 else GOOD)
            if sel:
                ui.rect(x - 18, y - 7, w + 10, ROW - 6, alpha(ACCENT, 0.13))
                ui.rect(x - 18, y - 7, 4, ROW - 6, ACCENT)
            ui.text(x, y, f"{i + 1}", "m", WHITE if sel else DIM)
            ui.text(x + 60, y, f"{yv['dist']:.0f}°" + ("  moving" if yv["moving"] else ""), "s", FAINT)
            ui.text(x + 250, y, _ms(yv["total"]), "m", WHITE, 2)
            ui.text(x + 370, y, _ms(g["total"]), "m", GHOST_COL, 2)
            ui.text(x + w - 20, y, f"{diff * 1000:+.0f}", "m", col, 2)
            y += ROW - 6
        yv, g = self.pairs[self.sel]
        self._route(ui, yv, g, 680.0, 180.0, W - 680.0 - 96.0, 470.0)
        self._parts(ui, yv, g, 680.0, 690.0, W - 680.0 - 96.0)
        _footer(ui, "Up / Down  target        Y  worst one        A  watch it        B  back")

    def _route(self, ui, yv, g, x0, y0, w, h):
        """Each crosshair seen from the target: the red circle is the target (moving or not), and the
        lines are where your crosshair and the ghost's were against it, closing in until the kill."""
        ui.panel(x0, y0, w, h)
        ts, ey, ep, size, ty, tp = yv["path"]
        gts, gey, gep, gsize, gty, gtp = g["path"]
        cos = math.cos(math.radians(float(tp[0])))
        you = [(-float(ey[i]) * cos, -float(ep[i])) for i in range(len(ts))]
        gho = [(-float(gey[i]) * cos, -float(gep[i])) for i in range(len(gts))]
        pts = you + gho + [(0.0, 0.0)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 4.0 * yv["radius"], 1.0) * 1.15
        mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        k = min(w - 80, h - 80) / span
        X = lambda a: x0 + w / 2 + (a - mx) * k
        Y = lambda b: y0 + h / 2 - (b - my) * k

        def line(seq, col, width, t):
            prev = None
            for a, b in seq:
                p = (X(a), Y(b))
                if prev is not None:
                    ui.line(prev[0], prev[1], p[0], p[1], width, col)
                prev = p
            nxt = 0.0
            for i, (a, b) in enumerate(seq):             # a dot every 50 ms: where they bunch, it was slow
                if t[i] - t[0] >= nxt:
                    ui.disc(X(a), Y(b), 3.0, col)
                    nxt += 0.05
        ui.ring(X(0.0), Y(0.0), max(yv["radius"] * k, 4.0), 2.5, alpha(BAD, 0.85))
        # the straight line: from where you started to the target
        a = you[0]
        n = 24
        for s in range(0, n, 2):
            p0 = (a[0] * (1 - s / n), a[1] * (1 - s / n))
            p1 = (a[0] * (1 - (s + 1) / n), a[1] * (1 - (s + 1) / n))
            ui.line(X(p0[0]), Y(p0[1]), X(p1[0]), Y(p1[1]), 1.5, alpha(WHITE, 0.45))
        line(gho, GHOST_COL, 2.5, gts)
        line(you, YOU_COL, 2.5, ts)
        ui.disc(X(you[0][0]), Y(you[0][1]), 6.0, YOU_COL)
        ui.disc(X(gho[0][0]), Y(gho[0][1]), 6.0, GHOST_COL)
        ui.text(x0 + 24, y0 + 18, "seen from the target (red circle)" + ("  - it was moving" if yv["moving"] else "")
                + "  ·  white you  ·  teal ghost  ·  dots 50 ms apart  ·  dashed: straight to it", "s", FAINT)

    def _parts(self, ui, yv, g, x0, y0, w):
        cols = {"react": (0.55, 0.6, 0.7), "move": (0.3, 0.6, 1.0), "fix": WARN[:3], "shoot": (0.75, 0.5, 1.0),
                "finish": BAD[:3]}
        total = max(yv["total"], g["total"], 1e-6)
        for row, (who, rec, col) in enumerate((("YOU", yv, YOU_COL), ("GHOST", g, GHOST_COL))):
            y = y0 + row * 56
            ui.text(x0, y + 4, who, "s", col)
            x = x0 + 90
            bw = w - 220
            for part in engage.PARTS:
                v = rec["parts"][part]
                if not v:
                    continue
                ww = bw * v / total
                ui.rect(x, y, ww, 22, alpha(cols[part] + (1.0,), 0.85))
                if ww > 52:
                    ui.text(x + ww / 2, y + 2, f"{v * 1000:.0f}", "s", (0.0, 0.0, 0.0, 1.0), 1)
                x += ww
            ui.text(x0 + w, y + 2, _ms(rec["total"]), "m", col, 2)
        y = y0 + 120
        x = x0 + 90
        names = dict(engage.NAMES, move="getting on it") if yv["moving"] else engage.NAMES
        for part in engage.PARTS:
            if yv["moving"] and part == "fix":
                continue
            ui.rect(x, y + 5, 12, 12, cols[part] + (1.0,))
            x += 20 + ui.text(x + 18, y, names[part], "s", DIM) + 18
        y += 40
        if yv["moving"]:
            bits = []
            if yv.get("on_pct") is not None and g.get("on_pct") is not None:
                bits.append(f"on it while firing {yv['on_pct']:.0f}% (ghost {g['on_pct']:.0f}%)")
            if yv["shots"] and g["shots"]:
                bits.append(f"bullets landed {100 * yv['hits'] / yv['shots']:.0f}% (ghost {100 * g['hits'] / g['shots']:.0f}%)")
            ui.text(x0, y, "   ·   ".join(bits), "s", DIM)
        elif yv["land_share"] is not None and g["land_share"] is not None:
            land = f"first movement left {yv['land_share'] * 100:.0f}% to go (ghost {g['land_share'] * 100:.0f}%)"
            corr = f"{yv['corrections']} correction{'s' if yv['corrections'] != 1 else ''} (ghost {g['corrections']})"
            over = "went past it" if yv["overshoot"] else "did not go past it"
            ui.text(x0, y, f"{land}   ·   {corr}   ·   you {over}", "s", DIM)

    def _tracking(self, ui):
        """A drill with one target for the whole run: your error over time against the ghost's."""
        c = self.cmp
        x, y = 96.0, 200.0
        lag, rv = c.get("lag"), c.get("reversal")
        ui.para(x, y, c.get("advice") or "", "l", WHITE, ui.W - 192)
