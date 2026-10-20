"""Small drawing helpers shared by every menu screen."""
from __future__ import annotations

from .theme import ACCENT, DIM, WHITE, alpha

ROW = 46.0


def _header(ui, title, sub=""):
    ui.text(96, 56, title, "xl", WHITE)
    if sub:
        ui.text(98, 116, sub, "m", DIM)


# The hints are written for a controller ("A  play        B  back"). On mouse and keyboard the
# same actions are these keys.
_KEYS = {"A": "Enter", "B": "Esc", "LB / RB": "Q / E", "A / Enter": "Enter", "B / Esc": "Esc",
         "D-pad / arrows": "Arrows / W A S D", "Left / Right": "Left / Right (A / D)"}


# Footer hints that a click can press: the controller letter each one starts with -> the action.
_CLICK = {"A": "accept", "B": "back", "X": "x", "Y": "y", "A / Enter": "accept", "B / Esc": "back"}


def _hit(ui, x, y, w, h, what):
    """Make a rectangle answer the mouse pointer this frame (see App._pointer). Later ones win."""
    hits = getattr(ui, "hits", None)
    if hits is not None:
        hits.append((x, y, w, h, what))


def _footer(ui, hints):
    kbm = getattr(ui, "kbm", False)
    parts = []
    for part in hints.split("        "):
        key, sep, what = part.partition("  ")
        parts.append((key, (_KEYS.get(key, key) if kbm else key) + sep + what))
    text = "        ".join(p for _, p in parts)
    ui.text(ui.W - 96, 1022, text, "s", DIM, 2)
    # each hint is also a button: clicking "Esc  back" goes back
    x = ui.W - 96 - ui.width(text, "s")
    gap = ui.width("        ", "s")
    for key, shown in parts:
        pw = ui.width(shown, "s")
        if key in _CLICK:
            _hit(ui, x - 8, 1014, pw + 16, 36, ("nav", _CLICK[key]))
        x += pw + gap


def _runs(n: int) -> str:
    return f"{n} run" if n == 1 else f"{n} runs"


def _row(ui, x, y, w, label, selected, value=None, col=None, value_col=None, indent=0.0, hit=None):
    """One menu line. Returns the x just past the label, for trailing markers.
    hit: what pointing at this line means to its screen (Screen.point); None = not pointable."""
    if hit is not None:
        _hit(ui, x - 18, y - 7, w, ROW - 4, hit)
    if selected:
        ui.rect(x - 18, y - 7, w, ROW - 4, alpha(ACCENT, 0.13))
        ui.rect(x - 18, y - 7, 4, ROW - 4, ACCENT)
    end = x + indent + ui.text(x + indent, y, label, "l", col or (WHITE if selected else DIM))
    if value is not None:
        ui.text(x + w - 40, y + 3, value, "m", value_col or (ACCENT if selected else DIM), 2)
    return end


class Screen:
    text_mode = False
    decor = True         # draw the drifting target spheres behind this screen

    def __init__(self, app):
        self.app = app

    def point(self, hit, click: bool) -> bool:
        """The mouse pointer moved onto (or clicked) a line drawn with hit=... Returns True when
        the click should also press A on it. By default a hit is the line's index in self.sel."""
        if hasattr(self, "sel"):
            self.sel = hit
        return click

    def update(self, nav, now): ...
    def draw(self, ui): ...
