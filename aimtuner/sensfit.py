"""Where does performance peak? The curve fit behind the sensitivity finder.

Scores from short drills are noisy - the same player at the same sensitivity
swings several percent from one block to the next - so the best value is never
read off the single best block. Instead a parabola is fitted through the blocks
against the LOG of the sensitivity (halving and doubling are equally big steps),
with a term for drift over the session (warming up, tiring), and the top of that
parabola is the answer. Blocks near the top count for more than blocks far from
it: a parabola only describes a real performance curve close to its peak.

Two things keep the stated range honest:
  * the fit is repeated on resampled blocks (a bootstrap), which shows how far
    noise alone can move the answer;
  * a second, lopsided curve (its own steepness on each side of the peak) is
    fitted as well. A parabola is symmetric, and if your real curve is not -
    too fast costing more than too slow, say - the two disagree. The range is
    stretched to take in both.

In simulation, with scores that vary 8% from block to block, the best value comes
out within about 2-3% of the truth when the real curve is symmetric or flat-topped
and within about 5-8% when it is strongly lopsided, and the range holds the truth
about four times in five. Expect a range of roughly +/-10% after two rounds. That
is not a weakness of the method so much as a fact about sensitivity: the top of
the curve is flat, which is also why `plateau` is reported.

Blocks from several sessions go into one fit. You do not score the same on every
day - and the days did not test the same values, since each closes in further than
the last - so putting raw scores together would read "I was sharper on Thursday"
as "Thursday's values are better". Each session therefore gets a level of its own
(and its own drift): the one curve is fitted to how each session's blocks compare
with each other, never to how one day compares with another.
"""
from __future__ import annotations

import math

import numpy as np

BAND = 0.30         # log units, about +/-35%: blocks this far from the peak still count 60%
GRID = 29           # trial peak positions for the lopsided fit
PLATEAU = 0.02      # "as good as the best" = within this share of the fitted peak
DRIFT_MIN = 6       # blocks a session needs before it gets a drift term of its own (a level it always has)


def _within(rows, w, ses):
    """Take each session's own level - and drift, where it has one - out of every row, by weighted
    least squares inside the session. Fitting what is left gives the same curve as fitting the
    levels and drifts along with it, and stays quick however many sessions there are.

    rows (m, n); w (n,) weights; ses = (G, which, t, can): G (S, n) is 1 where a block belongs to a
    session, which (n,) says the same as indices, t (n,) is a block's time within its session and
    can (S,) which sessions have a drift.  -> what is left (m, n), levels (m, S), drifts (m, S)"""
    G, which, t, can = ses
    sw, swt, swtt = G @ w, G @ (w * t), G @ (w * t * t)
    den = sw * swtt - swt * swt
    ok = can & (den > 1e-9 * sw * swtt) & (swtt > 0.0)
    wr = rows * w
    swv, swtv = wr @ G.T, (wr * t) @ G.T
    slope = np.where(ok, (sw * swtv - swt * swv) / np.where(ok, den, 1.0), 0.0)
    lev = np.where(sw > 0.0, (swv - slope * swt) / np.where(sw > 0.0, sw, 1.0), 0.0)
    return rows - lev[:, which] - slope[:, which] * t, lev, slope


def _wls(X, y, w, ses):
    """Weighted least squares of y on the rows of X and every session's level and drift.
    -> (a, b, levels (S,), drifts (S,))"""
    ww = np.ones(len(y)) if w is None else w
    left, _, _ = _within(np.vstack([X, y]), ww, ses)
    sq = np.sqrt(ww)
    beta, *_ = np.linalg.lstsq((left[:-1] * sq).T, left[-1] * sq, rcond=None)
    _, lev, slope = _within((y - beta @ X)[None, :], ww, ses)
    return float(beta[0]), float(beta[1]), lev[0], slope[0]


def _top(a, b, lo, hi):
    """x of the maximum of a*x^2 + b*x within [lo, hi], and the edge it ran into (-1, 0, +1)."""
    if a < -1e-9:
        xs = -b / (2.0 * a)
        if lo <= xs <= hi:
            return xs, 0
        return (lo, -1) if xs < lo else (hi, 1)
    # no interior maximum (still climbing, or noise bent it the wrong way): best end of what was tested
    return (lo, -1) if a * lo * lo + b * lo > a * hi * hi + b * hi else (hi, 1)


