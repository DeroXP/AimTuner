"""The DPI screen: what the mouse says its DPI is (if it can say), and a ruler test for any mouse."""
from __future__ import annotations

import math
import statistics

from . import config, dpi
from . import mouse as mousemod
from .theme import ACCENT, DIM, FAINT, GOOD, WARN, WHITE
from .uikit import ROW, Screen, _footer, _header, _row

LENGTHS = (5.0, 10.0, 15.0, 20.0, 25.0, 30.0)      # centimetres to slide; longer is more exact
MIN_COUNTS = 150                                    # shorter than this was a click, not a slide


class DpiScreen(Screen):
    ROWS = ("auto", "read", "length", "use", "clear")

    def __init__(self, app):
        super().__init__(app)
        self.sel = 0
        self.length = 10.0
        self.passes: list = []      # DPI from each slide
        self.acc = None             # [dx, dy] of the slide in progress
        self.note = ""
        self.asked = False

    def _rows(self):
        return [r for r in self.ROWS if r not in ("use", "clear") or self.passes]

    def result(self):
        """(measured DPI, the setting it most likely is) from the slides so far."""
        if not self.passes:
            return None
        got = statistics.median(self.passes)
        return got, dpi.nearest_step(got)

    def update(self, nav, now):
        app, cfg = self.app, self.app.cfg
        m = cfg["mouse"]
        rows = self._rows()
        self.sel = min(self.sel, len(rows) - 1)
        if "up" in nav:
            self.sel = (self.sel - 1) % len(rows)
        if "down" in nav:
            self.sel = (self.sel + 1) % len(rows)
        row = rows[self.sel]
        step = (1 if "right" in nav else 0) - (1 if "left" in nav else 0)
        if row == "length" and step:
            self.length = LENGTHS[(LENGTHS.index(self.length) + step) % len(LENGTHS)]
            self.passes = []
        if "accept" in nav or (row == "auto" and step):
            if row == "auto":
                m["dpi_auto"] = not m["dpi_auto"]
                app.save()
            elif row == "read":
                self.asked = app.dpi.ask(app.mouse.device_path()) or self.asked
                self.note = "Asking the mouse..."
            elif row == "use":
                m["dpi"] = self.result()[1]
                m["dpi_auto"] = False       # a measured value is the player's word; do not overwrite it
                app.save()
                app.toast(f"Mouse DPI set to {m['dpi']}")
            elif row == "clear":
                self.passes = []
        if "back" in nav:
            app.pop()
            return
        last = app.dpi.last
        if self.asked and last is not None and self.note == "Asking the mouse...":
            self.note = ""
        # a slide: hold the left button (or Shoot), move, let go
        shoot = cfg["kbm"]["shoot"]
        for _, kind, a, b in app.mouse.events:
            if kind == mousemod.BUTTON and a in ("M1", shoot):
                if b:
                    self.acc = [0, 0]
                elif self.acc is not None:
                    counts = math.hypot(*self.acc)
                    if counts >= MIN_COUNTS:
                        self.passes = (self.passes + [dpi.measure(counts, self.length)])[-7:]
                    self.acc = None
            elif kind == mousemod.MOTION and self.acc is not None:
                self.acc[0] += a
                self.acc[1] += b

    def draw(self, ui):
        app, cfg = self.app, self.app.cfg
        m = cfg["mouse"]
        last = app.dpi.last
        _header(ui, "MOUSE DPI", f"now {m['dpi']}   ·   " + ("read from the mouse automatically" if m["dpi_auto"]
                                                              else "set by hand"))
        x, y, w = 96.0, 210.0, 820.0
        res = self.result()
        labels = {
            "auto": ("Read it from the mouse automatically", "On" if m["dpi_auto"] else "Off"),
            "read": ("Ask the mouse now", (f"{last['dpi']} DPI" if last and last.get("dpi") else
                                           ("no answer" if last else ""))),
            "length": ("Ruler test: distance to slide", f"‹ {self.length:.0f} cm ›"),
            "use": ("Use the measured value", f"{res[1]} DPI" if res else ""),
            "clear": ("Clear the slides", f"{len(self.passes)}"),
        }
        rows = self._rows()
        for i, r in enumerate(rows):
            if r == "length":
                y += 18
            _row(ui, x, y, w, labels[r][0], i == min(self.sel, len(rows) - 1), labels[r][1], hit=i)
            y += ROW
        y += 24
        if last and last.get("dpi"):
            y = ui.para(x, y, f"{last['name']} reports {last['dpi']} DPI"
                        + (f" ({last['dpi_y']} vertically)" if last.get("dpi_y") not in (None, last["dpi"]) else "")
                        + ". That is the mouse's own answer, so it follows the DPI button as well.", "m", GOOD, w - 40) + 12
        elif last:
            y = ui.para(x, y, (last.get("name") or "This mouse") + ": " + last.get("why", "no answer") + ".",
                        "m", WARN, w - 40) + 12
        if self.note:
            y = ui.para(x, y, self.note, "m", DIM, w - 40) + 12
        y = ui.para(x, y, "Windows cannot ask a mouse for its DPI, so no program can detect it for every mouse. "
                    "Razer mice can be asked directly, and this does that. For any other mouse, the ruler test "
                    "on the right measures it.", "m", DIM, w - 40) + 12
        ui.para(x, y, "DPI is only used to turn centimetres into counts and back. Get it wrong and every "
                "centimetre figure is off by the same share - and so is the Core track's turn speed, which is set "
                "in centimetres.", "s", FAINT, w - 40)

        px = 1000.0
        pw = ui.W - px - 96
        ui.panel(px, 190, pw, 620)
        tx, ty, tw = px + 40, 224.0, pw - 80
        ui.text(tx, ty, "RULER TEST", "s", FAINT)
        btn = mousemod.label(cfg["kbm"]["shoot"])
        yy = ui.para(tx, ty + 34, f"1.  Lay a ruler beside the mouse.\n2.  Hold {btn.lower()} and slide the mouse "
                     f"exactly {self.length:.0f} cm in a straight line - slowly, without lifting it.\n"
                     "3.  Let go. Do it three times or more.", "m", WHITE, tw) + 18
        if self.acc is not None:
            counts = math.hypot(*self.acc)
            yy = ui.para(tx, yy, f"Sliding...  {counts:,.0f} counts", "l", ACCENT, tw) + 12
        if self.passes:
            ui.text(tx, yy, "SLIDES", "s", FAINT)
            yy += 28
            for k, p in enumerate(self.passes):
                ui.text(tx, yy, f"{k + 1}", "m", FAINT)
                ui.text(tx + 60, yy, f"{p:,.0f} DPI", "m", WHITE)
                yy += 30
            got, stepped = res
            spread = (max(self.passes) - min(self.passes)) / got * 100.0 if len(self.passes) > 1 else None
            yy += 10
            ui.text(tx, yy, f"{stepped}", "h", ACCENT)
            ui.text(tx + ui.width(f"{stepped}", "h") + 24, yy + 44, "DPI", "l", WHITE)
            yy += 104
            line = f"Measured {got:,.0f}"
            if spread is not None:
                line += f", slides within {spread:.0f}% of each other"
            line += (". Mice are set in round steps, so that is taken as the nearest one."
                     if stepped != round(got) else ".")
            yy = ui.para(tx, yy, line, "s", DIM, tw) + 8
            if spread is not None and spread > 8.0:
                ui.para(tx, yy, "Those slides disagree by more than a ruler should allow. Go slower and keep the "
                        "mouse flat, or clear them and try a longer distance.", "s", WARN, tw)
        else:
            ui.para(tx, yy, "A longer slide is more exact: being 2 mm out is 4% over 5 cm and under 1% over 25 cm.",
                    "s", DIM, tw)
        hints = "A  select        Left / Right  change"
        _footer(ui, hints + "        B  back")
