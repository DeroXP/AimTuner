"""One scenario run: camera, aim integration, the game clock, HUD, and the run record.

Frame order matters for the numbers. Shots are judged against the view the
player was actually looking at (the previous frame's camera) *before* this
frame's stick movement is applied, and every event is converted from the input
thread's wall-clock timestamp to the game clock, which stops while paused.
"""
from __future__ import annotations

import datetime
import gc
import math
import random
import statistics

from . import __version__, capture, config, sens
from . import mouse as mousemod
from . import pad as padmod
from .codkit import style_label
from .gfx import Camera
from .theme import ACCENT, BAD, DIM, FAINT, WARN, WHITE, alpha

EYE = (0.0, 5.0, 0.0)


def draw_crosshair(ui, ch: dict, cx: float | None = None, cy: float | None = None):
    """Crosshair in raw pixels, with even thickness so it straddles the true centre."""
    s = ui.s
    x = ui.fb_w * 0.5 if cx is None else cx * s
    y = ui.fb_h * 0.5 if cy is None else cy * s
    col = config.CROSSHAIR_COLORS[ch["color"]] + (1.0,)
    th = max(2, round(ch["thickness"] * s / 2) * 2)
    ln = max(2, round(ch["size"] * 2.4 * s))
    gap = round(ch["gap"] * 1.6 * s)
    o = max(1, round(s))
    style = ch["style"]
    parts = []
    if style in ("cross", "cross_dot"):
        parts += [(x - gap - ln, y - th / 2, ln, th), (x + gap, y - th / 2, ln, th),
                  (x - th / 2, y - gap - ln, th, ln), (x - th / 2, y + gap, th, ln)]
    if style in ("dot", "cross_dot", "circle"):
        d = th if style != "dot" else max(th, round(ch["size"] * 0.9 * s / 2) * 2)
        parts.append((x - d / 2, y - d / 2, d, d))
    for px, py, w, h in parts:
        ui.px_rect(px - o, py - o, w + 2 * o, h + 2 * o, (0.0, 0.0, 0.0, 0.75))
    if style == "circle":
        r = (gap + ln * 0.6) / s
        ui.ring(x / s, y / s, r, (th + 2 * o) / s, (0.0, 0.0, 0.0, 0.75))
        ui.ring(x / s, y / s, r, th / s, col)
    for px, py, w, h in parts:
        ui.px_rect(px, py, w, h, col)


