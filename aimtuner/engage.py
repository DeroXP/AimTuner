"""Where each target's time went, for a captured run (yours, or the ghost's of the same run).

For every target that was killed, from the moment it could be gone for (it showed, and the
previous kill was done) to its kill:

  react    until the crosshair started towards it
  move     the first, fast movement (until it braked to a fifth of its top speed)
  fix      corrections, until the crosshair was on it
  shoot    on it, until the first shot at it
  finish   first shot to kill (several bullets on the Call of Duty and Siege tracks)

plus how far the first movement left you from it (a share of the distance), how many
corrections there were and whether you went past it. On a moving target it also measures how
far behind it you were while firing, and, after it changed direction, how long until you turned
with it.

The ghost played the same targets (ghost.py), so `pairs` sets your target n beside its target n.
"""
from __future__ import annotations

import math
import re
import statistics

import numpy as np

from .capture import P, V
from .codkit import BODY_R, HEAD_R

PARTS = ("react", "move", "fix", "shoot", "finish")
NAMES = {"react": "reaction", "move": "first movement", "fix": "corrections", "shoot": "getting the shot off",
         "finish": "finishing it"}
MOVE_MIN = 25.0             # deg/s of approach that counts as having started towards it
BRAKE = 0.2                 # the first movement has ended when the speed falls to this share of its top


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def aim_points(cap, k):
    """(frames, aim points (n, 3), angular-radius distances (n,)) for target k: a dummy's chest, a
    sphere's centre."""
    fr, ps = cap.track_of(k)
    kind = cap.head["keys"][k]["kind"]
    if kind == "dummy":
        pts = np.stack([ps[:, P["x"]], ps[:, P["y"]] + 1.18 - 0.38 * ps[:, P["a"]], ps[:, P["z"]]], axis=1)
        rad = np.full(len(fr), BODY_R)
    else:
        pts = ps[:, :3]
        rad = ps[:, P["a"]]
    return fr, pts, rad


def errors(cap, fr, pts, rad):
    """Angular error (deg, signed yaw and pitch, and size) from the view to the points, and their
    angular radius, at frames fr."""
    v = cap.view[fr]
    e = v[:, [V["ex"], V["ey"], V["ez"]]]
    d = pts - e
    flat = np.hypot(d[:, 0], d[:, 2])
    dist = np.hypot(flat, d[:, 1])
    ty = np.degrees(np.arctan2(d[:, 0], -d[:, 2]))
    tp = np.degrees(np.arctan2(d[:, 1], flat))
    ey = (ty - v[:, V["yaw"]] + 180.0) % 360.0 - 180.0
    ep = tp - v[:, V["pitch"]]
    size = np.hypot(ey * np.cos(np.radians(v[:, V["pitch"]])), ep)
    ang = np.degrees(np.arcsin(np.clip(rad / np.maximum(dist, 1e-6), 0.0, 1.0)))
    return ey, ep, size, ang, ty, tp


def _orig_key(cap, k):
    """The run's own target number a key stands for: a ghost's key is "(target, lap)"."""
    s = cap.head["keys"][k]["key"]
    if cap.head.get("ghost"):
        m = re.match(r"\((\d+), (\d+)\)", s)
        if m:
            return int(m.group(1)), int(m.group(2))
    return k, 0


