"""Run log + the comparison maths shared by the in-app screens and analyze.py.

Every finished run is written twice:
  logs/runs.jsonl          one line per run - the summary, quick to scan and aggregate
  logs/runs/<run_id>.json  the same record plus every per-target event (and the
                           error trace for tracking runs), for digging into one run
Endless runs (no clock, played until stopped) are practice and go to their own file,
logs/endless.jsonl, so that nothing here ever compares them with timed runs.

Comparisons are honest about noise: a difference between two controllers is only
called "clear" when Welch's t-test puts it under p = 0.05 with at least four runs
each. Aim scores swing 5-10% run to run on the same pad, so a single better run
means nothing.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics

from . import tracks


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-") or "pad"


def _line(rec: dict) -> str:
    return json.dumps({k: v for k, v in rec.items() if not k.startswith("_")}, separators=(",", ":")) + "\n"


class Log:
    """The run log. `all` is every line of runs.jsonl; `runs` - what every screen counts - leaves
    out runs the anti-cheat flagged (unless their player said they were genuine) and lines whose
    seal shows they were edited by hand (ac/seal.py)."""

    def __init__(self, log_dir: str):
        from .ac import seal

        self.dir = log_dir
        self.path = os.path.join(log_dir, "runs.jsonl")
        self.all = load_runs(log_dir)
        self.endless = load_runs(log_dir, "endless.jsonl")
        seal.verify(self.all)
        seal.verify(self.endless)
        self.runs = [r for r in self.all if self.counts(r)]

    @staticmethod
    def counts(rec: dict) -> bool:
        from . import ac
        from .ac import seal

        return seal.trusted(rec) and ac.counts(rec)

    def tampered(self) -> list:
        return [r for r in self.all + self.endless if r.get("_seal") in ("edited", "stripped")]

    def save(self, rec: dict, detail: dict) -> str:
        """Append the summary line, then write the detail file. Raises OSError if
        either could not be written; the run is kept in memory regardless so the
        results and compare screens still show it."""
        from .ac import seal

        seal.seal(rec, self.all[-1] if self.all else None)
        rec["_seal"] = "ok"
        self.all.append(rec)
        if self.counts(rec):
            self.runs.append(rec)
        os.makedirs(os.path.join(self.dir, "runs"), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(_line(rec))
        full = os.path.join(self.dir, "runs", f"{rec['run_id']}_{slug(rec['controller'])}.json")
        with open(full, "w", encoding="utf-8") as f:
            json.dump(dict({k: v for k, v in rec.items() if not k.startswith("_")}, **detail), f, indent=1)
        return full

    def save_endless(self, rec: dict) -> None:
        """An endless run: one line in endless.jsonl, and not in `runs` (nothing compares it)."""
        from .ac import seal

        seal.seal(rec, self.endless[-1] if self.endless else None)
        rec["_seal"] = "ok"
        self.endless.append(rec)
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, "endless.jsonl"), "a", encoding="utf-8") as f:
            f.write(_line(rec))

    def _rewrite(self, runs: list, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in runs:
                f.write(_line(r))
        os.replace(tmp, path)

    def update(self, rec: dict) -> None:
        """A logged run changed (its anti-cheat verdict came in, or was overridden): reseal it,
        rewrite its line, and count it or not accordingly."""
        from .ac import seal

        if rec.get("_seal") in ("ok", None):
            seal.reseal(rec)
        if rec.get("endless"):
            if any(r is rec for r in self.endless):
                self._rewrite(self.endless, os.path.join(self.dir, "endless.jsonl"))
            return
        if not any(r is rec for r in self.all):
            return
        # only this run moves in or out of the counted list (it is a live list other screens hold)
        inside = any(r is rec for r in self.runs)
        if self.counts(rec) and not inside:
            self.runs.append(rec)
        elif not self.counts(rec) and inside:
            self.runs.remove(rec)
        self._rewrite(self.all, self.path)

    def rename_controller(self, old: str, new: str) -> None:
        """Renaming a profile carries its history along, or its stats would split in two."""
        from .ac import seal

        for runs, path in ((self.all, self.path), (self.endless, os.path.join(self.dir, "endless.jsonl"))):
            hit = [r for r in runs if r.get("controller") == old]
            if not hit:
                continue
            for r in hit:
                r["controller"] = new
                if r.get("_seal") == "ok":
                    seal.reseal(r)
            self._rewrite(runs, path)


def load_runs(log_dir: str, name: str = "runs.jsonl") -> list:
    out = []
    try:
        with open(os.path.join(log_dir, name), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue    # a torn last line must not hide every other run
    except FileNotFoundError:
        pass
    return out


def counted(runs: list) -> list:
    """Every run except the warm-up pass of a guided comparison, which is played to be thrown away."""
    return [r for r in runs if not (r.get("duel") or {}).get("warmup")]


# ------------------------------------------------------------------ grouping
def groups(runs: list, scenario: str, fingerprint: str | None = None) -> dict:
    """{label: [run, ...]} for one scenario, oldest first.

    A label is the controller name; if a controller was run with more than one
    Shoot/Aim bind pair those become separate columns, since "same pad, bumpers
    versus triggers" is exactly the kind of thing worth comparing.
    """
    pool = [r for r in counted(runs) if fingerprint is None or r.get("fingerprint") == fingerprint]
    # Decide the split over every scenario at once so labels stay the same from
    # one scenario's table to the next.
    binds_of: dict = {}
    for r in pool:
        b = r.get("binds", {})
        binds_of.setdefault(r.get("controller", "?"), set()).add((b.get("shoot"), b.get("ads")))
    split = any(len(v) > 1 for v in binds_of.values())
    out: dict = {}
    for r in pool:
        if r.get("scenario") != scenario:
            continue
        name = r.get("controller", "?")
        if split:
            b = r.get("binds", {})
            name = f"{name} [{b.get('shoot')}/{b.get('ads')}]"
        out.setdefault(name, []).append(r)
    return out


def scoreboard(runs: list, fingerprint: str | None = None, track: str = "core") -> dict | None:
    """Head-to-head on each scenario's primary metric, for the two most-used labels."""
    count: dict = {}
    scns = tracks.BY_TRACK[track].scenarios
    per = {s.id: groups(runs, s.id, fingerprint) for s in scns}
    for grp in per.values():
        for lb, rs in grp.items():
            count[lb] = count.get(lb, 0) + len(rs)
    if len(count) < 2:
        return None
    a, b = sorted(count, key=lambda k: -count[k])[:2]
    rows, wins, clear = [], [0, 0], [0, 0]
    for s in scns:
        grp = per[s.id]
        if not grp:
            continue
        key, label, unit, better, dec = next(m for m in s.metrics if m[0] == s.primary)
        va, vb = values(grp.get(a, []), key), values(grp.get(b, []), key)
        v = verdict(va, vb, better)
        rows.append({"scenario": s, "label": label, "unit": unit, "dec": dec, "verdict": v,
                     "a": describe(va, better), "b": describe(vb, better)})
        if v and v["winner"] is not None:
            wins[v["winner"]] += 1
            if v["conf"] == "clear":
                clear[v["winner"]] += 1
    return {"labels": [a, b], "rows": rows, "wins": wins, "clear": clear}