class Game:
    def __init__(self, app, scn_cls, playlist=None, over=None):
        """`over` customises a run for the sensitivity finder: {"cod": settings to
        override, "duration": s, "count": countdown s, "title": str, "on_done":
        fn(stats), "on_abort": fn()}. A run with on_done is not written to the log.

        {"endless": True} plays the scenario with no end: no clock running out, no trial
        count, until the player stops it from the pause menu. That is practice, not a
        measurement - a ten-minute run cannot be compared with one-minute ones - so it is
        saved apart from the runs (logs/endless.jsonl) and never enters a comparison."""
        self.app = app
        cfg = self.cfg = app.cfg
        self.pad = app.pad
        # One input device per run, decided by the profile: a controller, or mouse and keyboard.
        # The other one is not read at all while the run lasts (so a mouse can never be helped
        # along by a thumb on a stick, nor the other way round).
        self.kbm = config.kind(cfg) == "kbm"
        self.mouse = app.mouse
        self.binds = config.binds(cfg)
        self.over = over or {}
        # How the view turns on the Core track - its own settings, or the track's that every track
        # has been told to follow (sens.py). The kits ask the same question for their tracks.
        self.aim = sens.pad(cfg, "core", self.over.get("sens"))
        self.msens = sens.mouse(cfg, "core", self.over.get("sens"))
        self.m_deg = self.msens.deg             # Core track: degrees per mouse count
        self.m_hz: list = []
        self.m_age: list = []
        self._m_ref = (app.mouse.reports, app.mouse.paired)
        self.rng = random.Random()
        self.eye = EYE               # fixed for the Core track; the Call of Duty kit moves it
        self.fov = float(cfg["game"]["fov"])
        self.zoom_max = float(cfg["game"]["ads_zoom"])
        self.cam = Camera()
        self.yaw = self.pitch = 0.0
        self.yaw_rate = self.pitch_rate = self.turn_rate = self.frame_turn = 0.0
        self.fwd = (0.0, 0.0, -1.0)
        self.ads_t = 0.0
        self.ads_held = self.fire_held = False
        self.aim_raw = (0.0, 0.0)
        self.size = config.SIZE_MULT[cfg["game"]["target_size"]]
        self.color = config.TARGET_COLORS[cfg["game"]["target_color"]]
        self.endless = bool(self.over.get("endless"))
        self.duration = math.inf if self.endless else float(self.over.get("duration") or app.opts.duration
                                                             or cfg["game"]["duration"])
        self.t = 0.0             # game clock (s), stops while paused
        self.offset = 0.0        # wall clock - game clock
        self.state = "countdown"
        self.count = 0.6 if app.opts.fast else float(self.over.get("count", 3.0))
        self._beep = None
        self._settled = False
        self.started = False
        self.playlist = playlist
        self.pauses = 0
        self.frames: list = []
        self.gpu_ms: list = []
        self.notes: list = []
        self.ghosts: list = []
        self.hit_t, self.hit_kill, self.hit_head = -1.0, False, False
        # Call of Duty and Siege tracks: a kit owns movement, sensitivity, the weapon - and, on the
        # Call of Duty track only, aim assist.
        self.roll = 0.0             # view tilt in degrees (a lean on the Siege track)
        kit_cls = getattr(scn_cls, "kit_cls", None)
        self.kit = kit_cls(self) if kit_cls else None
        self.rec = None             # the run's capture, for its replay and its ghost (capture.py)
        self.capture = None
        self.scn = scn_cls(self)
        if self.kit:
            self.kit.attach(self.scn)
        if capture.wanted(self):
            self.rec = capture.Recorder(self)
        self._update_cam()

    # ---- geometry the scenarios use ----------------------------------------
    def at(self, yaw_deg: float, pitch_deg: float, dist: float):
        y, p = math.radians(yaw_deg), math.radians(pitch_deg)
        cp = math.cos(p)
        e = self.eye
        return (e[0] + dist * math.sin(y) * cp, e[1] + dist * math.sin(p), e[2] - dist * math.cos(y) * cp)

    def err_deg(self, pos) -> float:
        e = self.eye
        dx, dy, dz = pos[0] - e[0], pos[1] - e[1], pos[2] - e[2]
        f = self.fwd
        c = (dx * f[0] + dy * f[1] + dz * f[2]) / math.sqrt(dx * dx + dy * dy + dz * dz)
        return math.degrees(math.acos(max(-1.0, min(1.0, c))))

    def hit(self, pos, r: float) -> bool:
        e = self.eye
        dx, dy, dz = pos[0] - e[0], pos[1] - e[1], pos[2] - e[2]
        f = self.fwd
        b = dx * f[0] + dy * f[1] + dz * f[2]
        return b > 0.0 and (dx * dx + dy * dy + dz * dz - b * b) <= r * r

    def label(self, action: str) -> str:
        """What the player presses for Shoot / Aim / ..., in words."""
        name = self.binds[action]
        return mousemod.label(name) if self.kbm else padmod.LABEL[name]

    def stick_samples(self):
        """This frame's raw aim-stick samples as (game time, x, y)."""
        i = 1 if self.cfg["aim"]["stick"] == "left" else 3
        off = self.offset
        return [(s[0] - off, s[i], s[i + 1]) for s in self.pad.samples]

    # ---- effects ------------------------------------------------------------
    def sfx(self, name):
        self.app.sfx.play(name)

    def fx_hit(self, kill: bool, head: bool = False):
        self.hit_t, self.hit_kill, self.hit_head = self.t, kill, head
        self.sfx("kill" if kill else ("hit" if head else "tick"))
        if self.rec is not None:
            self.rec.event("kill" if kill else "hit", head=bool(head))

    def fx_miss(self):
        self.sfx("miss")
        if self.rec is not None:
            self.rec.event("miss")

    def note(self, text, col, dur=0.6):
        self.notes = [(text, col, self.t + dur)]

    def pop(self, pos, r):
        self.ghosts.append((pos, r, self.color, self.t))

    # ---- state --------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.state != "done"

    def _update_cam(self):
        z = self.ads_t * self.ads_t * (3.0 - 2.0 * self.ads_t)
        zoom = 1.0 + (self.zoom_max - 1.0) * z
        self.cam.eye = self.eye
        self.cam.set(self.yaw, self.pitch, self.fov, self.app.gfx.aspect, zoom, self.roll)
        self.fwd = self.cam.fwd

    def _go(self, now):
        self.state = "run"
        self._settled = False
        self.sfx("go")
        if self.kbm:        # starting or resuming: read which binds are really down, and keep the pointer in
            self.mouse.resync(self.binds.values())
            self.app.grab_mouse(True)
        if self.started:
            return
        self.started = True
        self.t = 0.0
        self.offset = now
        self._m_ref = (self.mouse.reports, self.mouse.paired)
        self.pad.locked = True
        self.pad.set_scanning(False)   # probing empty XInput slots would add jitter to timestamps
        self.pad.hz_log = []
        gc.collect()
        gc.disable()                   # a collection mid-run is a visible hitch at 240 Hz
        self.scn.begin(0.0)

    def pause(self):
        if self.state == "run":
            self.state = "paused"
            self.pauses += 1
            if self.kbm:
                self.app.grab_mouse(False)

    def _leave(self):
        gc.enable()
        if self.kbm:
            self.app.grab_mouse(False)
        if self.pad.active != padmod.VIRTUAL_SLOT:
            self.pad.locked = False
        self.pad.set_scanning(True)
        self.pad.hz_log = None
        self.state = "done"

    def abort(self):
        self._leave()
        self.app.end_game(None, self)
        if self.over.get("on_abort"):
            self.over["on_abort"]()

    def update(self, dt: float, now: float, nav: set, raw_dt: float):
        pad = self.pad
        st = self.state
        if st == "countdown":
            if nav & {"back", "start"}:
                # (an endless run being resumed has something to keep: B stops it and shows it)
                return self.finish() if self.endless and self.started else self.abort()
            self.count -= dt
            n = math.ceil(self.count)
            if n != self._beep and n > 0:
                self._beep = n
                self.sfx("count")
            if self.count <= 0.0:
                self._go(now)
            return
        if st == "paused":
            if nav & {"accept", "start"}:
                self.state, self.count, self._beep = "countdown", 1.5, None
            elif "x" in nav:
                self._leave()
                self.app.start(type(self.scn), self.playlist, self.over)
            elif "back" in nav:
                if self.endless:            # the only way an endless run ends: keep it and show it
                    self.finish()
                else:
                    self.abort()
            return
        if st != "run":
            return
        if "start" in nav or "esc" in nav or (not self.kbm and (pad.lost or pad.active is None)):
            return self.pause()

        self.t += dt
        self.offset = now - self.t
        scn, binds = self.scn, self.binds
        shoot, ads = binds["shoot"], binds["ads"]
        off = self.offset
        if self.kbm:
            m = self.mouse
            if m.hz:
                self.m_hz.append(m.hz)
            if m.dx or m.dy:
                self.m_age.append(m.age_ms)
            if self.kit:
                self.kit.step(dt, m.events, off)
            else:
                self._aim_mouse(dt, off)
        elif self.kit:
            self.kit.step(dt, pad.events, off)
        else:
            for t, name, down in pad.events:
                if name == shoot:
                    if down:
                        scn.on_shoot(t - off)
                elif name == ads:
                    scn.on_ads(t - off, down)
            self._aim(dt)
        scn.update(dt, self.t)
        if self.rec is not None:
            self.rec.frame(self)
        if self._settled:
            self.frames.append(raw_dt)
        else:
            self._settled = True     # the frame after a (re)start includes the gc pass / pause
        if scn.done or (scn.timed and self.t >= self.duration):
            self.finish()

    def _aim(self, dt):
        aim, pad, binds = self.cfg["aim"], self.pad, self.cfg["binds"]
        sx, sy = pad.stick(aim["stick"])
        self.aim_raw = (sx, sy)
        self.ads_held = pad.down(binds["ads"])
        self.fire_held = pad.down(binds["shoot"])
        step = dt / 0.12
        self.ads_t += max(-step, min(step, (1.0 if self.ads_held else 0.0) - self.ads_t))
        if self.scn.lock_aim:
            self.yaw_rate = self.pitch_rate = 0.0
        else:
            resp = self.aim
            ax, ay, _ = resp.shape(sx, sy)
            k = 1.0 + (resp.ads - 1.0) * self.ads_t
            self.yaw_rate = ax * resp.rate_h * k
            self.pitch_rate = (-ay if aim["invert_y"] else ay) * resp.rate_v * k
            self.yaw = (self.yaw + self.yaw_rate * dt + 180.0) % 360.0 - 180.0
            self.pitch = max(-80.0, min(80.0, self.pitch + self.pitch_rate * dt))
        self.turn_rate = math.hypot(self.yaw_rate * math.cos(math.radians(self.pitch)), self.pitch_rate)
        self.frame_turn = self.turn_rate * dt
        self._update_cam()

    def _aim_mouse(self, dt, off):
        """Core track on a mouse: this frame's reports, replayed in the order they happened.

        Counts turn the view one for one - no acceleration, no smoothing, nothing added. A click
        is judged where the crosshair was at that click, with the movement before it applied and
        the movement after it not yet: at 240 fps a flick can cover a target's width inside one
        frame, and "where it was when the frame ended" would be the wrong answer.
        """
        m, scn, binds = self.mouse, self.scn, self.binds
        shoot, ads = binds["shoot"], binds["ads"]
        # (the Core track's own ADS setting: 1.00 = the same speed across the screen as from the hip,
        # i.e. scaled by the zoom; another track's rule is applied to this track's zoom the same way)
        k = self.m_deg * (1.0 + (self.msens.ads(self.fov, self.zoom_max) - 1.0) * self.ads_t)
        ky = k if self.cfg["aim"]["invert_y"] else -k
        yaw0, pitch0 = self.yaw, self.pitch
        turned = 0.0
        free = not scn.lock_aim
        for t, kind, a, b in m.events:
            if kind == mousemod.MOTION:
                if free:
                    self.yaw += a * k
                    self.pitch = max(-80.0, min(80.0, self.pitch + b * ky))
                    turned += math.hypot(a * k * math.cos(math.radians(self.pitch)), b * k)
            elif kind == mousemod.BUTTON:
                if a == shoot:
                    if b:
                        self._update_cam()          # the view as it was at this click
                        scn.on_shoot(t - off)
                elif a == ads:
                    scn.on_ads(t - off, b)
        if not -180.0 <= self.yaw < 180.0:
            self.yaw = (self.yaw + 180.0) % 360.0 - 180.0
        self.ads_held = m.down(ads)
        self.fire_held = m.down(shoot)
        step = dt / 0.12
        self.ads_t += max(-step, min(step, (1.0 if self.ads_held else 0.0) - self.ads_t))
        self.aim_raw = (0.0, 0.0)
        if dt > 0.0:
            self.yaw_rate = ((self.yaw - yaw0 + 180.0) % 360.0 - 180.0) / dt
            self.pitch_rate = (self.pitch - pitch0) / dt
            self.turn_rate = turned / dt
        self.frame_turn = turned
        self._update_cam()

    def presented(self, t_wall: float):
        if self.state == "run":
            self.scn.presented(t_wall - self.offset)
            self.gpu_ms.append(self.app.gfx.gpu_ms)

    # ---- drawing ------------------------------------------------------------
    def spheres(self):
        out = list(self.scn.spheres()) if self.started else []
        if self.ghosts:
            keep = []
            for g in self.ghosts:
                pos, r, col, t0 = g
                k = (self.t - t0) / 0.14
                if k < 1.0:
                    keep.append(g)
                    c = (col[0] * 0.5 + 0.5, col[1] * 0.5 + 0.5, col[2] * 0.5 + 0.5)
                    out.append((pos, r * (1.0 + 0.55 * k), c, (1.0 - k) * 0.55, 0.0))
            self.ghosts = keep
        if self.rec is not None and self.state == "run":
            self.rec.spheres(out)
        return out

    def world_args(self) -> dict:
        """Arena geometry for the renderer (the Core track uses the default room)."""
        w = getattr(self.scn, "world", None)
        if w is None:
            return {}
        return {"room": w.room, "boxes": w.boxes, "smokes": w.smokes,
                "smoke_density": w.smoke_density, "t": self.t, "soft_from": getattr(w, "soft_from", None)}

    def draw(self, ui):
        W, scn, cfg = ui.W, self.scn, self.cfg
        cx = W / 2
        binds = cfg["binds"]
        if self.state == "countdown" and not self.started:
            ui.rect(0, 200, W, 560, (0.0, 0.0, 0.0, 0.45))
            ui.text(cx, 236, (self.over.get("title") or scn.name).upper(), "xl", WHITE, 1)
            if self.endless:
                ui.text(cx, 548, "ENDLESS  ·  NO CLOCK  ·  "
                        + ("ESC, THEN ESC AGAIN, FINISHES" if self.kbm else "START, THEN B, FINISHES"), "m", ACCENT, 1)
            y = 312
            for ln in ui.wrap(scn.how, "m", 980):
                ui.text(cx, y, ln, "m", DIM, 1)
                y += 28
            ui.text(cx, 410, str(max(math.ceil(self.count), 1)), "h", ACCENT, 1, scale=1.7)
            line = f"SHOOT  {self.label('shoot')}"
            if scn.needs_ads or not scn.lock_aim:
                line += f"        AIM  {self.label('ads')}"
            if self.kbm and self.kit and scn.can_move:
                line += "        MOVE  " + " ".join(self.label(k) for k in ("forward", "left", "back", "right"))
            ui.text(cx, 640, line, "l", WHITE, 1)
            pl = self.playlist
            sub = cfg["active_profile"] + (f"   ·   {pl['index'] + 1} of {len(pl['items'])}" if pl else "")
            extra = getattr(self.kit, "controls", None)     # a kit's own controls (Siege: lean, crouch, reload)
            if extra:
                ui.text(cx, 682, extra(), "s", DIM, 1)
            if getattr(self.kit, "assist_note", None):      # a track with no aim assist for anyone says so
                ui.text(cx, 588, self.kit.assist_note, "m", WARN, 1)
            elif self.kit and self.kbm:
                ui.text(cx, 588, "AIM ASSIST: NONE  ·  MOUSE AND KEYBOARD NEVER GET IT", "m", WARN, 1)
            elif self.kit:
                style = style_label(cfg["cod"]["aa_style"]).upper()
                ui.text(cx, 588, f"AIM ASSIST: {style} STYLE" if scn.aa else "AIM ASSIST: OFF", "m",
                        ACCENT if scn.aa else WARN, 1)
            ui.text(cx, 712 if extra else 696, sub + ("   ·   Esc cancels" if self.kbm else "   ·   B / Esc cancels"),
                    "s", FAINT, 1)
            return

        ui.rect(0, 0, W, 108, (0.0, 0.0, 0.0, 0.55))      # the ceiling lights pass behind the HUD text
        if self.endless and scn.timed:      # nothing to count down to: how long it has gone on
            m, s = divmod(int(self.t), 60)
            ui.text(cx, 24, f"{m}:{s:02d}", "xl", WHITE, 1)
        elif scn.timed:
            ui.text(cx, 24, f"{max(self.duration - self.t, 0.0):.1f}", "xl", WHITE, 1)
        else:
            ui.text(cx, 24, scn.progress(), "xl", WHITE, 1)
        ui.text(cx, 82, scn.name.upper() + ("   ·   ENDLESS" if self.endless else ""), "s",
                ACCENT if self.endless else DIM, 1)
        x = 56
        for label, val in scn.hud():
            ui.text(x, 30, val, "l", WHITE)
            ui.text(x, 66, label, "s", DIM)
            x += 190
        ui.text(W - 56, 30, cfg["active_profile"], "m", DIM, 2)
        ui.text(W - 56, 58, f"{self.label('shoot')} shoot  ·  {self.label('ads')} aim", "s", FAINT, 2)

        scn.draw(ui, self)
        if self.kit:
            self.kit.draw(ui)
        if scn.crosshair:
            draw_crosshair(ui, cfg["crosshair"])
        age = self.t - self.hit_t
        if 0.0 <= age < 0.13:
            tone = BAD if self.hit_kill else (WARN if self.hit_head else WHITE)
            c = alpha(tone, 1.0 - age / 0.13)
            for sx, sy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                ui.line(cx + sx * 12, 540 + sy * 12, cx + sx * 25, 540 + sy * 25, 3, c)
        if self.notes:
            text, col, until = self.notes[0]
            if self.t < until:
                ui.text(cx, 600, text, "l", col, 1)
            else:
                self.notes = []

        if self.state == "paused":
            ui.rect(0, 0, W, ui.H, (0.0, 0.0, 0.0, 0.62))
            ui.text(cx, 400, "PAUSED", "xl", WHITE, 1)
            quit_ = "Finish and see results" if self.endless else "Quit to menu"
            if self.endless:
                m, s = divmod(int(self.t), 60)
                ui.text(cx, 600, f"Endless  ·  {m}:{s:02d} played", "m", ACCENT, 1)
            if self.kbm:
                ui.text(cx, 520, f"Enter  Resume        X  Restart        Esc  {quit_}", "m", DIM, 1)
            else:
                if self.pad.active is None:
                    ui.text(cx, 470, "Controller disconnected", "m", WARN, 1)
                ui.text(cx, 520, f"A  Resume        X  Restart        B  {quit_}", "m", DIM, 1)
        elif self.state == "countdown":
            ui.text(cx, 330, str(max(math.ceil(self.count * 2), 1)), "h", ACCENT, 1, scale=1.4)

    # ---- result -------------------------------------------------------------
    def _frame_stats(self) -> dict:
        fr = sorted(self.frames)
        if not fr:
            return {}
        n = len(fr)
        worst = fr[int(n * 0.99):] or fr[-1:]
        return {
            "fps_avg": round(n / sum(fr), 1),
            "fps_1pct_low": round(len(worst) / sum(worst), 1),
            "frame_ms_p99": round(fr[min(int(n * 0.99), n - 1)] * 1000.0, 3),
            "frame_ms_max": round(fr[-1] * 1000.0, 3),
            "gpu_ms_avg": round(statistics.fmean(self.gpu_ms), 3) if self.gpu_ms else None,
        }

    def _input_record(self, hz, xinput, fs) -> dict:
        """How the input arrived during this run, for the log."""
        pad = self.pad
        out = {
            "kind": "kbm" if self.kbm else "pad",
            "report_hz_median": round(statistics.median(hz)) if hz else None,
            "report_hz_max": round(max(hz)) if hz else None,
            "poll_hz": round(pad.poll_hz) if xinput else None,
            # +/- this many ms on every button and stick timestamp
            "timestamp_resolution_ms": round(500.0 / pad.poll_hz, 3) if xinput
            else (round(500.0 / fs["fps_avg"], 3) if fs and not self.kbm else None),
        }
        if self.kbm:
            m, age = self.mouse, sorted(self.m_age)
            out.update({
                "aim_assist": False,            # never, whatever the scenario
                "dpi": self.cfg["mouse"]["dpi"],
                "reports": m.reports - self._m_ref[0],
                # share of mouse reports that were read together with another (see mouse.Mouse.paired)
                "paired_pct": round(100.0 * (m.paired - self._m_ref[1]) / max(m.reports - self._m_ref[0], 1), 1),
                # how old the newest report was when a frame used it: the input lag added on this side
                "sample_age_ms_median": round(age[len(age) // 2], 3) if age else None,
                "sample_age_ms_p99": round(age[min(int(len(age) * 0.99), len(age) - 1)], 3) if age else None,
            })
        return out

    def finish(self):
        app, pad, cfg, scn = self.app, self.pad, self.cfg, self.scn
        if self.over.get("on_done"):         # a sensitivity-finder block: hand the numbers back, log nothing
            out = scn.summary()
            if self.kit:
                out.update(self.kit.summary())
            out["duration_s"] = round(self.t, 2)
            self._leave()
            app.end_game(None, self)
            self.over["on_done"](out)
            return
        hz = list(pad.hz_log or [])
        info = dict(pad.info() or {"backend": "none"})
        xinput = info.get("backend") == "xinput" and pad.poll_hz
        if self.kbm:
            hz, xinput = self.m_hz, False
            info = {"backend": "bot" if self.mouse.virtual else "rawinput", "id": None, "name": "Mouse and keyboard"}
        fs = self._frame_stats()
        self._leave()
        now = datetime.datetime.now()
        track = scn.track
        settings = config.comparable_for(cfg, track)
        stats_ = scn.summary()
        if self.kit:
            stats_.update(self.kit.summary())
        rec = {
            "schema": 1,
            "app": __version__,
            "run_id": now.strftime("%Y%m%d-%H%M%S") + "-" + scn.id,
            "session_id": app.session_id,
            "time": now.isoformat(timespec="seconds"),
            "controller": cfg["active_profile"],
            "track": track,
            "scenario": scn.id,
            "scenario_name": scn.name,
            "duration_s": round(self.t, 2),
            "playlist": self.playlist["id"] if self.playlist else None,
            # guided comparison: which session / block / pass this run belongs to (stats.duel_cells)
            "duel": self.playlist.get("duel") if self.playlist else None,
            "pauses": self.pauses,
            "binds": dict(self.binds),
            "fingerprint": config.fingerprint_for(cfg, track),
            "settings": dict(settings, **{"aim.invert_y": cfg["aim"]["invert_y"]}),
            "device": info,
            "input": self._input_record(hz, xinput, fs),
            "display": dict({
                "width": app.gfx.w, "height": app.gfx.h, "refresh_hz": app.refresh,
                "vsync": cfg["video"]["vsync"], "fps_cap": app.frame_cap(),
                "quality": cfg["video"]["quality"], "reflections": cfg["video"]["reflections"],
            }, **fs),
            "stats": stats_,
        }
        try:
            if self.endless:            # practice, kept apart: see __init__
                rec["endless"] = True
                rec["run_id"] += "-endless"
                app.log.save_endless(rec)
            else:
                app.log.save(rec, {"events": scn.events, "trace": scn.trace()})
        except OSError as e:
            print(f"log: could not write run {rec['run_id']}: {e}")
            app.toast("Could not write this run to the log - see aimtuner.log", 6.0)
        if self.rec is not None:        # the replay, and the targets the ghost will play (written in the background)
            self.capture = self.rec.finish({"run_id": rec["run_id"], "time": rec["time"], "controller": rec["controller"],
                                            "duration": round(self.t, 3), "endless": self.endless,
                                            "stats": stats_, "metrics": [list(m) for m in scn.metrics],
                                            "primary": scn.primary})
            capture.save_async(self.capture, app.log.dir, rec["run_id"])
        self.sfx("done")
        app.end_game(rec, self)
