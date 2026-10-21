"""The anti-cheat on screen: a run's verdict, its details, and checking a friend's share code."""
from __future__ import annotations

import time

from . import ac, stats, tracks
from .ac import seal
from .theme import ACCENT, BAD, DIM, FAINT, GOOD, WARN, WHITE
from .uikit import Screen, _footer, _header

LOOK = {"verified": ("VERIFIED", GOOD), "unverified": ("UNVERIFIED", WARN), "flagged": ("FLAGGED", BAD)}
BUILD = {"official": "the published build, unmodified", "modified": "a MODIFIED build",
         "source": "not the published build (run from source or built locally)", "checking": "build not yet checked"}


def verdict_line(ui, rec, x, y, w) -> None:
    """One line under a run's results: what the anti-cheat made of it."""
    i = rec.get("integrity")
    if not isinstance(i, dict):
        ui.text(x, y, "ANTI-CHEAT  ·  not checked (this run is from before it)", "s", FAINT)
        return
    word, col = LOOK.get(i.get("status"), ("?", DIM))
    why = (i.get("flags") or i.get("doubts") or [""])[0]
    if i.get("status") == "flagged":
        why += "  -  " + ("you said it was you: counted" if i.get("override") else "not counted")
    elif i.get("status") == "verified":
        why = "nothing found" + ("" if i.get("behaviour") != "pending" else " so far")
    if i.get("behaviour") == "pending":
        why += "   (still checking how it was played…)"
    ui.text(x, y, "ANTI-CHEAT", "s", FAINT)
    ui.text(x + 130, y, word, "s", col)
    lim = w - 260
    while why and ui.width(why, "s") > lim:
        why = why[:-4] + "…"
    ui.text(x + 260, y, why, "s", DIM)


class IntegrityScreen(Screen):
    """Everything the anti-cheat found for one run, and why."""

    decor = False

    def __init__(self, app, rec):
        super().__init__(app)
        self.rec = rec

    def update(self, nav, now):
        if nav & {"back", "accept", "i"}:
            self.app.pop()

    def draw(self, ui):
        rec = self.rec
        i = rec.get("integrity") or {}
        _header(ui, "ANTI-CHEAT", f"{rec.get('scenario_name')}   ·   {rec.get('controller')}   ·   {rec.get('run_id')}")
        x, y, w = 96.0, 200.0, ui.W - 192
        word, col = LOOK.get(i.get("status"), ("NOT CHECKED", DIM))
        ui.text(x, y, word, "xl", col)
        y += 80
        sections = (("FOUND - why it is flagged", i.get("flags") or [], BAD),
                    ("STOPS IT BEING VERIFIED", i.get("doubts") or [], WARN))
        for title, items, c in sections:
            if not items:
                continue
            ui.text(x, y, title, "s", FAINT)
            y += 28
            for it in items:
                y = ui.para(x + 20, y, "·  " + it, "m", c, w - 40) + 6
            y += 14
        beh = i.get("behaviour")
        ui.text(x, y, "HOW IT WAS PLAYED", "s", FAINT)
        y += 30
        if beh == "pending":
            ui.text(x + 20, y, "still being checked…", "m", DIM)
            y += 34
        elif beh is None or beh == "none":
            ui.text(x + 20, y, "not checked (no capture of this run)", "m", DIM)
            y += 34
        else:
            names = {"instant_lock": "Instant lock", "smoothing": "Aim smoothing", "triggerbot": "Triggerbot",
                     "reversal": "Turning with a target", "clockwork": "Reaction spread", "machine_path": "Machine-made turns", "anti_recoil": "Anti-recoil"}
            for k, d in beh.items():
                lv = d.get("level")
                c = BAD if lv == "flag" else (WARN if lv == "suspect" else GOOD)
                ui.text(x + 20, y, names.get(k, k), "m", WHITE)
                ui.text(x + 330, y, {"flag": "cheat-like", "suspect": "unusual", "ok": "human"}.get(lv, lv), "m", c)
                ui.text(x + 520, y, f"{d.get('say', '')}  (from {d.get('n')})", "s", DIM)
                y += 34
            if not beh:
                ui.text(x + 20, y, "too little in this run to judge", "m", DIM)
                y += 34
        y += 16
        inp = i.get("input")
        bits = [BUILD.get(i.get("build"), str(i.get("build")))]
        if i.get("capture_hidden") is not None:
            bits.append("hidden from screen capture" if i.get("capture_hidden") else "visible to screen capture")
        if i.get("clock_drift") is not None:
            bits.append(f"clock within {i['clock_drift'] * 100:.2f}%")
        if inp:
            bits.append(f"{inp.get('injected_motion', 0)} software-made mouse reports, {inp.get('mice_moving', 0)} mouse moving")
        seal_state = rec.get("_seal")
        if seal_state:
            bits.append({"ok": "log line sealed and intact", "unsealed": "log line from before seals",
                         "edited": "log line EDITED by hand", "stripped": "log line's seal REMOVED",
                         "gap": "a run before this one is missing from the log"}.get(seal_state, seal_state))
        ui.para(x, y, "   ·   ".join(bits), "s", FAINT, w)
        ui.para(x, 900, "What is checked: where every mouse report and key came from; cheat, macro and debugging "
                "programs running; code loaded into AimTuner; the game's clock; the window hidden from screen "
                "capture; and how the run was played, against how people play. It cannot see a cheat it has no "
                "trace of - nothing on your own computer can be made impossible to fool - but it catches the "
                "off-the-shelf ones.", "s", DIM, w)
        _footer(ui, "B  back")