def _quad(x, y, ses, lo, hi):
    """Fit y = a*x^2 + b*x + (each session's level and drift) to everything, then twice more with
    the weight on blocks near the peak.
    -> (peak x, edge, coefficients used, weights or None, coefficients of the first fit)"""
    X = np.vstack([x * x, x])
    wide = coef = _wls(X, y, None, ses)
    xs, side = _top(coef[0], coef[1], lo, hi)
    w = None
    for _ in range(3):
        w2 = np.exp(-0.5 * ((x - xs) / BAND) ** 2)
        if len(np.unique(np.round(x[w2 > 0.4], 4))) < 4 or w2.sum() < 8.0:
            break                               # too little data near the peak to stand on its own
        c2 = _wls(X, y, w2, ses)
        x2, s2 = _top(c2[0], c2[1], lo, hi)
        if s2 and not side:
            break                               # had a peak, lost it close up: trust the wider view
        # (the other way round is welcome: distant blocks can make a curve that has already
        # turned over near the edge look as if it were still climbing)
        xs, side, coef, w = x2, s2, c2, w2
    return xs, side, coef, w, wide


def _split(x, y, ses, lo, hi, xs, w):
    """Peak of a two-sided parabola - its own curvature on each side - or None if no
    position gives a proper peak. Tried at GRID positions around the symmetric answer."""
    m = np.linspace(max(lo, xs - 0.35), min(hi, xs + 0.35), GRID)
    d = x[None, :] - m[:, None]
    L, R = np.minimum(d, 0.0) ** 2, np.maximum(d, 0.0) ** 2
    ok = ((L > 0).sum(1) >= 3) & ((R > 0).sum(1) >= 3)
    if not ok.any():
        return None
    L, R, m = L[ok], R[ok], m[ok]
    g, n = L.shape
    ww = np.ones(n) if w is None else w
    left, _, _ = _within(np.vstack([L, R, y[None, :]]), ww, ses)
    L, R, yl = left[:g], left[g:2 * g], left[-1]
    # with the sessions' levels and drifts out, each position is two unknowns: the two curvatures
    LL, RR, LR = (ww * L * L).sum(1) + 1e-9, (ww * R * R).sum(1) + 1e-9, (ww * L * R).sum(1)
    Ly, Ry = (ww * L * yl).sum(1), (ww * R * yl).sum(1)
    det = LL * RR - LR * LR
    cl, cr = (Ly * RR - Ry * LR) / det, (Ry * LL - Ly * LR) / det
    r = cl[:, None] * L + cr[:, None] * R - yl[None, :]
    sse = (ww[None, :] * r * r).sum(1)
    sse[(cl >= 0.0) | (cr >= 0.0) | ~np.isfinite(sse)] = np.inf     # must fall away on both sides
    k = int(np.argmin(sse))
    return float(m[k]) if np.isfinite(sse[k]) else None


def _noise(x, y):
    """Block-to-block scatter, from blocks played at the same value (each is played twice a round)."""
    num = den = 0.0
    for v in np.unique(np.round(x, 4)):
        grp = y[np.abs(x - v) < 1e-4]
        if len(grp) > 1:
            num += float(((grp - grp.mean()) ** 2).sum())
            den += len(grp) - 1
    return math.sqrt(num / den) if den > 0 else 0.0