def engagements(cap) -> list:
    """One dict per killed target, in the order they died (see the notes at the top)."""
    t = cap.t
    ev = cap.head["events"]
    kills = sorted((e["t"], e["key"]) for e in ev if e["kind"] == "removed" and e.get("why") == "kill" and e.get("key", -1) >= 0)
    shot_ev = sorted((e["t"], bool(e.get("hit"))) for e in ev if e["kind"] == "shot")
    shots = np.array([s[0] for s in shot_ev] or [9e9])
    hits = np.array([s[1] for s in shot_ev] or [False])
    fire = cap.col("fire")
    yaw_all, pitch_all = cap.col("yaw"), cap.col("pitch")
    out, prev = [], None
    for t_kill, k in kills:
        fr, pts, rad = aim_points(cap, k)
        if len(fr) < 2:
            prev = t_kill
            continue
        vis = cap.tp[cap.tk == k][:, P["vis"]] > 0.5
        shown = t[fr][vis] if vis.any() else t[fr]
        t0 = float(shown[0])
        if prev is not None and prev > t0:
            t0 = prev
        prev = t_kill
        m = (t[fr] >= t0 - 1e-9) & (t[fr] <= t_kill + 1e-9)
        if m.sum() < 2:
            continue
        f, pp, rr = fr[m], pts[m], rad[m]
        ey, ep, size, ang, ty, tp = errors(cap, f, pp, rr)
        ts = t[f]
        dt = np.diff(ts)
        dt[dt <= 0] = 1e-3
        approach = -np.diff(size) / dt                       # how fast the gap is closing, deg/s
        approach = np.concatenate([approach[:1], approach])
        dyaw = (np.diff(yaw_all[f]) + 180.0) % 360.0 - 180.0
        speed = np.hypot(dyaw * np.cos(np.radians(pitch_all[f][1:])), np.diff(pitch_all[f])) / dt
        speed = np.concatenate([speed[:1], speed])           # how fast the crosshair itself is moving
        tgt_speed = np.hypot(np.diff(ty), np.diff(tp)) / dt
        moving = float(np.median(tgt_speed)) > 5.0 if len(tgt_speed) else False
        rec = {"key": _orig_key(cap, k), "t0": t0, "t_kill": t_kill, "dist": float(size[0]),
               "radius": float(ang[0]), "moving": moving}
        on = np.nonzero(size <= ang)[0]
        i_on = int(on[0]) if len(on) else None
        rec["t_on"] = float(ts[i_on]) if i_on is not None else None
        # reaction: the crosshair sets off towards it (not when it was already on its way)
        start = None
        if not (speed[0] >= MOVE_MIN and approach[0] > 0):
            ok = np.nonzero((speed >= MOVE_MIN) & (approach > 0))[0]
            start = int(ok[0]) if len(ok) else None
        rec["t_react"] = float(ts[start]) if start is not None else None
        s0 = start if start is not None else 0
        rec["land_share"] = rec["overshoot"] = rec["corrections"] = None
        if moving:
            # a moving target: no single first movement to speak of - it is getting on it, then staying on
            rec["t_move_end"] = rec["t_on"]
        else:
            # the first movement: from there to where it braked
            stop = i_on if i_on is not None and i_on > s0 else len(speed) - 1
            sp = speed[s0:stop + 1]
            if len(sp) > 2:
                ipk = int(np.argmax(sp))
                pk = float(sp[ipk])
                after = np.nonzero(sp[ipk:] <= max(BRAKE * pk, 10.0))[0]
                i_end = s0 + ipk + (int(after[0]) if len(after) else len(sp) - 1 - ipk)
                # corrections: the crosshair setting off again after that, before it got on the target
                seg = speed[i_end:stop + 1]
                lim = max(0.25 * pk, 20.0)
                bursts, up = 0, False
                for a in seg:
                    if a >= lim and not up:
                        bursts, up = bursts + 1, True
                    elif a < 0.5 * lim:
                        up = False
                rec["corrections"] = bursts
            else:
                i_end = s0
                rec["corrections"] = 0
            if i_on is not None and i_on < i_end:
                i_end = i_on
            rec["t_move_end"] = float(ts[i_end])
            d0 = max(float(size[s0]), 1e-6)
            rec["land_share"] = float(size[i_end]) / d0       # gap left after the first movement, share of the way
            e0 = np.array([ey[s0], ep[s0]])
            rec["overshoot"] = bool(np.any((ey[s0:] * e0[0] + ep[s0:] * e0[1] < 0) & (size[s0:] > ang[s0:])))
        j = int(np.searchsorted(shots, t0 - 1e-9))
        rec["t_shot"] = float(shots[j]) if j < len(shots) and shots[j] <= t_kill + 1e-9 else None
        m_sh = (shots >= t0 - 1e-9) & (shots <= t_kill + 1e-9)
        rec["shots"], rec["hits"] = int(m_sh.sum()), int(hits[m_sh].sum())
        if moving:
            fm = fire[f] > 0.5
            on_t = (size <= ang)
            rec["lag_deg"] = float(np.mean(size[fm])) if fm.any() else float(np.mean(size))
            rec["on_pct"] = float(np.mean(on_t[fm])) * 100.0 if fm.any() else None    # on it while firing
        rec["parts"] = _parts(rec)
        rec["total"] = t_kill - t0
        rec["path"] = (ts, ey, ep, size, ty, tp)
        out.append(rec)
    return out


