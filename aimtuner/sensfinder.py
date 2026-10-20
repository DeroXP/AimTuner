"""Sensitivity finder: which value on the game's scale do you actually play best at?

It plays one short drill over and over at a spread of values, without telling you
which value is which (knowing would bias you), in a shuffled order that is then
mirrored so warming up or tiring cannot favour one value. A curve is fitted
through the scores and its peak is the answer (see sensfit.py), reported with
the range the data can really support.

Two passes, because the game has two numbers:
  hip  - stick sensitivity. The drill is all big turns. While it varies, the ADS
         multiplier is counter-adjusted so your ADS speed stays exactly what it
         was; otherwise both would change at once.
  ads  - the ADS multiplier, at the stick sensitivity you settled on. The drill
         is acquiring and tracking a strafing target through the sights.

Aim assist is on in both, in whichever style is selected, because that is how
the game is played and it changes which sensitivity is controllable.

On mouse and keyboard the same two passes search a mouse's two numbers instead:
  hip  - mouse sensitivity, as a distance: centimetres of mouse travel for one
         full turn. That is the one unit that means the same in every game, and
         it is what both tracks are set to when you apply. LOWER IS FASTER. The
         ADS multiplier is left alone while it varies: on a mouse it is a ratio
         to this, so the sights speed up and slow down with it, as in the game.
  ads  - the Call of Duty track's mouse ADS multiplier, at the sensitivity you
         settled on.
There is no aim assist in either, because a mouse never gets any (codkit.NoAssist).

The drills are the Call of Duty track's by default. LB / RB on the first screen
switches to the Siege track's (DRILLS): the same two drills with a Siege gun,
Siege's distances and no aim assist for anyone, for a sensitivity to take there.

Sessions add up. Every block is logged with the conditions it was played under
(_context: the controller or mouse, the drills, the calibration, the aim-assist
style ...), and a finder opens with every earlier block played under the same
ones already counted, for both passes - so a round today narrows the answer that
last week's rounds began. Two things make that honest:
  * each session gets a level of its own in the fit (sensfit), so a good day or a
    bad one cannot pass for a better or a worse sensitivity;
  * an ADS block tested a speed through the sights, and its setting only means
    that speed at the hip sensitivity it was played at. Each one is logged with
    the speed, and is placed on today's scale by it (_placed).
Y on the first screen starts the selected pass over. Nothing is deleted: a line in
the log marks the spot, and blocks before it are no longer counted.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import random
import time

from . import codkit, codscn, config, r6kit, r6scn, sens, sensfit
from .theme import ACCENT, DIM, FAINT, GOOD, WARN, WHITE, alpha
from .uikit import ROW, Screen, _footer, _header, _row

BLOCK_S = 20.0
# Whose drills are played. The best sensitivity is not the same number with aim assist as without
# it (slowdown lets a thumb control more speed), so there is a set for each game: the Call of Duty
# track's, with its aim assist on a controller, and the Siege track's, with none.
DRILLS = {"cod": (codscn.SensTurn, codscn.SensStrafe), "r6": (r6scn.SensTurn, r6scn.SensStrafe)}
TRACK = {"cod": "Call of Duty", "r6": "Siege"}
# Whichever drills are played, the numbers searched are whichever track's the
# controller (or the mouse) takes its sensitivity from - see sens.FINDER and phases() below. These
# are the two passes for a controller on the Call of Duty track's own scale...
PHASES = {
    "hip": {"name": "Stick sensitivity", "key": "cod.sens_h", "lo": 0.10, "hi": 4.00, "drill": codscn.SensTurn,
            "what": "Big turns from the hip onto dummies beside and behind you."},
    "ads": {"name": "ADS multiplier", "key": "cod.ads_mult", "lo": 0.10, "hi": 2.00, "drill": codscn.SensStrafe,
            "what": "Picking up and tracking a strafing dummy through the sights."},
}
# ...and for a mouse on it.
MOUSE_PHASES = {
    "hip": {"name": "Mouse sensitivity", "lo": 5.0, "hi": 150.0, "drill": codscn.SensTurn,
            "what": "Big turns onto dummies beside and behind you.",
            "digits": 1, "fmt": "{:.1f} cm", "num": "{:.1f}", "axis": "cm per full turn (further right is slower)",
            "edge": ("or fewer - it was the fastest tested", "or more - it was the slowest tested"),
            "end": ("the fastest this goes", "the slowest this goes")},
    "ads": {"name": "ADS multiplier", "key": "cod.mouse_ads", "lo": 0.10, "hi": 3.00, "drill": codscn.SensStrafe,
            "what": "Picking up and tracking a strafing dummy through the sights."},
}
# what a pass says about its values unless it has words of its own (low end, high end)
_WORDS = {"digits": 2, "fmt": "{:.2f}", "num": "{:.2f}",
          "edge": ("or lower - it was the lowest value tested", "or higher - it was the highest value tested"),
          "end": ("the bottom of the scale", "the top of the scale")}
AGREE = 0.05        # log units: the tracks' mouse sensitivities count as the same within about 5%
SAME = 0.002        # log units: sights turning within 0.2% of how they turned in a block are the same speed
MANY = 12           # more distinct values than this on a chart are averaged in groups, not one by one


def phases(kbm: bool, dialect: str, track: str = "cod") -> dict:
    """The two passes: played with `track`'s drills, searching the settings of the track `dialect`
    (the one whose sensitivity is in use)."""
    base = MOUSE_PHASES if kbm else PHASES
    if dialect == "cod" and track == "cod":
        return base
    out = {ph: dict(base[ph]) for ph in base}
    if dialect != "cod":
        spec = sens.FINDER["kbm" if kbm else "pad"][dialect]
        for ph in ("ads",) if kbm else ("hip", "ads"):
            key, lo, hi, digits, fmt = spec[ph]
            out[ph].update(key=key, lo=lo, hi=hi, digits=digits, fmt=fmt, num="{:.%df}" % digits)
    if dialect == "r6":
        out["ads"]["name"] = "ADS sensitivity"
    out["hip"]["drill"], out["ads"]["drill"] = DRILLS[track]
    return out


def _path(log_dir):
    return os.path.join(log_dir, "sens.jsonl")


def load(log_dir) -> list:
    out = []
    try:
        with open(_path(log_dir), "r", encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return out


def stacked(recs: list, context: dict, phase: str) -> list:
    """The blocks in the log that count towards a pass under these conditions: every session's,
    back to the last time that search was started over (a line with "reset" in it)."""
    out: list = []
    for r in recs:
        if r.get("phase") != phase or r.get("context") != context:
            continue
        if r.get("reset"):
            out = []
        elif "value" in r and "perf" in r:
            out.append(r)
    return out


def rounds_in(blocks: list) -> int:
    """How many rounds those blocks amount to. One stopped before half-way is not counted."""
    seen: dict = {}
    for r in blocks:
        k = (r.get("session"), r.get("round", 0))
        seen[k] = seen.get(k, 0) + 1
    return sum(1 for n in seen.values() if n >= 5)


def sessions_in(blocks: list) -> int:
    return len({r.get("session") for r in blocks})


def averages(points: list, scores: list) -> list:
    """(value, mean score) for a chart's green dots: one per value tested - or, once sessions have
    piled up more values than can be told apart, one per group of neighbouring values."""
    by: dict = {}
    for p, s in zip(points, scores):
        by.setdefault(p[0], []).append(s)
    if len(by) <= MANY:
        return [(v, sum(ps) / len(ps)) for v, ps in sorted(by.items())]
    lo, hi = math.log(min(by)), math.log(max(by))
    bins: dict = {}
    for p, s in zip(points, scores):
        k = min(int((math.log(p[0]) - lo) / max(hi - lo, 1e-9) * MANY), MANY - 1)
        bins.setdefault(k, []).append((math.log(p[0]), s))
    return [(math.exp(sum(x for x, _ in b) / len(b)), sum(s for _, s in b) / len(b)) for _, b in sorted(bins.items())]


def performance(stats: dict) -> float:
    """One number for a block: damage per second, discounted for missed shots."""
    t = max(stats.get("duration_s") or BLOCK_S, 1e-6)
    return stats.get("damage", 0) / t * (0.4 + 0.6 * (stats.get("accuracy_pct") or 0.0) / 100.0)


def mouse_now(app, track: str = "cod") -> tuple:
    """(centimetres per turn you are on with a mouse, the track that says so - or None when they agree).

    Each track keeps its own mouse sensitivity. Where they differ, "what you are on" is the one
    your most recent mouse run was played with (or, if there has been none, the track whose drills
    the finder is playing)."""
    cfg = app.cfg
    if cfg["sens"]["kbm"] in config.DIALECTS:       # every track follows one of them: that one is the answer
        return sens.mouse(cfg, track).cm360(cfg), None
    cms = {t: sens.own_mouse_cm(cfg, t) for t in config.DIALECTS}
    mid = sorted(cms.values())[1]
    if max(abs(math.log(cm / mid)) for cm in cms.values()) <= AGREE:    # all within 5% of the middle one
        return cms["core"], None
    for r in reversed(app.log.runs):
        if (r.get("input") or {}).get("kind") == "kbm":
            t = r.get("track") if r.get("track") in cms else track
            return cms[t], t
    return cms[track], track


class SensFinder(Screen):
    decor = False

    def __init__(self, app, track: str | None = None):
        super().__init__(app)
        c = app.cfg["cod"]
        # which device it is searching for; a finder is for one or the other from start to finish
        self.kbm = config.kind(app.cfg) == "kbm"
        kind_ = "kbm" if self.kbm else "pad"
        # whose drills it plays: Call of Duty's unless asked for Siege's (or everything follows Siege)
        self.track = track if track in DRILLS else ("r6" if app.cfg["sens"][kind_] == "r6" else "cod")
        # whose settings it searches: the track this device's sensitivity comes from - the drills'
        # own track, unless every track follows another's
        self.dialect = config.sens_source(app.cfg, kind_, self.track)
        self.phases = phases(self.kbm, self.dialect, self.track)
        self.phase = "hip"
        self.state = "intro"
        self.session = time.strftime("%Y%m%d-%H%M%S")
        self.blocks = {"hip": [], "ads": []}    # every block that counts: earlier sessions' and this one's
        self.before = {"hip": 0, "ads": 0}      # how many of them were there when this session began
        self.pts = {"hip": [], "ads": []}       # the same blocks as the fit takes them (see _points)
        self.rounds = {"hip": 0, "ads": 0}
        self.level = {"hip": 0, "ads": 0}       # how far each search has closed in (see sensfit.plan)
        self.fits = {"hip": None, "ads": None}
        self.confirm = None                     # the pass Y was pressed on once: again starts it over
        self.queue: list = []
        self.round_len = 0
        self.rng = random.Random()
        self.src = None
        if self.kbm:
            # what the player came in with, in centimetres per turn (and which track said so, if they differ)
            cm, self.src = mouse_now(app, self.track)
            self.base = {"cm": cm}
        else:
            # what the player came in with: the hip pass holds ADS speed (deg/s) at this
            now = sens.pad(app.cfg, self.track)
            self.base = {"ads_speed": now.rate_h * now.ads}
        self.msg = ""
        self._load()

    # ---- words --------------------------------------------------------------
    def _w(self, key, phase=None):
        return self.phases[phase or self.phase].get(key, _WORDS.get(key))

    def _fmt(self, v, phase=None) -> str:
        return self._w("fmt", phase).format(v)

    def _key(self, name: str) -> str:
        return {"A": "Enter", "B": "Esc"}.get(name, name) if self.kbm else name

    # ---- data ---------------------------------------------------------------
    def _context(self) -> dict:
        cfg = self.app.cfg
        c = cfg["cod"]
        if self.track == "r6":          # Siege's drills: what matters is the gun and the view, not Call of Duty's settings
            r = cfg["r6"]
            return {"controller": cfg["active_profile"], "drills": "r6", "input": "kbm" if self.kbm else "pad",
                    "dialect": self.dialect, "dpi": cfg["mouse"]["dpi"] if self.kbm else None, "weapon": r["weapon"],
                    "sight": r6kit.sight_of(r), "barrel": r["barrel"], "grip": r6kit.grip_of(r), "fov": r["fov"],
                    "model": config.R6_MODEL}
        if self.kbm:
            return {"controller": cfg["active_profile"], "input": "kbm", "dpi": cfg["mouse"]["dpi"],
                    "dialect": self.dialect, "mdc": c["mouse_mdc"], "fov": c["fov"], "ads_fov": c["ads_fov"],
                    "model": config.COD_MODEL}
        if self.dialect != "cod":       # another track's stick settings: its values mean nothing on this scale
            return {"controller": cfg["active_profile"], "dialect": self.dialect, "aa_style": c["aa_style"],
                    "model": config.COD_MODEL}
        per_unit, ratio = config.cod_rates(cfg)
        return {"controller": cfg["active_profile"], "curve": c["curve"], "aa_style": c["aa_style"],
                "per_unit": round(per_unit, 1), "ads_ratio": round(ratio, 3),
                "dz": [c["dz_r_min"], c["dz_r_max"]], "model": config.COD_MODEL}

    def _load(self):
        """Count every earlier session on this pad (or mouse) and these settings: both searches carry
        on from where they had got to, and this session's blocks are fitted together with theirs."""
        recs, ctx = load(self.app.log.dir), self._context()
        for ph, spec in self.phases.items():
            b = self.blocks[ph] = stacked(recs, ctx, ph)
            self.before[ph] = len(b)
            self.rounds[ph] = rounds_in(b)
            self.level[ph] = max((r.get("level", 0) for r in b), default=0)
            self._refit(ph)
            self.level[ph] = sensfit.next_level(self.level[ph], self.fits[ph], spec["lo"], spec["hi"])

    def _write(self, rec: dict):
        try:
            os.makedirs(self.app.log.dir, exist_ok=True)
            with open(_path(self.app.log.dir), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except OSError as e:
            print(f"sens: could not write to the log: {e}")

    def _start_over(self):
        """Stop counting what has been played for the selected pass. The blocks stay in the log; a
        line after them says the search began again here, and `stacked` reads no further back."""
        ph = self.phase
        n = len(self.blocks[ph])
        self._write({"time": datetime.datetime.now().isoformat(timespec="seconds"), "session": self.session,
                     "phase": ph, "reset": True, "context": self._context()})
        self.blocks[ph], self.pts[ph], self.fits[ph] = [], [], None
        self.before[ph] = self.rounds[ph] = self.level[ph] = 0
        self.msg = (f"{self.phases[ph]['name']} search started over. The {n} blocks played before are still in "
                    "logs/sens.jsonl, but no longer counted.")

    def _value_now(self, phase) -> float:
        if self.kbm and phase == "hip":
            return self.base["cm"]
        return config.get(self.app.cfg, self.phases[phase]["key"])

    def _settings_for(self, v: float) -> dict:
        """What a block at value `v` plays with: trial values for sens.py (see its notes), saved nowhere."""
        if self.kbm:
            if self.phase == "hip":         # v is centimetres per turn, whichever track's scale is in use
                return {"cm360": v}
            return {"cm360": self.base["cm"], "ads": v}
        if self.phase == "hip":
            return {"hip": v, "ads_speed": self.base["ads_speed"]}      # ADS speed unchanged
        return {"ads": v}

    def _sights(self, value: float) -> float:
        """Mouse: how much slower the view turns per count through the sights the drills use, with
        the ADS setting at `value`."""
        cfg = self.app.cfg
        if self.track == "r6":
            fov, zoom = r6kit.hfov(cfg["r6"]), r6kit.zoom_of(cfg["r6"])
        else:
            fov, zoom = float(cfg["cod"]["fov"]), codkit.sight_zoom(cfg["cod"])
        return sens.mouse(cfg, self.track, {"ads": value}).ads(fov, zoom)

    def _sights_at(self, value: float) -> float:
        """How the view turns through the sights with the ADS setting at `value` and hip sensitivity
        as it is now: degrees per second at full stick on a controller, centimetres of travel per
        turn on a mouse. That - not the setting - is what an ADS block tested."""
        if self.kbm:
            return self.base["cm"] / self._sights(value)
        p = sens.pad(self.app.cfg, self.track, {"ads": value})
        return p.rate_h * p.ads

    def _placed(self, rec: dict, memo: dict):
        """Where an ADS block belongs on today's scale: the setting that turns the sights the way they
        turned in that block. That is the number it was played at unless hip sensitivity has changed
        since - then the old number would stand for another speed. None when no setting on the scale
        gives that speed (or the block is from before blocks were logged with it)."""
        was, v = rec.get("sights"), rec["value"]
        if not was:
            return None
        if (v, was) not in memo:
            ph = self.phases["ads"]
            lo, hi = ph["lo"], ph["hi"]
            at_lo, at_hi = self._sights_at(lo), self._sights_at(hi)
            if abs(math.log(self._sights_at(v) / was)) < SAME:
                memo[v, was] = v
            elif not min(at_lo, at_hi) <= was <= max(at_lo, at_hi):
                memo[v, was] = None
            else:
                rising = at_hi > at_lo          # degrees per second rise with the setting; centimetres per turn fall
                for _ in range(48):
                    mid = 0.5 * (lo + hi)
                    if (self._sights_at(mid) < was) == rising:
                        lo = mid
                    else:
                        hi = mid
                memo[v, was] = round(0.5 * (lo + hi), 6)
        return memo[v, was]

    def _points(self, phase) -> list:
        """Every block that counts towards a pass, as sensfit.fit takes them:
        (value on today's scale, score, order played, session)."""
        out, memo = [], {}
        for i, r in enumerate(self.blocks[phase]):
            v = r["value"] if phase == "hip" else self._placed(r, memo)
            if v is not None:
                out.append((v, r["perf"], r.get("order", i), r.get("session")))
        return out

    def _refit(self, phase):
        pts = self.pts[phase] = self._points(phase)
        self.fits[phase] = sensfit.fit(pts) if len(pts) >= 6 else None

    # ---- flow ---------------------------------------------------------------
    def _start_round(self):
        ph = self.phases[self.phase]
        f = self.fits[self.phase]
        centre = f["best"] if f else self._value_now(self.phase)
        self.queue = sensfit.plan(centre, self.level[self.phase], ph["lo"], ph["hi"], self.rng, f["side"] if f else 0,
                                  self._w("digits"))
        self.round_len = len(self.queue)
        self.state = "running"

    def _next_block(self):
        ph = self.phases[self.phase]
        if not self.queue:
            self.rounds[self.phase] += 1
            self._refit(self.phase)
            self.level[self.phase] = sensfit.next_level(self.level[self.phase], self.fits[self.phase], ph["lo"], ph["hi"])
            self.state = "result"
            self.app.sfx.play("done")
            return
        k = self.round_len - len(self.queue) + 1
        over = {"sens": self._settings_for(self.queue[0]), "duration": self.app.opts.duration or BLOCK_S,
                "count": 2.0, "title": f"{ph['name']} - block {k} of {self.round_len}",
                "on_done": self._block_done, "on_abort": self._aborted}
        if not self.app.start(ph["drill"], None, over):
            self.state = "intro"

    def _block_done(self, stats: dict):
        v = self.queue.pop(0)
        rec = {
            "time": datetime.datetime.now().isoformat(timespec="seconds"), "session": self.session,
            "phase": self.phase, "value": v, "perf": round(performance(stats), 3),
            "round": self.rounds[self.phase], "level": self.level[self.phase], "order": len(self.blocks[self.phase]),
            "settings": dict({k: round(x, 4) for k, x in self._settings_for(v).items()}, dialect=self.dialect),
            "kills": stats.get("kills"), "shots": stats.get("shots"), "hits": stats.get("hits"),
            "accuracy_pct": stats.get("accuracy_pct"), "damage": stats.get("damage"),
            "duration_s": stats.get("duration_s"), "context": self._context(),
            "device": None if self.kbm else (self.app.pad.info() or {}).get("id"),
        }
        if self.kbm:        # a mouse's sensitivity pass is in centimetres per turn, not on the game's scale
            rec["unit"] = "cm per turn" if self.phase == "hip" else "multiplier"
        if self.phase == "ads":
            # what was really tested: how the sights turned. A later session places the block by this
            # (see _placed), because the setting alone means another speed at another hip sensitivity.
            rec["sights"] = round(self._sights_at(v), 3)
            rec["sights_unit"] = "cm per turn" if self.kbm else "deg/s"
        self.blocks[self.phase].append(rec)
        self._write(rec)
        if self.queue and len(self.queue) == self.round_len // 2:
            self.state = "rest"

    def _aborted(self):
        self.queue = []
        self._refit(self.phase)
        self.state = "intro"
        self.msg = "Round stopped - the blocks you finished are kept."

    def _apply(self):
        f = self.fits[self.phase]
        if not f:
            return
        was = self.phase
        if self.kbm:
            self._apply_mouse(f["best"])
        else:
            self._apply_pad(f["best"])
        self.app.save()
        self.state = "intro"
        if was == "hip":
            self._refit("ads")      # hip sensitivity moved: the ADS blocks sit elsewhere on its scale now

    def _put(self, phase, value):
        """Store a result in the setting that pass searched: on its scale's steps, inside its range."""
        ph, digits = self.phases[phase], self._w("digits", phase)
        v = round(min(max(value, ph["lo"]), ph["hi"]), digits)
        v = int(v) if digits == 0 else v
        config.put(self.app.cfg, ph["key"], v)
        return v

    def _whose(self) -> str:
        """Which track's settings a result went into, when that needs saying."""
        kind_ = "kbm" if self.kbm else "pad"
        if self.app.cfg["sens"][kind_] in config.DIALECTS:
            return f" These are {sens.NAMES[self.dialect]}'s settings, which every track follows."
        return ""

    def _apply_pad(self, best):
        cfg = self.app.cfg
        c = cfg["cod"]
        hip, ads = self.phases["hip"], self.phases["ads"]
        if self.phase == "hip":
            spec = sens.FINDER["pad"][self.dialect]
            now = sens.pad(cfg, self.track)
            ads_speed = now.rate_h * now.ads
            old, old_v = config.get(cfg, hip["key"]), config.get(cfg, spec["v"])
            best = self._put("hip", best)
            digits = self._w("digits", "hip")
            v = round(min(max(old_v * best / old, hip["lo"]), hip["hi"]), digits)       # vertical keeps its ratio
            config.put(cfg, spec["v"], int(v) if digits == 0 else v)
            # ...and the ADS setting moves to whatever keeps the ADS speed you had, on its own scale
            want = ads_speed / sens.pad(cfg, self.track).rate_h
            lo, hi = ads["lo"], ads["hi"]
            for _ in range(40):
                mid = 0.5 * (lo + hi)
                if sens.pad(cfg, self.track, {"ads": mid}).ads < want:
                    lo = mid
                else:
                    hi = mid
            got = self._put("ads", hi)
            ended = hi - ads["lo"] < 1e-6 or ads["hi"] - hi < 1e-6
            self.msg = f"{hip['name']} set to {self._fmt(best, 'hip')}. {ads['name']} moved to {self._fmt(got, 'ads')} "
            self.msg += ("- the end of the scale, so your ADS speed has changed. Run the ADS pass next." if ended
                         else "so ADS speed is unchanged.")
            self.phase = "ads"
        else:
            best = self._put("ads", best)
            now = sens.pad(cfg, self.track)
            self.msg = f"{ads['name']} set to {self._fmt(best, 'ads')}"
            if self.dialect == "cod":
                # What was tested is an ADS speed, and the multiplier only means that speed at this stick
                # sensitivity - so the speed is what gets remembered (config.hold_ads_speed keeps it).
                c["ads_hold"] = round(best * c["sens_h"], 4)
                self.msg += (f". It is kept as a speed ({now.rate_h * now.ads:.0f}°/s): change stick sensitivity "
                             "later and the multiplier moves so that you keep it.")
            else:
                self.msg += f": {now.rate_h * now.ads:.0f}°/s through the sights."
        now = sens.pad(cfg, self.track)
        self.base = {"ads_speed": now.rate_h * now.ads}
        self.msg += self._whose()

    def _apply_mouse(self, best):
        cfg = self.app.cfg
        if self.phase == "hip":
            # A distance per turn is the same sensitivity on every track, so each gets it in its own
            # words: centimetres on the Core track, the game's own number for this DPI on the others.
            cm = round(best, 1)
            got = sens.set_mouse_cm360(cfg, cm)
            cod_cm = config.cod_mouse_cm360(cfg)
            self.msg = (f"Mouse sensitivity set to {cm:.1f} cm per turn on every track. In Call of Duty that is "
                        f"{got['cod']:.2f} at {cfg['mouse']['dpi']} DPI")
            self.msg += "" if abs(math.log(cod_cm / cm)) < 0.01 else \
                f" - the nearest its scale gets, {cod_cm:.1f} cm per turn"
            self.msg += f"; in Siege, {got['r6']:g} with the multiplier unit at {cfg['r6']['mouse_unit']:g}."
            self.base = {"cm": cm}
            self.src = None
            self.phase = "ads"
        else:
            v = self._put("ads", best)
            ads = self.phases["ads"]
            self.msg = (f"{ads['name']} set to {self._fmt(v, 'ads')}: {self.base['cm'] / self._sights(v):.1f} cm per "
                        f"turn through the {TRACK[self.track]} track's sights.")
            self.msg += self._whose() or (f" That is the {TRACK[self.track]} track's setting; the other tracks' ADS "
                                          "settings are their own and have not moved.")

    def point(self, hit, click):
        if self.state != "intro":
            return False
        if hit[0] == "phase":
            if hit[1] != self.phase:
                self.phase, self.confirm = hit[1], None
            return click
        if click:                           # the Drills line: the other game's drills, like LB / RB
            self.app.pop()
            self.app.push(SensFinder(self.app, "cod" if self.track == "r6" else "r6"))
        return False

    def update(self, nav, now):
        app = self.app
        if (config.kind(app.cfg) == "kbm") != self.kbm:
            # The input device changed under it (a click selects the mouse, a pad button the pad). The
            # two search different numbers, so nothing carries over: start again for the new one.
            app.pop()
            app.push(SensFinder(app))
            app.toast(f"Sensitivity finder restarted for {app.cfg['active_profile']}", 4.0)
            return
        if self.state == "running":
            self._next_block()
        elif self.state == "rest":
            if "accept" in nav:
                self.state = "running"
            elif "back" in nav:
                self._aborted()
        elif self.state == "result":
            if "accept" in nav:
                self._start_round()
            elif "x" in nav:
                self._apply()
            elif "back" in nav:
                self.state = "intro"
        else:
            if nav and "y" not in nav:
                self.confirm = None             # anything but a second Y keeps the blocks
            if nav & {"up", "down", "left", "right"}:
                self.phase = "ads" if self.phase == "hip" else "hip"
            if nav & {"lb", "rb"}:
                # The other game's drills. Blocks played under one say nothing about the other, so this
                # is a finder of its own (which counts the sessions played on those drills).
                app.pop()
                app.push(SensFinder(app, "cod" if self.track == "r6" else "r6"))
            elif "back" in nav:
                app.pop()
            elif "accept" in nav:
                self.msg = ""
                self._start_round()
            elif "x" in nav and self.fits[self.phase]:
                self.state = "result"
            elif "y" in nav and self.blocks[self.phase]:
                if self.confirm == self.phase:
                    self.confirm = None
                    self._start_over()
                else:
                    self.confirm = self.phase

    # ---- drawing ------------------------------------------------------------
    def draw(self, ui):
        _header(ui, "SENSITIVITY FINDER", "measured, not guessed - " +
                ("in centimetres of mouse travel per turn" if self.kbm
                 else "on " + sens.FINDER["pad"][self.dialect]["scale"]))
        if self.state == "result":
            self._draw_result(ui)
        elif self.state == "rest":
            cx = ui.W / 2
            ui.text(cx, 380, "HALFWAY", "xl", WHITE, 1)
            ui.text(cx, 460, "Shake your hands out. The second half replays the same values in reverse order.", "m", DIM, 1)
            ui.text(cx, 540, f"Press {self._key('A')} to carry on", "l", ACCENT, 1)
            _footer(ui, "A  continue        B  stop this round")
        elif self.state == "running":
            ui.text(ui.W / 2, 500, "…", "xl", DIM, 1)
        else:
            self._draw_intro(ui)

    def _draw_intro(self, ui):
        app, c, W = self.app, self.app.cfg["cod"], ui.W
        x, y, w = 96.0, 210.0, 820.0
        for ph, spec in self.phases.items():
            sel = ph == self.phase
            n = len(self.blocks[ph])
            f = self.fits[ph]
            val = f"now {self._fmt(self._value_now(ph), ph)}"
            if f:
                val += f"   ·   best so far {self._fmt(f['best'], ph)}" + (" (rough)" if self.rounds[ph] < 2 else "")
            elif n:
                val += f"   ·   {n} blocks"
            _row(ui, x, y, w, spec["name"], sel, val, hit=("phase", ph))
            y += ROW
        # whose drills: not a pass to select, a switch (LB / RB) - shown in the list so it is seen
        if self.track == "r6":
            drills = f"Siege  ·  {r6kit.gun_of(app.cfg['r6'])['name']}  ·  no aim assist"
        else:
            drills = "Call of Duty  ·  " + ("no aim assist on a mouse" if self.kbm else "aim assist on, as in the game")
        _row(ui, x, y + 6, w, "Drills", False, f"‹ {drills} ›", hit=("drills",))
        y += ROW + 6
        ph = self.phases[self.phase]
        first = self.phases["hip"]["name"].lower()
        mins = math.ceil(10 * ((app.opts.duration or BLOCK_S) + 4.0) / 60.0)
        y = ui.para(x, y + 30, f"{ph['what']} One round is ten {int(app.opts.duration or BLOCK_S)}-second blocks, "
                    f"about {mins} minutes. You are not told which value each block uses.", "m", WHITE, w - 40) + 14
        y = ui.para(x, y, f"Do {first} first, apply it, then the ADS multiplier. Play at least two rounds of "
                    "each: the first only finds the neighbourhood, every later one closes in on the peak and narrows "
                    "the range. Rounds add up from one session to the next, so a round whenever you sit down keeps "
                    "improving the answer. Play it the way you play the game: this finds the value you score best "
                    "with, which is not always the one that feels nicest at first.", "m", DIM, w - 40) + 14
        if self.kbm:
            y = ui.para(x, y, "Mouse sensitivity is searched as a distance - centimetres of mouse travel for one full "
                        "turn, so fewer is faster - and applying it sets every track. The sights follow it: the ADS "
                        "setting is a ratio, and stays where it is until its own pass.", "m", DIM, w - 40) + 14
        blocks = self.blocks[self.phase]
        if self.confirm == self.phase and blocks:
            k = sessions_in(blocks)
            ui.para(x, y + 6, f"Start the {ph['name'].lower()} search over? Its {len(blocks)} blocks from {k} "
                    f"session{'s' if k != 1 else ''} stay in the log but will no longer count. Press Y again to do "
                    "it, anything else to keep them.", "m", WARN, w - 40)
        else:
            if self.msg:
                y = ui.para(x, y + 6, self.msg, "m", GOOD, w - 40) + 10
            if self.before[self.phase]:
                old = blocks[:self.before[self.phase]]
                k = sessions_in(old)
                try:
                    d = datetime.datetime.fromisoformat(old[0]["time"])
                    since = f", the first on {d.day} {d.strftime('%B')}"
                except (KeyError, ValueError):
                    since = ""
                lost = len(blocks) - len(self.pts[self.phase])
                ui.para(x, y + 6, f"{ph['name']}: building on {len(old)} blocks from {k} earlier "
                        f"session{'s' if k != 1 else ''}{since} - every one played on this "
                        f"{'mouse profile' if self.kbm else 'controller'} with these settings."
                        + (f" ({lost} cannot be placed on today's scale and are left out.)" if lost else ""),
                        "m", DIM, w - 40)

        px = 1000.0
        pw = W - px - 96
        ui.panel(px, 190, pw, 620)
        tx, ty, tw = px + 40, 224.0, pw - 80
        ui.text(tx, ty, "YOUR SETTINGS NOW", "s", FAINT)
        follows = app.cfg["sens"]["kbm" if self.kbm else "pad"] in config.DIALECTS
        ads_name = self.phases["ads"]["name"]
        ads_now = self._fmt(self._value_now("ads"), "ads")
        if self.kbm:
            m, cm = app.cfg["mouse"], self.base["cm"]
            rows = [("Mouse sensitivity", f"{cm:.1f} cm per turn"),
                    ("On Call of Duty's scale", f"{config.cod_mouse_sens_for(app.cfg, cm):.2f} at {m['dpi']} DPI"),
                    (ads_name, ads_now),
                    ("Through the sights", f"{cm / self._sights(self._value_now('ads')):.1f} cm per turn"),
                    ("Sensitivity from", sens.NAMES[self.dialect] if follows else "each track's own settings"),
                    ("Aim assist", "none - never on a mouse"),
                    ("Profile", app.cfg["active_profile"])]
        else:
            now = sens.pad(app.cfg, self.track)
            held = "  (held)" if self.dialect == "cod" and c.get("ads_hold") else ""
            rows = [(self.phases["hip"]["name"], self._fmt(self._value_now("hip"), "hip")), (ads_name, ads_now),
                    ("Hip turn speed", f"{now.rate_h:.0f}°/s"),
                    ("ADS turn speed", f"{now.rate_h * now.ads:.0f}°/s" + held),
                    ("Sensitivity from", sens.NAMES[self.dialect] if follows else "this track's own settings"),
                    ("Aim assist", "none - Siege has none" if self.track == "r6"
                     else codkit.style_label(c["aa_style"]) + " style"),
                    ("Controller", app.cfg["active_profile"])]
        yy = ty + 34
        for k, v in rows:
            ui.text(tx, yy, k, "m", FAINT)
            ui.text(tx + tw, yy, v, "m", WHITE, 2)
            yy += 36
        if self.kbm:
            if self.src:        # the tracks disagree: say which one the search starts from, and why
                cms = {t: sens.own_mouse_cm(app.cfg, t) for t in config.DIALECTS}
                yy = ui.para(tx, yy + 20, f"Your tracks are set differently: Core {cms['core']:.1f} cm per turn, Call "
                             f"of Duty {cms['cod']:.1f} cm, Siege {cms['r6']:.1f} cm. The search starts from "
                             f"{sens.NAMES[self.src]}'s - "
                             + ("the one your last mouse run used" if any((r.get("input") or {}).get("kind") == "kbm"
                                                                           for r in app.log.runs)
                                else "these drills are that track's") + " - and applying a result sets them all.",
                             "s", WARN, tw) - 4
            ui.para(tx, yy + 24, "The centimetres are only as right as the DPI in Settings. A different mouse, mouse "
                    "pad or grip can peak at a different value. Every block is saved to logs/sens.jsonl.", "s", DIM, tw)
        else:
            ui.para(tx, yy + 24, "Results are per controller: a pad with a lighter or shorter stick can peak at a "
                    "different value. Every block is saved to logs/sens.jsonl.", "s", DIM, tw)
        hints = ("A  play a round        Up / Down  which setting        LB / RB  "
                 + ("Call of Duty" if self.track == "r6" else "Siege") + " drills")
        if self.fits[self.phase]:
            hints += "        X  see the result"
        if self.blocks[self.phase]:
            hints += "        Y  start over"
        _footer(ui, hints + "        B  back")

    def _draw_result(self, ui):
        app, W = self.app, ui.W
        ph, f, pts = self.phases[self.phase], self.fits[self.phase], self.pts[self.phase]
        fmt = self._fmt
        x = 96.0
        if not f:
            ui.text(x, 220, "Not enough blocks yet to fit a curve - play a full round.", "l", WARN)
            _footer(ui, "A  play a round        B  back")
            return
        # each block's score with its session's level taken out (its own score, if there is one session)
        scores = f.get("adj") or []
        if len(scores) != len(pts):
            scores = [p[1] for p in pts]
        several = len({p[3] for p in pts}) > 1
        now_v = self._value_now(self.phase)
        # at the edge of what was tested the answer is one-sided; at the end of the scale it is final
        beyond = f["side"] and not sensfit.pinned(f, ph["lo"], ph["hi"])
        rough = self.rounds[self.phase] < 2         # one round = five values, twice each: a first look, no more
        ui.text(x, 196, f"BEST {ph['name'].upper()}" + ("   ·   ROUGH: ONE ROUND SO FAR" if rough else ""), "s",
                WARN if rough else FAINT)
        hx = x + max(250.0, ui.text(x, 222, fmt(f["best"]), "h", ACCENT) + 40.0)
        if beyond:
            head = self._w("edge")[0 if f["side"] < 0 else 1]
        elif f["side"]:
            head = self._w("end")[0 if f["side"] < 0 else 1]
        else:
            head = f"likely between {fmt(f['lo'])} and {fmt(f['hi'])}"
        ui.text(hx, 248, head, "l", WHITE)
        ui.text(hx, 290, f"you are on {fmt(now_v)}", "m", DIM)
        y = 348
        # compare on one curve: the one near the peak if it reaches your value, else the one through everything
        near = f["span"][0] <= now_v <= f["span"][1]
        at = min(max(now_v, f["tested"][0]), f["tested"][1])
        gain = 100.0 * (sensfit.curve(f, f["best"], not near) / max(sensfit.curve(f, at, not near), 1e-9) - 1.0)
        n_ses = len({p[3] for p in pts})
        lines = [f"{len(pts)} blocks over {self.rounds[self.phase]} round(s)"
                 + (f" in {n_ses} sessions" if several else "") + "; your score moves about "
                 f"±{f['noise_pct']:.0f}% from one block to the next."]
        if several:
            lines.append("Each session is compared with itself: its general level is taken out first, so a good "
                         "day or a bad one does not move the answer.")
        if rough:
            means: dict = {}
            for p, s in zip(pts, scores):
                means.setdefault(p[0], []).append(s)
            top = max(means, key=lambda v: sum(means[v]) / len(means[v]))
            where = (f" Your highest-scoring blocks were at {fmt(top)}, which one round cannot tell from luck."
                     if abs(math.log(top / f["best"])) > 0.15 else "")
            lines.append("This is a first look from widely spaced values. The next round tests close around the peak "
                         "and often moves it - play it before you apply anything." + where)
        flat = f["plateau"]
        how_much = "under 1%" if gain < 1.0 else (f"about {gain:.0f}%" if gain <= 50.0 else "more than 50%")
        if not f["side"] and f["lo"] <= now_v <= f["hi"]:
            lines.append("Your current value is inside that range: there is no evidence it is wrong.")
        elif flat and flat[0] <= now_v <= flat[1]:
            lines.append("Your current value is outside that range but still on the flat top of the curve: the fit "
                         f"puts the gain from switching at {how_much}.")
        elif gain >= 0.5:
            lines.append(f"The fitted curve puts the best value {how_much} ahead of your current one.")
        if flat:
            lines.append(f"Anything from {fmt(flat[0])} to {fmt(flat[1])} scores within about 2% of the best - the top "
                         "of the curve is flat, so small differences will not be felt.")
        if beyond:
            lines.append("The true best may lie beyond what was tested. Play another round - it will look further "
                         "that way.")
        elif f["side"]:
            lines.append("Your scores were still improving at the end of the scale, so that end is the answer.")
        if self.kbm and self.phase == "hip":
            lines.append(f"At {app.cfg['mouse']['dpi']} DPI that is {config.cod_mouse_sens_for(app.cfg, f['best']):.2f} "
                         "on Call of Duty's mouse sensitivity scale. Applying sets every track to this distance.")
        elif self.kbm:
            cm = self.base["cm"]
            lines.append(f"At this value a full turn through the sights takes {cm / self._sights(f['best']):.1f} cm "
                         f"of mouse travel, against {cm:.1f} cm from the hip.")
        elif self.phase == "ads":
            at = sens.pad(app.cfg, self.track, {"ads": f["best"]})
            lines.append(f"At this value your ADS turn speed is {at.rate_h * at.ads:.0f}°/s."
                         + (" Applying keeps that speed, not just the number: if stick sensitivity changes later, the "
                            "multiplier moves with it." if self.dialect == "cod" else ""))
        for ln in lines:
            y = ui.para(x, y, ln, "m", DIM, 760) + 8
        follows = app.cfg["sens"]["kbm" if self.kbm else "pad"] in config.DIALECTS
        whose = f"{sens.NAMES[self.dialect]}'s settings" + (", which every track follows" if follows else "")
        if self.kbm:
            does = (" to every track and moves on to the ADS pass. Applying changes their settings fingerprints"
                    if self.phase == "hip" else f" to {whose}. Applying changes the settings fingerprint")
        else:
            does = (f" to {whose}" + (" (the ADS setting is adjusted so ADS speed stays the same) and moves on to "
                                      "the ADS pass." if self.phase == "hip" else ".")
                    + " Applying changes the settings fingerprint")
        again = "plays another round, looking past that edge." if beyond else "plays another, narrower round around the peak."
        ui.para(x, y + 14, f"{self._key('A')} {again} X applies the value{does}, so later track runs start a fresh "
                "comparison.", "s", FAINT, 760)

        # chart: every block, the fitted curve, the peak and its range
        num = self._w("num").format
        cx0, cy0, cw, ch = 920.0, 200.0, W - 920.0 - 96.0, 600.0
        ui.panel(cx0, cy0, cw, ch)
        gx0, gy0, gw, gh = cx0 + 70, cy0 + 40, cw - 110, ch - 110
        lo_v, hi_v = f["tested"][0] * 0.93, f["tested"][1] * 1.07
        p_lo, p_hi = min(scores) * 0.9, max(scores) * 1.08
        X = lambda v: gx0 + gw * (math.log(v) - math.log(lo_v)) / (math.log(hi_v) - math.log(lo_v))
        Y = lambda p: gy0 + gh * (1.0 - (min(max(p, p_lo), p_hi) - p_lo) / max(p_hi - p_lo, 1e-9))
        ui.rect(X(f["lo"]), gy0, max(X(f["hi"]) - X(f["lo"]), 2.0), gh, alpha(ACCENT, 0.12))
        ui.rect(X(f["best"]) - 1, gy0, 2, gh, ACCENT)
        if lo_v < now_v < hi_v:
            ui.rect(X(now_v) - 1, gy0, 2, gh, alpha(WHITE, 0.35))
            ui.text(X(now_v), gy0 + gh + 30, "now", "s", DIM, 1)
        ui.text(X(f["best"]), gy0 - 30, num(f["best"]), "m", ACCENT, 1)
        if f["plateau"]:
            ui.rect(X(f["plateau"][0]), gy0 + gh - 6, max(X(f["plateau"][1]) - X(f["plateau"][0]), 2.0), 6, alpha(GOOD, 0.55))
        prev = None
        c_lo, c_hi = max(lo_v, f["span"][0]), min(hi_v, f["span"][1])    # drawn only where it describes the data
        for i in range(61):
            v = c_lo * (c_hi / c_lo) ** (i / 60.0)
            p = (X(v), Y(sensfit.curve(f, v)))
            if prev:
                ui.line(prev[0], prev[1], p[0], p[1], 2.5, WHITE)
            prev = p
        for p, s in zip(pts, scores):
            ui.disc(X(p[0]), Y(s), 4, alpha(WHITE, 0.35))
        last_label = -1e9
        for v, mean in averages(pts, scores):
            ui.disc(X(v), Y(mean), 7, GOOD)
            if X(v) - last_label > 44:          # values that close together would print on top of each other
                ui.text(X(v), gy0 + gh + 8, num(v), "s", FAINT, 1)
                last_label = X(v)
        ui.rect(gx0, gy0 + gh, gw, 1, (1.0, 1.0, 1.0, 0.25))
        ui.text(cx0 + 24, gy0 - 6, "score", "s", FAINT)
        ui.text(cx0 + cw - 24, gy0 + gh + 52, (self._w("axis") or ph["name"].lower())
                + "  ·  each dot is one block" + (", levelled by session" if several else "")
                + ", green is the average" + (", green bar = within 2% of best" if f["plateau"] else ""), "s", FAINT, 2)
        _footer(ui, "A  another round        X  apply " + fmt(f["best"]) + (" anyway" if rough else "") + "        B  back")
