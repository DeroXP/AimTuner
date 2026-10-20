"""Menu screens. Everything is drivable from the pad (D-pad / left stick, A, B)
and from the keyboard (arrows, Enter, Esc); only naming a profile needs keys."""
from __future__ import annotations

import math
import time

from . import codkit, config, r6kit, sens, stats, tracks
from . import mouse as mousemod
from . import pad as padmod
from .dpiscreen import DpiScreen
from .duel import Duel
from .game import draw_crosshair
from .labscreen import Lab
from .progress import Progress
from .sensfinder import SensFinder, mouse_now
from .theme import ACCENT, BAD, DIM, FAINT, GOOD, WARN, WHITE, alpha
from .uikit import ROW, Screen, _footer, _header, _hit, _row, _runs


def _blabel(name: str) -> str:
    """A bind in words, whichever device it belongs to."""
    return padmod.LABEL.get(name) or mousemod.label(name)


def _bind_text(cfg) -> str:
    b = config.binds(cfg)
    return f"{_blabel(b['shoot'])} shoot · {_blabel(b['ads'])} aim"


def _finder_now(app) -> str:
    """What the sensitivity finder would start from: the settings in use, in their own units."""
    cfg = app.cfg
    kind_ = config.kind(cfg)
    spec = sens.FINDER[kind_][config.sens_source(cfg, kind_, "cod")]
    ads = spec["ads"][4].format(config.get(cfg, spec["ads"][0]))
    if kind_ == "kbm":
        return f"now {mouse_now(app)[0]:.1f} cm · ADS {ads}"
    return f"now {spec['hip'][4].format(config.get(cfg, spec['hip'][0]))} · ADS {ads}"


def _sens_line(cfg, track: str) -> str:
    """One line on how the view turns on this track for the active input device - its own settings,
    or the track's it follows - and what aim assist there is."""
    kbm = config.kind(cfg) == "kbm"
    c = cfg["cod"]
    if kbm:
        ms = sens.mouse(cfg, track)
        src = ms.src
        line = f"Mouse: {ms.cm360(cfg):.1f} cm per turn at {cfg['mouse']['dpi']} DPI"
    else:
        p = sens.pad(cfg, track)
        src = p.src
        if src == "cod":
            line = f"Sensitivity {c['sens_h']:.2f} / {c['sens_v']:.2f}   ·   ADS {c['ads_mult']:.2f}   ·   {c['curve'].title()} curve"
        elif track == "core" and src == "core":
            return ""               # the Core track's own stick settings: nothing a line here adds
        else:
            line = f"{p.rate_h:.0f}°/s hip   ·   {p.rate_h * p.ads:.0f}°/s ADS"
    if src != track:
        line += f"   ·   follows {sens.NAMES[src]}"
    if track == "cod":
        line += "   ·   no aim assist on a mouse" if kbm else f"   ·   aim assist {codkit.style_label(c['aa_style'])} style"
    elif track == "r6":
        from . import r6kit

        line = f"{r6kit.gun_of(cfg['r6'])['name']}   ·   " + line + "   ·   no aim assist"
    elif kbm:
        line += "   ·   raw input, no acceleration"
    return line


# -------------------------------------------------------------------- main
class MainMenu(Screen):
    ITEMS = ("core", "cod", "r6", "progress", "sens", "lab", "pad", "binds", "settings", "compare", "duel", "quit")

    def __init__(self, app):
        super().__init__(app)
        self.sel = 0

    def update(self, nav, now):
        app = self.app
        n = len(self.ITEMS)
        if "up" in nav:
            self.sel = (self.sel - 1) % n
        if "down" in nav:
            self.sel = (self.sel + 1) % n
        kind = self.ITEMS[self.sel]
        if kind == "pad" and ("left" in nav or "right" in nav):
            names = [p["name"] for p in app.cfg["profiles"]]
            i = names.index(app.cfg["active_profile"])
            app.set_profile(names[(i + (1 if "right" in nav else -1)) % len(names)])
        if "accept" in nav:
            if kind in tracks.BY_TRACK:
                app.push(TrackScreen(app, tracks.BY_TRACK[kind]))
            elif kind == "progress":
                app.push(Progress(app))
            elif kind in ("lab", "duel") and config.kind(app.cfg) == "kbm":
                what = {"lab": "The Controller lab tests a controller",
                        "duel": "Guided comparison is between two controllers"}[kind]
                app.toast(what + " - switch to a controller profile to use it", 5.0)
            elif kind == "duel":
                app.push(Duel(app))
            elif kind == "sens":
                app.push(SensFinder(app))
            elif kind == "lab":
                app.push(Lab(app))
            elif kind == "pad":
                app.push(Controllers(app))
            elif kind == "binds":
                app.push(KbmBinds(app) if config.kind(app.cfg) == "kbm" else Binds(app))
            elif kind == "settings":
                app.push(Settings(app))
            elif kind == "compare":
                app.push(Compare(app))
            elif kind == "quit":
                app.quit()

    def draw(self, ui):
        app, cfg = self.app, self.app.cfg
        W = ui.W
        _header(ui, "AIMTUNER", "aim benchmark and trainer  ·  controller, or mouse and keyboard")
        x, y, w = 96.0, 210.0, 560.0
        c = cfg["cod"]
        kbm = config.kind(cfg) == "kbm"
        rows = {
            "core": ("Core track", f"{sum(1 for s in tracks.CORE.scenarios if app.playable(s))} scenarios"),
            "cod": ("Call of Duty track", f"{len(tracks.COD.scenarios)} scenarios"),
            "r6": ("Siege track", f"{len(tracks.R6.scenarios)} scenarios"),
            "progress": ("Progress and training", "your runs over time"),
            "duel": ("Guided comparison", "controllers only" if kbm else "A - B - B - A"),
            "sens": ("Sensitivity finder", _finder_now(app)),
            "lab": ("Controller lab", "controllers only" if kbm else "speed test"),
            "pad": ("Input device", None),
            "binds": ("Binds", _bind_text(cfg)),
            "settings": ("Settings", None),
            "compare": ("Compare controllers", _runs(len(app.log.runs))),
            "quit": ("Quit", None),
        }
        for i, kind in enumerate(self.ITEMS):
            sel = i == self.sel
            if kind in ("sens", "pad", "compare", "quit"):
                y += 18
            label, val = rows[kind]
            if kind == "pad":
                val = "‹ " + cfg["active_profile"] + " ›" if sel else cfg["active_profile"]
            _row(ui, x, y, w, label, sel, val, hit=i)
            y += ROW

        px, py = 720.0, 190.0
        pw, ph = W - px - 96.0, 760.0
        ui.panel(px, py, pw, ph)
        kind = self.ITEMS[self.sel]
        tx, ty, tw = px + 40, py + 34, pw - 80
        if kind in tracks.BY_TRACK:
            self._track_panel(ui, tx, ty, tw, tracks.BY_TRACK[kind])
        elif kind == "progress":
            ui.text(tx, ty, "Progress and training", "xl", WHITE)
            ui.para(tx, ty + 70, "Your own runs over time on the active controller: a line through every run of "
                    "each scenario, your best, your last three, and whether you are improving, steady or slipping."
                    "\n\n\"Train my weakest five\" builds a playlist from the scenarios where the largest share of "
                    "your shots is missing, and works it out again after every run.\n\nThere are no other players' "
                    "scores in here, so \"weakest\" means where you are losing the most, not how you rank.",
                    "m", DIM, tw)
        elif kind == "duel":
            ui.text(tx, ty, "Guided comparison", "xl", WHITE)
            ui.para(tx, ty + 70, "The fair way to answer \"which controller is better for me\". Five scenarios, a "
                    "warm-up pass that is thrown away, then passes in A - B - B - A order, with the screen telling "
                    "you which pad to pick up.\n\nYou improve as you play whichever pad you hold. In this order "
                    "that improvement lands on both pads equally, so it cancels out and what is left is the "
                    "controllers.\n\nIt says plainly whether the gap is bigger than the noise - and if it is not, "
                    "roughly how much more play it would take to find out.", "m", DIM, tw)
        elif kind == "sens":
            ui.text(tx, ty, "Sensitivity finder", "xl", WHITE)
            scale = ("in centimetres of mouse travel per full turn - the same sensitivity in any game -" if kbm
                     else "on the game's own scale")
            ui.para(tx, ty + 70, "Plays the same short drills at a spread of sensitivities, in a shuffled, "
                    "mirrored order so warming up cannot favour any one of them, then fits a curve through "
                    "your scores and reads off the peak - first for hip-fire sensitivity, then for the ADS "
                    f"multiplier.\n\nIt reports the best value {scale} together with the range "
                    "it is actually confident about. Scores vary run to run, so that range is the honest part; "
                    "the more rounds you play, the narrower it gets."
                    + ("\n\nThis is the mouse's own search: no aim assist in any block, and a result is applied "
                       "to both tracks." if kbm else ""), "m", DIM, tw)
        elif kind == "lab":
            ui.text(tx, ty, "Controller lab", "xl", WHITE)
            ui.para(tx, ty + 70, "A one-minute speed test of the pad itself: how often it reports and how evenly, "
                    "each stick's rest position, outer range, resolution and snap-back, how fast your Shoot "
                    "button cycles, and how the triggers travel.\n\nIt also tells you the lowest and highest "
                    "deadzones this particular pad can run in-game.\n\nIt measures what software can see. True "
                    "press-to-PC latency needs a hardware rig and is not claimed here.", "m", DIM, tw)
        elif kind == "pad":
            self._pad_panel(ui, tx, ty, tw)
        elif kind == "binds":
            ui.text(tx, ty, "Binds", "xl", WHITE)
            ui.para(tx, ty + 70, "Choose which buttons Shoot and Aim use - bumpers, triggers, or anything else on "
                    "the pad - and how far a trigger must travel to count as pressed. Includes a live input "
                    "viewer.\n\nBinds are stored with every run, so the same controller on RB/LB and on RT/LT "
                    "shows up as two separate columns when you compare.", "m", DIM, tw)
        elif kind == "settings":
            ui.text(tx, ty, "Settings", "xl", WHITE)
            ui.para(tx, ty + 70, "Core-track aim settings, the Call of Duty sensitivity and deadzones, round "
                    "length, crosshair and display options.\n\nSettings that change difficulty are marked with a "
                    "dot. Runs are only compared against runs made with the same values, so pick them once and "
                    "leave them alone while you are testing controllers.", "m", DIM, tw)
        elif kind == "compare":
            ui.text(tx, ty, "Compare controllers", "xl", WHITE)
            ty = ui.para(tx, ty + 70, "Every metric, side by side, with an honest read on whether a difference is "
                         "real or just run-to-run noise.", "m", DIM, tw) + 20
            self._scoreboard(ui, tx, ty, tw)
        else:
            ui.text(tx, ty, "Quit", "xl", WHITE)
            ui.para(tx, ty + 70, "Runs are saved as you finish them - nothing is lost by closing.", "m", DIM, tw)

        self._status(ui)
        _footer(ui, "A / Enter  select        D-pad / arrows  move        F11  fullscreen")

    def _track_panel(self, ui, x, y, w, track):
        app = self.app
        ui.text(x, y, track.name + " track", "xl", WHITE)
        y = ui.para(x, y + 70, track.blurb, "m", DIM, w) + 22
        fp = config.fingerprint_for(app.cfg, track.id)
        played: dict = {}
        for r in app.log.runs:
            if r.get("fingerprint") == fp:
                played[r.get("scenario")] = played.get(r.get("scenario"), 0) + 1
        ui.text(x, y, "MECHANIC" if track.groups[0][0] else "SCENARIO", "s", FAINT)
        ui.text(x + w, y, "YOUR RUNS, CURRENT SETTINGS", "s", FAINT, 2)
        y += 28
        rows = [(g, sum(played.get(s.id, 0) for s in scns), ", ".join(s.name for s in scns)) for g, scns in track.groups] \
            if track.groups[0][0] else [(s.name, played.get(s.id, 0), "") for s in track.scenarios]
        off = {s.name for s in track.scenarios if not app.playable(s)}     # needs a controller, and this is a mouse
        for name, n, detail in rows:
            ui.text(x, y, name, "m", FAINT if name in off else WHITE)
            if detail:
                ui.text(x + 250, y + 2, detail, "s", FAINT)
            ui.text(x + w, y, "controller only" if name in off else (str(n) if n else "-"), "m", DIM if n else FAINT, 2)
            y += 33
        line = _sens_line(app.cfg, track.id)
        if line:
            ui.text(x, y + 16, line, "s", ACCENT)

    def _scoreboard(self, ui, x, y, w):
        sb = stats.scoreboard(self.app.log.runs, config.fingerprint(self.app.cfg))
        if not sb:
            ui.text(x, y, "Run scenarios on two controllers to see a head-to-head here.", "m", FAINT)
            return
        a, b = sb["labels"]
        ui.text(x, y, "CORE TRACK  ·  PRIMARY METRIC, HEAD TO HEAD", "s", FAINT)
        y += 34
        ui.text(x, y, f"{a}   {sb['wins'][0]}", "l", WHITE)
        ui.text(x + w, y, f"{sb['wins'][1]}   {b}", "l", WHITE, 2)
        y += 44
        ui.text(x, y, f"{sb['clear'][0]} clear", "m", DIM)
        ui.text(x + w, y, f"{sb['clear'][1]} clear", "m", DIM, 2)

    def _pad_panel(self, ui, x, y, w):
        app, pad = self.app, self.app.pad
        ui.text(x, y, "Input device", "xl", WHITE)
        y = ui.para(x, y + 70, "Each run is logged under the active profile - a controller, or mouse and keyboard. "
                    "Left / right switches it; A opens the list to add, rename or link profiles. Using a device "
                    "selects it too: click the mouse, or press a button on a controller.", "m", DIM, w) + 20
        info = pad.info()
        rows = [("Active profile", app.cfg["active_profile"])]
        if config.kind(app.cfg) == "kbm":
            m, mc = app.mouse, app.cfg["mouse"]
            rows += [("Mouse", "raw input - no acceleration, no smoothing" if m.available else "raw input unavailable"),
                     ("Report rate", f"{m.hz:,.0f} Hz" if m.hz else "move the mouse to measure it"),
                     ("Core track", f"{mc['cm360']:.1f} cm per turn at {mc['dpi']} DPI"),
                     ("Call of Duty track", f"sensitivity {app.cfg['cod']['mouse_sens']:.2f} = "
                                            f"{config.cod_mouse_cm360(app.cfg):.1f} cm per turn"),
                     ("Aim assist", "none, on either track")]
            for k, v in rows:
                ui.text(x, y, k, "m", FAINT)
                ui.text(x + 230, y, v, "m", WHITE)
                y += 34
            if not m.available:
                ui.para(x, y + 16, "Windows refused raw mouse input: " + (m.error or "no reason given"), "m", WARN, w)
            return
        if info:
            rows += [("Connection", f"{info['backend']}  ·  {info['name']}"),
                     ("USB id", info.get("id") or "unknown"),
                     ("Report rate", f"{pad.hz:,.0f} Hz" if pad.hz else "-"),
                     ("Input thread", f"{pad.poll_hz:,.0f} Hz" if pad.poll_hz else "-")]
            owner = app.owner_of(info.get("id"))
            rows.append(("Linked to", owner or "no profile yet"))
        else:
            rows.append(("Connection", "no controller detected"))
        for k, v in rows:
            ui.text(x, y, k, "m", FAINT)
            ui.text(x + 230, y, v, "m", WHITE)
            y += 34
        if info and info.get("id") and not app.owner_of(info["id"]):
            ui.para(x, y + 16, "This pad is not linked to a profile. Open Controller and choose which one it is; "
                    "after that it is recognised automatically whenever you plug it in or press a button on it.",
                    "m", WARN, w)

    def _status(self, ui):
        app, pad = self.app, self.app.pad
        info = pad.info()
        y = 1018
        if config.kind(app.cfg) == "kbm":
            m = app.mouse
            ok = m.available
            (ui.disc(104, y + 12, 6, GOOD) if ok else ui.ring(104, y + 12, 6, 2, BAD))
            bits = [app.cfg["active_profile"], "raw input" if ok else "raw input unavailable"]
            if m.hz:
                bits.append(f"{m.hz:,.0f} Hz")
            ui.text(122, y, "   ·   ".join(bits), "m", WHITE if ok else WARN)
        elif info:
            ui.disc(104, y + 12, 6, GOOD)
            bits = [app.cfg["active_profile"]]
            if info.get("id"):
                bits.append(info["id"])
            if pad.hz:
                bits.append(f"{pad.hz:,.0f} Hz")
            x = 122 + ui.text(122, y, "   ·   ".join(bits), "m", WHITE)
            if info.get("id") and not app.owner_of(info["id"]):
                ui.text(x + 24, y, "not linked to a profile yet", "m", WARN)
        else:
            ui.ring(104, y + 12, 6, 2, BAD)
            ui.text(122, y, "No controller detected", "m", WARN)


