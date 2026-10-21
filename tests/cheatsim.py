"""Simulated cheats, for testing the anti-cheat. Test code only: never imported by the app.

Each plays through the game's real input path (the same virtual pad / mouse queue as the
self-test bot), with what a cheat has that a player does not: the targets' true positions.

    snap       a classic aimbot: locks on within a frame or two of a target showing, fires on it
    smooth     a "legit" aimbot: closes a fixed share of the gap every frame (smoothing), fires on it
    human_ai   a humanized "AI" aimbot of the screen-reading kind: a frame-grab-and-detect delay,
               a random human-looking reaction, a planned minimum-jerk movement (bell-shaped speed,
               as a hand's) of a Fitts's-law duration, a little aim-point scatter, a short delay
               before firing, and for a moving target a lead from the detected motion
    hand       a hand's pace (a reaction, then a slow correction): the aim under anti-recoil
plus anti-recoil (ar=True): while fire is held, a steady pull down from the first bullet, the way
a recoil script or a controller adapter's "anti-recoil" does it.
"""
import math
import random

from aimtuner import mouse as mousemod
from aimtuner import pad as padmod
from aimtuner.bot import Bot


def _mjerk(s):
    s = min(max(s, 0.0), 1.0)
    return s * s * s * (10.0 - 15.0 * s + 6.0 * s * s)