def _parts(r) -> dict:
    """The target's time split into PARTS (seconds; None where a moment was not seen)."""
    t0, tk = r["t0"], r["t_kill"]
    react = r["t_react"]
    move_end = r["t_move_end"]
    on = r["t_on"]
    shot = r["t_shot"]
    out = dict.fromkeys(PARTS)
    a = t0
    if react is not None:
        out["react"] = react - t0
        a = react
    if move_end is not None and move_end >= a:
        out["move"] = move_end - a
        a = move_end
    if on is not None and on >= a:
        out["fix"] = on - a
        a = on
    if shot is not None and shot >= a:
        out["shoot"] = shot - a
        a = shot
    out["finish"] = max(tk - a, 0.0)
    return out


def reversals(cap) -> list:
    """Moving targets only: after each change of direction, how long until the crosshair turned with
    it (seconds), for the one target being tracked at the time."""
    out = []
    t = cap.t
    for k, info in enumerate(cap.head["keys"]):
        fr, pts, rad = aim_points(cap, k)
        if len(fr) < 30:
            continue
        ey, ep, size, ang, ty, tp = errors(cap, fr, pts, rad)
        ts = t[fr]
        dt = np.maximum(np.diff(ts), 1e-3)
        tv = np.diff(ty) / dt
        vv = np.diff(cap.view[fr, V["yaw"]])
        vv = ((vv + 180.0) % 360.0 - 180.0) / dt
        sgn = np.sign(tv)
        flips = np.nonzero((sgn[1:] != sgn[:-1]) & (np.abs(tv[1:]) > 3.0))[0] + 1
        for i in flips:
            d = sgn[i]
            j = np.nonzero(vv[i:i + 200] * d > 3.0)[0]
            if len(j) and np.all(size[max(i - 20, 0):i] < 4.0 * np.maximum(ang[max(i - 20, 0):i], 0.5)):
                out.append(float(ts[i + j[0]] - ts[i]))
    return out


