"""Guided comparison: two controllers, compared the way that actually answers the question.

Why it exists. The first real comparison on this app ended in a tie that was
mostly an artefact: scores climbed all evening whichever pad was in hand, so the
pad played later looked better. Playing "all of A, then all of B" measures the
order, not the controllers.

What it does instead:
  * a short fixed set of scenarios, the same every time, so passes are quick and
    alike;
  * one warm-up pass that is played and thrown away;
  * then blocks of four passes in A-B-B-A order (the next block B-A-A-B). Anything
    that drifts steadily while you play - warming up, tiring, learning the drill -
    lands equally on both pads and cancels out of the difference;
  * so the first pad is asked for twice running: warm-up, then pass 1. The first
    person to use this read that as the screen being stuck and gave up, twice, so
    the hand-over screen now draws the whole block with the warm-up in it and says
    so in words, the warm-up can be skipped, and a block left unfinished can be
    carried on instead of starting again from the warm-up;
  * it tells you which pad to pick up, and will not start a pass on the wrong one;
  * the verdict is computed from pairs (stats.duel_cells / duel_summary), says
    plainly whether the gap is bigger than the noise, and if it is not, roughly how
    much more play a gap that size would need.

Blocks add up across sessions as long as the settings fingerprint is the same, so
a comparison can be finished over several evenings.
"""
from __future__ import annotations

import datetime
import math
import time

from . import codscn, config, r6scn, scenarios, stats, tracks
from .theme import ACCENT, BAD, DIM, FAINT, GOOD, WARN, WHITE
from .uikit import ROW, Screen, _footer, _header, _row

# Five per track: enough different mechanics to mean something, few enough that a pass is short.
SETS = {
    "core": (scenarios.Flick, scenarios.Micro, scenarios.Grid, scenarios.SmoothTrack, scenarios.StrafeTrack),
    "cod": (codscn.SnapShot, codscn.StrafeSpray, codscn.RecoilHold, codscn.StickyTrack, codscn.MirrorStrafe),
    "r6": (r6scn.OneTap, r6scn.HeadHeight, r6scn.MagDump, r6scn.CrouchSpam, r6scn.DoorHold),
}
VERDICT = {"clear": ("a real difference", GOOD), "leaning": ("leaning, not settled", WARN),
           "noise": ("inside run-to-run noise", DIM), "few": ("too little to say", DIM)}


def order(block: int) -> str:
    """Which pad plays each of a block's four passes: A-B-B-A, and the mirror image next time."""
    return "ABBA" if block % 2 == 0 else "BAAB"


RESUME_H = 3.0      # an unfinished block can be picked up again for this long; a day later it is a different you
WARM_MIN = 20.0     # ...and if the break was longer than this, it starts with a fresh warm-up


