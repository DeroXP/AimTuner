"""Self-test tooling: a synthetic player and a scripted tour of the app.

The bot feeds fake controller samples through the same queue the XInput thread
uses, so a self-test exercises the real input path, every scenario, the log
writer and the comparison maths without a human holding a pad. It is NOT an aim
assist and is never active outside --selftest.
"""
from __future__ import annotations

import math
import os
import random
import time

from . import mouse as mousemod
from . import pad as padmod
from . import tracks
from .screens import Binds, CodCalibrate, Compare, Controllers, Settings, TrackScreen


class Bot:
    def __init__(self, app, react=0.20, gain=14.0, noise=0.05, throw=0.045, seed=1):
        self.app = app
        self.rng = random.Random(seed)
        self.configure(react, gain, noise, throw)
        self._due = None
        self._last_pos = None
        self._bear = None           # (time, bearing, elevation) of the target last frame, to lead a mover
        self._seen_at = 0.0
        self._tap_until = self._next_tap = 0.0
        self._n = [0.0, 0.0]
        self._mt = 0.0              # mouse hand: last step, count remainders, what it is holding down
        self._res = [0.0, 0.0]
        self._mheld: set = set()
        self._lean = 0              # which way it wants to lean on the Siege track (-1, 0, +1)
        self._click = False         # a stick click it sent last frame (so the next one is a fresh press)
        self.hand = None            # fastest the mouse hand moves, cm/s (None: no limit; a check sets it)
        app.pad.use_virtual("bot")
        app.mouse.use_virtual()

    def configure(self, react, gain, noise, throw):
        self.react, self.gain, self.noise, self.throw = react, gain, noise, throw

    def _mouse_out(self, now, g, ry, rp, shoot, ads, mx, my):
        """The same intentions through a mouse and keyboard: whole counts for the turn it wants this
        frame (the remainder carried over, as a real sensor does), button and key edges for the rest."""
        m, binds = self.app.mouse, g.binds
        dt = min(now - self._mt, 0.05) if self._mt else 0.0
        self._mt = now
        if g.kit:
            deg = g.kit.m_deg * (g.kit.m_ads if g.ads_held else 1.0)
        else:
            deg = g.m_deg * (1.0 + (g.msens.ads(g.fov, g.zoom_max) - 1.0) * g.ads_t)
        up = 1.0 if g.cfg["aim"]["invert_y"] else -1.0
        vx, vy = (ry + self._n[0] * 40.0) / deg, (rp + self._n[1] * 40.0) / deg * up     # counts per second
        if self.hand:               # a real hand tops out, so a slow sensitivity turns slowly
            lim = self.hand / 2.54 * g.cfg["mouse"]["dpi"]
            vx, vy = max(-lim, min(lim, vx)), max(-lim, min(lim, vy))
        self._res[0] += vx * dt
        self._res[1] += vy * dt
        cx, cy = int(self._res[0]), int(self._res[1])
        self._res[0] -= cx
        self._res[1] -= cy
        if cx or cy:
            m.inject(now, mousemod.MOTION, cx, cy)
        want = {binds["shoot"]: shoot, binds["ads"]: ads, binds["right"]: mx > 0.3, binds["left"]: mx < -0.3,
                binds["forward"]: my > 0.3, binds["back"]: my < -0.3,
                binds["lean_l"]: self._lean < 0, binds["lean_r"]: self._lean > 0}
        for name, on in want.items():
            if on != (name in self._mheld):
                (self._mheld.add if on else self._mheld.discard)(name)
                m.inject(now, mousemod.BUTTON, name, on)

    def step(self, now: float):
        app = self.app
        g, cfg = app.game, app.cfg
        aim = cfg["aim"]
        ax = ay = mx = my = 0.0
        ry = rp = 0.0               # the turn rate it wants, deg/s (what a mouse hand is given directly)
        shoot = ads = False
        self._lean = 0
        if g and g.state == "run":
            scn = g.scn
            if scn.id == "reaction":
                if scn.state == "go":
                    if self._due is None:
                        self._due = now + max(0.12, self.rng.gauss(self.react, 0.018))
                    shoot = now >= self._due
                else:
                    self._due = None
            elif scn.id == "sticksnap":
                if scn.state == "go" and scn.cue:
                    if self._due is None:
                        self._due = now + max(0.10, self.rng.gauss(self.react * 0.9, 0.015))
                    if now >= self._due:
                        k = min((now - self._due) / self.throw, 1.0)
                        dx, dy = scn.DIRS[scn.cue]
                        ax, ay = dx * k, dy * k
                else:
                    self._due = None
            else:
                live = scn.live()
                kit, eye = g.kit, g.eye
                if kit is not None:
                    ads = bool(scn.dummies)             # sights up whenever there is something to shoot
                    mv = scn.bot_move(g)
                    if mv is None and (scn.strafe_required or scn.shoot_back):
                        mv = (math.copysign(1.0, math.sin(now * 2.6)), 0.0)
                    if mv:
                        mx, my = mv
                    self._lean = scn.bot_lean(g) if hasattr(scn, "bot_lean") else 0
                if not live:
                    self._last_pos = self._bear = None
                else:
                    pos, r = min(live, key=lambda t: g.err_deg(t[0]))
                    if self._last_pos is None or math.dist(pos, self._last_pos) > 1.5:
                        self._seen_at = now + max(0.08, self.rng.gauss(self.react, 0.02))
                        self._bear = None
                    self._last_pos = pos
                    if now >= self._seen_at:
                        dx, dy, dz = pos[0] - eye[0], pos[1] - eye[1], pos[2] - eye[2]
                        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                        ey = (math.degrees(math.atan2(dx, -dz)) - g.yaw + 180.0) % 360.0 - 180.0
                        ep = math.degrees(math.asin(dy / dist)) - g.pitch
                        for i in range(2):      # smoothed hand tremor
                            self._n[i] += (self.rng.gauss(0.0, self.noise) - self._n[i]) * 0.08
                        if kit is not None:
                            # Chasing the error alone trails a steady mover by a constant gap; a person
                            # leads it. Add most of the target's own rate of travel across the view.
                            bear, elev = ey + g.yaw, ep + g.pitch
                            lead_y = lead_p = 0.0
                            if self._bear is not None and now > self._bear[0]:
                                dtb = now - self._bear[0]
                                lead_y = ((bear - self._bear[1] + 180.0) % 360.0 - 180.0) / dtb
                                lead_p = (elev - self._bear[2]) / dtb
                                if abs(lead_y) > 400.0 or abs(lead_p) > 400.0:
                                    lead_y = lead_p = 0.0
                            self._bear = (now, bear, elev)
                            ry, rp = ey * self.gain + 0.8 * lead_y, ep * self.gain + 0.8 * lead_p
                            if not g.kbm:
                                ax = kit.stick_for(ry) + self._n[0]
                                ay = kit.stick_for(rp, True) + self._n[1]
                        else:
                            k = 1.0 + (g.aim.ads - 1.0) * g.ads_t
                            ry, rp = ey * self.gain, ep * self.gain
                            ax = g.aim.stick_for(ry, False, k) + self._n[0]
                            ay = g.aim.stick_for(rp, True, k) + self._n[1]
                            ads = scn.needs_ads
                        if aim["invert_y"]:
                            ay = -ay
                        radius = math.degrees(math.asin(min(r / dist, 1.0)))
                        err = g.err_deg(pos)
                        if scn.hold_fire:
                            shoot = True if kit is None else err < radius * 1.5
                        elif err < radius * 0.8 and (not scn.needs_ads or g.ads_t > 0.9):
                            if now >= self._next_tap:
                                self._tap_until, self._next_tap = now + 0.03, now + 0.14
                        shoot = shoot or now < self._tap_until
        if g and g.kbm:
            return self._mouse_out(now, g, ry, rp, shoot, ads, mx, my)
        btn = lt = rt = 0
        # Siege track on a pad: a lean is a click of a stick, and the same click stands you up again -
        # so click only when where it leans is not where it wants to lean, and never two frames running.
        kit = g.kit if g and g.state == "run" else None
        if kit is not None and hasattr(kit, "lean_want") and ads and kit.lean_want != self._lean and not self._click:
            go = self._lean if self._lean else kit.lean_want
            btn |= padmod.BIT["RS" if go > 0 else "LS"]
            self._click = True
        else:
            self._click = False
        for want, name in ((shoot, cfg["binds"]["shoot"]), (ads, cfg["binds"]["ads"])):
            if want:
                if name == "RT":
                    rt = 255
                elif name == "LT":
                    lt = 255
                else:
                    btn |= padmod.BIT[name]
        s16 = lambda v: int(max(-1.0, min(1.0, v)) * 32767)
        if aim["stick"] == "left":
            app.pad.inject(now, btn, lt, rt, s16(ax), s16(ay), s16(mx), s16(my))
        else:
            app.pad.inject(now, btn, lt, rt, s16(mx), s16(my), s16(ax), s16(ay))


