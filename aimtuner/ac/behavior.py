"""How a run was played, judged against how people play: the behavioural half of the anti-cheat.

Works from the run's capture (capture.py): every frame's view and input, every target's pose.
Each detector looks for one thing a cheat does that a hand does not, gives its evidence in
numbers and in words, and says nothing until it has enough samples to say it fairly:

  instant lock   a target that appeared out of nowhere, turned onto within a frame or two of
                 showing (a classic aimbot) - people need 120 ms or more to start
  smoothing      the turn onto a target at its fastest the moment it starts, then slowing all
                 the way in (an aimbot's "smooth" setting closing a share of the gap per frame).
                 A hand speeds up and then slows: its peak comes near the middle of the movement
  triggerbot     the shot fired a frame after the crosshair touched the target, nearly every
                 time, and never early - people who click on arrival also click a little early
  reversal       a moving target changes direction, and the crosshair turns with it sooner than
                 sight can report the change (people: 120 ms and more)
  machine path   turns as straight as a ruler (a "humanized" aimbot's planned movement) - a hand's
                 turns bow. Never enough on its own to flag a run
  anti-recoil    a pull against the recoil that arrives in one step the frame fire goes down and
                 leaves in one step the frame it comes up - a recoil script, or a controller
                 adapter's anti-recoil adding its pull on top of the thumb's. A hand ramps in (or
                 was already pulling) and eases off

What is "a person" here comes from published motor-control figures (visual reaction, the shape
of an aimed movement, reaction to a change of direction) and was checked against real runs on
both a controller and a mouse, and against the ghost (a model of a strong human) - none of which
may be flagged. Each detector's threshold sits well outside all of them.
"""
from __future__ import annotations

import math

import numpy as np

from .. import engage
from ..capture import P, V

MIN_STATIC = 6          # targets a static detector needs before it says anything
MIN_SPRAY = 4           # sprays the anti-recoil detector needs


def _speed(cap):
    t = cap.t
    y, p = cap.col("yaw"), cap.col("pitch")
    dt = np.maximum(np.diff(t), 1e-4)
    dy = (np.diff(y) + 180.0) % 360.0 - 180.0
    s = np.hypot(dy * np.cos(np.radians(p[1:])), np.diff(p)) / dt
    return np.concatenate([s[:1], s]) if len(s) else np.zeros(len(t))


def _first_seen(cap):
    """For every target key, the time it was first visible."""
    out = {}
    vis = cap.tp[:, P["vis"]] > 0.5
    for k in np.unique(cap.tk):
        m = (cap.tk == k) & vis
        if m.any():
            out[int(k)] = float(cap.t[int(cap.tf[m][0])])
    return out


def _static(cap):
    """Engagements with a target that stood still, with what the detectors need of each."""
    t, sp = cap.t, _speed(cap)
    ev = cap.head.get("events", [])
    kills = sorted(e["t"] for e in ev if e["kind"] == "removed" and e.get("why") == "kill")
    seen = _first_seen(cap)
    shot_ev = sorted((e["t"], bool(e.get("hit"))) for e in ev if e["kind"] == "shot")
    out = []
    for e in engage.engagements(cap):
        k = e["key"][0]
        r = dict(e)
        # fresh: it appeared after the last kill, from nothing - a real stimulus to react to (not the
        # next target of a row already on screen, which a player is turning to before it "starts")
        prev = max((x for x in kills if x < e["t0"] - 1e-6), default=None)
        first = seen.get(k, e["t0"])
        i0 = int(np.searchsorted(t, first))
        rest = sp[max(i0 - 36, 0):i0 + 1]          # still for the 150 ms before it showed
        r["fresh"] = (prev is None or first > prev + 0.05) and (len(rest) == 0 or float(np.max(rest)) < 8.0)
        r["rt"] = (e["t_react"] - first) if e["t_react"] is not None else None
        # the first movement's speed profile: where its peak falls, and how sharply it started
        r["ttp"] = r["jump"] = r["straight"] = None
        if e["t_react"] is not None and (e["t_move_end"] or e["t_on"]) is not None and e["dist"] >= 8.0:
            a = int(np.searchsorted(t, e["t_react"]))
            while a > 0 and sp[a - 1] > 10.0 and a > int(np.searchsorted(t, e["t_react"])) - 60:
                a -= 1
            b = int(np.searchsorted(t, e["t_move_end"] if e["t_move_end"] is not None else e["t_on"]))
            if b - a < 3:
                # the whole turn in a frame or two, from standing still: no hand accelerates like that
                r["ttp"], r["jump"] = 0.0, 1.0
            else:
                seg = sp[a:b + 1]
                ipk = int(np.argmax(seg))
                r["ttp"] = float((t[a + ipk] - t[a]) / max(t[b] - t[a], 1e-3))
                r["jump"] = float(seg[min(1, len(seg) - 1)] / max(seg[ipk], 1e-6))
                # how far the path bowed off the straight line from start to end, share of its length
                yy = cap.col("yaw")[a:b + 1]
                pp = cap.col("pitch")[a:b + 1]
                xs = ((yy - yy[0] + 180.0) % 360.0 - 180.0) * math.cos(math.radians(float(pp[0])))
                ys = pp - pp[0]
                L = math.hypot(xs[-1], ys[-1])
                if L > 4.0 and not e["moving"]:          # a moving target bends any path
                    dev = np.abs(xs * ys[-1] - ys * xs[-1]) / L
                    r["straight"] = float(np.max(dev) / L)
            # where the first movement left the crosshair, against the target's size
        r["land_err"] = None
        if e["land_share"] is not None and e["radius"] > 0:
            r["land_err"] = float(e["land_share"] * e["dist"] / e["radius"])
        # the first shot at it: how long after the crosshair got onto it, and did it hit
        r["first_hit"] = None
        j = next((s for s in shot_ev if e["t0"] - 1e-6 <= s[0] <= e["t_kill"] + 1e-6), None)
        if j is not None:
            r["first_hit"] = j[1]
            r["first_shot"] = j[0]
        out.append(r)
    return out