class Duel(Screen):
    decor = False           # nothing drifting about behind "pick up this pad"

    def __init__(self, app):
        super().__init__(app)
        names = self._names()
        a = app.cfg["active_profile"] if app.cfg["active_profile"] in names else (names[0] if names else "")
        self.pair = [a, next((n for n in names if n != a), a)]
        self.track = tracks.CORE
        self.state = "setup"            # setup | handoff | playing | broken | summary
        self.session = time.strftime("%Y%m%d-%H%M%S")
        self.block = 0
        # this block's passes in order, {"pad": 0 or 1, "pass": 0-3 or None for the warm-up}, and which one is next
        self.steps: list = []
        self.at = 0
        self.warmed = False             # the pass just finished was the warm-up (the next screen says so)
        self.pl_id = None
        self.played = False
        self._cache = None
        self._open = None
        self.sel = len(self.rows()) - 1
        if "resume" in self.rows():
            self.sel = self.rows().index("resume")

    @property
    def queue(self) -> list:
        """The passes still to play."""
        return self.steps[self.at:]

    # ---- data ---------------------------------------------------------------
    def _names(self) -> list:
        """The profiles this can compare: controllers. (A mouse and a controller are not played with
        the same settings, so there is no fair pairing of their runs.)"""
        return [p["name"] for p in self.app.cfg["profiles"] if p.get("kind") != "kbm"]

    def rows(self) -> tuple:
        return ("a", "b", "track") + (("resume",) if self._unfinished() else ()) + ("go",)

    def _unfinished(self):
        """A block this pair started on this track in the last few hours and did not finish:
        {"session", "block", "done": counted passes finished, "minutes": since its last run}, else None.
        Without this, stepping out for a moment meant starting again from the warm-up."""
        runs = self.app.log.runs
        key = (self.track.id, self._fp(), tuple(self.pair), len(runs))
        if self._open is None or self._open[0] != key:
            found = None
            mine = [r for r in runs if r.get("duel") and r.get("track") == self.track.id
                    and r.get("fingerprint") == key[1] and r.get("controller") in self.pair]
            if mine:
                d = mine[-1]["duel"]
                same = [r for r in mine if (r["duel"].get("session"), r["duel"].get("block")) == (d.get("session"), d.get("block"))]
                try:
                    age = (datetime.datetime.now() - datetime.datetime.fromisoformat(mine[-1].get("time", ""))).total_seconds() / 60.0
                except ValueError:
                    age = 1e9
                want = {s.id for s in self._items()}
                seq, done = order(d.get("block") or 0), 0
                while done < 4 and want <= {r["scenario"] for r in same if r["duel"].get("pass") == done
                                            and r["controller"] == self.pair["AB".index(seq[done])]}:
                    done += 1
                warm = want <= {r["scenario"] for r in same if r["duel"].get("warmup")}
                if done < 4 and (done or warm) and 0.0 <= age <= RESUME_H * 60.0:
                    found = {"session": d.get("session"), "block": d.get("block") or 0, "done": done, "minutes": age}
            self._open = (key, found)
        return self._open[1]

    def _items(self):
        return SETS[self.track.id]

    def _fp(self):
        return config.fingerprint_for(self.app.cfg, self.track.id)

    def _summary(self):
        """Verdict over every finished block of this pair, on this track, with the current settings."""
        key = (self.track.id, self._fp(), tuple(self.pair), len(self.app.log.runs))
        if self._cache is None or self._cache[0] != key:
            cells = stats.duel_cells(self.app.log.runs, self.track.id, self._fp(), *self.pair)
            self._cache = (key, stats.duel_summary(cells))
        return self._cache[1]

    def _minutes(self, passes: int) -> int:
        return math.ceil(passes * len(self._items()) * (self.app.cfg["game"]["duration"] + 12) / 60.0)

    def _plan_block(self, warmup: bool, done: int = 0):
        """Lay out this block's four passes, the first `done` of them already played."""
        seq = order(self.block)
        self.steps = [{"pad": "AB".index(ch), "pass": i} for i, ch in enumerate(seq)]
        if warmup:      # on the pad that plays next, so each pad ends up with one pass straight after a swap
            self.steps.insert(done, {"pad": self.steps[done]["pad"], "pass": None})
        self.at, self.warmed = done, False
        self.state = "handoff"

    def _ready(self):
        """(can the pass start, what to tell the player). The pad in hand has to be the one whose turn it is."""
        app = self.app
        want = self.pair[self.steps[self.at]["pad"]]
        info = app.pad.info()
        if not info:
            return False, "No controller detected."
        dev = info.get("id")
        if not dev:
            return True, ""                          # nothing to tell pads apart by: take the player's word
        owner = app.owner_of(dev)
        if owner == want or (owner is None and not config.profile(app.cfg, want)["devices"]):
            return True, ""
        return False, (f"The pad in your hands is {owner}. Pick up {want} and press a button on it." if owner else
                       f"This pad ({dev}) is not linked to {want}. Link it under Controller first.")

    def _start_pass(self):
        app, step = self.app, self.steps[self.at]
        app.cfg["active_profile"] = self.pair[step["pad"]]
        app.save()
        tag = {"session": self.session, "block": self.block, "pass": step["pass"], "warmup": step["pass"] is None}
        pl = app.start_playlist(self._items(), f"duel-{self.session}", then="back", duel=tag)
        if pl:
            self.pl_id, self.state = pl["id"], "playing"

    # ---- flow ---------------------------------------------------------------
    def update(self, nav, now):
        app = self.app
        if self.state == "setup":
            self._update_setup(nav)
        elif self.state == "handoff":
            if "accept" in nav and self._ready()[0]:
                self._start_pass()
            elif "x" in nav and self.steps[self.at]["pass"] is None:
                self.at += 1                # already warm: straight to the passes that count
            elif "back" in nav:
                self.state = "summary" if self.played else "setup"
        elif self.state == "playing":
            # back here with no game running: either the pass was finished or it was abandoned
            done = sum(1 for r in app.log.runs if r.get("playlist") == self.pl_id)
            if done >= len(self._items()):
                self.warmed = self.steps[self.at]["pass"] is None
                self.played = self.played or not self.warmed
                self.at += 1
                self.state = "handoff" if self.at < len(self.steps) else "summary"
            else:
                self.state = "broken"
        elif self.state == "broken":
            if "accept" in nav:
                self.state = "handoff"
            elif "back" in nav:
                self.state = "summary" if self.played else "setup"
        else:
            if "accept" in nav:
                if self.at < len(self.steps):       # stopped part-way through: carry on where it was left
                    self.state = "handoff"
                else:
                    self.block += 1
                    self._plan_block(warmup=False)
            elif "back" in nav:
                app.pop()

    def _update_setup(self, nav):
        app = self.app
        names = self._names()
        rows = self.rows()
        self.sel = min(self.sel, len(rows) - 1)
        if "up" in nav:
            self.sel = (self.sel - 1) % len(rows)
        if "down" in nav:
            self.sel = (self.sel + 1) % len(rows)
        row = rows[self.sel]
        d = (1 if "right" in nav else 0) - (1 if "left" in nav else 0)
        if d and row in ("a", "b"):
            i = "ab".index(row)
            self.pair[i] = names[(names.index(self.pair[i]) + d) % len(names)]
        elif d and row == "track":
            self.track = tracks.TRACKS[(tracks.TRACKS.index(self.track) + d) % len(tracks.TRACKS)]
        if "back" in nav:
            app.pop()
        elif "accept" in nav and row == "resume":
            u = self._unfinished()
            self.session, self.block, self.played = u["session"], u["block"], u["done"] > 0
            self._plan_block(warmup=u["minutes"] > WARM_MIN, done=u["done"])
        elif "accept" in nav and row == "go" and self.pair[0] != self.pair[1]:
            self.session = time.strftime("%Y%m%d-%H%M%S")
            self.block, self.played = 0, False
            sm = self._summary()
            if sm:                      # carry on from blocks already played with these settings
                self.block = sm["blocks"]
            self._plan_block(warmup=True)

    # ---- drawing ------------------------------------------------------------
    def draw(self, ui):
        sub = f"{self.pair[0]}  vs  {self.pair[1]}   ·   {self.track.name} track"
        _header(ui, "GUIDED COMPARISON", sub if self.state != "setup" else "two controllers, in an order that keeps it fair")
        getattr(self, "_draw_" + ("handoff" if self.state == "playing" else self.state))(ui)

    def _draw_setup(self, ui):
        app, W = self.app, ui.W
        x, y, w = 96.0, 210.0, 700.0
        same = self.pair[0] == self.pair[1]
        u = self._unfinished()
        vals = {"a": self.pair[0], "b": self.pair[1], "track": self.track.name,
                "resume": f"pass {u['done'] + 1} of 4 is next" if u else "",
                "go": "pick two different controllers" if same else f"about {self._minutes(5)} min"}
        labels = {"a": "Controller A", "b": "Controller B", "track": "Track", "resume": "Carry on with the block you started",
                  "go": "Start a new block instead" if u else "Start: warm-up, then A - B - B - A"}
        rows = self.rows()
        for i, row in enumerate(rows):
            sel = i == min(self.sel, len(rows) - 1)
            if row in ("resume", "go") and rows[i - 1] == "track":
                y += 18
            v = vals[row]
            _row(ui, x, y, w, labels[row], sel, f"‹ {v} ›" if sel and row in ("a", "b", "track") else v,
                 value_col=WARN if (row == "go" and same) else None, hit=i)
            y += ROW
        y = ui.para(x, y + 30, "Five scenarios, played as passes: one warm-up that is thrown away, then four that count, "
                    "in the order A, B, B, A. The screen tells you which pad to pick up each time. Controller A is "
                    "therefore played twice in a row at the start - once to warm up, once for real.", "m", WHITE, w - 20) + 14
        y = ui.para(x, y, "You get better as you play, whichever pad you are holding. Playing all of one and then all "
                    "of the other hands that improvement to the second pad. In A-B-B-A order it falls on both "
                    "equally, so what is left is the controllers.", "m", DIM, w - 20) + 14
        ui.para(x, y, "Round length is the one in Settings; shorter rounds make shorter passes. Every run is logged "
                "as usual, and blocks add up across days while the settings stay the same. A block you leave "
                "unfinished can be carried on from here for the next three hours.", "s", FAINT, w - 20)

        px = 900.0
        pw = W - px - 96
        ui.panel(px, 190, pw, 640)
        tx, ty, tw = px + 40, 224.0, pw - 80
        ui.text(tx, ty, "THE FIVE SCENARIOS", "s", FAINT)
        yy = ty + 32
        for s in self._items():
            ui.text(tx, yy, s.name, "m", WHITE)
            ui.text(tx + tw, yy + 2, stats.primary_of(s)[1], "s", FAINT, 2)
            yy += 34
        yy += 24
        ui.text(tx, yy, f"SO FAR  ·  THESE TWO, THIS TRACK, CURRENT SETTINGS ({self._fp()})", "s", FAINT)
        yy += 32
        sm = None if same else self._summary()
        if not sm:
            ui.para(tx, yy, "No finished blocks yet.", "m", DIM, tw)
        else:
            self._verdict(ui, tx, yy, tw, sm, small=True)
        _footer(ui, "Left / Right  change        A  start        B  back")

    def _verdict(self, ui, x, y, w, sm, small=False):
        """The headline: who is ahead, by how much, and whether that means anything. Returns the y below it."""
        word, col = VERDICT[sm["conf"]]
        size = "l" if small else "xl"
        if sm["conf"] == "few":
            head = "Too little to say yet"
        elif sm["winner"] is None:
            head = "Dead level"
        elif sm["conf"] in ("clear", "leaning"):
            head = f"{self.pair[sm['winner']]} ahead by {abs(sm['mean']):.1f}%"
        else:
            head = "No real difference yet"
        ui.text(x, y, head, size, col if sm["conf"] == "clear" else WHITE)
        y += 46 if small else 66
        bits = [word, f"{sm['blocks']} block{'s' if sm['blocks'] != 1 else ''}", f"{sm['cells']} paired results"]
        if sm["sd"] is not None:
            bits.append(f"spread ±{sm['sd']:.1f}%")
        if sm["p"] is not None:
            bits.append(f"p = {sm['p']:.2f}" if sm["p"] >= 0.01 else "p < 0.01")
        ui.text(x, y, "   ·   ".join(bits), "s" if small else "m", col)
        y += 30 if small else 40
        if sm["conf"] == "noise" and sm["winner"] is not None:
            y = ui.para(x, y, f"{self.pair[sm['winner']]} is {abs(sm['mean']):.1f}% up, which is less than these runs "
                        "wobble by on their own.", "s" if small else "m", DIM, w) + 6
        if sm["conf"] == "clear":
            tail = "That gap is larger than chance would produce in runs this consistent."
        elif sm["blocks"] < 2 and sm["p"] is not None and sm["p"] < 0.05:
            tail = "One block cannot be called clear, however lopsided: play a second to confirm it."
        elif sm["more"] is None:
            tail = "Play another block for a first read on the noise."
        elif sm["more"] > 20:
            tail = ("A gap this small would take more than 20 further blocks to prove. For your hands, on this "
                    "track, these two perform the same.")
        else:
            tail = (f"If the gap is real and this size, about {sm['more']} more block{'s' if sm['more'] != 1 else ''} "
                    f"({self._minutes(4 * sm['more'])} min) should settle it.")
        return ui.para(x, y, tail, "s" if small else "m", DIM, w) + 10

    def _draw_handoff(self, ui):
        cx, step = ui.W / 2, self.steps[self.at]
        want = self.pair[step["pad"]]
        ok, why = self._ready()
        warm = step["pass"] is None
        ui.text(cx, 236, "WARM-UP  ·  THROWN AWAY" if warm else
                f"BLOCK {self.block + 1}  ·  PASS {step['pass'] + 1} OF 4  ·  COUNTS", "l", WARN if warm else ACCENT, 1)
        ui.text(cx, 316, "PICK UP", "l", DIM, 1)
        ui.text(cx, 366, want, "h", WHITE, 1)
        # the whole block, warm-up included, so that "the same pad again" is expected rather than a surprise
        n = len(self.steps)
        x0 = cx - (n * 170 - 30) / 2
        for i, st in enumerate(self.steps):
            w_up = st["pass"] is None
            col = ACCENT if i == self.at else (FAINT if i < self.at else DIM)
            if w_up and i == self.at:
                col = WARN
            bx = x0 + i * 170
            ui.frame(bx, 516, 140, 58, col, 2.5 if i == self.at else 1.5)
            ui.text(bx + 70, 524 if not w_up else 532, "warm-up" if w_up else str(st["pass"] + 1), "m" if w_up else "l", col, 1)
            ui.text(bx + 70, 584, self.pair[st["pad"]][:16], "s", col, 1)
            ui.text(bx + 70, 606, "done" if i < self.at else ("not counted" if w_up else ("now" if i == self.at else "")), "s", col, 1)
        if warm:
            note = (f"This pass is only to loosen up. The four that count come after it, and the first of them is on "
                    f"{want} as well - so you will be asked for this pad twice in a row.")
        elif self.warmed:
            note = f"Warm-up done. This is the first pass that counts - on {want} again, as planned."
        else:
            note = ""
        if note:
            ui.text(cx, 660, note, "m", WARN if warm else GOOD, 1)
        ui.text(cx, 712, f"{len(self._items())} scenarios, about {self._minutes(1)} min", "m", DIM, 1)
        if ok:
            ui.text(cx, 776, "Press A to start", "l", GOOD, 1)
        else:
            ui.text(cx, 776, why, "m", WARN, 1)
        _footer(ui, "A  start the pass        " + ("X  skip the warm-up (if you have just been playing)        " if warm else "")
                + "B  stop here")

    def _draw_broken(self, ui):
        cx = ui.W / 2
        ui.text(cx, 360, "PASS NOT FINISHED", "xl", WARN, 1)
        ui.text(cx, 440, "A pass only counts when all five scenarios were played. The runs you did finish are still in the log.",
                "m", DIM, 1)
        ui.text(cx, 520, "A  play this pass again        B  stop here", "l", WHITE, 1)
        _footer(ui, "A  again        B  stop")

    def _draw_summary(self, ui):
        W = ui.W
        sm = self._summary()
        x, y = 96.0, 200.0
        if not sm:
            ui.text(x, y, "No finished block yet", "xl", WHITE)
            ui.para(x, y + 70, "A block is four counted passes. Nothing can be said until one is complete.", "m", DIM, 900)
            _footer(ui, ("A  carry on with this block" if self.at < len(self.steps) else "A  play a block") + "        B  done")
            return
        y = self._verdict(ui, x, y, W - 192, sm) + 16
        px, pw = 96.0, W - 192
        ui.panel(px, y, pw, 70 + 44 * len(sm["rows"]))
        c0, c1, c2, c3, c4 = px + 36, px + pw * 0.46, px + pw * 0.62, px + pw * 0.82, px + pw - 36
        ui.text(c1, y + 20, self.pair[0].upper()[:20], "s", FAINT, 2)
        ui.text(c2, y + 20, self.pair[1].upper()[:20], "s", FAINT, 2)
        ui.text(c3, y + 20, "DIFFERENCE", "s", FAINT, 2)
        ui.text(c4, y + 20, "BLOCKS WON", "s", FAINT, 2)
        yy = y + 54
        for r in sm["rows"]:
            _, label, unit, _, dec = stats.primary_of(r["scenario"])
            ahead = None if r["rel"] == 0 else (0 if r["rel"] > 0 else 1)
            ui.text(c0, yy, r["scenario"].name, "m", WHITE)
            ui.text(c0 + 240, yy + 3, label, "s", FAINT)
            ui.text(c1, yy, stats.fmt(r["a"], dec, unit), "m", WHITE if ahead == 0 else DIM, 2)
            ui.text(c2, yy, stats.fmt(r["b"], dec, unit), "m", WHITE if ahead == 1 else DIM, 2)
            ui.text(c3, yy, "level" if ahead is None else f"{self.pair[ahead]} +{abs(r['rel']):.1f}%", "m", DIM, 2)
            ui.text(c4, yy, f"{r['wins'][0]} - {r['wins'][1]}", "m", DIM, 2)
            yy += 44
        ui.para(96, yy + 34, "Each row pairs the two pads inside one block, so both were played at the same point of "
                "your warm-up. The headline is the average of every row of every block, with each scenario counting "
                "the same. Warm-up passes are not in it.", "s", DIM, W - 192)
        if self.at < len(self.steps):
            left = len(self.steps) - self.at
            _footer(ui, f"A  carry on with this block ({left} pass{'es' if left != 1 else ''} left)        B  done")
        else:
            nxt = order(self.block + 1)
            _footer(ui, f"A  another block ({' - '.join(nxt)}, about {self._minutes(4)} min)        B  done")
