"""Drive the sensitivity finder offscreen with the self-test bot: two rounds of hip, apply, one of ADS."""
import json, os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import config, sensfinder
from aimtuner.app import App
from aimtuner.bot import Bot

scratch = tempfile.mkdtemp(prefix="aimtuner-sens-")
shots = sys.argv[1] if len(sys.argv) > 1 else os.path.join(scratch, "shots")
opts = types.SimpleNamespace(windowed=False, offscreen=(1920, 1080), bench=False, selftest=True, only=None,
                             repeats=1, shots=None, duration=3.0, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
app.bot = Bot(app)
errors = []


def expect(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


def key(*names):
    app.keys.update(names)
    yield


def wait(pred, secs=240.0, what=""):
    end = time.perf_counter() + secs
    while not pred():
        if time.perf_counter() > end:
            expect(False, "timeout: " + what)
            return
        if isinstance(app.screens[-1], sensfinder.SensFinder) and app.screens[-1].state == "rest" and app.game is None:
            app.keys.add("enter")
        yield


def shot(name):
    app.pending_shot = os.path.join(shots, name + ".png")
    yield
    yield


def script():
    c = app.cfg["cod"]
    config_rates = config.cod_rates(app.cfg)
    S = sensfinder.SensFinder(app)
    app.push(S)
    for _ in range(5):
        yield
    yield from shot("sens_1_intro")
    yield from key("enter")
    yield from wait(lambda: app.game is not None, what="first block")
    g = app.game
    expect(g.kit is not None and g.over["sens"]["hip"] in S.queue
           and abs(g.kit.rate_h - g.over["sens"]["hip"] * config_rates[0]) < 1e-6, "block runs at a planned value")
    expect(abs(g.kit.rate_h * g.kit.ads_scale - 644.0 * 0.254) < 1.0,
           f"hip pass holds ADS speed constant: {g.kit.rate_h * g.kit.ads_scale:.1f} deg/s")
    yield from wait(lambda: S.state == "result", what="round 1")
    expect(len(S.blocks["hip"]) == 10 and S.fits["hip"] is not None, f"10 blocks, fitted: {S.fits['hip'] and round(S.fits['hip']['best'], 2)}")
    vals = [b["value"] for b in S.blocks["hip"]]
    expect(vals[:5] == vals[5:][::-1] and len(set(vals)) == 5, f"mirrored order: {vals}")
    yield from shot("sens_2_result_round1")
    yield from key("enter")
    yield from wait(lambda: S.state == "result" and len(S.blocks["hip"]) == 20, what="round 2")
    f = S.fits["hip"]
    print("   hip fit:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in f.items() if k not in ("coef", "wide", "adj")})
    yield from shot("sens_3_result_round2")
    old_ads_speed = c["sens_h"] * c["ads_mult"]
    yield from key("x")
    expect(abs(c["sens_h"] - round(f["best"], 2)) < 1e-9 and S.phase == "ads" and S.state == "intro", f"applied: {c['sens_h']}")
    want = old_ads_speed / c["sens_h"]              # the multiplier that keeps ADS speed where it was
    if 0.10 <= want <= 2.00:
        expect(abs(c["sens_h"] * c["ads_mult"] - old_ads_speed) / old_ads_speed < 0.02, "ADS speed preserved on apply")
    else:       # the bot's "best" can land where the game's 0.10-2.00 multiplier cannot follow
        expect(c["ads_mult"] in (0.10, 2.00) and "end of the scale" in S.msg,
               f"ADS multiplier would need {want:.2f}: held at the end of the scale, and the player is told")
    yield from shot("sens_4_intro_ads")
    yield from key("enter")
    yield from wait(lambda: S.state == "result", what="ads round")
    expect(len(S.blocks["ads"]) == 10, "ads blocks")
    fa = S.fits["ads"]
    print("   ads fit:", fa and {k: (round(v, 3) if isinstance(v, float) else v) for k, v in fa.items() if k not in ("coef", "wide", "adj")})
    yield from shot("sens_5_result_ads")
    expect(S.rounds["ads"] == 1 and S.rounds["hip"] == 2, "one ADS round played (the result screen calls that rough), two of hip")
    yield from key("x")
    expect(fa is None or abs(c["ads_mult"] - round(fa["best"], 2)) < 1e-9, f"ads applied: {c['ads_mult']}")
    # the ADS answer is a speed: it must survive stick sensitivity being changed afterwards, by hand
    held = c["ads_hold"]
    expect(held is not None and abs(held - c["ads_mult"] * c["sens_h"]) < 1e-6, f"ADS result kept as a speed: {held}")
    from aimtuner import screens
    st = screens.Settings(app, start="cod.sens_h")
    row = st.rows[st.sel]
    before = (c["sens_h"], c["ads_mult"])
    step = -1 if c["sens_h"] > 1.0 else 1
    for _ in range(4):
        st._change(row, step)                       # four clicks of 0.05 on stick sensitivity
    want = min(max(held / c["sens_h"], 0.10), 2.00)
    expect(abs(c["sens_h"] - before[0]) > 0.15 and abs(c["ads_mult"] - want) < 0.006,
           f"changing stick sensitivity {before[0]:.2f} -> {c['sens_h']:.2f} moved the multiplier {before[1]:.2f} -> {c['ads_mult']:.2f}")
    if 0.10 < want < 2.00:
        expect(abs(c["sens_h"] * c["ads_mult"] - held) / held < 0.01, "so ADS speed is what the finder found")
    st2 = screens.Settings(app, start="cod.ads_mult")
    st2._change(st2.rows[st2.sel], 1)
    expect(c["ads_hold"] is None, "setting the multiplier by hand lets go of the held speed")
    by_hand = c["ads_mult"]
    st._change(row, step)
    expect(c["ads_mult"] == by_hand and c["ads_hold"] is None, "after that, stick sensitivity leaves the multiplier alone")
    c["sens_h"] = before[0]
    app.save()
    recs = sensfinder.load(scratch)
    expect(len(recs) == 30 and not os.path.exists(os.path.join(scratch, "runs.jsonl")), f"30 blocks in sens.jsonl, nothing in the run log ({len(recs)})")
    saved = json.load(open(opts.config, encoding="utf-8"))
    expect(saved["cod"]["sens_h"] == c["sens_h"], "config saved")

    print("another session builds on the first")
    expect(all(r.get("sights") and r["sights_unit"] == "deg/s" for r in recs if r["phase"] == "ads")
           and not any("sights" in r for r in recs if r["phase"] == "hip"),
           "each ADS block is logged with how fast the sights turned, in deg/s")
    r0 = next(r for r in recs if r["phase"] == "ads")
    expect(abs(r0["sights"] - r0["value"] * c["sens_h"] * config_rates[0] * config_rates[1]) < 0.01,
           f"   e.g. multiplier {r0['value']:.2f} at stick sensitivity {c['sens_h']:.2f} was {r0['sights']:.1f} deg/s")
    app.pop()
    while time.strftime("%Y%m%d-%H%M%S") == S.session:
        yield
    S2 = sensfinder.SensFinder(app)
    expect(S2.session != S.session and len(S2.blocks["hip"]) == 20 and len(S2.blocks["ads"]) == 10
           and S2.before == {"hip": 20, "ads": 10} and S2.rounds == {"hip": 2, "ads": 1},
           "a new finder opens with the earlier session already counted: 20 + 10 blocks, 2 rounds and 1")
    expect(S2.fits["hip"] is not None and abs(S2.fits["hip"]["best"] - f["best"]) < 1e-9 and S2.level == S.level,
           f"so its answer and how far the search has closed in are where the first session left them ({S2.level})")
    expect(all(r["context"] == S2._context() for ph in ("hip", "ads") for r in S2.blocks[ph]), "only blocks played under these conditions count")
    style0 = c["aa_style"]
    c["aa_style"] = "classic" if style0 != "classic" else "bo7"
    other = sensfinder.SensFinder(app)
    expect(not other.blocks["hip"] and not other.blocks["ads"], "under another aim-assist style nothing is carried over")
    c["aa_style"] = style0

    # An ADS block tested a speed. At another stick sensitivity the same speed is another multiplier,
    # so that is where the block is placed - or nowhere, if the scale cannot reach it.
    as_played = [p[0] for p in S2.pts["ads"]]
    expect(as_played == [r["value"] for r in S2.blocks["ads"]], "at the same stick sensitivity the ADS blocks sit where they were played")
    hip0 = c["sens_h"]
    c["sens_h"] = round(hip0 * (0.8 if hip0 > 1.0 else 1.25), 2)
    k = hip0 / c["sens_h"]
    S2._refit("ads")
    ok, kept = True, 0
    for r in S2.blocks["ads"]:
        v, want = S2._placed(r, {}), r["value"] * k
        kept += v is not None
        if 0.101 <= want <= 1.99:           # (a block within a hair of an end of the scale may go either way)
            ok = ok and v is not None and abs(v / want - 1.0) < 1e-4 and abs(S2._sights_at(v) / r["sights"] - 1.0) < 1e-4
        elif want < 0.099 or want > 2.01:
            ok = ok and v is None
    expect(ok and len(S2.pts["ads"]) == kept,
           f"stick sensitivity {hip0:.2f} -> {c['sens_h']:.2f}: each ADS block moves to the multiplier that gives its speed "
           f"(x{k:.2f}); {len(S2.blocks['ads']) - kept} fall off the 0.10 - 2.00 scale and are left out")
    c["sens_h"] = hip0
    S2._refit("ads")
    expect([p[0] for p in S2.pts["ads"]] == as_played, "and back again")

    app.push(S2)
    yield from shot("sens_6_intro_second_session")
    yield from key("enter")
    yield from wait(lambda: S2.state == "result" and len(S2.blocks["hip"]) == 30, what="a round in the second session")
    f2 = S2.fits["hip"]
    new = S2.blocks["hip"][20:]
    expect(f2["sessions"] == 2 and len(f2["adj"]) == 30 and S2.rounds["hip"] == 3 and all(r["session"] == S2.session for r in new),
           f"its round is fitted together with the first session's two: 30 blocks, 2 sessions, best {f2['best']:.2f}")
    expect(len({r["value"] for r in new}) == 5 and all(r["level"] == S.level["hip"] for r in new),
           "and it was planned from where the search had got to, not from scratch")
    mean = lambda rs: sum(rs) / len(rs)
    gap = abs(mean(f2["adj"][:20]) - mean(f2["adj"][20:])) / f2["scale"]
    print(f"   session levels after levelling differ by {100 * gap:.1f}% of the mean score")
    yield from shot("sens_7_result_two_sessions")
    yield from key("esc")
    expect(S2.state == "intro", "back to the first screen")

    print("starting a search over")
    S2.phase = "hip"
    yield from key("y")
    expect(S2.confirm == "hip" and len(S2.blocks["hip"]) == 30, "Y asks first")
    yield from shot("sens_8_start_over")
    yield from key("down")
    expect(S2.confirm is None and S2.phase == "ads" and len(S2.blocks["hip"]) == 30, "any other key keeps the blocks")
    yield from key("up")
    yield from key("y")
    yield from key("y")
    expect(not S2.blocks["hip"] and S2.fits["hip"] is None and S2.rounds["hip"] == 0 and S2.level["hip"] == 0
           and len(S2.blocks["ads"]) == 10 and "started over" in S2.msg, "Y twice starts the stick sensitivity search over; the ADS search is untouched")
    recs = sensfinder.load(scratch)
    expect(len(recs) == 41 and sum(1 for r in recs if r.get("reset")) == 1 and sum(1 for r in recs if r.get("phase") == "hip" and "value" in r) == 30,
           "nothing was deleted: all 40 blocks are still in the log, with one line marking the spot")
    S3 = sensfinder.SensFinder(app)
    expect(not S3.blocks["hip"] and len(S3.blocks["ads"]) == 10, "and the next session starts that search from nothing")
    yield from key("enter")
    yield from wait(lambda: app.game is not None, what="a block after starting over")
    expect(len(set(S2.queue)) == 5 and S2.level["hip"] == 0, "the next round is a wide first round again")
    app.game.abort()
    yield


class Auto:
    def __init__(self):
        self.gen = script()

    def step(self, now):
        try:
            next(self.gen)
        except StopIteration:
            app.quit()


app.auto = Auto()
app.run()
print("sens finder:", "all passed" if not errors else f"FAILED {errors}")
sys.exit(1 if errors else 0)