class CheckCode(Screen):
    """Paste a friend's share code: what they played, how it went, and what the anti-cheat said."""

    decor = False

    def __init__(self, app):
        super().__init__(app)
        self.buf = ""
        self.body = None
        self.err = None

    @property
    def text_mode(self):
        return True

    def update(self, nav, now):
        app = self.app
        if "paste" in app.keys:
            try:
                import glfw

                clip = glfw.get_clipboard_string(app.win)
                if clip:
                    self.buf = (clip.decode("utf-8", "replace") if isinstance(clip, bytes) else str(clip)).strip()[:4000]
                    self._read()
            except Exception:
                pass
            return
        for ch in app.typed:
            if len(self.buf) < 4000 and (ch.isalnum() or ch in "-_"):
                self.buf += ch
        if "backspace" in app.keys and self.buf:
            self.buf = self.buf[:-1]
            self.body = self.err = None
        if "enter" in app.keys or "accept" in nav:
            self._read()
        if "esc" in app.keys or "back" in nav:
            app.pop()

    def _read(self):
        self.body, self.err = seal.read_code(self.buf) if self.buf else (None, "paste a code first (Ctrl+V)")

    def draw(self, ui):
        _header(ui, "CHECK A SHARE CODE", "a friend's run, as they shared it - and what the anti-cheat said about it")
        x, w = 96.0, ui.W - 192
        ui.rect(x, 200, w, 56, (0.0, 0.0, 0.0, 0.5))
        ui.frame(x, 200, w, 56, ACCENT)
        shown = self.buf if len(self.buf) < 90 else self.buf[:44] + " … " + self.buf[-40:]
        caret = "|" if int(time.perf_counter() * 2) % 2 else ""
        ui.text(x + 16, 212, (shown or "paste the code here with Ctrl+V") + caret, "m", WHITE if shown else FAINT)
        y = 300
        if self.err:
            ui.text(x, y, "NOT VALID", "xl", BAD)
            ui.text(x, y + 74, self.err, "l", DIM)
        elif self.body:
            b = self.body
            word, col = LOOK.get(b.get("v"), ("NOT CHECKED", DIM))
            ui.text(x, y, f"{b.get('n') or b.get('s')}", "xl", WHITE)
            ui.text(x + w, y + 14, word, "xl", col, 2)
            ui.text(x, y + 74, f"{b.get('c')}  ·  {'mouse and keyboard' if b.get('k') == 'kbm' else 'controller'}"
                    f"  ·  {b.get('t')}  ·  {b.get('d')} s" + ("  ·  endless" if b.get("e") else ""), "m", DIM)
            scn = tracks.BY_ID.get(b.get("s"))
            y += 130
            if scn is not None:
                for key, label, unit, _better, dec in scn.metrics[:6]:
                    v = (b.get("m") or {}).get(key)
                    if v is None:
                        continue
                    ui.text(x, y, label, "m", WHITE if key == scn.primary else DIM)
                    ui.text(x + 520, y, stats.fmt(v, dec, unit), "m", WHITE, 2)
                    y += 36
            y += 20
            reasons = b.get("w") or []
            if b.get("o"):
                reasons = ["its player said it was them and counted it anyway"] + reasons
            for r in reasons[:5]:
                y = ui.para(x, y, "·  " + r, "m", BAD if b.get("v") == "flagged" else WARN, w) + 4
            build = BUILD.get(b.get("b"), str(b.get("b")))
            log = {"ok": "", "edited": "  ·  its log line had been EDITED", "stripped": "  ·  its log line's seal was removed"}.get(b.get("l"), "")
            ui.text(x, max(y + 20, 840), f"made by AimTuner {b.get('a')}  ·  {build}  ·  settings {b.get('f')}{log}", "s", FAINT)
            ui.text(x, max(y + 46, 866), "Same settings fingerprint as yours = the same difficulty: the scores compare directly.",
                    "s", FAINT)
        else:
            ui.para(x, y, "On a run's results, C copies its share code. Your friend sends it to you; paste it here. "
                    "A code that was changed after AimTuner made it - a better score typed in - is caught.",
                    "m", DIM, w)
        _footer(ui, "Ctrl+V  paste        Enter  check        Esc  back")