def _flips(cap):
    """Moving targets: after each real change of direction (moving one way, then clearly the
    other), how long until the crosshair turned with it, in seconds. Only while the player was on
    or near the target, so it measures following it, not finding it."""
    out = []
    t = cap.t
    yaw_v = cap.col("yaw")
    for k in range(len(cap.head.get("keys", []))):
        fr, pts, rad = engage.aim_points(cap, k)
        if len(fr) < 60:
            continue
        ey, ep, size, ang, ty, tp = engage.errors(cap, fr, pts, rad)
        ts = t[fr]
        dt = np.diff(ts)
        if np.any(dt <= 0):
            continue
        step = float(np.median(dt))
        # the target's own turns: its bearing from one fixed point (where the player mostly stood),
        # so a change of direction made by the player's own strafing - which they see coming - is not one
        eye = np.median(cap.view[fr][:, [V["ex"], V["ey"], V["ez"]]], axis=0)
        fixed = np.degrees(np.arctan2(pts[:, 0] - eye[0], -(pts[:, 2] - eye[2])))
        tv = ((np.diff(fixed) + 180.0) % 360.0 - 180.0) / np.maximum(dt, 1e-4)
        vv = ((np.diff(yaw_v[fr]) + 180.0) % 360.0 - 180.0) / np.maximum(dt, 1e-4)
        w = max(int(round(0.02 / step)), 1)
        ker = np.ones(w) / w
        tvs = np.convolve(tv, ker, mode="same")
        vvs = np.convolve(vv, ker, mode="same")
        gap, win = max(int(round(0.05 / step)), 1), max(int(round(0.2 / step)), 3)
        cross = np.flatnonzero(np.sign(tvs[1:]) != np.sign(tvs[:-1])) + 1
        last = -10 ** 9
        for c in cross:
            if c - win < 0 or c + win >= len(tvs) or c - last < win:
                last = c
                continue
            last = c
            before, after = tvs[c - win:c - gap].mean(), tvs[c + gap:c + win].mean()
            if before * after >= 0 or min(abs(before), abs(after)) < 4.0:
                continue
            if np.any((cross > c - win) & (cross < c + win) & (cross != c)):
                continue                        # jittering about zero, not a turn
            if not np.all(size[c - win:c] < 3.0 * np.maximum(ang[c - win:c], 0.6)):
                continue
            d = np.sign(after)
            resp = np.flatnonzero(vvs[c - gap:c + int(0.6 / step)] * d > 0.5 * abs(after))
            if len(resp):
                out.append(float(ts[c - gap + resp[0]] - ts[c]))
    return out


