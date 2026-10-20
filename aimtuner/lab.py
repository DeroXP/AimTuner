"""Controller Lab: everything about a pad that can be measured from the PC side.

Each function takes captured rows - (slot, t, packet, buttons, lt, rt, lx, ly,
rx, ry) straight from the driver, one per state change, stamped by the input
thread while it spins (a few microseconds of uncertainty) - and returns plain
numbers. No rendering here, so the maths can be tested on synthetic data.

What this can NOT tell you is true input latency: the delay between your finger
moving and the PC hearing about it. Software only sees the report arrive; it
never knows when the press physically happened. Review sites measure that with
a rig that actuates the button electrically or with a solenoid. Everything
below is the part that needs no rig: how often and how evenly the pad reports,
and how the sticks, buttons and triggers behave.
"""
from __future__ import annotations

import json
import math
import os
import statistics

AXIS = 32767.0
BINS = 72           # 5 degree slices around the stick


def _xy(row, which: str):
    x, y = (row[6], row[7]) if which == "left" else (row[8], row[9])
    return max(-1.0, x / AXIS), max(-1.0, y / AXIS)


# ------------------------------------------------------------- report timing
def report_rate(rows) -> dict | None:
    """How often, and how evenly, new data arrives.

    Only intervals between consecutive packets are used. Where the packet counter
    jumped by more than one, reports arrived that this PC did not stamp
    individually (the capture thread was briefly descheduled); those gaps are
    counted as `untimed` and left out rather than guessed at.

    The driver's counter only moves when the state changes, so this is the rate
    of NEW data. A pad that sends 8000 reports a second but only samples its
    sticks 2000 times a second reads 2000 here - which is the number that matters.
    """
    dts, untimed = [], 0
    for a, b in zip(rows, rows[1:]):
        d = (b[2] - a[2]) & 0xFFFFFFFF
        if d == 1:
            dts.append((b[1] - a[1]) * 1e6)
        elif d > 1:
            untimed += d - 1
    if len(dts) < 40:
        return None
    dts.sort()
    n = len(dts)
    med = dts[n // 2]
    core = [x for x in dts if 0.5 * med <= x <= 1.5 * med]
    busy = [x for x in dts if x <= 10.0 * med]         # ignore pauses in the hand movement
    width = med / 8.0
    hist = [0] * 32
    for x in dts:
        k = int(x / width)
        if k < 32:
            hist[k] += 1
    return {
        "rate_hz": round(1e6 / med),
        "interval_us": round(med, 1),
        "jitter_us": round(statistics.pstdev(core), 2) if len(core) > 1 else 0.0,
        "steady_pct": round(100.0 * len(core) / n, 1),      # intervals within +/-50% of typical
        "p99_us": round(dts[min(int(n * 0.99), n - 1)], 1),
        "worst_ms": round(busy[-1] / 1000.0, 3),
        "effective_hz": round(1e6 * len(busy) / sum(busy)) if busy else None,
        "samples": n,
        "untimed": untimed,
        "untimed_pct": round(100.0 * untimed / (n + untimed), 1),
        "hist": hist, "hist_bin_us": round(width, 2),
    }


# --------------------------------------------------------------------- sticks
def rest(rows, which: str) -> dict | None:
    """Where the stick sits with no thumb on it, and how much it wanders."""
    if not rows:
        return None
    pts = [_xy(r, which) for r in rows]
    # time-weighted: a value that was held for a long time counts for a long time
    w = [max(b[1] - a[1], 0.0) for a, b in zip(rows, rows[1:])] + [0.0]
    tot = sum(w)
    if tot <= 0.0:
        w, tot = [1.0] * len(pts), float(len(pts))
    mx = sum(p[0] * k for p, k in zip(pts, w)) / tot
    my = sum(p[1] * k for p, k in zip(pts, w)) / tot
    var = sum(((p[0] - mx) ** 2 + (p[1] - my) ** 2) * k for p, k in zip(pts, w)) / tot
    peak = max(math.hypot(*p) for p in pts)
    return {
        "offset_pct": round(100.0 * math.hypot(mx, my), 2),
        "noise_pct": round(100.0 * math.sqrt(var), 3),
        "peak_pct": round(100.0 * peak, 2),
        # the smallest in-game minimum deadzone that swallows everything seen at rest
        "min_deadzone": 0 if peak < 0.004 else math.ceil(100.0 * peak + 0.5),
        "changes_per_s": round((len(rows) - 1) / (rows[-1][1] - rows[0][1])) if len(rows) > 1 and rows[-1][1] > rows[0][1] else 0,
    }


def rim_bins(rows, which: str, bins=None, start: int = 0):
    """Largest radius reached in each 5-degree slice. Pass `bins` and `start` to
    extend a previous result with only the new rows (used for the live display)."""
    bins = bins if bins is not None else [0.0] * BINS
    for r in rows[start:]:
        x, y = _xy(r, which)
        m = math.hypot(x, y)
        if m > 0.3:
            k = int((math.atan2(y, x) + math.pi) / (2 * math.pi) * BINS) % BINS
            if m > bins[k]:
                bins[k] = m
    return bins


def circle(rows, which: str) -> dict | None:
    """Outer range from rolling the stick around its rim."""
    bins = rim_bins(rows, which)
    seen = [b for b in bins if b > 0.5]
    if len(seen) < BINS * 0.8:
        return None                        # the rim was not traced all the way round
    lo, hi = min(seen), max(seen)
    return {
        "reach_min_pct": round(100.0 * lo, 1),
        "reach_avg_pct": round(100.0 * statistics.fmean(seen), 1),
        "reach_max_pct": round(100.0 * hi, 1),
        "circularity_err_pct": round(100.0 * statistics.fmean(abs(b - 1.0) for b in seen), 2),
        "shape": "square corners" if hi > 1.10 else "round",
        # the highest in-game maximum deadzone at which every direction still reaches full
        "max_deadzone": max(50, min(99, math.floor(100.0 * lo) - 1)),
        "coverage_pct": round(100.0 * len(seen) / BINS),
        "bins": [round(b, 3) for b in bins],
    }


def resolution(rows, which: str) -> dict | None:
    """Native step size of the stick output, from the spacing of the values it produced.

    Mid-swing the stick moves several steps between two reports, so most neighbouring
    values seen are two or three steps apart; only where it slows (the ends of each
    axis) do truly adjacent values show up. The step is therefore the SMALLEST gap
    that turns up often, not the most common one."""
    out = {}
    for axis, idx in (("x", 6 if which == "left" else 8), ("y", 7 if which == "left" else 9)):
        vals = sorted({r[idx] for r in rows})    # signed: the two halves need not share one grid
        gaps: dict = {}
        for a, b in zip(vals, vals[1:]):
            if 0 < b - a <= 1024:
                gaps[b - a] = gaps.get(b - a, 0) + 1
        total = sum(gaps.values())
        if total < 60:
            continue
        need = max(12, total // 50)             # rarer than this is a fluke, not the stick's own step
        for g in sorted(gaps):
            # scaled-up 12-bit values are not always exact multiples, so count near-equal gaps together
            if sum(c for k, c in gaps.items() if g <= k <= g * 1.25) >= need:
                out[axis] = g
                break
    if not out:
        return None
    step = max(out.values())
    levels = 65536 // step
    return {"step": step, "levels": levels, "bits": round(math.log2(levels), 1)}


def flicks(rows, which: str) -> dict | None:
    """Snap-back: let the stick fly back from full deflection and see how far it
    overshoots centre to the other side, and how long it takes to settle."""
    pts = [(r[1],) + _xy(r, which) for r in rows]
    n = len(pts)
    events, i = [], 0
    while i < n:
        t, x, y = pts[i]
        if math.hypot(x, y) < 0.85:
            i += 1
            continue
        # throw: how long the way out took (last sample under 10% -> first over 85%)
        j, throw = i, None
        while j > 0 and t - pts[j][0] < 0.25:
            if math.hypot(pts[j][1], pts[j][2]) <= 0.1:
                throw = (t - pts[j][0]) * 1000.0
                break
            j -= 1
        # hold: stay in the peak region until it drops under 50%
        k = i
        ux = uy = 0.0
        while k < n and math.hypot(pts[k][1], pts[k][2]) >= 0.5:
            m = math.hypot(pts[k][1], pts[k][2])
            if m >= 0.85:
                ux, uy = pts[k][1] / m, pts[k][2] / m
                t_peak = pts[k][0]
            k += 1
        if k >= n:
            break
        t_rel = pts[k][0]
        if t_rel - t_peak > 0.08:          # eased back by hand rather than released: not a snap-back
            i = k
            continue
        over, settle, m_end = 0.0, t_rel, k
        while m_end < n and pts[m_end][0] - t_rel < 0.35:
            _, x, y = pts[m_end]
            over = max(over, -(x * ux + y * uy))
            if math.hypot(x, y) > 0.05:
                settle = pts[m_end][0]
            if math.hypot(x, y) >= 0.85:   # next flick already started
                break
            m_end += 1
        events.append({"overshoot_pct": 100.0 * over, "settle_ms": (settle - t_rel) * 1000.0, "throw_ms": throw})
        i = m_end
    if not events:
        return None
    throws = [e["throw_ms"] for e in events if e["throw_ms"] is not None]
    return {
        "count": len(events),
        "overshoot_avg_pct": round(statistics.fmean(e["overshoot_pct"] for e in events), 1),
        "overshoot_max_pct": round(max(e["overshoot_pct"] for e in events), 1),
        "settle_avg_ms": round(statistics.fmean(e["settle_ms"] for e in events), 1),
        "throw_fastest_ms": round(min(throws), 1) if throws else None,
        "throw_median_ms": round(statistics.median(throws), 1) if throws else None,
    }


# -------------------------------------------------------- buttons / triggers
def _presses(rows, control):
    """[(t_down, t_up or None)] for a button bit (int) or a trigger ('LT'/'RT')."""
    def down(r):
        if control == "LT":
            return r[4] >= 128
        if control == "RT":
            return r[5] >= 128
        return bool(r[3] & control)

    out, t0 = [], None
    for r in rows:
        d = down(r)
        if d and t0 is None:
            t0 = r[1]
        elif not d and t0 is not None:
            out.append((t0, r[1]))
            t0 = None
    if t0 is not None:
        out.append((t0, None))
    return out


def taps(rows, control) -> dict | None:
    pr = [p for p in _presses(rows, control) if p[1] is not None]
    if len(pr) < 4:
        return None
    held = [(u - d) * 1000.0 for d, u in pr]
    gaps = [(b[0] - a[1]) * 1000.0 for a, b in zip(pr, pr[1:])]
    span = pr[-1][0] - pr[0][0]
    return {
        "count": len(pr),
        "per_second": round((len(pr) - 1) / span, 2) if span > 0 else None,
        "press_median_ms": round(statistics.median(held), 1),
        "press_shortest_ms": round(min(held), 2),
        "gap_shortest_ms": round(min(gaps), 2),
        # a "press" or "release" shorter than any finger can make is the switch bouncing
        "bounces": sum(1 for x in held + gaps if x < 4.0),
    }


def chord(rows, first, second) -> dict | None:
    """Two buttons pressed 'together': how far apart they really landed (second - first, ms)."""
    a = [p[0] for p in _presses(rows, first)]
    b = [p[0] for p in _presses(rows, second)]
    deltas = []
    for ta in a:
        near = min(b, key=lambda tb: abs(tb - ta), default=None)
        if near is not None and abs(near - ta) < 0.15:
            deltas.append((near - ta) * 1000.0)
    if len(deltas) < 3:
        return None
    return {
        "count": len(deltas),
        "mean_ms": round(statistics.fmean(deltas), 2),
        "spread_ms": round(statistics.pstdev(deltas), 2),
        "worst_ms": round(max(deltas, key=abs), 2),
    }


def trigger(rows, which: str) -> dict | None:
    idx = 4 if which == "LT" else 5
    vals = [(r[1], r[idx]) for r in rows]
    top = max((v for _, v in vals), default=0)
    if top < 40:
        return None                        # never pulled
    pulls, t10 = [], None
    for t, v in vals:
        if v <= 25:
            t10 = t
        elif v >= 230 and t10 is not None:
            pulls.append((t - t10) * 1000.0)
            t10 = None
    steps = len({v for _, v in vals})
    return {
        "max_pct": round(100.0 * top / 255.0, 1),
        "steps": steps,
        "mode": "digital (hair trigger)" if steps <= 3 else "analogue",
        "pulls": len(pulls),
        "pull_fastest_ms": round(min(pulls), 2) if pulls else None,
        "pull_median_ms": round(statistics.median(pulls), 2) if pulls else None,
    }


# ------------------------------------------------------------------------ log
def path(log_dir: str) -> str:
    return os.path.join(log_dir, "lab.jsonl")


def save(log_dir: str, rec: dict) -> None:
    os.makedirs(log_dir, exist_ok=True)
    with open(path(log_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def load(log_dir: str) -> list:
    out = []
    try:
        with open(path(log_dir), "r", encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return out


def latest_by_controller(records: list) -> dict:
    out = {}
    for r in records:
        out[r.get("controller", "?")] = r
    return out