def values(runs: list, key: str) -> list:
    out = []
    for r in runs:
        v = r.get("stats", {}).get(key)
        if isinstance(v, (int, float)):
            out.append(float(v))
    return out


def describe(vals: list, better: int = 1) -> dict | None:
    if not vals:
        return None
    return {
        "n": len(vals), "mean": statistics.fmean(vals),
        "sd": statistics.stdev(vals) if len(vals) > 1 else None,
        "best": max(vals) if better > 0 else min(vals), "last": vals[-1],
    }


# -------------------------------------------------------------- significance
def _betacf(a, b, x):
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def welch_p(a: list, b: list):
    """Two-sided p-value that the two sets of runs share a mean (Welch's t-test)."""
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    if va + vb == 0.0:
        return 0.0 if statistics.fmean(a) != statistics.fmean(b) else 1.0
    t = (statistics.fmean(a) - statistics.fmean(b)) / math.sqrt(va + vb)
    df = (va + vb) ** 2 / ((va ** 2) / (len(a) - 1) + (vb ** 2) / (len(b) - 1))
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def verdict(a: list, b: list, better: int) -> dict | None:
    """Compare two sets of values. winner: 0 = a, 1 = b, None = identical."""
    if not a or not b:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    if ma == mb:
        return {"winner": None, "pct": 0.0, "p": None, "conf": "tie"}
    winner = 0 if (ma > mb) == (better > 0) else 1
    loser = abs(mb if winner == 0 else ma)     # "the winner is pct% better than the other"
    pct = abs(ma - mb) / loser * 100.0 if loser else None
    p = welch_p(a, b)
    if p is None or min(len(a), len(b)) < 4:
        conf = "few"        # not enough runs to say anything
    elif p < 0.05:
        conf = "clear"
    elif p < 0.20:
        conf = "leaning"
    else:
        conf = "noise"
    return {"winner": winner, "pct": pct, "p": p, "conf": conf}


CONF_TEXT = {
    "clear": "clear", "leaning": "leaning", "noise": "within noise",
    "few": "need more runs", "tie": "tie",
}