def _sprays(cap):
    """Each burst of firing that lasted: the pull against the recoil (in the input itself, before
    any game rule touches it), measured against the moment fire went down."""
    head = cap.head
    cfg = head.get("cfg", {})
    inv = bool(cfg.get("aim", {}).get("invert_y"))
    kbm = bool(head.get("kbm"))
    t = cap.t
    fire = cap.col("fire") > 0.5
    ay = cap.col("ay")
    # the input that turns the view up (positive), whatever the device and inversion
    if kbm:
        up = -ay if not inv else ay
    else:
        up = ay if not inv else -ay
    comp = -up                                  # pulling against a climb
    out = []
    edges = np.flatnonzero(np.diff(fire.astype(np.int8)))
    starts = [i + 1 for i in edges if fire[i + 1]]
    for s in starts:
        e = s
        while e < len(fire) and fire[e]:
            e += 1
        dur = t[e - 1] - t[s]
        if dur < 0.2:
            continue
        dt = float(np.median(np.diff(t[s:e]))) if e - s > 2 else 1 / 240
        if kbm:
            # counts per frame -> counts per second; a 240 Hz frame of a 1000 Hz mouse has 4 reports
            series = comp / np.maximum(np.diff(np.concatenate([[t[0] - dt], t])), 1e-4)
        else:
            series = comp
        base = float(np.mean(series[max(s - int(0.06 / dt), 0):s])) if s > 0 else 0.0
        seg = series[s:e] - base
        if len(seg) < 10:
            continue
        mid = seg[len(seg) // 5:]
        level = float(np.median(mid))
        if level <= 0:                          # not pulling down at all: nothing to judge
            out.append({"pull": False, "dur": float(dur)})
            continue
        # onset: when the pull first reached half of its steady level
        on = np.nonzero(seg >= 0.5 * level)[0]
        onset = float(on[0] * dt) if len(on) else None
        # roughness: how much it wanders frame to frame, against its level (a hand's tremor and
        # corrections; a script's pull is flat, or steps once per bullet)
        rough = float(np.median(np.abs(np.diff(mid))) / max(abs(level), 1e-6))
        cv = float(np.std(mid) / max(abs(level), 1e-6))
        # the edges: how much of the pull arrived in the frame fire went down, and left in the frame
        # it came up. A script adds its pull in one step and takes it away in one; a thumb or a hand
        # ramps into it (or was already pulling) and eases off.
        # (a mouse's counts come in whole numbers: a weak pull is 0, 1, 0, 1 a frame, so it is averaged
        # over 4 frames - 16 ms, still far quicker than a hand's 50-100 ms ramp)
        k = 4 if kbm else 2
        pre = series[max(s - k - 1, 0):s].mean() if s >= 1 else series[s]
        step_on = float((series[s:s + k].mean() - pre) / level)
        post = series[e:e + k] if e + k <= len(series) else series[e:]
        step_off = float((series[e - k:e].mean() - post.mean()) / level) if len(post) else None
        out.append({"pull": True, "dur": float(dur), "onset": onset, "rough": rough, "cv": cv, "level": level,
                    "step_on": step_on, "step_off": step_off})
    return out


def analyse(cap) -> dict:
    """Every detector's evidence for one captured run (see the notes at the top)."""
    res = {"detectors": {}, "notes": []}
    D = res["detectors"]
    if cap is None or cap.frames < 240:
        res["notes"].append("too short to judge")
        return res
    try:
        st = _static(cap)
    except Exception as e:                       # a capture the analysis does not understand
        st = []
        res["notes"].append(f"static analysis skipped: {type(e).__name__}")
    # ---- instant lock: fresh targets turned onto faster than sight allows
    fresh = [r for r in st if r["fresh"] and r["rt"] is not None and r["jump"] is not None]
    if len(fresh) >= MIN_STATIC:
        # turned onto it before sight could have reported it, and at full speed from the start (a person
        # drifting towards where they know one will be starts slowly - that is not this)
        fast = [r for r in fresh if r["rt"] < 0.085 and r["jump"] >= 0.4]
        share = len(fast) / len(fresh)
        D["instant_lock"] = {"n": len(fresh), "value": share, "median_rt_ms": float(np.median([r["rt"] for r in fresh]) * 1000),
                             "level": "flag" if share >= 0.5 else ("suspect" if share >= 0.3 else "ok"),
                             "say": f"{share * 100:.0f}% of {len(fresh)} new targets were turned onto within 85 ms of appearing"}
    # ---- clockwork: reactions to new targets all the same length. A person's vary from one to the
    # next by 25 ms and more (spread of a reaction time); a program waits the same time every time.
    rts = [r["rt"] for r in st if r["fresh"] and r["rt"] is not None and r["rt"] > 0]
    if len(rts) >= 8:
        q1, q3 = np.percentile(rts, [25, 75])
        spread = float((q3 - q1) / 1.35)
        # (a person reacting to something on a beat they can feel - a shield opening on a rhythm -
        # can get down to about 10 ms; nobody to 4)
        lvl = "flag" if spread < 0.004 and len(rts) >= 10 else ("suspect" if spread < 0.006 else "ok")
        D["clockwork"] = {"n": len(rts), "value": spread, "level": lvl,
                          "say": f"reactions to new targets varied by only {spread * 1000:.0f} ms "
                                 f"(median {float(np.median(rts)) * 1000:.0f} ms; a person: 10 ms and more)"}
    # ---- smoothing: the first movement fastest at its start. A hand starts slowly (a turn's first
    # frames are a small share of its top speed: 5-20% on the real runs this was checked on); an aimbot
    # closing a share of the gap every frame is at full speed from the first frame.
    prof = [r for r in st if r["jump"] is not None]
    hard = [r for r in prof if r["jump"] >= 0.85]
    if len(hard) >= 3 and len(prof) < MIN_STATIC:
        # too few turns for a median, but three at full speed from the first frame is enough: of about
        # 500 turns on the real runs this was checked on, none started above 65% of its top speed
        D["smoothing"] = {"n": len(prof), "value": 1.0, "ttp": 0.0, "level": "flag",
                          "say": f"{len(hard)} turns started at full speed from the first frame (a hand never does)"}
    elif len(prof) >= MIN_STATIC:
        ttp = float(np.median([r["ttp"] for r in prof]))
        jump = float(np.median([r["jump"] for r in prof]))
        lvl = "flag" if jump >= 0.5 else ("suspect" if jump >= 0.35 else "ok")
        D["smoothing"] = {"n": len(prof), "value": jump, "ttp": ttp, "level": lvl,
                          "say": f"turns started at {jump * 100:.0f}% of their top speed (a hand: about 10%) and "
                                 f"peaked {ttp * 100:.0f}% of the way through"}
    # ---- triggerbot: the first shot a frame or two after arriving, every time, and never early
    shots = [r for r in st if r.get("first_hit") is not None]
    if len(shots) >= 10:
        on = [r for r in shots if r["t_on"] is not None]
        quick = [r for r in on if 0.0 <= r["first_shot"] - r["t_on"] <= 0.015]
        share = len(quick) / len(shots)
        hit = sum(1 for r in shots if r["first_hit"]) / len(shots)
        lvl = "flag" if (share >= 0.8 and hit >= 0.95) else ("suspect" if share >= 0.6 and hit >= 0.9 else "ok")
        D["triggerbot"] = {"n": len(shots), "value": share, "first_hit": hit, "level": lvl,
                           "say": f"{share * 100:.0f}% of first shots came within 15 ms of reaching the target, "
                                  f"{hit * 100:.0f}% of them hit"}
    # ---- reversals: turning with a moving target before its change of direction could be seen
    try:
        fl = _flips(cap)
    except Exception:
        fl = []
    if len(fl) >= 8:
        med = float(np.median(fl))
        quick = sum(1 for x in fl if 0.0 <= x <= 0.06) / len(fl)
        lvl = "flag" if quick >= 0.7 else ("suspect" if quick >= 0.5 else "ok")
        D["reversal"] = {"n": len(fl), "value": quick, "median_ms": med * 1000, "level": lvl,
                         "say": f"turned with the target within 60 ms of it changing direction {quick * 100:.0f}% "
                                f"of the time (median {med * 1000:.0f} ms; people: 120 ms and more)"}
    # ---- machine path: turns as straight as a ruler. A hand's turns bow (the wrist and the thumb
    # pivot): on the real runs this was checked on, the median turn bowed off its straight line by
    # 1.6-3.8% of its length. A planned movement - a "humanized" aimbot's - bows by next to nothing.
    # Humanizers can add a curve on purpose, so this alone never flags a run; it can only add to
    # something else.
    paths = [r for r in st if r["straight"] is not None and not r["moving"]]
    if len(paths) >= 8:
        med = float(np.median([r["straight"] for r in paths]))
        lvl = "suspect" if med < 0.011 else "ok"
        D["machine_path"] = {"n": len(paths), "value": med, "level": lvl,
                             "say": f"turns bowed off a straight line by {med * 100:.1f}% of their length "
                                    "(a hand: 1.5% and more)"}
    # ---- anti-recoil
    try:
        sp = [s for s in _sprays(cap) if s.get("pull")]
    except Exception:
        sp = []
    sp = [x for x in sp if x.get("step_off") is not None]
    if len(sp) >= MIN_SPRAY:
        edge = float(np.median([min(x["step_on"], x["step_off"]) for x in sp]))
        need = 0.35 if len(sp) >= 6 else 0.5
        lvl = "flag" if edge >= need else ("suspect" if edge >= need * 0.7 else "ok")
        D["anti_recoil"] = {"n": len(sp), "value": edge, "level": lvl,
                            "say": f"the pull against recoil switched on and off with the trigger, {edge * 100:.0f}% of it "
                                   "in a single frame (a hand ramps in and eases off: under 10%)"}
    return res