# -------------------------------------------------------------------- track
class TrackScreen(Screen):
    """One track's scenarios, grouped by mechanic, with details and your results."""

    VISIBLE = 16

    def __init__(self, app, track):
        super().__init__(app)
        self.track = track
        self.rows = [("all", None)]
        for gname, scns in track.groups:
            if gname:
                self.rows.append(("head", gname))
            self.rows += [("scn", s) for s in scns]
        self.sel = 0
        self.top = 0
        self._cache = None

    def _move(self, d):
        n = len(self.rows)
        self.sel = (self.sel + d) % n
        while self.rows[self.sel][0] == "head":
            self.sel = (self.sel + d) % n
        if self.sel < self.top + 1:
            self.top = max(self.sel - 1, 0)
        elif self.sel >= self.top + self.VISIBLE:
            self.top = self.sel - self.VISIBLE + 1

    def update(self, nav, now):
        app = self.app
        if "up" in nav:
            self._move(-1)
        if "down" in nav:
            self._move(1)
        kind, arg = self.rows[self.sel]
        if "back" in nav:
            app.pop()
        elif "accept" in nav:
            if kind == "all":
                app.start_playlist(self.track.scenarios, self.track.id)
            else:
                app.start(arg)
        elif "x" in nav and kind == "scn" and len(self.track.groups) > 1:
            app.start_playlist(self.track.group_of(arg), arg.group.lower().replace(" ", "-"))
        elif "y" in nav and kind == "scn":
            app.start(arg, over={"endless": True})      # no clock: played until stopped (see Game)

    def _results(self, scn):
        app = self.app
        fp = config.fingerprint_for(app.cfg, scn.track)
        key = (scn.id, len(app.log.runs), fp)
        if self._cache is None or self._cache[0] != key:
            self._cache = (key, stats.metric_table(app.log.runs, scn.id, fp))
        return self._cache[1]

    def draw(self, ui):
        app, cfg, track, W = self.app, self.app.cfg, self.track, ui.W
        kbm = config.kind(cfg) == "kbm"
        _header(ui, track.name.upper() + " TRACK", f"{len(track.scenarios)} scenarios   ·   {cfg['active_profile']}")
        x, y, w = 96.0, 190.0, 560.0
        dur = cfg["game"]["duration"]
        for i in range(self.top, min(self.top + self.VISIBLE, len(self.rows))):
            kind, arg = self.rows[i]
            sel = i == self.sel
            if kind == "head":
                ui.text(x, y + 14, arg.upper(), "s", FAINT)
                ui.rect(x + ui.width(arg.upper(), "s") + 16, y + 23, w - ui.width(arg.upper(), "s") - 60, 1, (1.0, 1.0, 1.0, 0.08))
            elif kind == "all":
                _row(ui, x, y, w, "Run the whole track", sel, f"all {sum(1 for s in track.scenarios if app.playable(s))}",
                     hit=i)
            else:
                tag = None
                if not app.playable(arg):
                    tag = "controller only"
                elif track.id == "cod":       # a mouse gets no assist in any of them
                    tag = "assist" if (arg.aa and not kbm) else "no assist"
                _row(ui, x, y, w, arg.name, sel, tag, hit=i)
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
        if kind == "all":
            ui.text(tx, ty, "Whole track", "xl", WHITE)
            n = sum(1 for s in track.scenarios if app.playable(s))
            mins = math.ceil(n * (dur + 12) / 60.0)
            ty = ui.para(tx, ty + 66, f"All {n} scenarios back to back, about {mins} minutes at the current "
                         f"{dur} s round length.", "l", ACCENT, tw) + 14
            ui.para(tx, ty, "Each scenario is logged as its own run, so stopping early still counts for what you "
                    "finished.\n\nComparing controllers? Warm up first, then alternate pads (A, B, B, A) rather "
                    "than doing all of one and then all of the other - you get better as you play, and the "
                    "second pad would take the credit.", "m", DIM, tw)
        else:
            ui.text(tx, ty, arg.name, "xl", WHITE)
            if track.id == "r6":
                ui.text(tx + tw, ty + 14, "no aim assist: Siege has none", "m", WARN, 2)
            elif track.id == "cod" and kbm:
                ui.text(tx + tw, ty + 14, "aim assist: none on mouse", "m", WARN, 2)
            elif track.id == "cod":
                style = codkit.style_label(cfg["cod"]["aa_style"])
                ui.text(tx + tw, ty + 14, f"aim assist: {style} style" if arg.aa else "aim assist: off",
                        "m", GOOD if arg.aa else WARN, 2)
            elif not app.playable(arg):
                ui.text(tx + tw, ty + 14, "needs a controller", "m", WARN, 2)
            ty = ui.para(tx, ty + 66, arg.tagline, "l", ACCENT, tw) + 14
            if app.playable(arg):
                ty = ui.para(tx, ty, f"Y plays it endless: no clock{' or trial count' if not arg.timed else ''}, until "
                             "you stop it from the pause menu. Practice only - it is kept out of your averages.",
                             "m", DIM, tw) + 14
            if track.id == "cod" and kbm and arg.aa:
                ty = ui.para(tx, ty, "On a controller this drill runs with aim assist. On mouse and keyboard it is the "
                             "same drill with none at all - no slowdown, no rotation - so read the text below with "
                             "that in mind.", "s", WARN, tw) + 10
            ui.text(tx, ty, "HOW IT WORKS", "s", FAINT)
            ty = ui.para(tx, ty + 24, arg.how, "m", WHITE, tw) + 14
            ui.text(tx, ty, "WHAT IT TELLS YOU", "s", FAINT)
            ty = ui.para(tx, ty + 24, arg.measures, "m", DIM, tw) + 26
            self._mini_table(ui, tx, ty, tw, arg)
        hints = "A  play        B  back"
        if kind == "scn" and len(track.groups) > 1:
            hints = f"A  play        Y  endless        X  play all three {arg.group} drills        B  back"
        elif kind == "scn":
            hints = "A  play        Y  endless        B  back"
        _footer(ui, hints)

    def _mini_table(self, ui, x, y, w, scn):
        tab = self._results(scn)
        row = next(r for r in tab["rows"] if r["primary"])
        ui.text(x, y, f"YOUR RESULTS  ·  {row['label'].upper()}  ·  current settings", "s", FAINT)
        y += 30
        if not tab["labels"]:
            ui.text(x, y, "No runs yet with these settings.", "m", DIM)
            return
        cols = (x, x + w * 0.52, x + w * 0.70, x + w)
        ui.text(cols[1], y, "RUNS", "s", FAINT, 2)
        ui.text(cols[2] + 60, y, "AVERAGE", "s", FAINT, 2)
        ui.text(cols[3], y, "BEST", "s", FAINT, 2)
        y += 26
        for i, lb in enumerate(tab["labels"][:5]):
            c = row["cells"][i]
            active = lb.startswith(self.app.cfg["active_profile"])
            ui.text(cols[0], y, lb, "m", WHITE if active else DIM)
            ui.text(cols[1], y, str(tab["counts"][i]), "m", DIM, 2)
            if c:
                ui.text(cols[2] + 60, y, stats.fmt(c["mean"], row["dec"], row["unit"]), "m",
                        GOOD if row["best"] == i else WHITE, 2)
                ui.text(cols[3], y, stats.fmt(c["best"], row["dec"], row["unit"]), "m", DIM, 2)
            y += 32