class Cheat(Bot):
    def __init__(self, app, mode="smooth", ar=False, ar_rate=None, seed=3, **kw):
        super().__init__(app, react=0.2, gain=10.0, noise=0.0, throw=0.05, seed=seed)
        self.mode, self.ar = mode, ar
        self.ar_rate = ar_rate          # deg/s pulled down while firing (None: from the gun)
        self.crng = random.Random(seed * 7 + 1)
        self.plan = None                # human_ai: (t0, t1, y0, p0, y1, p1) of the movement in hand
        self.cur = None                 # the target being worked on (position when picked)
        self.fire_at = None
        self.hist = []                  # human_ai: (t, bearing, elevation) of the detected target
        self.fire_t0 = None

    # ---- what the cheat "sees" -------------------------------------------------------------
    def _angles(self, g, pos):
        eye = g.eye
        dx, dy, dz = pos[0] - eye[0], pos[1] - eye[1], pos[2] - eye[2]
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        bear = math.degrees(math.atan2(dx, -dz))
        elev = math.degrees(math.asin(max(-1.0, min(1.0, dy / dist))))
        return bear, elev, dist

    def step(self, now: float):
        app = self.app
        g = app.game
        if not (g and g.state == "run") or g.scn.id in ("reaction", "sticksnap"):
            return super().step(now)
        scn, kit = g.scn, g.kit
        ry = rp = 0.0
        mx = my = 0.0
        shoot = False
        ads = bool(scn.dummies) if kit is not None else scn.needs_ads
        self._lean = 0
        if kit is not None:
            mv = scn.bot_move(g)
            if mv is None and (scn.strafe_required or scn.shoot_back):
                mv = (math.copysign(1.0, math.sin(now * 2.6)), 0.0)
            if mv:
                mx, my = mv
            self._lean = scn.bot_lean(g) if hasattr(scn, "bot_lean") else 0
        live = scn.live()
        dt = 1.0 / 240.0
        if live:
            pos, r = min(live, key=lambda t: g.err_deg(t[0]))
            bear, elev, dist = self._angles(g, pos)
            radius = math.degrees(math.asin(min(r / dist, 1.0)))
            new = self.cur is None or math.dist(pos, self.cur) > 1.5
            self.cur = pos
            ey = (bear - g.yaw + 180.0) % 360.0 - 180.0
            ep = elev - g.pitch
            err = math.hypot(ey, ep)
            if self.mode == "snap":
                if new:
                    self._seen_at = now + self.crng.uniform(0.008, 0.020)       # a frame or two of detection
                if now >= self._seen_at:
                    ry, rp = ey / dt * 0.9, ep / dt * 0.9                      # there in a frame
                    shoot = err < radius * 0.9
            elif self.mode == "smooth":
                if new:
                    self._seen_at = now + self.crng.uniform(0.03, 0.06)
                if now >= self._seen_at:
                    k = 0.07                                                     # share of the gap per frame
                    lead_y = lead_p = 0.0
                    if self._bear is not None and now > self._bear[0] and not new:
                        dtb = now - self._bear[0]
                        lead_y = ((bear - self._bear[1] + 180.0) % 360.0 - 180.0) / dtb
                        lead_p = (elev - self._bear[2]) / dtb
                        if abs(lead_y) > 400 or abs(lead_p) > 400:
                            lead_y = lead_p = 0.0
                    self._bear = (now, bear, elev)
                    ry, rp = ey * k / dt + lead_y, ep * k / dt + lead_p
                    shoot = err < radius * 0.8
            elif self.mode == "hand":
                # a hand's pace: a human reaction, then closing the gap at about 10 /s (it takes a
                # quarter of a second to take out most of an error) - slow enough that what a recoil
                # script adds to the input is still there to see
                if new:
                    self._seen_at = now + max(0.15, self.crng.gauss(0.22, 0.04))
                if now >= self._seen_at:
                    ry, rp = ey * 10.0, ep * 10.0
                    shoot = err < radius * 0.8
            else:
                ry, rp, shoot = self._human_ai(g, now, new, bear, elev, radius, err)
        else:
            self.cur = self._bear = self.plan = None
            self.hist = []
        if scn.hold_fire and live:
            shoot = shoot or err < radius * 1.5
        # anti-recoil: pull down steadily from the first bullet while fire is held
        if self.ar and shoot:
            if self.fire_t0 is None:
                self.fire_t0 = now
            pull = self.ar_rate if self.ar_rate is not None else self._gun_pull(g)
            rp -= pull
        elif not shoot:
            self.fire_t0 = None
        return self._out(g, now, ry, rp, shoot, ads, mx, my)

    def _gun_pull(self, g):
        """How fast the gun climbs while firing, deg/s - what a recoil script is set to pull."""
        kit = g.kit
        if kit is None:
            return 6.0
        gun = getattr(kit, "gun", None)
        if isinstance(gun, dict) and "recoil" in gun:
            rc = gun["recoil"]
            grow = rc.get("grow", (0, 0, 1.0))[2]          # the climb steepens over a long spray
            return rc["climb"] * gun["rpm"] / 60.0 * (1.0 + grow) / 2.0
        return 7.0

    def _human_ai(self, g, now, new, bear, elev, radius, err):
        """Screen-capture aimbot dressed up as a person (see the module notes)."""
        DETECT = 0.028                      # frame grab + model + mouse adapter
        if new:
            self.plan = None
            self.hist = []
            self._seen_at = now + max(0.13, self.crng.gauss(0.19, 0.025))      # "humanized" reaction
            self.fire_at = None
        self.hist.append((now, bear, elev))
        self.hist = [h for h in self.hist if h[0] > now - 0.3]
        # what the detector reports: the target as it was DETECT ago
        seen = next((h for h in reversed(self.hist) if h[0] <= now - DETECT), None)
        if seen is None:
            return 0.0, 0.0, False
        if now < self._seen_at:
            return 0.0, 0.0, False
        # lead from the detected motion
        old = next((h for h in self.hist if h[0] >= seen[0] - 0.08), None)
        vy = vp = 0.0
        if old is not None and seen[0] - old[0] > 0.02:
            vy = ((seen[1] - old[1] + 180.0) % 360.0 - 180.0) / (seen[0] - old[0])
            vp = (seen[2] - old[2]) / (seen[0] - old[0])
        aim_y, aim_p = seen[1] + vy * DETECT, seen[2] + vp * DETECT
        ey = (aim_y - g.yaw + 180.0) % 360.0 - 180.0
        ep = aim_p - g.pitch
        if self.plan is None:
            amp = math.hypot(ey, ep)
            dur = 0.10 + 0.11 * math.log2(1.0 + amp / max(2.0 * radius, 0.3))  # Fitts's law
            sy, sp = self.crng.gauss(0.0, 0.25 * radius), self.crng.gauss(0.0, 0.25 * radius)
            self.plan = (now, now + dur, g.yaw, g.pitch, ey + sy, ep + sp)
        t0, t1, y0, p0, dy_, dp_ = self.plan
        s = _mjerk((now + 1.0 / 240.0 - t0) / (t1 - t0))
        want_y = y0 + dy_ * s + vy * (now - t0)
        want_p = p0 + dp_ * s + vp * (now - t0)
        dt = 1.0 / 240.0
        ry = ((want_y - g.yaw + 180.0) % 360.0 - 180.0) / dt
        rp = (want_p - g.pitch) / dt
        if now >= t1:
            ry, rp = (ey * 0.25) / dt + vy, (ep * 0.25) / dt + vp                 # stay on it
        shoot = False
        if err < radius * 0.75:
            if self.fire_at is None:
                self.fire_at = now + self.crng.uniform(0.02, 0.06)
            shoot = now >= self.fire_at
        return ry, rp, shoot

    def _out(self, g, now, ry, rp, shoot, ads, mx, my):
        if g.kbm:
            return self._mouse_out(now, g, ry, rp, shoot, ads, mx, my)
        kit = g.kit
        cfg = self.app.cfg
        if kit is not None:
            ax, ay = kit.stick_for(ry), kit.stick_for(rp, True)
        else:
            k = 1.0 + (g.aim.ads - 1.0) * g.ads_t
            ax, ay = g.aim.stick_for(ry, False, k), g.aim.stick_for(rp, True, k)
        if cfg["aim"]["invert_y"]:
            ay = -ay
        btn = lt = rt = 0
        for want, name in ((shoot, cfg["binds"]["shoot"]), (ads, cfg["binds"]["ads"])):
            if want:
                if name == "RT":
                    rt = 255
                elif name == "LT":
                    lt = 255
                else:
                    btn |= padmod.BIT[name]
        s16 = lambda v: int(max(-1.0, min(1.0, v)) * 32767)
        if cfg["aim"]["stick"] == "left":
            self.app.pad.inject(now, btn, lt, rt, s16(ax), s16(ay), s16(mx), s16(my))
        else:
            self.app.pad.inject(now, btn, lt, rt, s16(mx), s16(my), s16(ax), s16(ay))