def fmt(v, dec: int, unit: str = "") -> str:
    if v is None:
        return "-"
    s = f"{v:.{dec}f}"
    return s + unit if unit in ("%", "°") else (f"{s} {unit}" if unit else s)


def metric_table(runs: list, scenario: str, fingerprint: str | None = None) -> dict:
    """Everything a comparison view needs for one scenario."""
    scn = tracks.BY_ID[scenario]
    grp = groups(runs, scenario, fingerprint)
    labels = sorted(grp, key=lambda k: -len(grp[k]))
    rows = []
    for key, label, unit, better, dec in scn.metrics:
        cells = [describe(values(grp[lb], key), better) for lb in labels]
        v = None
        if len(labels) == 2:
            v = verdict(values(grp[labels[0]], key), values(grp[labels[1]], key), better)
        best = None
        means = [(c["mean"], i) for i, c in enumerate(cells) if c]
        if len(means) > 1:
            best = (max(means) if better > 0 else min(means))[1]
            if len({m for m, _ in means}) == 1:
                best = None
        rows.append({"key": key, "label": label, "unit": unit, "better": better, "dec": dec,
                     "cells": cells, "verdict": v, "best": best, "primary": key == scn.primary})
    return {"scenario": scn, "labels": labels, "counts": [len(grp[lb]) for lb in labels], "rows": rows}


# ------------------------------------------------------------------ progress
RECENT = 3          # "how you are doing now" = your last three runs
TREND_MIN = 5       # runs (with the current settings) before a trend is claimed
TREND_PCT = 3.0     # recent against earlier: less than this either way is "steady"


def primary_of(scn) -> tuple:
    """(key, label, unit, better, decimals) of a scenario's headline metric."""
    return next(m for m in scn.metrics if m[0] == scn.primary)


def progress(runs: list, scn, controller: str, fingerprint: str) -> dict:
    """One scenario's history on one controller, oldest run first.

    points      [(headline value, made with the current settings?, ISO time), ...] - every run
    n, last, best, recent    over the runs made with the current settings only; `recent` is
                the mean of the last three
    change_pct  `recent` against the runs before them, signed so that + is better; None
                until there are TREND_MIN runs, because three runs against one says nothing
    trend       "up" / "down" / "steady" / None
    """
    key, _, _, better, _ = primary_of(scn)
    points = []
    for r in counted(runs):
        if r.get("controller") == controller and r.get("scenario") == scn.id:
            v = r.get("stats", {}).get(key)
            if isinstance(v, (int, float)):
                points.append((float(v), r.get("fingerprint") == fingerprint, r.get("time", "")))
    cur = [v for v, same, _ in points if same]
    out = {"points": points, "n": len(cur), "last": None, "best": None, "recent": None, "change_pct": None, "trend": None}
    if cur:
        out["last"] = cur[-1]
        out["best"] = max(cur) if better > 0 else min(cur)
        out["recent"] = statistics.fmean(cur[-RECENT:])
    if len(cur) >= TREND_MIN:
        before = statistics.fmean(cur[:-RECENT])
        if before:
            pct = better * (out["recent"] - before) / abs(before) * 100.0
            out["change_pct"] = pct
            out["trend"] = "up" if pct >= TREND_PCT else ("down" if pct <= -TREND_PCT else "steady")
    return out


def grade(runs: list, scn, controller: str, fingerprint: str):
    """(share of shots or time on target over the last three runs, 0-100, were those runs made
    with other settings?) or None if the scenario has no such number or has never been played.

    This is what "weakest" is ranked by. There is nobody else's score to compare with, so it
    cannot say you are bad at something - only where the largest share of your bullets (or
    of your time on target) is being lost, which is where practice has the most to give back.
    Runs made with the current settings are used if there are any; otherwise the most recent
    runs with whatever settings they had, which is still a fair guide to what needs work.
    """
    key = getattr(scn, "grade", None)
    if not key:
        return None
    mine = [r for r in counted(runs) if r.get("controller") == controller and r.get("scenario") == scn.id
            and isinstance(r.get("stats", {}).get(key), (int, float))]
    now = [r for r in mine if r.get("fingerprint") == fingerprint]
    use = now or mine
    if not use:
        return None
    return statistics.fmean(r["stats"][key] for r in use[-RECENT:]), not now


def weakest(runs: list, track: str, controller: str, fingerprint: str, n: int = 5) -> dict:
    """The n scenarios of a track with the lowest grade() -> {"rows": [(scenario, grade, stale)],
    "ranked": how many scenarios could be ranked, "total": how many the track has that can be}."""
    rows = []
    scns = [s for s in tracks.BY_TRACK[track].scenarios if getattr(s, "grade", None)]
    for s in scns:
        g = grade(runs, s, controller, fingerprint)
        if g:
            rows.append((s, g[0], g[1]))
    rows.sort(key=lambda r: r[1])
    return {"rows": rows[:n], "ranked": len(rows), "total": len(scns)}