# ------------------------------------------------------------- controllers
class Controllers(Screen):
    def __init__(self, app):
        super().__init__(app)
        names = [p["name"] for p in app.cfg["profiles"]]
        self.sel = names.index(app.cfg["active_profile"])
        self.mode = "list"       # list | actions | name
        self.act = 0
        self.buf = ""
        self.renaming = None
        self.new_kbm = False
        self.confirm_delete = False

    @property
    def text_mode(self):
        return self.mode == "name"

    def point(self, hit, click):
        if isinstance(hit, tuple):          # a line of the selected profile's actions
            if hit[1] != self.act:
                self.act, self.confirm_delete = hit[1], False
        else:
            self.sel = hit
        return click

    def _actions(self, prof):
        app = self.app
        dev = (app.pad.info() or {}).get("id")
        kbm = prof.get("kind") == "kbm"
        acts = [("use", "Use mouse and keyboard" if kbm else "Use this controller")]
        if dev and dev not in prof["devices"] and not kbm:
            acts.append(("link", f"Link the connected pad ({dev}) to it"))
        acts.append(("rename", "Rename"))
        if len(app.cfg["profiles"]) > 1:
            acts.append(("delete", "Press again to delete" if self.confirm_delete else "Delete profile"))
        acts.append(("back", "Back"))
        return acts

    def update(self, nav, now):
        app = self.app
        profs = app.cfg["profiles"]
        if self.mode == "name":
            for ch in app.typed:
                if len(self.buf) < 28 and (ch.isalnum() or ch in " -_.+()"):
                    self.buf += ch
            if "backspace" in app.keys:
                self.buf = self.buf[:-1]
            # While typing, the app only turns the pad's A/B into accept/back - never keys.
            if "enter" in app.keys or "accept" in nav:
                name = self.buf.strip()
                taken = [p["name"] for p in profs if p is not self.renaming]
                if name and name not in taken:
                    if self.renaming:
                        old = self.renaming["name"]
                        self.renaming["name"] = name
                        app.log.rename_controller(old, name)
                        if app.cfg["active_profile"] == old:
                            app.cfg["active_profile"] = name
                    else:
                        profs.append(dict({"name": name, "devices": []}, **({"kind": "kbm"} if self.new_kbm else {})))
                        self.sel = len(profs) - 1
                    app.save()
                    self.mode = "list"
            elif "esc" in app.keys or "back" in nav:
                self.mode = "list"
            return
        if self.mode == "actions":
            prof = profs[self.sel]
            acts = self._actions(prof)
            if "up" in nav:
                self.act = (self.act - 1) % len(acts)
                self.confirm_delete = False
            if "down" in nav:
                self.act = (self.act + 1) % len(acts)
                self.confirm_delete = False
            if "back" in nav:
                self.mode = "list"
            elif "accept" in nav:
                kind = acts[self.act][0]
                if kind == "use":
                    app.set_profile(prof["name"], link=True)
                    self.mode = "list"
                elif kind == "link":
                    app.link_device(prof["name"])
                    self.act = 0
                elif kind == "rename":
                    self.mode, self.buf, self.renaming = "name", prof["name"], prof
                elif kind == "delete":
                    if not self.confirm_delete:
                        self.confirm_delete = True
                    else:
                        profs.remove(prof)
                        if app.cfg["active_profile"] == prof["name"]:
                            app.cfg["active_profile"] = profs[0]["name"]
                        self.sel = min(self.sel, len(profs) - 1)
                        self.mode, self.confirm_delete = "list", False
                        app.save()
                else:
                    self.mode = "list"
            return
        n = len(profs) + 2
        if "up" in nav:
            self.sel = (self.sel - 1) % n
        if "down" in nav:
            self.sel = (self.sel + 1) % n
        if "back" in nav:
            app.pop()
        elif "accept" in nav:
            if self.sel >= len(profs):
                self.new_kbm = self.sel == len(profs) + 1
                stem, k = ("Mouse & keyboard" if self.new_kbm else "Controller"), len(profs) + 1
                while f"{stem} {k}" in [p["name"] for p in profs]:
                    k += 1
                self.mode, self.buf, self.renaming = "name", f"{stem} {k}", None
            else:
                self.mode, self.act, self.confirm_delete = "actions", 0, False

    def draw(self, ui):
        app = self.app
        profs = app.cfg["profiles"]
        _header(ui, "INPUT DEVICES", "one profile per controller you are testing, and one for mouse and keyboard")
        x, y, w = 96.0, 210.0, 760.0
        for i, p in enumerate(profs):
            runs = sum(1 for r in app.log.runs if r.get("controller") == p["name"])
            active = p["name"] == app.cfg["active_profile"]
            what = "mouse and keyboard" if p.get("kind") == "kbm" else (", ".join(p["devices"]) or "not linked")
            val = what + "   ·   " + _runs(runs)
            _row(ui, x, y, w, p["name"], i == self.sel and self.mode != "name", val,
                 col=WHITE if active else None, indent=30, hit=i if self.mode == "list" else None)
            if active:
                ui.disc(x + 9, y + 17, 6, GOOD)
            else:
                ui.ring(x + 9, y + 17, 6, 1.5, FAINT)
            y += ROW
        listing = self.mode == "list"
        _row(ui, x, y + 12, w, "+ New controller profile", self.sel == len(profs) and self.mode != "name",
             hit=len(profs) if listing else None)
        _row(ui, x, y + 12 + ROW, w, "+ New mouse and keyboard profile", self.sel == len(profs) + 1 and self.mode != "name",
             hit=len(profs) + 1 if listing else None)

        px = 940.0
        pw = ui.W - px - 96
        ui.panel(px, 190, pw, 620)
        tx, ty, tw = px + 40, 224.0, pw - 80
        if self.mode == "name":
            ui.text(tx, ty, "Rename profile" if self.renaming else "New profile", "l", WHITE)
            ui.rect(tx, ty + 60, tw, 56, (0.0, 0.0, 0.0, 0.5))
            ui.frame(tx, ty + 60, tw, 56, ACCENT)
            caret = "|" if int(time.perf_counter() * 2) % 2 else ""
            ui.text(tx + 16, ty + 72, self.buf + caret, "l", WHITE)
            ui.para(tx, ty + 140, "Type a name on the keyboard.  Enter saves, Esc cancels.\n"
                    "(On the pad: A accepts the suggested name.)", "m", DIM, tw)
        elif self.mode == "actions":
            prof = profs[self.sel]
            ui.text(tx, ty, prof["name"], "l", WHITE)
            yy = ty + 62
            for i, (kind, label) in enumerate(self._actions(prof)):
                _row(ui, tx + 18, yy, tw - 10, label, i == self.act,
                     col=BAD if kind == "delete" and self.confirm_delete else None, hit=("act", i))
                yy += ROW
        else:
            info = app.pad.info()
            ui.text(tx, ty, "Connected right now", "s", FAINT)
            if info:
                owner = app.owner_of(info.get("id"))
                ui.text(tx, ty + 28, f"{info['name']}  ·  {info.get('id') or 'no USB id'}", "l", WHITE)
                ui.text(tx, ty + 70, f"linked to: {owner}" if owner else "not linked to any profile", "m",
                        GOOD if owner else WARN)
                hz = f"{app.pad.hz:,.0f} reports/s" if app.pad.hz else "report rate unknown"
                ui.text(tx, ty + 102, hz, "m", DIM)
            else:
                ui.text(tx, ty + 28, "No controller detected", "l", WARN)
            ui.para(tx, ty + 160, "Pick a profile and choose \"Use this controller\". If the connected pad is "
                    "not linked yet it gets linked to that profile, and from then on plugging it in - or "
                    "pressing any button on it while both are connected - switches profiles for you.\n\n"
                    "Every run also records the pad's USB id, so a run logged under the wrong name can "
                    "still be spotted afterwards.", "m", DIM, tw)
        _footer(ui, "A  select        B  back")