def fit(blocks, seed: int = 7, draws: int = 300):
    """blocks: [(value, performance, order_index), ...] or, from more than one session,
    [(value, performance, order_index, session), ...] - see the module notes.

    Returns None with fewer than three distinct values (or, across sessions, when no session
    compares enough values with each other to place a curve), else a dict:
      best      value at the fitted peak
      lo, hi    the range the data supports (see the module notes)
      side      -1 / +1 when the peak is at the low / high limit of what was tested, so
                the real optimum may lie beyond it and the next round should look there
      edge      the same as a yes / no
      plateau   (lo, hi): values the fit puts within 2% of the best, or None
      coef      (a, b, c, d) of the fit near the peak, on log(value) and performance / mean;
                c is the sessions' levels averaged, d the first session's drift
      wide      the same for the fit through every block
      span      (lo, hi): the values over which `coef` describes the data
      scale     the mean performance the fit was normalised by
      noise_pct block-to-block scatter, percent of the mean
      sessions  how many sessions the blocks came from
      adj       each block's performance with its session's level taken out: what it would have
                scored on an average day. (With one session that is the block's own score.)
    """
    vals = np.array([b[0] for b in blocks], float)
    perf = np.array([b[1] for b in blocks], float)
    order = np.array([b[2] for b in blocks], float)
    if len(set(np.round(vals, 4))) < 3 or perf.mean() <= 0:
        return None
    ids = [b[3] if len(b) > 3 else None for b in blocks]
    names = list(dict.fromkeys(ids))            # the sessions, in the order they were played
    which = np.array([names.index(i) for i in ids])
    S, n = len(names), len(vals)
    x = np.log(vals)
    scale = float(perf.mean())
    # Each session is put on its own scale (its mean = 1) and then given a level of its own in the
    # fit; a drift too, if it is long enough to show one. With one session this is the plain fit:
    # y = perf / mean, one level, one drift.
    y = np.empty(n)
    G = np.zeros((S, n))
    G[which, np.arange(n)] = 1.0
    t, can = np.zeros(n), np.zeros(S, bool)
    for s in range(S):
        m = which == s
        mean = float(perf[m].mean())
        y[m] = perf[m] / (mean if mean > 0.0 else scale)
        if S == 1 or m.sum() >= DRIFT_MIN:
            can[s] = True
            t[m] = (order[m] - order[m].mean()) / max(float(np.ptp(order[m])), 1.0)
    if S > 1:
        # only values compared within a session say anything about the curve
        if sum(len(set(np.round(x[which == s], 4))) - 1 for s in range(S)) < 2:
            return None
        if n <= 2 + S + int(can.sum()):
            can[:], t[:] = False, 0.0
        if n <= 2 + S:
            return None
    ses = (G, which, t, can)
    lo, hi = float(x.min()), float(x.max())
    xs, side, coef, w, wide = _quad(x, y, ses, lo, hi)
    alt = None if side else _split(x, y, ses, lo, hi, xs, w)

    rng = np.random.default_rng(seed)
    sym, lop = [], []
    for _ in range(draws):
        idx = rng.integers(0, n, n)
        if len(set(np.round(x[idx], 4))) < 3:
            continue
        drawn = (G[:, idx], which[idx], t[idx], can)
        q, s, _, wq, _ = _quad(x[idx], y[idx], drawn, lo, hi)
        sym.append(q)
        a = None if s else _split(x[idx], y[idx], drawn, lo, hi, q, wq)
        lop.append(q if a is None else a)
    ends = [xs] if alt is None else [xs, alt]
    if sym:
        ends += [float(v) for v in np.percentile(sym, [10, 90])] + [float(v) for v in np.percentile(lop, [10, 90])]
    else:
        ends += [lo, hi]

    def flat(co, wt):
        """(a, b, c, d) with one level for the curve to be drawn at: the sessions' own, averaged by
        how much each one counted."""
        share = G @ (np.ones(n) if wt is None else wt)
        return co[0], co[1], float((co[2] * share).sum() / max(float(share.sum()), 1e-12)), float(co[3][0])

    levels = coef[2]
    coef, wide = flat(coef, w), flat(wide, None)
    a, b, c, _ = coef
    adj = y - levels[which] + c                 # every block as if played at the average level
    plateau = None
    peak_y = a * xs * xs + b * xs + c
    if not side and a < -1e-9 and peak_y > 0.0:
        half = math.sqrt(PLATEAU * peak_y / -a)
        plateau = (math.exp(max(xs - half, lo)), math.exp(min(xs + half, hi)))
    reach = 2.15 * BAND                 # where a block's weight has fallen to a tenth
    span = (math.exp(lo), math.exp(hi)) if w is None else (math.exp(max(lo, xs - reach)), math.exp(min(hi, xs + reach)))
    return {
        "best": math.exp(xs), "lo": math.exp(min(ends)), "hi": math.exp(max(ends)),
        "side": side, "edge": bool(side), "plateau": plateau,
        "alt": None if alt is None else math.exp(alt),
        "coef": coef, "wide": wide, "span": span, "scale": scale, "n": n,
        "noise_pct": 100.0 * _noise(x, adj),
        "tested": (math.exp(lo), math.exp(hi)),
        "sessions": S, "adj": [float(v) * scale for v in adj],
    }


