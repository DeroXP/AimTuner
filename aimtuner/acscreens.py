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
                "programs running; code loaded into Core; the game's clock; the window hidden from screen "
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
        self.pc = None

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
            self.body = self.err = self.pc = None
        if "enter" in app.keys or "accept" in nav:
            self._read()
        if "esc" in app.keys or "back" in nav:
            app.pop()

    def _read(self):
        self.pc = None
        if not self.buf:
            self.body, self.err = None, "paste a code first (Ctrl+V)"
        elif self.buf.strip().startswith(seal.PC_PREFIX):
            self.pc, self.err = seal.read_pc_code(self.buf)
            self.body = None
        else:
            self.body, self.err = seal.read_code(self.buf)

    def draw(self, ui):
        _header(ui, "CHECK A CODE", "a friend's run or PC check, as they shared it - and what Core's anti-cheat said")
        x, w = 96.0, ui.W - 192
        ui.rect(x, 200, w, 56, (0.0, 0.0, 0.0, 0.5))
        ui.frame(x, 200, w, 56, ACCENT)
        shown = self.buf if len(self.buf) < 90 else self.buf[:44] + " … " + self.buf[-40:]
        caret = "|" if int(time.perf_counter() * 2) % 2 else ""
        ui.text(x + 16, 212, (shown or "paste the code here with Ctrl+V") + caret, "m", WHITE if shown else FAINT)
        y = 300
        if getattr(self, "pc", None):
            self._draw_pc(ui, x, w, y)
        elif self.err:
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
            if b.get("u"):
                reasons = ["made by a copy of Core that is not the published build: its result cannot be vouched for"] + reasons
            if b.get("o"):
                reasons = ["its player said it was them and counted it anyway"] + reasons
            for r in reasons[:5]:
                y = ui.para(x, y, "·  " + r, "m", BAD if b.get("v") == "flagged" else WARN, w) + 4
            build = BUILD.get(b.get("b"), str(b.get("b")))
            pcs = str(b.get("p") or "none")
            build += "  ·  PC check " + {"passed": "passed", "passed+admin": "passed (with admin rights)",
                                         "failed": "FAILED"}.get(pcs, "not run" if pcs.startswith("needed") or pcs == "none" else pcs)
            log = {"ok": "", "edited": "  ·  its log line had been EDITED", "stripped": "  ·  its log line's seal was removed"}.get(b.get("l"), "")
            ui.text(x, max(y + 20, 840), f"made by Core {b.get('a')}  ·  {build}  ·  settings {b.get('f')}{log}", "s", FAINT)
            ui.text(x, max(y + 46, 866), "Same settings fingerprint as yours = the same difficulty: the scores compare directly.",
                    "s", FAINT)
        else:
            ui.para(x, y, "On a run's results, C copies its share code. The Core PC check gives a code too. Your "
                    "friend sends it to you; paste it here. A code changed after Core made it is caught.",
                    "m", DIM, w)
        _footer(ui, "Ctrl+V  paste        Enter  check        Esc  back")

    def _draw_pc(self, ui, x, w, y):
        b = self.pc
        passed = b.get("v")
        ui.text(x, y, "PC CHECK", "xl", WHITE)
        ui.text(x + w, y + 14, "PASSED" if passed else "FAILED", "xl", GOOD if passed else BAD, 2)
        ui.text(x, y + 74, f"Core {b.get('a')}  ·  {b.get('t')}  ·  "
                + ("with admin rights" if b.get("e") else "without admin rights")
                + (f"  ·  {b.get('s')} s" if b.get("s") is not None else ""), "m", DIM)
        y += 130
        finds = b.get("f") or []
        if not finds:
            ui.text(x, y, "Nothing found.", "l", GOOD)
        for f in finds[:11]:
            lvl, what = f[0], f[1]
            kind, game = (f[3], f[4]) if len(f) >= 5 else ("", "")
            c = BAD if lvl == "fail" else WARN
            ui.text(x, y, "FAIL" if lvl == "fail" else "note", "m", c)
            head = (kind.upper() + ("" if game in ("", "any game", None) else f"  ·  {game}")) if kind else ""
            if head:
                ui.text(x + 90, y, head, "m", c)
                ui.text(x + 90 + ui.width(head, "m") + 20, y, what, "s", DIM)
            else:
                ui.text(x + 90, y, what, "m", WHITE)
            y += 40
        if b.get("u"):
            ui.text(x, max(y + 20, 900), "made by a copy of Core that is not the published build: its result cannot be vouched for",
                    "s", WARN)
        ui.text(x, 940, f"looked at {len(b.get('c') or [])} sources on PC {b.get('m', '')}", "s", FAINT)