def pairs(you: list, ghost: list) -> list:
    """(your target, the ghost's same target) for every target you both killed (its first time round)."""
    g = {r["key"][0]: r for r in ghost if r["key"][1] == 0}
    return [(r, g[r["key"][0]]) for r in you if r["key"][0] in g]


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def compare(you_cap, ghost_cap) -> dict:
    """Your run against the ghost's, part by part, and the one thing worth working on."""
    yo, gh = engagements(you_cap), engagements(ghost_cap)
    ps = pairs(yo, gh)
    rows = {}
    for part in PARTS:
        a = _med([y["parts"][part] for y, _ in ps])
        b = _med([g["parts"][part] for _, g in ps])
        rows[part] = (a, b)
    still = [(y, g) for y, g in ps if not y["moving"] and not g["moving"]]
    acc = lambda side: (sum(r["hits"] for r in side) / max(sum(r["shots"] for r in side), 1) * 100.0
                        if sum(r["shots"] for r in side) else None)
    out = {
        "you": yo, "ghost": gh, "pairs": ps, "parts": rows,
        "moving": sum(1 for y, _ in ps if y["moving"]) * 2 > len(ps),
        "total": (_med([y["total"] for y, _ in ps]), _med([g["total"] for _, g in ps])),
        "land": (_med([y["land_share"] for y, _ in still]), _med([g["land_share"] for _, g in still])),
        "corrections": (_mean([y["corrections"] for y, _ in still]), _mean([g["corrections"] for _, g in still])),
        "overshoot": (_mean([float(y["overshoot"]) for y, _ in still]), _mean([float(g["overshoot"]) for _, g in still])),
        "lag": (_med([y.get("lag_deg") for y, _ in ps]), _med([g.get("lag_deg") for _, g in ps])),
        "on_pct": (_med([y.get("on_pct") for y, _ in ps]), _med([g.get("on_pct") for _, g in ps])),
        "accuracy": (acc([y for y, _ in ps]), acc([g for _, g in ps])),
        "reversal": (_med(reversals(you_cap)), _med(reversals(ghost_cap))),
    }
    out["advice"] = advice(out, you_cap)
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def advice(c, cap) -> str:
    """One or two plain sentences on the biggest gap between you and the ghost."""
    ps = c["pairs"]
    ms = lambda x: f"{x * 1000:.0f} ms"
    rv = c["reversal"]
    if len(ps) < 3:                         # a tracking drill (one target all run), or too few kills to compare
        if rv[0] is not None and rv[1] is not None:
            if rv[0] > rv[1] + 0.02:
                return (f"When the target changes direction you take {ms(rv[0])} to turn with it; the ghost takes "
                        f"{ms(rv[1])}. Watch its feet, not where it is going - turn the moment it does.")
            return (f"You turn with the target as fast as the ghost does ({ms(rv[0])} against {ms(rv[1])}) - "
                    "what is left is staying smooth between the turns.")
        return "Not enough targets you both finished to compare them part by part."
    gaps = [((a or 0.0) - (b or 0.0), part, a, b) for part, (a, b) in c["parts"].items() if a is not None and b is not None]
    tot = c["total"]
    if not gaps or max(gaps)[0] < 0.02:
        return (f"You matched the ghost on every part of it - {ms(tot[0])} a target against its {ms(tot[1])}. "
                "Keep doing what you are doing.")
    if c["moving"]:
        # a moving target: getting on it (reaction + movement), then staying on it while the gun works
        get = ((c["parts"]["react"][0] or 0) + (c["parts"]["move"][0] or 0),
               (c["parts"]["react"][1] or 0) + (c["parts"]["move"][1] or 0))
        fin = (c["parts"]["finish"][0] or 0, c["parts"]["finish"][1] or 0)
        onp, accu, lag = c["on_pct"], c["accuracy"], c["lag"]
        if get[0] - get[1] >= fin[0] - fin[1]:
            s = f"Your biggest gap is getting onto it: {ms(get[0])} a target, against the ghost's {ms(get[1])}."
            r = c["parts"]["react"]
            if r[0] is not None and r[1] is not None and r[0] - r[1] > 0.04:
                s += f" Most of it is starting: you set off after {ms(r[0])}, the ghost after {ms(r[1])}."
            return s
        s = f"Your biggest gap is staying on it while you shoot: {ms(fin[0])} from first shot to kill, against {ms(fin[1])}."
        if onp[0] is not None and onp[1] is not None:
            s += f" While firing you were on it {onp[0]:.0f}% of the time; the ghost {onp[1]:.0f}%."
        if accu[0] is not None and accu[1] is not None:
            s += f" Bullets landed: you {accu[0]:.0f}%, ghost {accu[1]:.0f}%."
        if rv[0] is not None and rv[1] is not None and rv[0] > rv[1] + 0.03:
            s += f" When it changes direction you take {ms(rv[0])} to turn with it; the ghost {ms(rv[1])}."
        return s
    gap, part, a, b = max(gaps)
    s = f"Your biggest gap is {NAMES[part]}: {ms(a)} a target, against the ghost's {ms(b)}."
    land, corr, over = c["land"], c["corrections"], c["overshoot"]
    if part in ("move", "fix") and None not in (land[0], land[1], corr[0], corr[1]):
        s += (f" Your first movement leaves {land[0] * 100:.0f}% of the way still to go and you need "
              f"{corr[0]:.1f} corrections; the ghost's leaves {land[1] * 100:.0f}% and needs {corr[1]:.1f}.")
        if over[0] is not None and over[1] is not None and over[0] > over[1] + 0.15:
            s += f" You go past the target on {over[0] * 100:.0f}% of them (the ghost on {over[1] * 100:.0f}%)."
    elif part == "react":
        s += " Start moving as soon as it shows - the first movement does not need to be exact, the correction will be."
    elif part == "shoot":
        s += " You stop and settle before you fire; fire as the crosshair arrives."
    elif part == "finish":
        accu = c["accuracy"]
        if accu[0] is not None and accu[1] is not None:
            s += f" Bullets landed: you {accu[0]:.0f}%, ghost {accu[1]:.0f}% - hold the sights on it through the recoil."
    return s