def curve(fit_: dict, value: float, wide: bool = False) -> float:
    """Fitted performance (same units as the blocks) at a value, with drift taken out.
    wide=True uses the fit through every block, for values outside `span`."""
    a, b, c, _ = fit_["wide" if wide else "coef"]
    x = math.log(value)
    return (a * x * x + b * x + c) * fit_["scale"]


LEVELS = (
    (0.60, 0.78, 1.00, 1.28, 1.65),     # wide: find the neighbourhood
    (0.80, 0.90, 1.00, 1.11, 1.25),     # then close in
    (0.88, 0.94, 1.00, 1.06, 1.13),     # and again, for every round after that
)
# the peak was at the low edge of everything tested: reach well below it, keep two values as anchors
BEYOND = (0.50, 0.64, 0.80, 1.00, 1.22)
PINNED = 0.02       # log units: closer than 2% to an end of the game's scale counts as being at it


def pinned(fit_: dict, lo: float, hi: float) -> bool:
    """Is the best value at an end of the game's own scale (so there is nothing further out to try)?"""
    return math.log(fit_["best"] / lo) <= PINNED or math.log(hi / fit_["best"]) <= PINNED


def next_level(level: int, fit_, lo: float, hi: float) -> int:
    """The search closes in once the peak is inside what was tested - or pinned at an end of
    the scale. While the peak is at the edge of what was tested, it keeps reaching further instead."""
    if not fit_:
        return level
    return level + 1 if (not fit_["side"] or pinned(fit_, lo, hi)) else level


def plan(centre: float, level: int, lo: float, hi: float, rng, side: int = 0, digits: int = 2) -> list:
    """Values for the next round: a spread around `centre`, played in a shuffled
    order and then that order reversed, so any steady drift cancels out.

    level  how far the search has closed in (it only does so once the peak is inside
           what was tested); side: -1 / +1 when the last fit ran into that edge;
    digits the decimals the scale is set in (2 for the game's 0.01 steps, 1 for centimetres)."""
    step = 10.0 ** -digits
    centre = min(max(centre, lo), hi)
    room_lo, room_hi = math.log(centre / lo), math.log(hi / centre)     # how far the game's scale goes each way
    if side < 0 and room_lo > PINNED:
        factors = BEYOND
    elif side > 0 and room_hi > PINNED:
        factors = tuple(1.0 / f for f in reversed(BEYOND))
    else:
        factors = LEVELS[min(level, len(LEVELS) - 1)]
    offs = [math.log(f) for f in factors]
    # Where the scale ends before the spread does, squeeze that side to fit; with no room at
    # all on that side, slide the whole spread inwards so that it ends on the limit.
    for sign, room in ((-1.0, room_lo), (1.0, room_hi)):
        reach = max(sign * o for o in offs)
        if room <= PINNED:
            offs = [o - sign * reach for o in offs]
        elif reach > room:
            offs = [o * room / reach if sign * o > 0 else o for o in offs]
    vals = {round(min(max(centre * math.exp(o), lo), hi), digits) for o in offs}
    # the scale moves in steps (of 0.01 in the game), so at small values neighbours can still land on each other
    ratio = (factors[-1] / factors[0]) ** (1.0 / (len(factors) - 1))
    for _ in range(40):
        if len(vals) >= len(factors):
            break
        top, bottom = max(vals), min(vals)
        up = round(min(max(top * ratio, top + step), hi), digits)
        down = round(max(min(bottom / ratio, bottom - step), lo), digits)
        if top >= hi and down < bottom:         # folded at the top of the scale: add below
            vals.add(down)
        elif up > top:                          # folded at the bottom, or rounding merged two values
            vals.add(up)
        elif down < bottom:
            vals.add(down)
        else:
            break
    vals = sorted(vals)
    order = vals[:]
    rng.shuffle(order)
    return order + order[::-1]