class Autopilot:
    """Walks the menus, plays every scenario with two differently-skilled bots,
    takes screenshots, and quits. Runs entirely on a scratch config and log dir."""

    PLAYERS = (
        ("Bot Fast", dict(react=0.19, gain=15.0, noise=0.04, throw=0.040)),
        ("Bot Slow", dict(react=0.26, gain=10.0, noise=0.09, throw=0.060)),
    )

    def __init__(self, app, shots_dir, repeats=2, only=None, kbm=False):
        self.app = app
        self.shots_dir = shots_dir
        self.repeats = repeats
        self.only = only
        self.kbm = kbm              # the bots play on (virtual) mouse and keyboard instead of a pad
        self.errors: list = []
        self._gen = self._script()

    def step(self, now):
        try:
            next(self._gen)
        except StopIteration:
            self.app.quit()

    def _wait(self, secs):
        end = time.perf_counter() + secs
        while time.perf_counter() < end:
            yield

    def _shot(self, name):
        if self.shots_dir:
            self.app.pending_shot = os.path.join(self.shots_dir, name + ".png")

    def _script(self):
        app = self.app
        app.cfg["profiles"] = [dict({"name": n, "devices": []}, **({"kind": "kbm"} if self.kbm else {}))
                               for n, _ in self.PLAYERS]
        app.cfg["active_profile"] = self.PLAYERS[0][0]
        yield from self._wait(0.5)
        self._shot("01_menu")
        yield
        tour = (("02_settings", lambda: Settings(app)), ("02b_settings_cod", lambda: Settings(app, "cod.sens_h")),
                ("02c_calibrate", lambda: CodCalibrate(app)), ("03_binds", lambda: Binds(app)),
                ("04_controllers", lambda: Controllers(app)), ("06_track_core", lambda: TrackScreen(app, tracks.CORE)),
                ("07_track_cod", lambda: TrackScreen(app, tracks.COD)),
                ("08_track_r6", lambda: TrackScreen(app, tracks.R6)),
                ("09_settings_r6", lambda: Settings(app, "r6.weapon")))
        for name, make in tour:
            app.push(make())
            yield from self._wait(0.25)
            self._shot(name)
            yield
            app.pop()
        # --only takes scenario ids and/or track ids ("core", "cod", "r6")
        todo = [s for s in tracks.ALL if (not self.only or s.id in self.only or s.track in self.only) and app.playable(s)]
        for rep in range(self.repeats):
            for who, params in self.PLAYERS:
                app.set_profile(who)
                app.bot.configure(**params)
                for cls in todo:
                    app.start(cls)
                    game = app.game
                    if rep == 0 and who == self.PLAYERS[0][0] and cls is todo[0]:
                        yield from self._wait(0.2)
                        self._shot("05_countdown")
                    while app.game is game and game.state == "countdown":
                        yield
                    yield from self._wait(min(2.5, game.duration * 0.5))
                    if rep == 0 and who == self.PLAYERS[0][0]:
                        self._shot(f"10_run_{cls.id}")
                    while app.game is game:
                        yield
                    if rep == 0 and who == self.PLAYERS[0][0]:
                        yield from self._wait(0.15)
                        self._shot(f"20_results_{cls.id}")
                        yield
                    if len(app.screens) < 2:
                        self.errors.append(f"{cls.id}: no results screen")
                    app.pop()
        for track in {s.track for s in todo}:
            app.push(Compare(app, track=track))
            yield from self._wait(0.3)
            self._shot(f"30_compare_{track}")
            yield
            app.pop()
        yield from self._wait(0.2)
        self._shot("31_menu_after")
        yield