# ------------------------------------------------------- guided comparison
def _t_p(vals: list):
    """Two-sided p-value that the mean of `vals` is zero (one-sample t-test)."""
    n = len(vals)
    if n < 3:
        return None
    sd = statistics.stdev(vals)
    m = statistics.fmean(vals)
    if sd == 0.0:
        return 0.0 if m else 1.0
    t = m / (sd / math.sqrt(n))
    return _betai((n - 1) / 2.0, 0.5, (n - 1) / ((n - 1) + t * t))


def duel_cells(runs: list, track: str, fingerprint: str, a: str, b: str) -> list:
    """Paired results from guided comparisons of controllers a and b.

    A guided comparison plays a fixed set of scenarios in blocks of four passes, A-B-B-A (the
    next block B-A-A-B), so that whatever drifts steadily while you play - warming up, tiring,
    learning the drill - falls equally on both pads. One cell is one scenario in one finished
    block: {"scenario", "block", "a", "b", "rel"}, a and b being each pad's mean of its two
    runs and rel how far a is ahead of b in percent of their average (negative: behind).
    A pass that was played again replaces the earlier attempt.
    """
    blocks: dict = {}
    for r in runs:
        d = r.get("duel")
        if (not d or d.get("warmup") or r.get("track") != track or r.get("fingerprint") != fingerprint
                or r.get("controller") not in (a, b) or d.get("pass") not in (0, 1, 2, 3)):
            continue
        blocks.setdefault((d.get("session"), d.get("block")), {}).setdefault(r.get("scenario"), {})[d["pass"]] = r
    cells = []
    for bkey in sorted(blocks, key=str):
        for sid, passes in blocks[bkey].items():
            scn = tracks.BY_ID.get(sid)
            if scn is None or len(passes) < 4:
                continue
            who = [passes[i].get("controller") for i in range(4)]
            if not (who[0] == who[3] != who[1] == who[2]):
                continue
            key, _, _, better, _ = primary_of(scn)
            vals = {a: [], b: []}
            for i in range(4):
                v = passes[i].get("stats", {}).get(key)
                if isinstance(v, (int, float)):
                    vals[who[i]].append(float(v))
            if len(vals[a]) < 2 or len(vals[b]) < 2:
                continue
            va, vb = statistics.fmean(vals[a]), statistics.fmean(vals[b])
            mid = (abs(va) + abs(vb)) / 2.0
            if mid == 0.0:
                continue
            cells.append({"scenario": scn, "block": bkey, "a": va, "b": vb, "rel": better * (va - vb) / mid * 100.0})
    return cells


def duel_summary(cells: list) -> dict | None:
    """What the paired results add up to.

    mean      how far controller a is ahead over all cells, percent (negative: b is ahead)
    conf      "clear" needs p < 0.05 AND at least two blocks - one block, however lopsided,
              is one sitting; "leaning" is p < 0.20; "noise" anything else; "few" under 3 cells
    more      roughly how many more blocks would settle a gap of the size seen so far (80%
              chance of reaching p < 0.05), None when already clear or when there is no gap
    rows      per scenario: its mean on each pad, mean rel, and blocks won by a / by b
    """
    if not cells:
        return None
    rels = [c["rel"] for c in cells]
    n_blocks = len({c["block"] for c in cells})
    mean = statistics.fmean(rels)
    sd = statistics.stdev(rels) if len(rels) > 1 else None
    p = _t_p(rels)
    if p is None:
        conf = "few"
    elif p < 0.05 and n_blocks >= 2:
        conf = "clear"
    elif p < 0.20:
        conf = "leaning"
    else:
        conf = "noise"
    more = None
    if conf != "clear" and sd and abs(mean) > 1e-9:
        need = 7.85 * (sd / mean) ** 2                 # cells for 80% power at p = 0.05, two-sided
        more = max(1, math.ceil(need / (len(rels) / n_blocks)) - n_blocks)
    rows = []
    for scn in dict.fromkeys(c["scenario"] for c in cells):
        mine = [c for c in cells if c["scenario"] is scn]
        rows.append({"scenario": scn, "a": statistics.fmean(c["a"] for c in mine), "b": statistics.fmean(c["b"] for c in mine),
                     "rel": statistics.fmean(c["rel"] for c in mine), "n": len(mine),
                     "wins": (sum(1 for c in mine if c["rel"] > 0), sum(1 for c in mine if c["rel"] < 0))})
    return {"mean": mean, "sd": sd, "p": p, "conf": conf, "blocks": n_blocks, "cells": len(rels), "more": more,
            "winner": None if mean == 0 else (0 if mean > 0 else 1), "rows": rows}
