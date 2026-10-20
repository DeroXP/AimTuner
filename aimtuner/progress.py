"""Progress: your own runs over time on one controller, and a playlist made of the
scenarios where you are losing the most.

Everything else in the app compares controllers. This screen compares you with
yourself: for each scenario a line through every run you have made on the active
controller, your best, your last three, and whether those are ahead of or behind
the runs before them.

"Weakest" needs saying carefully. There are no other players' scores in here, so
nothing can say you are bad at a drill. What can be said is where the largest
share of your shots (or of your time on target) is being lost - see stats.grade -
and that is what "Train my weakest five" is built from.
"""
from __future__ import annotations

import math

from . import config, stats, tracks
from .theme import ACCENT, BAD, DIM, FAINT, GOOD, WARN, WHITE, alpha
from .uikit import ROW, Screen, _footer, _header, _row, _runs

TREND = {"up": ("▲", GOOD, "improving"), "down": ("▼", BAD, "slipping"), "steady": ("–", DIM, "steady")}


class Progress(Screen):
    decor = False           # a chart reads better without targets drifting behind it
    VISIBLE = 16
    PICK = 5

    def __init__(self, app, track=None):
        super().__init__(app)
        if track is None:       # open on the track this controller was last played on
            mine = [r for r in app.log.runs if r.get("controller") == app.cfg["active_profile"]]
            track = mine[-1].get("track", "core") if mine else "core"
        self.sel = 0
        self.top = 0
        self._cache = None
        self._set_track(tracks.BY_TRACK[track])

    def _set_track(self, track):
        self.track = track
        self.rows = [("train", None)]
        for gname, scns in track.groups:
            if gname:
                self.rows.append(("head", gname))
            self.rows += [("scn", s) for s in scns]
        self.sel = min(self.sel, len(self.rows) - 1)
        if self.rows[self.sel][0] == "head":
            self.sel += 1
        self.top = 0 if self.sel < self.VISIBLE else self.sel - self.VISIBLE + 1

    def _move(self, d):
        n = len(self.rows)
        self.sel = (self.sel + d) % n
        while self.rows[self.sel][0] == "head":
            self.sel = (self.sel + d) % n
        if self.sel < self.top + 1:
            self.top = max(self.sel - 1, 0)
        elif self.sel >= self.top + self.VISIBLE:
            self.top = self.sel - self.VISIBLE + 1

    def _data(self):
        """Everything the screen shows, recomputed only when a run is added or the settings change."""
        app = self.app
        who = app.cfg["active_profile"]
        fp = config.fingerprint_for(app.cfg, self.track.id)
        key = (self.track.id, who, fp, len(app.log.runs))
        if self._cache is None or self._cache[0] != key:
            runs = app.log.runs
            per = {s.id: (stats.progress(runs, s, who, fp), stats.grade(runs, s, who, fp)) for s in self.track.scenarios}
            self._cache = (key, per, stats.weakest(runs, self.track.id, who, fp, self.PICK))
        return self._cache[1], self._cache[2]

    def update(self, nav, now):
        app = self.app
        if "up" in nav:
            self._move(-1)
        if "down" in nav:
            self._move(1)
        if nav & {"lb", "rb", "x"}:
            k = tracks.TRACKS.index(self.track) + (-1 if "lb" in nav else 1)
            self._set_track(tracks.TRACKS[k % len(tracks.TRACKS)])
        kind, arg = self.rows[self.sel]
        if "back" in nav:
            app.pop()
        elif "accept" in nav:
            if kind == "scn":
                app.start(arg)
            else:
                picks = [s for s, _, _ in self._data()[1]["rows"]]
                if picks:
                    app.start_playlist(picks, "weakest", then="back")

    # ---- drawing ------------------------------------------------------------
    def draw(self, ui):
        app, cfg, track, W = self.app, self.app.cfg, self.track, ui.W
        per, weak = self._data()
        _header(ui, "PROGRESS", f"{cfg['active_profile']}   ·   {track.name} track   ·   your own runs over time")
        x, y, w = 96.0, 190.0, 560.0
        for i in range(self.top, min(self.top + self.VISIBLE, len(self.rows))):
            kind, arg = self.rows[i]
            sel = i == self.sel
            if kind == "head":
                ui.text(x, y + 14, arg.upper(), "s", FAINT)
                lw = ui.width(arg.upper(), "s")
                ui.rect(x + lw + 16, y + 23, w - lw - 60, 1, (1.0, 1.0, 1.0, 0.08))
            elif kind == "train":
                _row(ui, x, y, w, f"Train my weakest {len(weak['rows']) or self.PICK}", sel,
                     "playlist" if weak["rows"] else "no runs yet", hit=i)
            else:
                prog, g = per[arg.id]
                val = None
                if g:
                    val = f"{g[0]:.0f}%"
                elif prog["points"]:
                    val = _runs(len(prog["points"]))
                _row(ui, x, y, w, arg.name, sel, val, value_col=FAINT if (g and g[1]) else None, hit=i)
                if prog["trend"]:
                    mark, col, _ = TREND[prog["trend"]]
                    ui.text(x + w - 34, y + 3, mark, "m", col)
            y += ROW
        if self.top + self.VISIBLE < len(self.rows):
            ui.text(x, y + 2, "▼ more", "s", FAINT)
        if self.top > 0:
            ui.text(x + 200, 168, "▲ more", "s", FAINT)

        px, py = 720.0, 190.0
        pw, ph = W - px - 96.0, 760.0
        ui.panel(px, py, pw, ph)
        tx, ty, tw = px + 40, py + 34, pw - 80
        kind, arg = self.rows[self.sel]
        if kind == "train":
            self._train_panel(ui, tx, ty, tw, weak)
            hint = "A  play these" if weak["rows"] else ""
        else:
            self._scenario_panel(ui, tx, ty, tw, arg, *per[arg.id])
            hint = "A  play it"
        other = tracks.TRACKS[(tracks.TRACKS.index(track) + 1) % len(tracks.TRACKS)].name
        _footer(ui, (hint + "        " if hint else "") + f"X  {other} track        B  back")

    def _train_panel(self, ui, x, y, w, weak):
        cfg = self.app.cfg
        rows = weak["rows"]
        ui.text(x, y, "Train my weakest five", "xl", WHITE)
        if not rows:
            ui.para(x, y + 70, f"Nothing to rank yet: {cfg['active_profile']} has no runs on the {self.track.name} "
                    "track. Play the track once (or any of its scenarios) and this fills in.", "m", DIM, w)
            return
        dur = cfg["game"]["duration"]
        mins = math.ceil(len(rows) * (dur + 12) / 60.0)
        y = ui.para(x, y + 66, f"The {len(rows)} scenarios where the largest share of your shots is missing, back to "
                    f"back - about {mins} minutes.", "l", ACCENT, w) + 20
        ui.text(x, y, "SCENARIO", "s", FAINT)
        ui.text(x + w, y, "ON TARGET, LAST THREE RUNS", "s", FAINT, 2)
        y += 30
        for scn, g, stale in rows:
            ui.text(x, y, scn.name, "m", WHITE)
            if getattr(scn, "group", ""):
                ui.text(x + 260, y + 2, scn.group, "s", FAINT)
            ui.rect(x + w - 300, y + 12, 180, 6, (0.0, 0.0, 0.0, 0.5))
            ui.rect(x + w - 300, y + 12, 1.8 * max(0.0, min(g, 100.0)), 6, WARN if g < 50 else GOOD)
            ui.text(x + w, y, f"{g:.0f}%", "m", FAINT if stale else WHITE, 2)
            y += 36
        y += 14
        if any(stale for _, _, stale in rows):
            y = ui.para(x, y, "Grey numbers come from runs made with other settings - there are none with the "
                        "current ones yet. They are still a fair guide to what needs work.", "s", FAINT, w) + 12
        if weak["ranked"] < weak["total"]:
            y = ui.para(x, y, f"{weak['total'] - weak['ranked']} of this track's {weak['total']} scenarios have never been "
                        "played on this controller, so they could not be ranked.", "s", FAINT, w) + 12
        ui.para(x, y, "How this is chosen: by the share of your shots that hit (time on target, for the Core "
                "tracking drills) over your last three runs of each scenario. Nobody else's scores are in here, so "
                "this cannot say you are bad at something - some drills are simply harder - only where the most of "
                "your bullets are going astray, which is where practice gives the most back. The list is worked out "
                "again after every run.", "s", DIM, w)

    def _scenario_panel(self, ui, x, y, w, scn, prog, g):
        key, label, unit, better, dec = stats.primary_of(scn)
        ui.text(x, y, scn.name, "xl", WHITE)
        ui.text(x + w, y + 14, label + ("  ·  lower is better" if better < 0 else ""), "m", DIM, 2)
        pts = prog["points"]
        gx, gy, gw, gh = x + 8, y + 96, w - 16, 290.0
        ui.rect(gx - 8, gy - 16, gw + 16, gh + 32, (0.0, 0.0, 0.0, 0.30))
        if not pts:
            ui.text(gx + gw / 2, gy + gh / 2 - 14, "No runs on this controller yet.", "m", FAINT, 1)
        else:
            vals = [p[0] for p in pts]
            lo, hi = min(vals), max(vals)
            pad = (hi - lo) * 0.12 or max(abs(hi) * 0.1, 1.0)
            lo, hi = lo - pad, hi + pad
            n = len(pts)
            X = lambda i: gx + (gw * (i + 0.5) / n if n < 12 else gw * i / (n - 1))
            Y = lambda v: gy + gh * (1.0 - (v - lo) / (hi - lo))
            if prog["best"] is not None:
                ui.rect(gx, Y(prog["best"]), gw, 1, alpha(GOOD, 0.45))
                ui.text(gx + gw, Y(prog["best"]) - 24, "best " + stats.fmt(prog["best"], dec, unit), "s", GOOD, 2)
            prev = None
            for i, (v, same, _) in enumerate(pts):
                if same:
                    if prev is not None:
                        ui.line(prev[0], prev[1], X(i), Y(v), 2.0, alpha(WHITE, 0.45))
                    prev = (X(i), Y(v))
            for i, (v, same, _) in enumerate(pts):
                ui.disc(X(i), Y(v), 6 if same else 5, ACCENT if same else DIM)
            ui.text(gx, gy + gh + 22, pts[0][2][:10], "s", FAINT)
            ui.text(gx + gw, gy + gh + 22, pts[-1][2][:10], "s", FAINT, 2)
            if any(not same for _, same, _ in pts):
                ui.text(gx + gw / 2, gy + gh + 22, "bright: current settings    grey: other settings", "s", FAINT, 1)
        y = gy + gh + 70
        rows = [("Runs", f"{prog['n']} with the current settings" + (f"   ·   {len(pts)} in all" if len(pts) != prog["n"] else ""), WHITE)]
        if prog["n"]:
            rows += [("Last", stats.fmt(prog["last"], dec, unit), WHITE), ("Best", stats.fmt(prog["best"], dec, unit), WHITE),
                     ("Last three, average", stats.fmt(prog["recent"], dec, unit), WHITE)]
        if prog["trend"]:
            _, col, word = TREND[prog["trend"]]
            rows.append(("Trend", f"{word}: {prog['change_pct']:+.1f}% against the runs before", col))
        elif prog["n"]:
            rows.append(("Trend", f"needs {stats.TREND_MIN} runs with these settings (has {prog['n']})", FAINT))
        if g and (scn.grade != key or g[1]):        # what the training playlist ranks this scenario by
            glabel = next((m[1] for m in scn.metrics if m[0] == scn.grade), "On target")
            rows.append((glabel + ", last three", f"{g[0]:.0f}%" + ("  (runs with other settings)" if g[1] else ""), DIM if g[1] else WHITE))
        for k, v, col in rows:
            ui.text(x, y, k, "m", FAINT)
            ui.text(x + 280, y, v, "m", col)
            y += 36
        if not prog["n"] and pts:
            ui.para(x, y + 8, "None of these runs were made with the current settings, so there is no best or trend "
                    "to show yet. Play it once and the line starts again from here.", "s", DIM, w)
