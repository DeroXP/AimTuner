"""Controller Lab screen: walks through the captures with live feedback, then shows
the numbers next to the last result from another controller."""
from __future__ import annotations

import datetime
import math

from . import lab
from . import pad as padmod
from .theme import ACCENT, BAD, DIM, FAINT, GOOD, WARN, WHITE, alpha
from .uikit import Screen, _footer, _header

# key, seconds, optional, title, instruction
STEPS = (
    ("rest", 3.0, False, "Sticks at rest",
     "Take both thumbs off the sticks and keep them off."),
    ("circle_r", 6.0, False, "Right stick, around the rim",
     "Roll the RIGHT stick around its outer edge in brisk, steady circles. Keep it pressed against the rim the whole time."),
    ("circle_l", 6.0, False, "Left stick, around the rim",
     "Same again with the LEFT stick."),
    ("flick", 10.0, False, "Snap-back",
     "Push the {stick} stick all the way to one side, then let it slip off your thumb so it springs back by "
     "itself. About six times, in different directions."),
    ("tap", 5.0, False, "Shoot button speed",
     "Tap {shoot} as fast as you can."),
    ("chord", 8.0, False, "Aim and shoot together",
     "Press {aim} and {shoot} at the same instant, the way you would in a fight. About eight times."),
    ("trig", 6.0, True, "Triggers",
     "Pull both triggers all the way and let go, three or four times. They only matter if you bind them."),
)


def _ctl(name: str):
    """Bind name -> what lab.taps / lab.chord expect (a button bit, or 'LT' / 'RT')."""
    return name if name in ("LT", "RT") else padmod.BIT[name]


def _f(v, dec=0, unit=""):
    if v is None:
        return "-"
    s = f"{v:,.{dec}f}"
    return s + unit