# -------------------------------------------------------------------- binds
class Binds(Screen):
    ROWS = ("shoot", "ads", "thr", "bumpers", "triggers", "back")

    def __init__(self, app):
        super().__init__(app)
        self.sel = 0
        self.capture = None
        self.capture_until = 0.0

    def update(self, nav, now):
        app, cfg = self.app, self.app.cfg
        b = cfg["binds"]
        if self.capture:
            name = app.pad.first_press()
            if name:
                other = "ads" if self.capture == "shoot" else "shoot"
                if b[other] == name:          # taking the other action's button swaps them
                    b[other] = b[self.capture]
                b[self.capture] = name
                self.capture = None
                app.save()
            elif "esc" in app.keys or now > self.capture_until:
                self.capture = None
            return
        n = len(self.ROWS)
        if "up" in nav:
            self.sel = (self.sel - 1) % n
        if "down" in nav:
            self.sel = (self.sel + 1) % n
        row = self.ROWS[self.sel]
        if row == "thr":
            step = (1 if "right" in nav else 0) - (1 if "left" in nav else 0)
            if step:
                v = cfg["aim"]["trigger_threshold"] + 0.02 * step
                cfg["aim"]["trigger_threshold"] = round(max(0.02, min(0.90, v)), 2)
                app.apply_input()
                app.save()
        if "back" in nav:
            app.pop()
        elif "accept" in nav:
            if row in ("shoot", "ads"):
                self.capture, self.capture_until = row, now + 6.0
            elif row == "bumpers":
                b["shoot"], b["ads"] = "RB", "LB"
                app.save()
            elif row == "triggers":
                b["shoot"], b["ads"] = "RT", "LT"
                app.save()
            elif row == "back":
                app.pop()

    def draw(self, ui):
        app, cfg, pad = self.app, self.app.cfg, self.app.pad
        b = cfg["binds"]
        _header(ui, "BINDS", "which buttons shoot and aim")
        x, y, w = 96.0, 210.0, 720.0
        labels = {
            "shoot": ("Shoot", padmod.LABEL[b["shoot"]]),
            "ads": ("Aim  (ADS)", padmod.LABEL[b["ads"]]),
            "thr": ("Trigger actuation", f"‹ {cfg['aim']['trigger_threshold'] * 100:.0f}% ›"),
            "bumpers": ("Preset: bumpers", "RB shoot · LB aim"),
            "triggers": ("Preset: triggers", "RT shoot · LT aim"),
            "back": ("Back", None),
        }
        for i, key in enumerate(self.ROWS):
            if key in ("bumpers", "back"):
                y += 16
            label, val = labels[key]
            if self.capture == key:
                val = "press a button…"
            _row(ui, x, y, w, label, i == self.sel, val, value_col=WARN if self.capture == key else None, hit=i)
            y += ROW
        on_trigger = bool({b["shoot"], b["ads"]} & {"RT", "LT"})
        ui.para(x, y + 30, "Select Shoot or Aim, then press the button you want. Start and Back stay reserved "
                "for pausing. Trigger actuation is how far RT / LT must travel to register. "
                + ("A bind is on a trigger, so changing it starts a fresh comparison."
                   if on_trigger else
                   "Neither bind is on a trigger right now, so it has no effect and does not split your runs."),
                "m", DIM, w - 40)

        px = 900.0
        pw = ui.W - px - 96
        ui.panel(px, 190, pw, 640)
        tx, ty = px + 40, 224.0
        ui.text(tx, ty, "LIVE INPUT", "s", FAINT)
        info = pad.info()
        if not info:
            ui.text(tx, ty + 40, "No controller detected", "l", WARN)
        else:
            hz = f"{pad.hz:,.0f} reports/s" if pad.hz else "-"
            poll = f"   ·   read at {pad.poll_hz:,.0f} Hz" if pad.poll_hz and info["backend"] == "xinput" else ""
            ui.text(tx, ty + 28, f"{info.get('id') or info['name']}   ·   {hz}{poll}", "m", WHITE)
            dz = cfg["aim"]["deadzone"]
            for k, (sx, sy, name) in enumerate(((pad.lx, pad.ly, "LEFT"), (pad.rx, pad.ry, "RIGHT"))):
                cx, cy, R = tx + 110 + k * 280, ty + 190, 96.0
                aiming = (name == "LEFT") == (cfg["aim"]["stick"] == "left")
                ui.ring(cx, cy, R, 2.5, alpha(WHITE, 0.30))
                if aiming:
                    ui.ring(cx, cy, R * dz, 1.5, alpha(WARN, 0.8))
                ui.disc(cx + sx * R, cy - sy * R, 8, ACCENT if aiming else DIM)
                ui.text(cx, cy + R + 14, name + (" · aim" if aiming else ""), "s", DIM, 1)
                ui.text(cx, cy + R + 36, f"{sx:+.3f}  {sy:+.3f}", "s", FAINT, 1)
            thr = cfg["aim"]["trigger_threshold"]
            for k, (v, name, dn) in enumerate(((pad.lt, "LT", pad.lt_down), (pad.rt, "RT", pad.rt_down))):
                bx, by, bw = tx + k * 280, ty + 380, 220.0
                ui.rect(bx, by, bw, 16, (1.0, 1.0, 1.0, 0.10))
                ui.rect(bx, by, bw * v, 16, GOOD if dn else DIM)
                ui.rect(bx + bw * thr - 1, by - 5, 2, 26, WARN)
                ui.text(bx, by + 24, f"{name}  {v * 100:.0f}%", "s", DIM)
            names = ("LB", "RB", "A", "B", "X", "Y", "LS", "RS", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT")
            bx, by = tx, ty + 470
            for i, nm in enumerate(names):
                on = pad.down(nm)
                cxx, cyy = bx + (i % 6) * 92, by + (i // 6) * 56
                ui.rect(cxx, cyy, 80, 42, alpha(ACCENT, 0.9) if on else (1.0, 1.0, 1.0, 0.07))
                short = padmod.LABEL[nm].replace("D-pad ", "D-")
                ui.text(cxx + 40, cyy + 11, short, "s", (0.0, 0.0, 0.0, 1.0) if on else DIM, 1)
                if nm in (b["shoot"], b["ads"]):      # mark the two bound buttons
                    ui.disc(cxx + 71, cyy + 9, 3.5, WARN)
        _footer(ui, "A  select        Left / Right  adjust        B  back")


class KbmBinds(Screen):
    """Mouse and keyboard binds: Shoot, Aim and the four movement keys."""

    def __init__(self, app):
        super().__init__(app)
        self.rows = [a for a, _ in mousemod.ACTIONS] + ["@reset", "@back"]     # (@: not an action - "back" is one)
        self.sel = 0
        self.capture = None
        self.capture_until = 0.0
        self.armed = False

    def update(self, nav, now):
        app, b = self.app, self.app.cfg["kbm"]
        if self.capture:
            # the Enter (or click) that opened the capture must be let go before anything is taken
            if not self.armed:
                self.armed = not app.mouse.held
                return
            name = app.mouse.first_press()
            if name:
                for other, bound in b.items():      # taking another action's key swaps the two
                    if bound == name and other != self.capture:
                        b[other] = b[self.capture]
                b[self.capture] = name
                self.capture = None
                app.save()
            elif "esc" in app.keys or now > self.capture_until:
                self.capture = None
            return
        n = len(self.rows)
        if "up" in nav:
            self.sel = (self.sel - 1) % n
        if "down" in nav:
            self.sel = (self.sel + 1) % n
        row = self.rows[self.sel]
        if "back" in nav:
            app.pop()
        elif "accept" in nav:
            if row == "@reset":
                b.update(mousemod.DEFAULT_BINDS)
                app.save()
            elif row == "@back":
                app.pop()
            else:
                self.capture, self.capture_until, self.armed = row, now + 6.0, False

    def draw(self, ui):
        app, b, m = self.app, self.app.cfg["kbm"], self.app.mouse
        _header(ui, "BINDS", "mouse and keyboard")
        x, y, w = 96.0, 210.0, 720.0
        names = dict(mousemod.ACTIONS)
        for i, key in enumerate(self.rows):
            if key in ("forward", "lean_l", "@reset"):
                y += 12
            if key in names:
                label, val = names[key], mousemod.label(b[key])
            else:
                label, val = ("Reset them all to the defaults", None) if key == "@reset" else ("Back", None)
            if self.capture == key:
                val = "press a key or mouse button…"
            _row(ui, x, y, w, label, i == self.sel, val, value_col=WARN if self.capture == key else None, hit=i)
            y += ROW
        ui.para(x, y + 18, "Select a row, then press the key or mouse button you want for it. Esc and Enter stay "
                "reserved for pausing and confirming. Movement is used on the Call of Duty and Siege tracks; "
                "leaning, crouching and reloading on the Siege track only. Stored with every run, like a "
                "controller's binds.", "s", DIM, w - 40)

        px = 900.0
        pw = ui.W - px - 96
        ui.panel(px, 190, pw, 640)
        tx, ty, tw = px + 40, 224.0, pw - 80
        ui.text(tx, ty, "LIVE INPUT", "s", FAINT)
        if not m.available:
            ui.para(tx, ty + 40, "Raw mouse input is not available: " + (m.error or "unknown reason"), "m", WARN, tw)
        else:
            hz = f"{m.hz:,.0f} reports a second" if m.hz else "move the mouse to measure its report rate"
            ui.text(tx, ty + 28, "raw input   ·   " + hz, "m", WHITE)
            ui.text(tx, ty + 80, "THIS FRAME", "s", FAINT)
            ui.text(tx, ty + 106, f"{m.dx:+d}  {m.dy:+d} counts", "l", WHITE)
            cx, cy = tx + tw - 130, ty + 110       # a dot pushed about by the raw counts
            ui.ring(cx, cy, 70, 2.5, alpha(WHITE, 0.30))
            k = 70.0 / 60.0
            ui.disc(cx + max(-70, min(70, m.dx * k)), cy + max(-70, min(70, m.dy * k)), 8, ACCENT)
            by = ty + 230
            for i, (action, label) in enumerate(mousemod.ACTIONS):
                on = m.down(b[action])
                cxx, cyy = tx + (i % 2) * (tw / 2), by + (i // 2) * 62
                ui.rect(cxx, cyy, tw / 2 - 16, 46, alpha(ACCENT, 0.9) if on else (1.0, 1.0, 1.0, 0.07))
                ui.text(cxx + 14, cyy + 12, f"{label.strip()}  ·  {mousemod.label(b[action])}", "s",
                        (0.0, 0.0, 0.0, 1.0) if on else DIM)
            ui.para(tx, by + ((len(mousemod.ACTIONS) + 1) // 2) * 62 + 20, "Counts are the mouse's own, straight from the driver: Windows pointer speed and "
                    "\"enhance pointer precision\" do not touch them. Nothing is read while this window is not the "
                    "focused one.", "s", DIM, tw)
        _footer(ui, "A  select        B  back")


# ----------------------------------------------------------------- settings
# (key, label, kind, options, help). kind: num (lo, hi, step, fmt[, fine step]) | choice | bool | action
_FOLLOW = ("own", "core", "cod", "r6")
_S = [
    ("#", "SENSITIVITY ACROSS TRACKS"),
    ("sens.pad", "Controller sensitivity follows", "choice", _FOLLOW,
     "Pick one track and every track turns the way that track's settings say on a controller: the same "
     "full-stick speeds, the same deadzones and response curve, the same ADS speed. \"Each track's own\" leaves "
     "the three tracks on their own settings.\n\nOnly how the view turns is shared. Field of view, zoom, "
     "weapons and recoil stay each track's own - and so does aim assist, which only the Call of Duty track has."),
    ("sens.kbm", "Mouse sensitivity follows", "choice", _FOLLOW,
     "Pick one track and every track uses that track's mouse sensitivity: the same centimetres per turn from "
     "the hip, and that track's ADS rule applied to each track's own zoom. \"Each track's own\" leaves the three "
     "tracks on their own settings.\n\nThis is separate from the controller's choice above, so the mouse can "
     "follow one track and the controller another."),
    ("#", "CORE TRACK AIM"),
    ("aim.sens_h", "Horizontal sensitivity", "num", (40, 600, 10, "{:.0f}°/s"),
     "Turn speed with the stick fully deflected. Core track only - the Call of Duty track has its own "
     "sensitivity further down."),
    ("aim.sens_v", "Vertical sensitivity", "num", (40, 600, 10, "{:.0f}°/s"),
     "Look up/down speed with the stick fully deflected."),
    ("aim.ads_mult", "ADS sensitivity", "num", (0.2, 1.5, 0.05, "{:.2f}×"),
     "Multiplier on both sensitivities while Aim is held."),
    ("aim.curve", "Response curve", "choice", ("linear", "standard", "dynamic"),
     "How stick deflection maps to turn speed. Linear is 1:1, Standard is squared (finer near the centre), "
     "Dynamic is an S-curve. These are the usual families, not copies of any one game's curves."),
    ("aim.deadzone", "Inner deadzone", "num", (0.0, 0.30, 0.01, "{:.0%}"),
     "Stick travel ignored around the centre. Use the same value for every pad you compare, and set it just "
     "high enough that the worse stick does not drift."),
    ("aim.invert_y", "Invert Y", "bool", None, "Push up to look down. Applies to both tracks, and to the mouse."),
    ("aim.stick", "Aim stick", "choice", ("right", "left"), "Which stick turns the view. Applies to both tracks."),
    ("#", "MOUSE"),
    ("mouse.dpi_auto", "Read DPI from the mouse", "bool", None,
     "Where the mouse can say what its DPI is, keep the setting below equal to it - including when you press "
     "the mouse's DPI button. Razer mice can be asked; most others cannot, because Windows has no way to ask. "
     "For those, measure it (next row but one)."),
    ("mouse.dpi", "Mouse DPI", "num", (100, 32000, 50, "{:.0f}", 1),
     "What your mouse is set to (in its own software, or by its DPI button). It is only used to turn "
     "centimetres into counts and back - set it wrong and the centimetre figures are wrong, nothing else. "
     "Changing it here turns automatic reading off."),
    ("@dpi", "Read or measure DPI", "action", None,
     "Ask the mouse for its DPI now, or measure it with a ruler: slide the mouse a known distance and the "
     "counts it sends give the DPI. Works on any mouse."),
    ("mouse.cm360", "Core track: cm per full turn", "num", (5.0, 150.0, 1.0, "{:.1f} cm", 0.1),
     "How far the mouse travels to turn you once around, on the Core track. Lower is faster. This is the "
     "number to copy between games: the same centimetres per turn is the same sensitivity anywhere."),
    ("mouse.ads_mult", "Core track: ADS sensitivity", "num", (0.2, 2.0, 0.05, "{:.2f}×", 0.01),
     "With Aim held. 1.00 keeps the same speed across the screen as from the hip (the turn is scaled down "
     "by the zoom); lower is slower."),
    ("#", "CALL OF DUTY TRACK"),
    ("cod.sens_h", "Horizontal stick sensitivity", "num", (0.10, 4.00, 0.05, "{:.2f}", 0.01),
     "The game's own 0.10 - 4.00 scale. Left / right moves in steps of 0.05, LB / RB in steps of 0.01."),
    ("cod.sens_v", "Vertical stick sensitivity", "num", (0.10, 4.00, 0.05, "{:.2f}", 0.01),
     "The game's own 0.10 - 4.00 scale. Vertical turn speed is assumed to scale exactly like horizontal; "
     "only horizontal was measured."),
    ("cod.ads_mult", "ADS sensitivity multiplier", "num", (0.10, 2.00, 0.05, "{:.2f}", 0.01),
     "Multiplies your turn speed while aiming down sights, as in the game. How fast that actually is comes "
     "from the ADS half of the spin test."),
    ("cod.curve", "Aim response curve", "choice", ("dynamic", "standard", "linear"),
     "How turn speed builds as you push the stick. At half stick Linear gives about 25% of full speed, Dynamic "
     "21% and Standard 18%. Dynamic starts out as quick as Linear, is the slowest of the three around three-"
     "quarter stick, and catches up at the rim. Shapes come from a community measurement of the game, so "
     "mid-stick speeds are close rather than exact; full-stick speed is your own measurement."),
    ("cod.mouse_sens", "Mouse sensitivity", "num", config.COD_MOUSE_SENS + (0.25, "{:.2f}", 0.01),
     "The game's own mouse sensitivity number: each count turns the view 0.0066 degrees times this. Copy it "
     "straight from the game, with your real DPI set above."),
    ("cod.mouse_ads", "Mouse ADS sensitivity multiplier", "num", (0.10, 3.00, 0.05, "{:.2f}", 0.01),
     "As in the game, in its Relative mode: 1.00 leaves the zoom scaling alone, lower is slower through "
     "the sights."),
    ("cod.mouse_mdc", "Monitor distance coefficient", "num", (0.0, 2.0, 0.05, "{:.2f}", 0.01),
     "As in the game (default 1.33). It picks the point on screen where a hand movement covers the same "
     "distance from the hip and through the sights: 0 is the crosshair itself, 1.78 the side edge."),
    ("cod.dz_r_min", "Right stick min deadzone", "num", (0, 50, 1, "{:.0f}"),
     "Stick travel ignored around centre, in the game's units. The Controller lab reports the lowest value "
     "each pad can run without sending input at rest."),
    ("cod.dz_r_max", "Right stick max deadzone", "num", (50, 99, 1, "{:.0f}"),
     "Stick travel at which you reach full turn speed."),
    ("cod.dz_l_min", "Left stick min deadzone", "num", (0, 50, 1, "{:.0f}"), "Movement stick, around centre."),
    ("cod.dz_l_max", "Left stick max deadzone", "num", (50, 99, 1, "{:.0f}"),
     "Stick travel at which you reach full movement speed."),
    ("cod.fov", "Field of view", "num", (60, 120, 1, "{:.0f}°"), "As in the game: horizontal, at 16:9."),
    ("cod.ads_fov", "ADS field of view", "choice", ("affected", "independent"),
     "Affected: the sights zoom relative to your FOV setting. Independent: they keep the view they would "
     "have at FOV 80."),
    ("cod.aa_style", "Aim assist behaviour", "choice", ("bo7", "classic", "mw4", "coach"),
     "Whose rules rotational aim assist follows where a scenario has it; its strength is the same in all "
     "four.\n\nBlack Ops 7: strafing alone gives three quarters of it, the rest comes from following the "
     "target with the right stick; full from about 12 m.\n\nMW3 / Black Ops 6: any stick input gives all of "
     "it, from about 6 m.\n\nModern Warfare 4: as its beta ended, so it may change - strafing alone gives "
     "almost none, the right stick has to follow the target; no fade up close; follows jumps fully.\n\n"
     "Coach: no game's rules. It pays out for three habits at once - right stick following, strafing, "
     "staying centred - and shows which one is missing. Do all three and you are doing everything the other "
     "rule sets ask for."),
    ("@calibrate", "Turn speed calibration", "action", None,
     "Your real turn speed, from spins timed in the game. Open this to see or change the numbers."),
    ("#", "SIEGE TRACK"),
    ("r6.weapon", "Gun", "choice", r6kit.GUN_ORDER,
     "Nine guns from the game, easiest recoil first. Damage, fire rate and magazine are the game's own as of "
     "Year 11 Season 3. The recoil is built from how each gun is described - Siege's patterns are not published "
     "as numbers - so treat it as the gun's character, not a copy."),
    ("r6.sight", "Sight", "choice", ("1x", "2.5x"),
     "1x: any non-magnifying sight. 2.5x: the Magnified scope, which attackers' rifles can take; defenders' "
     "guns here are 1x only, as in the game. How far each zooms depends on your field of view, as it does there."),
    ("r6.barrel", "Barrel", "choice", r6kit.BARRELS,
     "Flash hider: 20% less climb on every bullet. Compensator: 35% less sideways kick. Muzzle brake: half the "
     "first bullet's kick, for tapping. As in the game since Year 8."),
    ("r6.grip", "Grip", "choice", r6kit.GRIPS,
     "Vertical: 20% less climb. Angled: 20% faster reload. Horizontal: 5% faster on your feet. As in the game "
     "since Year 9. The C8-SFW and MP7 take no grip."),
    ("r6.speed", "Operator speed", "choice", (3, 2, 1),
     "Your operator: 3-speed has 100 health and moves fastest, 2-speed 110, 1-speed 125 and slowest. Aiming "
     "down sights everyone moves at the same 2.34 m/s. Dummies come in all three."),
    ("r6.fov", "Field of view", "num", (60, 90, 1, "{:.0f}°"),
     "As in the game, and VERTICAL there: 60 to 90. (90 here is about 121 degrees across a 16:9 screen.)"),
    ("r6.sens_h", "Horizontal stick sensitivity", "num", (1, 100, 5, "{:.0f}", 1),
     "The game's 1 - 100 slider. How fast that really turns comes from the spin test below: Siege publishes "
     "no controller turn speeds."),
    ("r6.sens_v", "Vertical stick sensitivity", "num", (1, 100, 5, "{:.0f}", 1), "The game's 1 - 100 slider."),
    ("r6.ads", "ADS stick sensitivity", "num", (1, 100, 5, "{:.0f}", 1),
     "The game's 1 - 100 slider for turning while you aim down sights."),
    ("r6.dz_r", "Right stick deadzone", "num", (0, 50, 1, "{:.0f}"), "Aim stick travel ignored around centre, percent."),
    ("r6.dz_l", "Left stick deadzone", "num", (0, 50, 1, "{:.0f}"), "Movement stick, percent."),
    ("@r6cal", "Turn speed calibration", "action", None,
     "Siege's real stick turn speed, from spins timed in the game. Until you time yours the numbers in here "
     "are a placeholder."),
    ("r6.mouse_sens", "Mouse sensitivity", "num", (1, 100, 1, "{:.0f}"),
     "The game's own 1 - 100 slider. One count turns the view sensitivity x multiplier unit x 0.2865 degrees."),
    ("r6.mouse_unit", "Mouse multiplier unit", "choice", r6kit.UNITS,
     "MouseSensitivityMultiplierUnit in the game's GameSettings.ini, 0.02 unless you changed it. Players lower "
     "it because the slider only moves in whole numbers, which is coarse at low sensitivity."),
    ("r6.mouse_ads", "Mouse ADS sensitivity", "num", (1, 200, 1, "{:.0f}"),
     "The game's ADS slider. At 50 a hand movement covers the same distance on screen through the sight as from "
     "the hip (Ubisoft's own formula). The game also lets you set one per magnification; here one covers both."),
    ("#", "GAME"),
    ("game.duration", "Round length", "choice", (20, 30, 45, 60),
     "Seconds per timed scenario, on both tracks. Longer rounds give steadier numbers."),
    ("game.target_size", "Target size", "choice", ("small", "normal", "large"),
     "Scales every Core-track target. There is no aim assist there, so Normal is already generous."),
    ("game.fov", "Field of view", "num", (70, 120, 1, "{:.0f}°"), "Core track: horizontal FOV at 16:9."),
    ("game.ads_zoom", "ADS zoom", "num", (1.0, 2.0, 0.05, "{:.2f}×"), "Core track: how much holding Aim zooms the view."),
    ("game.target_color", "Target colour", "choice", tuple(config.TARGET_COLORS), "Pick whatever pops for you."),
    ("game.sounds", "Sounds", "bool", None, "Hit, miss and countdown sounds."),
    ("#", "CROSSHAIR"),
    ("crosshair.style", "Style", "choice", ("cross_dot", "cross", "dot", "circle"), ""),
    ("crosshair.color", "Colour", "choice", tuple(config.CROSSHAIR_COLORS), ""),
    ("crosshair.size", "Size", "num", (1, 14, 1, "{:.0f}"), ""),
    ("crosshair.gap", "Gap", "num", (0, 12, 1, "{:.0f}"), ""),
    ("crosshair.thickness", "Thickness", "num", (1, 6, 1, "{:.0f}"), ""),
    ("#", "DISPLAY"),
    ("video.fullscreen", "Fullscreen", "bool", None, "Fullscreen at the desktop resolution and refresh rate. F11 toggles."),
    ("video.vsync", "V-Sync", "bool", None,
     "Off gives the lowest input lag (and is fine with G-Sync). On removes tearing but queues a frame or two."),
    ("video.fps_cap", "Frame cap", "choice", (-2, 0, 120, 144, 165, 240, 360, 480, -1),
     "\"Auto\" (the default) caps one frame under the monitor's real refresh rate - 238 on a 239.99 Hz "
     "\"240 Hz\" screen, 178 on 179.9 Hz - so frames never queue up behind the display. \"Display\" caps "
     "at the refresh rate itself. Uncapped just burns power - the scene already renders far faster than "
     "any display."),
    ("video.quality", "Graphics", "choice", ("high", "low"),
     "High adds target glow and shadows, all computed in the pixel shader. Low is the bare grid."),
    ("video.reflections", "Floor reflections", "bool", None,
     "A glossy floor that mirrors the ceiling lights and the targets (High graphics only). Off by "
     "default: the mirrored copies move as you turn and pull the eye away from the real targets."),
    ("video.show_fps", "Performance overlay", "bool", None, "FPS, GPU time per frame and pad report rate."),
]
_NAMES = dict({k: v[1] for k, v in codkit.STYLES.items()}, cross_dot="Cross + Dot",
              own="Each track's own", core="Core track", cod="Call of Duty track", r6="Siege track")
_NAMES.update(r6kit.LABELS)
_NAMES.update({k: f"{g['name']}  ({g['who']})" for k, g in r6kit.GUNS.items()})
_NAMES.update({3: "3 - fast, 100 health", 2: "2 - 110 health", 1: "1 - slow, 125 health",
               "1x": "1x", "2.5x": "2.5x  (Magnified)"})


def _unused(cfg, key):
    """(input device, the track it follows) when `key` is part of a track's sensitivity that is not
    in use because that device takes its sensitivity from another track; else None."""
    for kind_, who in (("pad", "Controller"), ("kbm", "Mouse")):
        src = cfg["sens"][kind_]
        if src in config.DIALECTS and key != "mouse.dpi" and key not in config.SENS_KEYS[kind_][src]:
            if any(key in keys for d, keys in config.SENS_KEYS[kind_].items() if d != src):
                return who, src
    return None


def _cod_speeds(cfg):
    """(hip deg/s, ADS deg/s) at full stick for the current Call of Duty settings."""
    per_unit, ratio = config.cod_rates(cfg)
    hip = cfg["cod"]["sens_h"] * per_unit
    return hip, hip * cfg["cod"]["ads_mult"] * ratio


class Settings(Screen):
    VISIBLE = 15

    def __init__(self, app, start=None):
        super().__init__(app)
        self.rows = [r for r in _S]
        self.sel = 1
        self.top = 0
        self.buf = None         # a number being typed into the selected line (None: not typing)
        if start:
            while self.rows[self.sel][0] != start:
                self._move(1)

    @property
    def text_mode(self):
        return self.buf is not None

    def point(self, hit, click):
        if self.buf is not None:
            if not click:
                return False
            self._typed()                    # clicking away from a typed number keeps it
        if isinstance(hit, tuple):           # the ‹ › either side of the selected value
            self.sel = hit[1]
            if click:
                self._change(self.rows[hit[1]], hit[2])
            return False
        self.sel = hit
        return click

    def _typed(self):
        """Set the selected line to the number typed into it, kept inside its range."""
        row, buf = self.rows[self.sel], self.buf
        self.buf = None
        try:
            v = float(buf.replace(",", "."))
        except ValueError:
            return
        lo, hi, step = row[3][0], row[3][1], row[3][2]
        if "%" in row[3][3] and v > hi and v <= 100:
            v /= 100.0                      # "8" into a percentage means 8 %
        if not lo <= v <= hi:
            self.app.toast(f"{row[1]}: kept inside {row[3][3].format(lo)} - {row[3][3].format(hi)}", 3.5)
        self._change(row, 0, value=v)

    def _move(self, d):
        n = len(self.rows)
        self.sel = (self.sel + d) % n
        while self.rows[self.sel][0] == "#":
            self.sel = (self.sel + d) % n
        if self.sel < self.top + 1:
            self.top = max(self.sel - 1, 0)
        elif self.sel >= self.top + self.VISIBLE:
            self.top = self.sel - self.VISIBLE + 1

    def _text(self, row):
        key, _, kind, opt, _ = row
        if kind == "action":
            if key == "@dpi":
                return "open"
            if key == "@r6cal":
                own = sens.own_pad(self.app.cfg, "r6")
                return f"{own.rate_h:.0f}°/s hip · {own.rate_h * own.ads:.0f}°/s ADS"
            hip, ads = _cod_speeds(self.app.cfg)
            return f"{hip:.0f}°/s hip · {ads:.0f}°/s ADS"
        v = config.get(self.app.cfg, key)
        if key == "r6.sight" and r6kit.sight_of(self.app.cfg["r6"]) != v:
            return "1x  (all this gun takes)"
        if key == "r6.grip" and r6kit.grip_of(self.app.cfg["r6"]) != v:
            return "None  (this gun takes no grip)"
        if key == "r6.mouse_unit":
            return f"{v:g}"
        if kind == "bool":
            return "On" if v else "Off"
        if kind == "num":
            held = key == "cod.ads_mult" and self.app.cfg["cod"].get("ads_hold")
            return opt[3].format(v) + (" · held" if held else "")
        if key == "video.fps_cap":
            app = self.app
            hz = f"{app.refresh_exact:.2f}" if app.refresh_exact else str(app.refresh)
            return {-2: f"Auto: {app.auto_cap} fps ({hz} Hz)", 0: f"Display ({app.refresh} Hz)",
                    -1: "Uncapped"}.get(v, f"{v} fps")
        if key == "game.duration":
            return f"{v} s"
        return _NAMES.get(v, str(v).title())

    def _change(self, row, d, fine=False, value=None):
        app = self.app
        key, _, kind, opt, _ = row
        v = config.get(app.cfg, key)
        if kind == "bool":
            v = not v
        elif kind == "choice":
            v = opt[(opt.index(v) + d) % len(opt)] if v in opt else opt[0]
        else:
            lo, hi, step = opt[0], opt[1], (opt[4] if fine and len(opt) > 4 else opt[2])
            v = max(lo, min(hi, (v + d * step) if value is None else value))
            floats = isinstance(step, float) or (len(opt) > 4 and isinstance(opt[4], float))
            v = round(v, 2) if floats else int(round(v))
        config.put(app.cfg, key, v)
        if key.startswith("video."):
            app.apply_video()
        elif key == "game.sounds":
            app.sfx.enabled = bool(v)
        elif key == "cod.sens_h":
            mult = config.hold_ads_speed(app.cfg)       # the finder's ADS speed follows the stick sensitivity
            if mult is not None:
                app.toast(f"ADS multiplier moved to {mult:.2f} to keep your ADS speed")
        elif key == "cod.ads_mult" and app.cfg["cod"].get("ads_hold"):
            app.cfg["cod"]["ads_hold"] = None           # set by hand: no longer the finder's answer
            app.toast("ADS speed is no longer held at the finder's result")
        elif key == "mouse.dpi" and app.cfg["mouse"]["dpi_auto"]:
            app.cfg["mouse"]["dpi_auto"] = False        # set by hand: do not read it back over this
            app.toast("DPI set by hand - automatic reading is off")
        elif key in ("sens.pad", "sens.kbm") and v != "own":
            who = "A controller" if key == "sens.pad" else "The mouse"
            app.toast(f"{who} now turns the way the {_NAMES[v]} says, on every track", 4.0)
        app.save()

    def update(self, nav, now):
        app = self.app
        row = self.rows[self.sel]
        kind = row[2]
        if self.buf is not None:
            for ch in app.typed:
                if (ch.isdigit() or ch in ".,") and len(self.buf) < 8:
                    self.buf += ch
            if "backspace" in app.keys:
                self.buf = self.buf[:-1]
            elif "enter" in app.keys or "accept" in nav:
                self._typed()
            elif "esc" in app.keys or "back" in nav:
                self.buf = None
            elif nav & {"up", "down"}:        # moving off the line keeps what was typed, like a click away
                self._typed()
                self._move(-1 if "up" in nav else 1)
            return
        if kind == "num":
            # typing a digit on a number starts typing it in; Enter (or a click) opens it empty
            digits = "".join(ch for ch in app.typed if ch.isdigit() or ch in ".,")
            if digits or "enter" in app.keys or ("accept" in nav and config.kind(app.cfg) == "kbm"):
                self.buf = digits[:8]
                return
        if "up" in nav:
            self._move(-1)
        if "down" in nav:
            self._move(1)
        row = self.rows[self.sel]
        kind = row[2]
        if kind == "action":
            if "accept" in nav:
                self.app.push({"@dpi": DpiScreen, "@r6cal": SiegeCalibrate}.get(row[0], CodCalibrate)(self.app))
        else:
            if "left" in nav:
                self._change(row, -1)
            if "right" in nav or ("accept" in nav and kind != "num"):
                self._change(row, 1)
            if kind == "num" and len(row[3]) > 4:
                if "lb" in nav:
                    self._change(row, -1, True)
                if "rb" in nav:
                    self._change(row, 1, True)
        if "back" in nav:
            self.app.pop()

    def draw(self, ui):
        app, cfg = self.app, self.app.cfg
        fps = config.fingerprints(cfg)
        _header(ui, "SETTINGS", f"fingerprints: Core {fps['core']}  ·  Call of Duty {fps['cod']}  ·  Siege {fps['r6']}"
                "   ·   dotted settings change difficulty")
        # the settings that change difficulty for the input device in use (a mouse run does not care
        # about stick deadzones, nor a controller run about DPI) - read straight off what each track
        # would log, so a track that follows another's sensitivity dots that one's settings
        dotted = set(config.comparable(cfg)) | set(config.comparable_cod(cfg)) | set(config.comparable_r6(cfg))
        dotted.add("sens.kbm" if config.kind(cfg) == "kbm" else "sens.pad")
        if "cod.turn_rate_per_unit" in dotted:
            dotted.add("@calibrate")
        if "r6.turn_rate_per_unit" in dotted:
            dotted.add("@r6cal")
        x, y, w = 96.0, 196.0, 780.0
        for i in range(self.top, min(self.top + self.VISIBLE, len(self.rows))):
            row = self.rows[i]
            if row[0] == "#":
                ui.text(x, y + 14, row[1], "s", FAINT)
                lw = ui.width(row[1], "s")
                ui.rect(x + lw + 16, y + 23, w - lw - 56, 1, (1.0, 1.0, 1.0, 0.08))
            else:
                sel = i == self.sel
                val = self._text(row)
                typing = sel and self.buf is not None
                if typing:
                    caret = "|" if int(time.perf_counter() * 2) % 2 else " "
                    shown = f"type a value:  {self.buf}{caret}"
                else:
                    shown = val if (not sel or row[2] == "action") else f"‹ {val} ›"
                end = _row(ui, x, y, w, row[1], sel, shown, value_col=WARN if typing else None, hit=i)
                if sel and row[2] != "action" and not typing:
                    # the arrows either side of the value are buttons too
                    vr = x + w - 40
                    vw = ui.width(shown, "m")
                    _hit(ui, vr - vw - 12, y - 7, ui.width("‹ ", "m") + 20, ROW - 4, ("step", i, -1))
                    _hit(ui, vr - ui.width(" ›", "m") - 8, y - 7, ui.width(" ›", "m") + 28, ROW - 4, ("step", i, 1))
                if row[0] in dotted:
                    ui.disc(end + 16, y + 17, 4, WARN if sel else FAINT)
            y += ROW
        if self.top + self.VISIBLE < len(self.rows):
            ui.text(x, y + 4, "▼ more", "s", FAINT)
        if self.top > 0:
            ui.text(x + 260, 172, "▲ more", "s", FAINT)

        px = 960.0
        pw = ui.W - px - 96
        ui.panel(px, 190, pw, 640)
        row = self.rows[self.sel]
        tx, ty, tw = px + 40, 224.0, pw - 80
        ui.text(tx, ty, row[1], "l", WHITE)
        yy = ui.para(tx, ty + 52, row[4], "m", DIM, tw) + 20
        unused = _unused(cfg, row[0])
        if unused:
            yy = ui.para(tx, yy, f"Not in use right now: {unused[0].lower()} sensitivity follows the {_NAMES[unused[1]]} "
                         "on every track (top of this list).", "s", WARN, tw) + 20
        if row[0] in ("sens.pad", "sens.kbm"):
            yy = self._follow_table(ui, tx, yy, tw, "pad" if row[0] == "sens.pad" else "kbm") + 12
        if row[0] in ("r6.weapon", "r6.sight", "r6.barrel", "r6.grip"):
            yy = self._gun_panel(ui, tx, yy, tw) + 12
        if row[0] in ("mouse.dpi", "mouse.cm360", "mouse.ads_mult") or row[0].startswith(("cod.mouse_", "r6.mouse_")):
            # what the numbers come to, since a sensitivity only means something as a distance
            m, c = cfg["mouse"], cfg["cod"]
            if row[0].startswith("r6."):
                hip = sens.own_mouse_cm(cfg, "r6")
                r6 = cfg["r6"]
                ratio = r6kit.mouse_ads_ratio(r6["mouse_ads"], r6kit.zoom_of(r6))
                note = (f"Now: {hip:.1f} cm per turn from the hip at {m['dpi']} DPI, {hip / ratio:.1f} cm through the "
                        f"{r6kit.sight_of(r6)} sight (x{ratio:.3f}).")
            elif row[0].startswith("mouse."):
                deg = config.mouse_deg(cfg)
                note = (f"Now: {m['cm360']:.1f} cm per turn = {360.0 / deg:,.0f} counts per turn at {m['dpi']} DPI "
                        f"({deg * 1000:.2f} thousandths of a degree per count).")
            else:
                hip = config.cod_mouse_cm360(cfg)
                ratio = c["mouse_ads"] * codkit.mouse_ads_ratio(c["fov"], codkit.sight_zoom(c), c["mouse_mdc"])
                note = (f"Now: {hip:.1f} cm per turn from the hip at {m['dpi']} DPI, {hip / ratio:.1f} cm through the "
                        f"sights (x{ratio:.3f}).")
            yy = ui.para(tx, yy, note, "s", GOOD, tw) + 12
            yy = ui.para(tx, yy, "Used when the active profile is mouse and keyboard. Movement is read raw: no "
                         "acceleration, no smoothing, and no aim assist on either track.", "s", DIM, tw) + 20
        if row[0] in ("cod.sens_h", "cod.ads_mult") and cfg["cod"].get("ads_hold"):
            per_unit, ratio = config.cod_rates(cfg)
            ads = cfg["cod"]["ads_hold"] * per_unit * ratio
            yy = ui.para(tx, yy, f"The sensitivity finder's ADS speed ({ads:.0f}°/s) is being held: changing stick "
                         "sensitivity moves the ADS multiplier to keep it. Set the multiplier by hand to let go of it.",
                         "s", GOOD, tw) + 20
        if row[0] in dotted:
            yy = ui.para(tx, yy, "Changes difficulty: runs made with a different value are kept, but not "
                         "mixed into the same comparison.", "s", WARN, tw) + 20
        if row[0].startswith("aim.") and row[0] not in ("aim.invert_y", "aim.stick"):
            self._curve(ui, tx, max(yy, 500), min(tw, 400), 230, False)
        elif (row[0].startswith("cod.") and not row[0].startswith("cod.mouse_")
              and row[0] not in ("cod.aa_style", "cod.fov", "cod.ads_fov")) or row[0] == "@calibrate":
            self._curve(ui, tx, max(yy, 540), min(tw, 400), 200, True)      # only where the stick curve is the subject
        elif row[0].startswith("crosshair."):
            ui.rect(tx, 500, 300, 220, (0.10, 0.11, 0.13, 1.0))
            draw_crosshair(ui, cfg["crosshair"], tx + 150, 610)
        if self.buf is not None:
            lo, hi, fmt = row[3][0], row[3][1], row[3][3]
            _footer(ui, f"type a number ({fmt.format(lo)} - {fmt.format(hi)})        A / Enter  set it        B / Esc  cancel")
            return
        hint = "A  open" if row[2] == "action" else "Left / Right  change"
        if row[2] == "num" and len(row[3]) > 4:
            hint += "        LB / RB  fine"
        if row[2] == "num" and ui.kbm:
            hint += "        A  type a value"
        _footer(ui, hint + "        B  back")

    def _gun_panel(self, ui, x, y, w):
        """The chosen Siege gun: its numbers, and what an unheld magazine does with this barrel and grip."""
        import random

        c = self.app.cfg["r6"]
        gun = r6kit.gun_of(c)
        barrel, grip = c["barrel"], r6kit.grip_of(c)
        y = ui.para(x, y, gun["note"], "s", GOOD, w) + 10
        far = gun["falloff"][-1]
        hp = r6kit.TUNE["hp"]
        rows = (("Damage", f"{gun['dmg']}, down to {round(gun['dmg'] * far[1])} past {far[0]:.0f} m"),
                ("Fire rate", f"{gun['rpm']} a minute"),
                ("Magazine", f"{gun['mag']} + 1, empty in {gun['mag'] * 60.0 / gun['rpm']:.1f} s"),
                ("Body shots to kill", " / ".join(str(math.ceil(hp[s] / gun["dmg"])) for s in (3, 2, 1))
                 + "   (100 / 110 / 125 health)"),
                ("Headshots to kill", "1, always"))
        for k, v in rows:
            ui.text(x, y, k, "s", FAINT)
            ui.text(x + 170, y, v, "s", WHITE)
            y += 24
        # the spray: every bullet of one magazine with nothing pulling it down, 12 px to the degree
        bw, bh, s = min(w, 420.0), 250.0, 12.0
        y += 10
        ui.rect(x, y, bw, bh, (0.0, 0.0, 0.0, 0.35))
        ui.frame(x, y, bw, bh, (1.0, 1.0, 1.0, 0.10))
        rng = random.Random(4)
        px, py = x + bw / 2, y + bh - 14
        cx = cy = 0.0
        ui.disc(px, py, 3.5, WHITE)
        for n in range(gun["mag"]):
            sx, up = r6kit.recoil_step(gun, n, rng, barrel, grip)
            cx, cy = cx + sx, cy + up
            qx, qy = px + cx * s, py - cy * s
            if y + 4 < qy:
                ui.disc(min(max(qx, x + 4), x + bw - 4), qy, 3.0, ACCENT)
        ui.text(x + 10, y + 8, f"unheld magazine: climbs {cy:.0f}°, drifts {abs(cx):.1f}° "
                + ("right" if cx > 0 else "left"), "s", DIM)
        return y + bh

    def _follow_table(self, ui, x, y, w, kind_):
        """What each track's own settings come to for this device, so the choice is between numbers."""
        cfg = self.app.cfg
        cur = cfg["sens"][kind_]
        ui.text(x, y, "WHAT EACH TRACK'S SETTINGS COME TO", "s", FAINT)
        y += 28
        for d in config.DIALECTS:
            if kind_ == "kbm":
                val = f"{sens.own_mouse_cm(cfg, d):.1f} cm per turn"
            else:
                r = sens.own_pad(cfg, d)
                val = f"{r.rate_h:.0f}°/s hip  ·  {r.rate_h * r.ads:.0f}°/s ADS"
            on = cur == d
            ui.text(x, y, _NAMES[d], "m", ACCENT if on else WHITE)
            ui.text(x + w, y, val + ("   ◄ every track" if on else ""), "m", ACCENT if on else DIM, 2)
            y += 32
        return y

    def _curve(self, ui, x, y, w, h, cod):
        """Plot stick deflection -> turn speed for the current deadzones and curve."""
        cfg = self.app.cfg
        aim = cfg["aim"]
        if cod:
            c = cfg["cod"]
            full = _cod_speeds(cfg)[0]
            fn = lambda sx, sy: codkit.shape(sx, sy, c["dz_r_min"] / 100.0, c["dz_r_max"] / 100.0, c["curve"])[2]
        else:
            full = aim["sens_h"]
            fn = lambda sx, sy: math.hypot(*padmod.shape(sx, sy, aim["deadzone"], aim["curve"]))
        ui.rect(x, y, w, h, (0.0, 0.0, 0.0, 0.35))
        ui.frame(x, y, w, h, (1.0, 1.0, 1.0, 0.10))
        ui.line(x, y + h, x + w, y, 1, (1.0, 1.0, 1.0, 0.10))
        prev = None
        for i in range(49):
            d = i / 48.0
            p = (x + d * w, y + h - fn(d, 0.0) * h)
            if prev:
                ui.line(prev[0], prev[1], p[0], p[1], 2.5, ACCENT)
            prev = p
        sx, sy = self.app.pad.stick(aim["stick"])
        m = min(math.hypot(sx, sy), 1.0)
        f = fn(sx, sy)
        ui.disc(x + m * w, y + h - f * h, 6, WHITE)
        ui.text(x, y + h + 8, "stick deflection", "s", FAINT)
        ui.text(x + w, y + h + 8, f"now: {f * full:.0f}°/s of {full:.0f}", "s", DIM, 2)


class CodCalibrate(Screen):
    """The spin test: turn speed measured in the real game, typed in here."""

    ROWS = (
        ("hip_turns", "Hip-fire: full spins timed", 1, 40, 1, 1, "{:.0f}"),
        ("hip_seconds", "Hip-fire: seconds they took", 0.5, 120.0, 0.05, 0.01, "{:.2f} s"),
        ("ads_turns", "Aiming down sights: full spins timed", 1, 40, 1, 1, "{:.0f}"),
        ("ads_seconds", "Aiming down sights: seconds they took", 0.5, 240.0, 0.05, 0.01, "{:.2f} s"),
        ("sens", "Stick sensitivity in the game at the time", 0.10, 4.00, 0.05, 0.01, "{:.2f}"),
        ("ads_mult", "ADS multiplier in the game at the time", 0.10, 2.00, 0.05, 0.01, "{:.2f}"),
    )

    SECTION = "cod"         # whose spin test this is (the Siege track has one too: SiegeCalibrate)

    def __init__(self, app):
        super().__init__(app)
        self.sel = 1
        self.buf = ""

    @property
    def text_mode(self):
        return bool(self.buf)

    def point(self, hit, click):
        if self.buf and click:               # clicking away from a typed number keeps it
            try:
                self._set(self.ROWS[self.sel][0], float(self.buf))
            except ValueError:
                pass
            self.buf = ""
        if not self.buf:
            self.sel = hit
        return False

    def _set(self, key, v):
        _, _, lo, hi, step, _, _ = next(r for r in self.ROWS if r[0] == key)
        v = max(lo, min(hi, v))
        self.app.cfg[self.SECTION]["cal"][key] = int(round(v)) if isinstance(step, int) else round(v, 2)
        self.app.save()

    def update(self, nav, now):
        app = self.app
        key, _, lo, hi, step, fine, _ = self.ROWS[self.sel]
        for ch in app.typed:
            if (ch.isdigit() or ch == ".") and len(self.buf) < 7:
                self.buf += ch
        if self.buf:
            if "backspace" in app.keys:
                self.buf = self.buf[:-1]
            elif "enter" in app.keys or "accept" in nav:
                try:
                    self._set(key, float(self.buf))
                except ValueError:
                    pass
                self.buf = ""
            elif "esc" in app.keys or "back" in nav:
                self.buf = ""
            return
        cal = app.cfg[self.SECTION]["cal"]
        if "up" in nav:
            self.sel = (self.sel - 1) % len(self.ROWS)
        if "down" in nav:
            self.sel = (self.sel + 1) % len(self.ROWS)
        d = (1 if "right" in nav else 0) - (1 if "left" in nav else 0)
        df = (1 if "rb" in nav else 0) - (1 if "lb" in nav else 0)
        if d or df:
            self._set(key, cal[key] + d * step + df * fine)
        if "back" in nav:
            app.pop()

    def draw(self, ui):
        app, cfg = self.app, self.app.cfg
        cal, c = cfg["cod"]["cal"], cfg["cod"]
        _header(ui, "TURN SPEED CALIBRATION", "the spin test - measured in your game, not assumed")
        x, y, w = 96.0, 210.0, 860.0
        for i, (key, label, *_rest, fmt) in enumerate(self.ROWS):
            sel = i == self.sel
            if i in (2, 4):
                y += 16
            val = (self.buf + "_") if (sel and self.buf) else fmt.format(cal[key])
            _row(ui, x, y, w, label, sel, f"‹ {val} ›" if sel and not self.buf else val, hit=i)
            y += ROW
        ui.para(x, y + 26, "How to measure: in a private match, stand in the open and hold the right stick hard "
                "over. Time a number of full spins with a stopwatch, once from the hip and once aiming down "
                "sights with the gun you use. More spins means less timing error.\n\nLeft / right changes a "
                "value, LB / RB makes fine changes, or just type the number and press Enter.", "m", DIM, w - 40)

        px = 1040.0
        pw = ui.W - px - 96
        ui.panel(px, 190, pw, 640)
        tx, ty, tw = px + 40, 224.0, pw - 80
        hip = 360.0 * cal["hip_turns"] / cal["hip_seconds"]
        ads = 360.0 * cal["ads_turns"] / cal["ads_seconds"]
        per_unit, ratio = config.cod_rates(cfg)
        ui.text(tx, ty, "WHAT THE NUMBERS SAY", "s", FAINT)
        lines = [
            (f"{hip:.0f}°/s", f"hip-fire turn speed at {cal['sens']:.2f}  ({cal['hip_seconds'] / cal['hip_turns']:.2f} s a spin)"),
            (f"{per_unit:.0f}°/s", "per 1.00 of stick sensitivity"),
            (f"{ads:.0f}°/s", f"aiming down sights at ×{cal['ads_mult']:.2f}"),
            (f"{100 * ads / hip:.0f}%", "of hip speed while aiming"),
        ]
        yy = ty + 34
        for big, small in lines:
            ui.text(tx, yy, big, "l", WHITE)
            ui.text(tx + 150, yy + 6, small, "m", DIM)
            yy += 46
        now_hip, now_ads = _cod_speeds(cfg)
        ui.text(tx, yy + 20, "THIS TRACK NOW", "s", FAINT)
        ui.text(tx, yy + 50, f"{now_hip:.0f}°/s hip   ·   {now_ads:.0f}°/s ADS", "l", ACCENT)
        ui.text(tx, yy + 90, f"at {c['sens_h']:.2f} sensitivity and ×{c['ads_mult']:.2f} ADS, full stick", "m", DIM)
        sx, sy = app.pad.stick(cfg["aim"]["stick"])
        f = codkit.shape(sx, sy, c["dz_r_min"] / 100.0, c["dz_r_max"] / 100.0, c["curve"])[2]
        ui.text(tx, yy + 150, "YOUR STICK RIGHT NOW", "s", FAINT)
        ui.text(tx, yy + 180, f"{f * now_hip:.0f}°/s hip   ·   {f * now_ads:.0f}°/s ADS", "l", WHITE)
        ui.para(tx, yy + 240, "Only full-stick speed is measured here. How fast you turn at part deflection follows "
                "the response curve, whose shape comes from a community measurement of the game. The game also "
                "ramps speed up briefly at full stick; that ramp is not reproduced.", "s", DIM, tw)
        _footer(ui, "Left / Right  change        LB / RB  fine        type a number + Enter        B  back")


class SiegeCalibrate(CodCalibrate):
    """The Siege track's spin test. Siege publishes no controller turn speeds at all, so until this
    is filled in from the real game the track's own stick speeds are a placeholder."""

    SECTION = "r6"
    ROWS = (
        ("hip_turns", "Hip-fire: full spins timed", 1, 40, 1, 1, "{:.0f}"),
        ("hip_seconds", "Hip-fire: seconds they took", 0.5, 240.0, 0.05, 0.01, "{:.2f} s"),
        ("ads_turns", "Aiming down sights: full spins timed", 1, 40, 1, 1, "{:.0f}"),
        ("ads_seconds", "Aiming down sights: seconds they took", 0.5, 480.0, 0.05, 0.01, "{:.2f} s"),
        ("sens", "Horizontal sensitivity in the game at the time", 1, 100, 5, 1, "{:.0f}"),
        ("ads", "ADS sensitivity in the game at the time", 1, 100, 5, 1, "{:.0f}"),
    )

    def draw(self, ui):
        app, cfg = self.app, self.app.cfg
        cal, c = cfg["r6"]["cal"], cfg["r6"]
        _header(ui, "SIEGE TURN SPEED", "the spin test - Siege publishes no controller turn speeds, so measure yours")
        x, y, w = 96.0, 210.0, 860.0
        for i, (key, label, *_rest, fmt) in enumerate(self.ROWS):
            sel = i == self.sel
            if i in (2, 4):
                y += 16
            val = (self.buf + "_") if (sel and self.buf) else fmt.format(cal[key])
            _row(ui, x, y, w, label, sel, f"‹ {val} ›" if sel and not self.buf else val, hit=i)
            y += ROW
        ui.para(x, y + 26, "How to measure: in the Shooting Range or a custom game, hold the right stick hard over "
                "and time a number of full spins with a stopwatch - once from the hip, once aiming down a 1x sight. "
                "Enter the sensitivities you had set while you did it. Use the Siege settings you really play on "
                "(rotation curve, acceleration): the test measures the result of all of them at full stick.\n\n"
                "Left / right changes a value, LB / RB makes fine changes, or type the number and press Enter.",
                "m", DIM, w - 40)

        px = 1040.0
        pw = ui.W - px - 96
        ui.panel(px, 190, pw, 640)
        tx, ty, tw = px + 40, 224.0, pw - 80
        hip = 360.0 * cal["hip_turns"] / cal["hip_seconds"]
        ads = 360.0 * cal["ads_turns"] / cal["ads_seconds"]
        per_unit, _ = config.r6_rates(cfg)
        ui.text(tx, ty, "WHAT THE NUMBERS SAY", "s", FAINT)
        lines = [
            (f"{hip:.0f}°/s", f"hip-fire turn speed at {cal['sens']}  ({cal['hip_seconds'] / cal['hip_turns']:.2f} s a spin)"),
            (f"{per_unit:.1f}°/s", "per point of stick sensitivity"),
            (f"{ads:.0f}°/s", f"aiming down sights at ADS {cal['ads']}"),
            (f"{100 * ads / hip:.0f}%", "of hip speed while aiming"),
        ]
        yy = ty + 34
        for big, small in lines:
            ui.text(tx, yy, big, "l", WHITE)
            ui.text(tx + 150, yy + 6, small, "m", DIM)
            yy += 46
        own = sens.own_pad(cfg, "r6")
        ui.text(tx, yy + 20, "THIS TRACK'S OWN SETTINGS NOW", "s", FAINT)
        ui.text(tx, yy + 50, f"{own.rate_h:.0f}°/s hip   ·   {own.rate_h * own.ads:.0f}°/s ADS", "l", ACCENT)
        ui.text(tx, yy + 90, f"at {c['sens_h']} sensitivity and ADS {c['ads']}, full stick", "m", DIM)
        note = ("Speed is taken to rise in step with the slider, which Siege does not confirm. Part-stick speeds "
                "follow a squared curve after the deadzone: Siege's own curves (Classic, Updated) and its "
                "time-based acceleration have never been measured by anyone, so they are not reproduced.")
        src = cfg["sens"]["pad"]
        if src in config.DIALECTS and src != "r6":
            note = (f"Not in use right now: on a controller every track follows the {_NAMES[src]}. " + note)
        ui.para(tx, yy + 150, note, "s", WARN if src in config.DIALECTS and src != "r6" else DIM, tw)
        _footer(ui, "Left / Right  change        LB / RB  fine        type a number + Enter        B  back")


# ------------------------------------------------------------------ results
class Results(Screen):
    def __init__(self, app, rec, playlist=None, capture=None):
        super().__init__(app)
        self.rec, self.playlist = rec, playlist
        self.capture = capture          # the run as captured: its replay, and the targets the ghost plays
        self.t0 = time.perf_counter()
        self.scn = scn = tracks.BY_ID[rec["scenario"]]
        self.endless = bool(rec.get("endless"))
        # An endless run is set against nothing: its length is its own, so averages of timed runs
        # (and their totals - kills, score) would say nothing about it.
        grp = {} if self.endless else stats.groups(app.log.runs, scn.id, rec["fingerprint"])
        mine = next((lb for lb, rs in grp.items() if any(r is rec for r in rs)), None)
        prev = [r for r in grp.get(mine, []) if r is not rec]
        others = [(lb, rs) for lb, rs in grp.items() if lb != mine]
        others.sort(key=lambda kv: -len(kv[1]))
        self.other_label = others[0][0] if others else None
        self.rows = []
        self.new_best = False
        for key, label, unit, better, dec in scn.metrics:
            v = rec["stats"].get(key)
            own = stats.describe(stats.values(prev, key), better)
            oth = stats.describe(stats.values(others[0][1], key), better) if others else None
            tone = None
            if own and isinstance(v, (int, float)) and v != own["mean"]:
                tone = GOOD if (v > own["mean"]) == (better > 0) else BAD
            if key == scn.primary and own and isinstance(v, (int, float)):
                self.new_best = (v > own["best"]) if better > 0 else (v < own["best"])
            self.rows.append((label, unit, dec, v, own, oth, tone, key == scn.primary))
        self.next_cls = None
        if playlist and playlist["index"] + 1 < len(playlist["items"]):
            self.next_cls = playlist["items"][playlist["index"] + 1]
        # the ghost - a strong human player - plays the same targets in the background (replay.py)
        self.job = None
        if capture is not None:
            from . import ghost, replay

            if ghost.playable(capture.head["scenario"]):
                self.job = replay.GhostJob(app, capture)

    def _ready(self) -> bool:
        return self.job is not None and self.job.result is not None

    def update(self, nav, now):
        app = self.app
        if self._ready() and nav & {"x", "y"}:
            from . import replay

            if "y" in nav:
                app.push(replay.ReplayScreen(app, self.capture, self.job, scn_name=self.scn.name))
            else:
                app.push(replay.RouteScreen(app, self.capture, self.job, scn_name=self.scn.name))
            return
        if time.perf_counter() - self.t0 < 0.8:      # don't let a mashed Shoot button skip the screen
            return
        if nav & {"accept", "back"} and self.job is not None and not self.job.done:
            self.job.cancel()                       # leaving before the ghost is done: it is not needed
        if "accept" in nav:
            app.pop()
            if self.playlist:
                if self.next_cls:
                    app.start(self.next_cls, dict(self.playlist, index=self.playlist["index"] + 1))
                elif self.playlist.get("then") != "back":       # "back": the screen underneath takes it from here
                    app.push(Compare(app, track=self.scn.track))
            else:
                app.start(self.scn, over={"endless": True} if self.endless else None)
        elif "back" in nav:
            app.pop()

    def draw(self, ui):
        rec, W = self.rec, ui.W
        _header(ui, self.scn.name.upper(), f"{rec['controller']}   ·   "
                f"{_blabel(rec['binds']['shoot'])} shoot / {_blabel(rec['binds']['ads'])} aim"
                + ("   ·   endless" if self.endless else ""))
        prim = next(r for r in self.rows if r[7])
        ui.text(98, 210, prim[0].upper(), "s", FAINT)
        ui.text(96, 236, stats.fmt(prim[3], prim[2], prim[1]), "h", WHITE)
        y = 350
        if self.new_best:
            ui.text(98, y, "NEW BEST ON THIS CONTROLLER", "m", GOOD)
            y += 34
        if self.endless:
            m, s = divmod(int(rec["duration_s"]), 60)
            ui.text(98, y, f"ENDLESS  ·  {m}:{s:02d} PLAYED", "l", ACCENT)
            y = ui.para(98, y + 46, "Practice: kept in logs/endless.jsonl, out of your averages, Progress and every "
                        "comparison - its length is its own, so it is never set against the timed runs.", "m", DIM,
                        600) - 40
        elif prim[4]:
            ui.text(98, y, f"your average here: {stats.fmt(prim[4]['mean'], prim[2], prim[1])}"
                    f"  over {prim[4]['n']} earlier runs", "m", DIM)
        else:
            ui.text(98, y, "first run on this controller with these settings", "m", DIM)
        coach = rec["stats"].get("coach")
        if coach:               # Coach rules: how much of each habit was there while a target was in reach
            y += 86
            ui.text(98, y, "COACH  ·  WHILE A TARGET WAS IN YOUR BUBBLE", "s", FAINT)
            names = (("track", "Right stick following it"), ("strafe", "Strafing"), ("centre", "Centred on it"))
            for key, name in names:
                v = coach.get(key)
                if v is None:
                    continue
                y += 40
                ui.text(98, y, name, "m", WHITE if key == coach.get("focus") else DIM)
                ui.rect(400, y + 12, 180, 6, (0.0, 0.0, 0.0, 0.5))
                ui.rect(400, y + 12, 1.8 * v, 6, GOOD if v >= 80 else (WARN if v >= 30 else BAD))
                ui.text(640, y, f"{v:.0f}%", "m", WHITE, 2)
            focus = dict(names).get(coach.get("focus"))
            if focus and coach[coach["focus"]] < 80:
                ui.text(98, y + 48, f"Work on: {focus.lower()}", "m", ACCENT)
            elif focus:
                ui.text(98, y + 48, "All three habits were there.", "m", GOOD)

        px, py = 760.0, 196.0
        pw = W - px - 96
        ui.panel(px, py, pw, 80 + 44 * len(self.rows))
        # columns, right-aligned: this run, the ghost (when there is one), your average, the other pad
        heads = [("run", "THIS SESSION" if self.endless else "THIS RUN")]
        if self.job is not None:
            heads.append(("ghost", "GHOST"))
        if not self.endless:
            heads.append(("avg", "YOUR AVERAGE"))
        if self.other_label:
            heads.append(("other", self.other_label.upper()[:22]))
        first = 0.62 if len(heads) <= 2 else 0.50
        xs = {k: px + pw * first + (pw - 36 - pw * first) * i / max(len(heads) - 1, 1) for i, (k, _) in enumerate(heads)}
        if len(heads) == 1:
            xs["run"] = px + pw - 36
        for k, label in heads:
            ui.text(xs[k], py + 22, label, "s", ACCENT if k == "ghost" else FAINT, 2)
        gstats = self.job.result["stats"] if self._ready() else None
        y = py + 58
        for (label, unit, dec, v, own, oth, tone, primary), (key, *_rest) in zip(self.rows, self.scn.metrics):
            ui.text(px + 36, y, label, "m", WHITE if primary else DIM)
            ui.text(xs["run"], y, stats.fmt(v, dec, unit), "m", tone or WHITE, 2)
            if "ghost" in xs:
                gv = stats.fmt(gstats.get(key), dec, unit) if gstats else "…"
                ui.text(xs["ghost"], y, gv, "m", ACCENT, 2)
            if "avg" in xs:
                ui.text(xs["avg"], y, stats.fmt(own["mean"], dec, unit) if own else "-", "m", DIM, 2)
            if "other" in xs:
                ui.text(xs["other"], y, stats.fmt(oth["mean"], dec, unit) if oth else "-", "m", DIM, 2)
            y += 44
        if self.job is not None:
            self._ghost_note(ui, px, y + 52, pw)

        d, i = rec["display"], rec["input"]
        bits = [f"{d.get('fps_avg', 0):.0f} fps avg", f"1% low {d.get('fps_1pct_low', 0):.0f}"]
        if d.get("gpu_ms_avg") is not None:
            bits.append(f"GPU {d['gpu_ms_avg']:.2f} ms/frame")
        if i.get("report_hz_max"):
            bits.append(f"pad peaked at {i['report_hz_max']:,} Hz")
        if i.get("timestamp_resolution_ms") is not None:
            bits.append(f"input timestamps ±{i['timestamp_resolution_ms']:.2f} ms")
        ui.text(98, 960, "   ·   ".join(bits), "s", FAINT)
        ui.text(98, 986, f"saved to logs/{'endless' if self.endless else 'runs'}.jsonl   ·   {rec['run_id']}", "s", FAINT)
        extra = "Y  replay with the ghost        X  every target        " if self._ready() else ""
        if self.endless:
            _footer(ui, extra + "A  again, endless        B  menu")
        elif self.playlist:
            k, n = self.playlist["index"] + 1, len(self.playlist["items"])
            last = "A  done" if self.playlist.get("then") == "back" else "A  benchmark done - compare"
            nxt = f"A  next: {self.next_cls.name} ({k + 1}/{n})" if self.next_cls else last
            _footer(ui, extra + f"{nxt}        B  stop" + ("" if self.playlist.get("then") == "back" else " benchmark"))
        else:
            _footer(ui, extra + "A  run again        B  menu")

    def _ghost_note(self, ui, x, y, w):
        """The ghost's verdict: what it was, and the one thing it does better."""
        job = self.job
        ui.text(x, y, "THE GHOST  ·  A STRONG HUMAN PLAYER ON YOUR EXACT TARGETS", "s", ACCENT)
        y += 30
        if job.error:
            ui.para(x, y, "The ghost could not play this run - see aimtuner.log.", "m", WARN, w)
        elif job.result is None:
            ui.para(x, y, f"Playing your targets... {job.progress * 100:.0f}%", "m", DIM, w)
        else:
            text = job.result["compare"].get("advice") or ""
            y = ui.para(x, y, text, "l", WHITE, w) + 8
            ui.para(x, y, "Same targets, same moves, same settings and rules as you - only its aim is its own. "
                    "Y to watch it over your run, X to go through it target by target.", "s", DIM, w)


# ------------------------------------------------------------------ compare
class Compare(Screen):
    def __init__(self, app, scenario=None, track=None):
        super().__init__(app)
        if scenario in tracks.BY_ID:
            track = tracks.BY_ID[scenario].track
        self.track = tracks.BY_TRACK[track or "core"]
        self.all_settings = False
        self._cache = None
        self._pick(scenario)

    def _pick(self, scenario=None):
        ids = [s.id for s in self.track.scenarios]
        if scenario not in ids:
            # open on the first scenario that actually has runs for the current settings
            fp = config.fingerprint_for(self.app.cfg, self.track.id)
            played = {r.get("scenario") for r in self.app.log.runs if r.get("fingerprint") == fp}
            scenario = next((i for i in ids if i in played), ids[min(2, len(ids) - 1)])
        self.i = ids.index(scenario)

    def update(self, nav, now):
        n = len(self.track.scenarios)
        if "left" in nav:
            self.i = (self.i - 1) % n
        if "right" in nav:
            self.i = (self.i + 1) % n
        if nav & {"lb", "rb"}:
            k = tracks.TRACKS.index(self.track) + (1 if "rb" in nav else -1)
            self.track = tracks.TRACKS[k % len(tracks.TRACKS)]
            self._pick()
        if "y" in nav:
            self.all_settings = not self.all_settings
        if nav & {"back", "accept"}:
            self.app.pop()

    def _data(self):
        app = self.app
        fp = None if self.all_settings else config.fingerprint_for(app.cfg, self.track.id)
        key = (self.track.id, self.i, len(app.log.runs), fp)
        if self._cache is None or self._cache[0] != key:
            scn = self.track.scenarios[self.i]
            tab = stats.metric_table(app.log.runs, scn.id, fp)
            hidden = sum(1 for r in app.log.runs if r.get("scenario") == scn.id) - sum(tab["counts"]) if fp else 0
            self._cache = (key, tab, stats.scoreboard(app.log.runs, fp, self.track.id), hidden)
        return self._cache[1:]

    def draw(self, ui):
        app, W = self.app, ui.W
        tab, sb, hidden = self._data()
        scn = tab["scenario"]
        _header(ui, "COMPARE", f"{self.track.name} track   ·   averages over every logged run   ·   mean ± spread")
        ui.text(W - 96, 70, f"‹  {scn.name}  ›", "xl", ACCENT, 2)
        sub = f"scenario {self.i + 1} of {len(self.track.scenarios)}"
        if getattr(scn, "group", ""):
            sub = f"{scn.group}   ·   " + sub
        ui.text(W - 96, 130, sub, "s", FAINT, 2)

        labels = tab["labels"][:4]
        px, py = 96.0, 196.0
        pw = W - 192
        ui.panel(px, py, pw, 96 + 46 * len(tab["rows"]))
        if not labels:
            ui.text(px + 40, py + 40, "No runs for this scenario with the current settings yet.", "m", DIM)
        else:
            two = len(labels) == 2
            x0 = px + 36
            x_first = px + pw * 0.30
            span = pw * (0.40 if two else 0.66)
            colx = [x_first + span * (k + 1) / len(labels) for k in range(len(labels))]
            for k, lb in enumerate(labels):
                ui.text(colx[k], py + 20, lb, "m", WHITE, 2)
                ui.text(colx[k], py + 46, _runs(tab["counts"][k]), "s", FAINT, 2)
            if two:
                ui.text(px + pw - 36, py + 20, "DIFFERENCE", "s", FAINT, 2)
            y = py + 86
            for row in tab["rows"]:
                ui.text(x0, y, row["label"], "m", WHITE if row["primary"] else DIM)
                for k in range(len(labels)):
                    c = row["cells"][k]
                    if not c:
                        ui.text(colx[k], y, "-", "m", FAINT, 2)
                        continue
                    s = stats.fmt(c["mean"], row["dec"], row["unit"])
                    if c["sd"] is not None:
                        s += f"  ± {c['sd']:.{row['dec']}f}"
                    ui.text(colx[k], y, s, "m", GOOD if row["best"] == k else WHITE, 2)
                v = row["verdict"]
                if two and v and v["winner"] is not None:
                    col = {"clear": GOOD, "leaning": WARN}.get(v["conf"], FAINT)
                    pct = f"{v['pct']:.1f}%" if v["pct"] is not None else ""
                    ui.text(px + pw - 36, y, f"{labels[v['winner']]} +{pct} · {stats.CONF_TEXT[v['conf']]}", "s", col, 2)
                y += 46

        y = py + 96 + 46 * len(tab["rows"]) + 28
        if sb:
            a, b = sb["labels"]
            ui.text(96, y, f"OVERALL  ·  primary metric of each {self.track.name} scenario played", "s", FAINT)
            ui.text(96, y + 28, f"{a}  {sb['wins'][0]}  ({sb['clear'][0]} clear)      "
                    f"{b}  {sb['wins'][1]}  ({sb['clear'][1]} clear)", "l", WHITE)
            y += 84
        fp = config.fingerprint_for(app.cfg, self.track.id)
        note = ("Showing every run regardless of settings - treat with care." if self.all_settings else
                f"Only runs made with the current settings ({fp}) are shown."
                + (f"  {hidden} run(s) with other settings hidden." if hidden > 0 else ""))
        ui.para(96, y, note + "  \"Clear\" means the gap is larger than run-to-run noise (p < 0.05, at "
                "least 4 runs each). Anything else needs more runs before it means something.", "s", DIM, W - 192)
        _footer(ui, "Left / Right  scenario        LB / RB  track        Y  "
                + ("current settings only" if self.all_settings else "include all settings") + "        B  back")