class PCCheckScreen(Screen):
    """The PC check: what it looks at, asking first; then it runs, then the verdict."""

    decor = False

    def __init__(self, app, first=False):
        super().__init__(app)
        self.first = first
        self.state = "intro" if (first or app.pc_record is None) else "result"
        self.res = app.pc_record
        self.step, self.frac = "", 0.0
        self.note = ""
        self.thread = None
        self.top = 0

    def _run(self, admin: bool):
        import os
        import threading

        from . import __version__
        from .ac import pccheck

        def work():
            res = None
            if admin:
                out = self.app.pc_path + ".admin"
                self.step = "waiting for Windows to allow admin rights"
                if pccheck.run_elevated(out):
                    res = pccheck.read_elevated(out)
                if res is None:
                    self.note = "Admin rights were not given, so it checked without them."
            if res is None:
                res = pccheck.Scan(elevated=False, progress=self._progress).run()
            self.res = self.app.pc_record = pccheck.save(self.app.pc_path, res, __version__)
            self.app.pc_status = "passed" if res["passed"] else "failed"
            self.state = "result"

        self.state = "running"
        self.thread = threading.Thread(target=work, name="pccheck", daemon=True)
        self.thread.start()

    def _progress(self, text, frac):
        self.step, self.frac = text, frac

    def update(self, nav, now):
        if self.state == "running":
            return
        if self.state == "intro":
            if "accept" in nav:
                self._run(True)
            elif "x" in nav:
                self._run(False)
            elif "back" in nav:
                self.app.pop()
            return
        if "up" in nav:
            self.top = max(self.top - 1, 0)
        if "down" in nav:
            self.top += 1
        if "x" in nav:
            self.state = "intro"
        elif nav & {"back", "accept"}:
            self.app.pop()

    def draw(self, ui):
        x, w = 96.0, ui.W - 192
        _header(ui, "PC CHECK", "a look at this PC for cheats, and for traces of hiding them")
        if self.state == "intro":
            y = ui.para(x, 200, "Done once when Core is installed or updated. If it passes, your runs can be verified; "
                        "if it fails or is skipped, every run is unverified (you can still play and practise).", "l", WHITE, w) + 24
            y = ui.para(x, y, "It looks at: program history (UserAssist, MuiCache, BAM, Prefetch), installed programs, "
                        "services and drivers, mice and keyboards (by USB id), Desktop, Downloads, Documents and the "
                        "temp folder, folders opened, the recycle bin, scripts for Logitech G HUB and AutoHotkey, "
                        "Python packages, and signs of traces being wiped (event logs cleared, the change journal "
                        "deleted, trace services switched off, cleaning commands).", "m", DIM, w) + 18
            y = ui.para(x, y, "Private: nothing is sent anywhere, and nothing is kept but what matches the list of cheat "
                        "and bypass names. It takes a few seconds.", "m", GOOD, w) + 18
            ui.para(x, y, "With admin rights it can also read the full program history (BAM, Prefetch), recently "
                    "deleted files and the security log - Windows will ask. Without them it checks the rest.", "m", DIM, w)
            _footer(ui, "A  check (with admin rights)        X  check without admin rights        B  "
                    + ("not now" if self.first else "back"))
            return
        if self.state == "running":
            ui.text(x, 260, "Checking…", "xl", WHITE)
            ui.text(x, 340, self.step, "l", DIM)
            ui.rect(x, 400, 600, 8, (1.0, 1.0, 1.0, 0.1))
            ui.rect(x, 400, 600 * self.frac, 8, ACCENT)
            return
        r = self.res or {}
        ok = r.get("passed")
        ui.text(x, 196, "PASSED" if ok else "FAILED", "xl", GOOD if ok else BAD)
        ui.text(x + 260, 214, f"{r.get('time', '')}  ·  Core {r.get('app', '')}  ·  "
                + ("with admin rights" if r.get("elevated") else "without admin rights")
                + (f"  ·  {r.get('seconds')} s" if r.get("seconds") is not None else ""), "m", DIM)
        y = 280
        if self.note:
            ui.text(x, y, self.note, "m", WARN)
            y += 36
        if r.get("failed_at_start"):
            ui.text(x, y, f"Failed when Core started on {r['failed_at_start']} - something was found since the check.", "m", BAD)
            y += 36
        finds = r.get("findings") or []
        if not finds:
            ui.text(x, y, "Nothing found.", "l", GOOD)
            y += 50
        rows = finds[self.top:self.top + 12]
        for f in rows:
            lvl, what, where = f[0], f[1], f[2]
            kind, game = (f[3], f[4]) if len(f) >= 5 else (None, None)
            ui.text(x, y, "FAIL" if lvl == "fail" else "note", "m", BAD if lvl == "fail" else WARN)
            if kind:
                # most likely what it is, and for which game - then what found it, and where
                head = kind.upper() + ("" if game in (None, "any game") else f"  ·  {game}")
                ui.text(x + 90, y, head, "m", BAD if lvl == "fail" else WARN)
                ui.text(x + 90 + ui.width(head, "m") + 24, y, what, "m", WHITE)
            else:
                ui.text(x + 90, y, what, "m", WHITE)
            short = where if len(where) < 110 else "…" + where[-108:]
            ui.text(x + 90, y + 28, short, "s", FAINT)
            y += 62
        if finds and any(len(f) >= 5 for f in finds):
            kinds = {}
            for f in finds:
                if f[0] == "fail" and len(f) >= 5:
                    kinds.setdefault(f[3], set()).add(f[4])
            if kinds:
                def one(k, g):
                    if g <= {"any game"}:
                        return k                    # not about one game (hiding traces, an input device...)
                    named = sorted(g - {"any game", "game unknown"})
                    return f"{k} ({', '.join(named) if named else 'game unknown'})"

                summary = "  ·  ".join(one(k, g) for k, g in kinds.items())
                ui.text(x, 880, "Most likely: " + summary, "m", BAD)
        if len(finds) > self.top + 12:
            ui.text(x, y, f"▼ {len(finds) - self.top - 12} more", "s", FAINT)
        cov = r.get("coverage") or {}
        ui.para(x, 930, "Looked at: " + "  ·  ".join(f"{k} {v}" for k, v in cov.items()), "s", FAINT, w)
        _footer(ui, "Up / Down  scroll        X  check again        B  back")