class Lab(Screen):
    def __init__(self, app):
        super().__init__(app)
        self.state = "intro"
        self.i = 0
        self.rows = None
        self.t0 = self.lock = 0.0
        self.data: dict = {}
        self.live: dict = {}
        self.records = lab.load(app.log.dir)
        self.rec = None

    @property
    def decor(self):
        return self.state == "intro"      # nothing drifting behind the stick displays

    # ---- flow ---------------------------------------------------------------
    def _usable(self) -> bool:
        info = self.app.pad.info()
        return bool(info) and info["backend"] in ("xinput", "bot")

    def _last(self):
        return lab.latest_by_controller(self.records).get(self.app.cfg["active_profile"])

    def _other(self, rec):
        rest = [r for r in self.records if r.get("controller") != rec.get("controller")]
        return rest[-1] if rest else None

    def _stop(self):
        if self.rows is not None:
            self.app.pad.capture_stop()
            self.rows = None
        self.app.capturing = False

    def _next(self, now):
        self.i += 1
        self.lock = now + 0.7        # a button still being mashed must not start the next step
        if self.i >= len(STEPS):
            self._finish()
        else:
            self.state = "ready"

    def update(self, nav, now):
        app, pad = self.app, self.app.pad
        if self.state == "intro":
            if "back" in nav:
                app.pop()
            elif "accept" in nav and self._usable():
                self.i, self.data, self.state, self.lock = 0, {}, "ready", now + 0.3
            elif "y" in nav and self._last():
                self.rec, self.state = self._last(), "results"
        elif self.state == "ready":
            if now < self.lock:
                return
            if "back" in nav:
                self.state = "intro"
            elif "accept" in nav:
                self.rows = pad.capture_start()
                self.t0, self.state, self.app.capturing = now, "capturing", True
                self.live = {"idx": 0, "bins": None, "count": 0, "armed": False}
            elif "x" in nav and STEPS[self.i][2]:
                self._next(now)
        elif self.state == "capturing":
            self._track()
            if "esc" in nav or "start" in nav or pad.active is None:
                self._stop()
                self.state, self.lock = "ready", now + 0.5
            elif now - self.t0 >= STEPS[self.i][1]:
                self.data[STEPS[self.i][0]] = self.rows
                self._stop()
                app.sfx.play("go")
                self._next(now)
        elif self.state == "results":
            if nav & {"back", "accept"}:
                self.state, self.rec = "intro", None

    def _track(self):
        """Cheap incremental feedback while recording (never re-walks old rows)."""
        key, live, rows = STEPS[self.i][0], self.live, self.rows
        n = len(rows)
        if key in ("circle_r", "circle_l"):
            live["bins"] = lab.rim_bins(rows, "right" if key == "circle_r" else "left", live["bins"], live["idx"])
        elif key == "flick":
            idx = 6 if self.app.cfg["aim"]["stick"] == "left" else 8
            for r in rows[live["idx"]:n]:
                m = math.hypot(r[idx], r[idx + 1]) / lab.AXIS
                if m >= 0.85:
                    live["armed"] = True
                elif m < 0.3 and live["armed"]:
                    live["armed"] = False
                    live["count"] += 1
        elif key in ("tap", "chord"):
            shoot = self.app.cfg["binds"]["shoot"]
            live["count"] += sum(1 for _, name, down in self.app.pad.events if down and name == shoot)
        live["idx"] = n

    def _finish(self):
        app, d = self.app, self.data
        b = app.cfg["binds"]
        aim = app.cfg["aim"]["stick"]
        move = "left" if aim == "right" else "right"
        circ = {"right": d.get("circle_r", []), "left": d.get("circle_l", [])}
        rest_rows = d.get("rest", [])

        def stick(which, with_flick):
            out = {"rest": lab.rest(rest_rows, which), "rim": lab.circle(circ[which], which),
                   "resolution": lab.resolution(circ[which], which)}
            if with_flick:
                out["flick"] = lab.flicks(d.get("flick", []), which)
            return out

        at_rest = lab.report_rate(rest_rows)
        self.rec = rec = {
            "schema": 1,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "controller": app.cfg["active_profile"],
            "device": dict(app.pad.info() or {}),
            # measured while the aim stick was circling, i.e. with data changing every report
            "report": lab.report_rate(circ[aim]),
            "report_at_rest_hz": at_rest["effective_hz"] if at_rest else 0,
            "aim_stick": dict(stick(aim, True), side=aim),
            "move_stick": dict(stick(move, False), side=move),
            "shoot": dict(lab.taps(d.get("tap", []), _ctl(b["shoot"])) or {}, bind=b["shoot"]),
            "chord": dict(lab.chord(d.get("chord", []), _ctl(b["ads"]), _ctl(b["shoot"])) or {},
                          first=b["ads"], second=b["shoot"]),
            "triggers": {t: lab.trigger(d.get("trig", []), t) for t in ("LT", "RT")},
        }
        try:
            lab.save(app.log.dir, rec)
        except OSError as e:
            print(f"lab: could not write the result: {e}")
            app.toast("Could not write the lab result to the log", 5.0)
        self.records.append(rec)
        self.data = {}
        self.state = "results"
        app.sfx.play("done")

    # ---- drawing ------------------------------------------------------------
    def _text(self, step) -> str:
        cfg = self.app.cfg
        return step[4].format(stick=cfg["aim"]["stick"].upper(), shoot=padmod.LABEL[cfg["binds"]["shoot"]],
                              aim=padmod.LABEL[cfg["binds"]["ads"]])

    def _stick(self, ui, cx, cy, R, x, y, label, bins=None):
        ui.ring(cx, cy, R, 2.5, alpha(WHITE, 0.30))
        if bins:
            pts = []
            for k, b in enumerate(bins):
                if b > 0.0:
                    a = (k + 0.5) / lab.BINS * 2 * math.pi - math.pi
                    pts.append((cx + math.cos(a) * b * R, cy - math.sin(a) * b * R, k))
            for (x0, y0, k0), (x1, y1, k1) in zip(pts, pts[1:] + pts[:1]):
                if (k1 - k0) % lab.BINS == 1:
                    ui.line(x0, y0, x1, y1, 3, GOOD)
        ui.disc(cx + x * R, cy - y * R, 9, ACCENT)
        ui.text(cx, cy + R + 16, label, "s", DIM, 1)

    def draw(self, ui):
        app, pad, W = self.app, self.app.pad, ui.W
        cx = W / 2
        _header(ui, "CONTROLLER LAB", "what the PC can measure about this pad")
        if self.state == "intro":
            self._intro(ui)
        elif self.state == "results":
            self._results(ui)
        else:
            key, secs, optional, title, _ = step = STEPS[self.i]
            ui.text(cx, 200, f"STEP {self.i + 1} OF {len(STEPS)}", "s", FAINT, 1)
            ui.text(cx, 228, title, "xl", WHITE, 1)
            y = 306
            for ln in ui.wrap(self._text(step), "l", 1180):
                ui.text(cx, y, ln, "l", DIM if self.state == "capturing" else WHITE, 1)
                y += 40
            if key in ("rest", "circle_r", "circle_l", "flick"):
                bins = self.live.get("bins") if self.state == "capturing" else None
                self._stick(ui, cx - 190, 600, 130, pad.lx, pad.ly, "LEFT", bins if key == "circle_l" else None)
                self._stick(ui, cx + 190, 600, 130, pad.rx, pad.ry, "RIGHT", bins if key == "circle_r" else None)
                if key == "flick" and self.state == "capturing":
                    ui.text(cx, 790, f"{self.live['count']} snap-backs seen", "l", ACCENT, 1)
                elif key.startswith("circle") and bins:
                    seen = sum(1 for b in bins if b > 0.5)
                    ui.text(cx, 790, f"rim traced: {100 * seen // lab.BINS}%", "l", ACCENT, 1)
            elif key in ("tap", "chord"):
                if self.state == "capturing":
                    ui.text(cx, 520, str(self.live["count"]), "h", ACCENT, 1, scale=1.6)
            else:
                for k, (v, name) in enumerate(((pad.lt, "LT"), (pad.rt, "RT"))):
                    bx = cx - 330 + k * 380
                    ui.rect(bx, 590, 280, 22, (1.0, 1.0, 1.0, 0.10))
                    ui.rect(bx, 590, 280 * v, 22, ACCENT)
                    ui.text(bx, 622, f"{name}  {v * 100:.0f}%", "m", DIM)
            if self.state == "capturing":
                k = min((app.now - self.t0) / secs, 1.0)
                ui.rect(cx - 400, 880, 800, 10, (1.0, 1.0, 1.0, 0.12))
                ui.rect(cx - 400, 880, 800 * k, 10, ACCENT)
                ui.text(cx, 902, "recording", "s", WARN, 1)
                _footer(ui, "Start / Esc  stop this step")
            else:
                ui.text(cx, 872, "Press A when you are ready", "l", ACCENT, 1)
                _footer(ui, "A  start" + ("        X  skip" if optional else "") + "        B  cancel")

    def _intro(self, ui):
        app, W = self.app, ui.W
        info = app.pad.info()
        x, y, w = 96.0, 200.0, 820.0
        ui.text(x, y, app.cfg["active_profile"], "l", WHITE)
        dev = f"{info['name']}  ·  {info.get('id') or 'no USB id'}" if info else "No controller detected"
        ui.text(x, y + 40, dev, "m", DIM if info else WARN)
        y = ui.para(x, y + 96, "Seven short steps, about a minute: report rate and how even it is, each stick's "
                    "rest position, outer range, resolution and snap-back, how fast your Shoot button really "
                    "cycles, how closely Aim and Shoot land together, and the triggers.", "m", WHITE, w) + 16
        y = ui.para(x, y, "While it records, the input thread stops sleeping and watches the pad continuously, so "
                    "each report is timed to a few microseconds.", "m", DIM, w) + 16
        y = ui.para(x, y, "What it cannot tell you is true input latency - the delay between your finger moving "
                    "and the PC hearing about it. Software never knows when the press physically happened; that "
                    "takes a hardware rig.", "m", DIM, w) + 30
        if info and not self._usable():
            ui.para(x, y, "This pad is not in XInput mode, so it can only be read once per frame - too coarse for "
                    "these tests. Switch it to XInput and reconnect.", "m", WARN, w)
        elif info:
            ui.text(x, y, "Press A to begin", "l", ACCENT)

        px = 1000.0
        pw = W - px - 96
        ui.panel(px, 190, pw, 620)
        tx, ty = px + 40, 224.0
        ui.text(tx, ty, "LAST RESULTS", "s", FAINT)
        ty += 34
        latest = lab.latest_by_controller(self.records)
        if not latest:
            ui.text(tx, ty, "No controller has been tested yet.", "m", DIM)
        for name, r in latest.items():
            rep = r.get("report") or {}
            ui.text(tx, ty, name, "l", WHITE if name == app.cfg["active_profile"] else DIM)
            ui.text(tx, ty + 38, f"{r['time'][:16].replace('T', '  ')}", "s", FAINT)
            ui.text(tx, ty + 62, f"{_f(rep.get('rate_hz'))} Hz   ·   jitter {_f(rep.get('jitter_us'), 1)} µs   ·   "
                    f"{_f(rep.get('steady_pct'), 1)}% steady", "m", DIM)
            ty += 118
        _footer(ui, "A  run the test" + ("        Y  show this pad's last result" if self._last() else "") + "        B  back")

    def _results(self, ui):
        rec, W = self.rec, ui.W
        other = self._other(rec)
        ui.text(W - 96, 70, rec["controller"], "xl", ACCENT, 2)
        ui.text(W - 96, 130, rec["time"][:16].replace("T", "  ") + "   ·   " + (rec["device"].get("id") or ""), "s", FAINT, 2)

        def g(r, *path):
            for p in path:
                r = (r or {}).get(p) if isinstance(r, dict) else None
            return r

        def rows_for(defs):
            out = []
            for d in defs:
                if isinstance(d, str):
                    out.append(d)
                    continue
                label, path, dec, unit, better = d
                out.append((label, g(rec, *path), g(other, *path) if other else None, dec, unit, better))
            return out

        aim = (rec.get("aim_stick") or {}).get("side", "right").upper()
        move = (rec.get("move_stick") or {}).get("side", "left").upper()
        shoot = padmod.LABEL.get(g(rec, "shoot", "bind"), "Shoot")
        first = padmod.LABEL.get(g(rec, "chord", "first"), "Aim")
        left = rows_for([
            "REPORTING  (aim stick moving)",
            ("New data arrives", ("report", "rate_hz"), 0, " Hz", 1),
            ("Typical interval", ("report", "interval_us"), 1, " µs", -1),
            ("Jitter", ("report", "jitter_us"), 1, " µs", -1),
            ("Intervals on time", ("report", "steady_pct"), 1, "%", 1),
            ("Slowest 1% of intervals", ("report", "p99_us"), 0, " µs", -1),
            ("Worst gap", ("report", "worst_ms"), 2, " ms", -1),
            ("Changes per second at rest", ("report_at_rest_hz",), 0, " Hz", 0),
            "BUTTONS",
            (f"{shoot} taps per second", ("shoot", "per_second"), 1, "", 1),
            (f"{shoot} shortest press", ("shoot", "press_shortest_ms"), 1, " ms", -1),
            (f"{shoot} switch bounces", ("shoot", "bounces"), 0, "", -1),
            (f"{shoot} after {first}, together", ("chord", "mean_ms"), 1, " ms", 0),
            ("   spread of that gap", ("chord", "spread_ms"), 1, " ms", -1),
            "TRIGGERS",
            ("LT range", ("triggers", "LT", "max_pct"), 0, "%", 1),
            ("LT fastest full pull", ("triggers", "LT", "pull_fastest_ms"), 1, " ms", -1),
            ("RT range", ("triggers", "RT", "max_pct"), 0, "%", 1),
            ("RT fastest full pull", ("triggers", "RT", "pull_fastest_ms"), 1, " ms", -1),
        ])
        right = rows_for([
            f"AIM STICK  ({aim})",
            ("Rest offset from centre", ("aim_stick", "rest", "offset_pct"), 2, "%", -1),
            ("Rest noise", ("aim_stick", "rest", "noise_pct"), 3, "%", -1),
            ("Lowest safe min deadzone", ("aim_stick", "rest", "min_deadzone"), 0, "", -1),
            ("Weakest direction reaches", ("aim_stick", "rim", "reach_min_pct"), 1, "%", 1),
            ("Highest safe max deadzone", ("aim_stick", "rim", "max_deadzone"), 0, "", 1),
            ("Roundness error", ("aim_stick", "rim", "circularity_err_pct"), 2, "%", -1),
            ("Resolution", ("aim_stick", "resolution", "bits"), 1, " bit", 1),
            ("Snap-back overshoot, average", ("aim_stick", "flick", "overshoot_avg_pct"), 1, "%", -1),
            ("Snap-back overshoot, worst", ("aim_stick", "flick", "overshoot_max_pct"), 1, "%", -1),
            ("Settles after release in", ("aim_stick", "flick", "settle_avg_ms"), 0, " ms", -1),
            ("Fastest throw to the rim", ("aim_stick", "flick", "throw_fastest_ms"), 0, " ms", -1),
            f"MOVE STICK  ({move})",
            ("Rest offset from centre", ("move_stick", "rest", "offset_pct"), 2, "%", -1),
            ("Lowest safe min deadzone", ("move_stick", "rest", "min_deadzone"), 0, "", -1),
            ("Weakest direction reaches", ("move_stick", "rim", "reach_min_pct"), 1, "%", 1),
            ("Highest safe max deadzone", ("move_stick", "rim", "max_deadzone"), 0, "", 1),
            ("Roundness error", ("move_stick", "rim", "circularity_err_pct"), 2, "%", -1),
            ("Resolution", ("move_stick", "resolution", "bits"), 1, " bit", 1),
        ])
        gap = 32.0
        pw = (W - 192 - gap) / 2
        for col, rows in enumerate((left, right)):
            px, py = 96 + col * (pw + gap), 178.0
            ui.panel(px, py, pw, 30.5 * len(rows) + 62)
            c1, c2 = px + pw * 0.70, px + pw - 28
            ui.text(c1, py + 14, "THIS PAD", "s", FAINT, 2)
            if other:
                ui.text(c2, py + 14, other["controller"].upper()[:16], "s", FAINT, 2)
            y = py + 44
            for r in rows:
                if isinstance(r, str):
                    ui.text(px + 28, y + 6, r, "s", ACCENT)
                else:
                    label, v, o, dec, unit, better = r
                    tone = WHITE
                    if better and v is not None and o is not None and v != o:
                        tone = GOOD if (v > o) == (better > 0) else BAD
                    ui.text(px + 28, y, label, "m", DIM)
                    ui.text(c1, y, _f(v, dec, unit), "m", tone, 2)
                    if other:
                        ui.text(c2, y, _f(o, dec, unit), "m", FAINT, 2)
                y += 30.5

        hist = (rec.get("report") or {}).get("hist")
        hx, hy, hw, hh = 96.0, 858.0, pw, 96.0
        if hist and max(hist) > 0:
            ui.text(hx, hy - 26, "TIME BETWEEN REPORTS", "s", FAINT)
            top = max(hist)
            bw = hw / len(hist)
            for k, c in enumerate(hist):
                if c:
                    h = max(2.0, hh * c / top)
                    ui.rect(hx + k * bw + 1, hy + hh - h, bw - 2, h, ACCENT)
            ui.rect(hx, hy + hh, hw, 1, (1.0, 1.0, 1.0, 0.2))
            bin_us = rec["report"]["hist_bin_us"]
            for k in (0, 8, 16, 24, 32):
                ui.text(hx + k * bw, hy + hh + 6, f"{k * bin_us:.0f} µs", "s", FAINT, 1 if k else 0)
        a_min, a_max = g(rec, "aim_stick", "rest", "min_deadzone"), g(rec, "aim_stick", "rim", "max_deadzone")
        m_min, m_max = g(rec, "move_stick", "rest", "min_deadzone"), g(rec, "move_stick", "rim", "max_deadzone")
        tx = 96 + pw + gap
        ui.text(tx, 832, "DEADZONES THIS PAD CAN RUN IN-GAME", "s", FAINT)
        ui.text(tx, 858, f"{aim.title()} stick:  min {_f(a_min)} or higher   ·   max {_f(a_max)} or lower", "m", WHITE)
        ui.text(tx, 888, f"{move.title()} stick:  min {_f(m_min)} or higher   ·   max {_f(m_max)} or lower", "m", WHITE)
        ui.para(tx, 924, "Min below that and the resting stick sends input by itself; max above that and some "
                "directions never reach full speed.", "s", DIM, pw)
        untimed = g(rec, "report", "untimed_pct")
        if untimed is not None and untimed > 15:
            ui.text(96, 990, f"{untimed:.0f}% of reports could not be timed individually (this PC was busy) - "
                    "rerun with other programs closed for a cleaner reading.", "s", WARN)
        _footer(ui, "green = better than the other pad        B  back")
