"""Anti-cheat: every run gets a verdict, kept in its log record ("integrity").

  verified    nothing found, and everything that can be checked was checked
  unverified  nothing that proves cheating, but something stops the run being vouched for: a
              macro tool or debugger running, an unknown DLL loaded into AimTuner, screen-capture
              protection off, a build that is not the published one, too little to judge by
  flagged     evidence of cheating: input made up by software or from a second device, a cheat
              program running, the game's clock tampered with, or play no hand produces (behavior.py)

A flagged run is kept (and shown, with its reasons) but not counted in bests, averages or
comparisons - unless its player says it was them ("count it anyway"), which is recorded too.

Three layers, cheapest first:
  input      where every mouse report and key came from (mouse.parse): software-made input has no
             device, and a second device moving the aim at the same time is an input box
  system     what is running and loaded, the clocks, and the window hidden from screen readers
  behaviour  how the run was played (behavior.py), from its capture, in the background

None of it can stop someone determined (the code is on their computer); it stops the
off-the-shelf ways, and makes the rest work.
"""
from __future__ import annotations

import threading
import time

from . import behavior, system

LEVELS = ("verified", "unverified", "flagged")


class RunGuard:
    """One run's anti-cheat, from the countdown to the verdict."""

    def __init__(self, app, game):
        self.app, self.g = app, game
        self.virtual = bool(getattr(app.mouse, "virtual", True)) or bool(getattr(app, "offscreen", False))
        self.kbm = bool(game.kbm)
        self.prov0 = app.mouse.provenance() if hasattr(app.mouse, "provenance") else None
        self.sys0 = None if self.virtual else system.snapshot()
        self.clocks = system.Clocks()
        self.hidden = False
        self.hide_failed = False
        self.result = None              # the behavioural analysis, once done
        self._thread = None

    # ---- while it runs ---------------------------------------------------------------------
    def protect(self, on: bool):
        """Hide the window from screen capture while the run is live (the setting allowing)."""
        app = self.app
        if self.virtual or not app.cfg.get("anticheat", {}).get("hide_capture", True):
            return
        hwnd = getattr(app, "hwnd", None)
        if on and not self.hidden:
            self.hidden = system.hide_from_capture(hwnd, True)
            self.hide_failed = not self.hidden
        elif not on and self.hidden:
            system.hide_from_capture(hwnd, False)
            self.hidden = False

    # ---- the verdict -------------------------------------------------------------------------
    def judge(self, rec: dict, cap) -> dict:
        """Everything but the behaviour, now; the behaviour in the background (see poll)."""
        app, cfg = self.app, self.app.cfg
        flags, doubts, notes = [], [], []
        out = {"v": 1, "checked": time.strftime("%Y-%m-%dT%H:%M:%S"), "build": getattr(app, "build_state", "source")}
        # -- input
        if self.virtual:
            doubts.append("played by the self-test (virtual input)")
        elif self.kbm and self.prov0 is not None:
            p1 = app.mouse.provenance()
            d = {k: p1[k] - self.prov0[k] for k in ("inj_motion", "inj_button", "inj_key", "absolute")}
            devs = {h: n - self.prov0["motion_devs"].get(h, 0) for h, n in p1["motion_devs"].items()}
            devs = {h: n for h, n in devs.items() if n > 0}
            keys = {k: n - self.prov0["inj_keys"].get(k, 0) for k, n in p1["inj_keys"].items()}
            bound = set(rec.get("binds", {}).values()) | set(cfg.get("kbm", {}).values())
            inj_bound = {k: n for k, n in keys.items() if n > 0 and k in bound}
            out["input"] = {"injected_motion": d["inj_motion"], "injected_buttons": d["inj_button"],
                            "injected_bound_keys": inj_bound, "absolute": d["absolute"], "mice_moving": len(devs)}
            total = sum(devs.values()) + d["inj_motion"]
            if d["inj_motion"] > 0 and d["inj_motion"] >= max(3, 0.002 * total):
                flags.append(f"mouse movement made by software ({d['inj_motion']} reports with no device behind them)")
            if d["inj_button"] > 0:
                flags.append(f"mouse clicks made by software ({d['inj_button']})")
            if inj_bound:
                flags.append("bound keys pressed by software (" + ", ".join(sorted(inj_bound)) + ")")
            if len(devs) > 1:
                ordered = sorted(devs.values(), reverse=True)
                if ordered[1] >= max(20, 0.02 * sum(ordered)):
                    flags.append(f"two devices moving the aim at once ({ordered[1]} reports from the second) - "
                                 "an input box or a second mouse")
            if d["absolute"] > 20:
                doubts.append("aim moved by an absolute-position device (a tablet, or remote control)")
        # -- system
        if not self.virtual:
            sys1 = system.snapshot()
            tools = {t[1]: t[2] for t in (self.sys0 or {}).get("tools", []) + sys1["tools"]}
            for name, kind in sorted(tools.items()):
                (flags if kind == "cheat" else doubts).append(
                    f"{name} was running" + (" (it can run recoil scripts and remap input)" if kind == "macro" else ""))
            mods = sorted(set((self.sys0 or {}).get("foreign_modules", []) + sys1["foreign_modules"]))
            if mods:
                doubts.append("unknown code loaded into AimTuner: " + ", ".join(m.split("\\")[-1] for m in mods[:4]))
                out["modules"] = mods[:12]
            if sys1["debugger"] or (self.sys0 or {}).get("debugger"):
                doubts.append("a debugger was attached")
            if sys1["interception"]:
                doubts.append("the Interception input driver is installed (it can make input look like the real mouse's)")
            drift = self.clocks.drift()
            out["clock_drift"] = round(drift, 5) if drift is not None else None
            if drift is not None:
                if drift > 0.02:
                    flags.append(f"the game's clock ran {drift * 100:.1f}% off the system's (a speed hack)")
                elif drift > 0.005:
                    doubts.append(f"the game's clock ran {drift * 100:.1f}% off the system's")
            hide = cfg.get("anticheat", {}).get("hide_capture", True)
            out["capture_hidden"] = bool(hide and not self.hide_failed)
            hooks = sorted(set((self.sys0 or {}).get("capture_hooks", []) + sys1.get("capture_hooks", [])))
            if hooks and hide:
                doubts.append("a game-capture hook (OBS) was copying the game's frames, which screen-capture "
                              "protection cannot stop: " + ", ".join(h.split("\\")[-1] for h in hooks[:3]))
            # the quick part of the PC check again: a cheat started or installed since AimTuner started
            pc = getattr(app, "pc_status", "needed")
            if pc == "passed":
                from . import pccheck

                quick = pccheck.Scan(quick=True).run()
                bad = [f for f in quick["findings"] if f[0] == "fail"]
                if bad:
                    flags.append("found during the run: " + bad[0][1])
            if not hide:
                doubts.append("screen-capture protection was off (Settings), so a screen-reading aimbot could see the game")
            elif self.hide_failed:
                doubts.append("Windows would not hide the game from screen capture")
            if out["build"] == "modified":
                flags.append("AimTuner's own files have been changed since it was published")
            elif out["build"] != "official":
                doubts.append("not the published build (run from source, or built locally)")
            out["pc_check"] = pc
            out["pc_check_admin"] = bool((getattr(app, "pc_record", None) or {}).get("elevated"))
            if pc != "passed":
                doubts.append({"failed": "this PC failed its PC check (Settings > Anti-cheat)",
                               "tampered": "this PC's PC check record was changed by hand",
                               }.get(pc, "this PC has not had its PC check"))
        out["flags"], out["doubts"], out["notes"] = flags, doubts, notes
        out["behaviour"] = "pending" if cap is not None else "none"
        if cap is None and not self.virtual:
            doubts.append("no capture of this run, so how it was played was not checked")
        self._decide(out)
        rec["integrity"] = out
        if cap is not None:
            self._thread = threading.Thread(target=self._analyse, args=(cap,), name="anticheat", daemon=True)
            self._thread.start()
        return out

    def _analyse(self, cap):
        try:
            self.result = behavior.analyse(cap)
        except Exception as e:
            self.result = {"detectors": {}, "notes": [f"analysis failed: {type(e).__name__}: {e}"]}

    def poll(self, rec) -> bool:
        """Fold the behavioural verdict into the record once it is ready. True when it just was."""
        if self.result is None or rec.get("integrity", {}).get("behaviour") != "pending":
            return False
        out = rec["integrity"]
        det = self.result.get("detectors", {})
        out["behaviour"] = {k: {"level": d["level"], "n": d["n"], "value": round(float(d["value"]), 4), "say": d["say"]}
                            for k, d in det.items()}
        strong = [d["say"] for d in det.values() if d["level"] == "flag"]
        weak = [d["say"] for d in det.values() if d["level"] == "suspect"]
        out["flags"] = out["flags"] + strong
        if len(weak) >= 2:
            out["flags"].append("several things no hand does: " + "; ".join(weak))
        elif weak:
            out["doubts"] = out["doubts"] + [weak[0]]
        if not det and "too short to judge" in self.result.get("notes", []):
            out["doubts"] = out["doubts"] + ["too short to judge how it was played"]
        self._decide(out)
        return True

    def _decide(self, out):
        if self.virtual and out["flags"]:       # the self-test's bots: reported, never thrown out of its sums
            out["doubts"] = out["doubts"] + ["(self-test) " + f for f in out["flags"]]
            out["flags"] = []
        out["status"] = "flagged" if out["flags"] else ("unverified" if out["doubts"] else "verified")


def status(rec) -> str | None:
    """A logged run's verdict, or None for runs from before the anti-cheat."""
    i = rec.get("integrity")
    return i.get("status") if isinstance(i, dict) else None


def counts(rec) -> bool:
    """Does this run count in bests, averages and comparisons?"""
    i = rec.get("integrity")
    if not isinstance(i, dict) or i.get("status") != "flagged":
        return True
    return bool(i.get("override"))
