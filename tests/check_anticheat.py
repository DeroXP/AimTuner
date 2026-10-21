"""The anti-cheat (aimtuner/ac/): input provenance, the run verdict, the system checks, the sealed
log, share codes, the build signature, and the behavioural detectors - against simulated cheats
(tests/cheatsim.py, played through the real game) and against human play (the ghost, and the real
captures in logs/captures when there are any), none of which may be flagged.

    python tests/check_anticheat.py
"""
import copy, json, os, struct, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glob

import glfw
import numpy as np

from aimtuner import ac, capture, ghost, mouse, stats, tracks
from aimtuner.ac import behavior, seal, system
from aimtuner.app import App
from aimtuner.capture import V
from cheatsim import Cheat

errors, checks = [], [0]


def expect(cond, msg):
    checks[0] += 1
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        errors.append(msg)


# ------------------------------------------------------------------ input provenance (mouse.parse)
def rawmouse(hdev, dx, dy, bflags=0, flags=0):
    body = struct.pack("<HxxHHIiiI", flags, bflags, 0, 0, dx, dy, 0)
    head = struct.pack("<IIQQ", 0, 24 + len(body), hdev, 0)
    rec = head + body
    return rec + b"\0" * ((-len(rec)) % 8)


def rawkey(hdev, vk, up=False):
    body = struct.pack("<HHHHII", 0, 1 if up else 0, 0, vk, 0, 0)
    head = struct.pack("<IIQQ", 1, 24 + len(body), hdev, 0)
    rec = head + body
    return rec + b"\0" * ((-len(rec)) % 8)


buf = rawmouse(0x1234, 5, 0) + rawmouse(0, 3, 1) + rawmouse(0, 0, 0, bflags=0x0001) + rawmouse(0x5678, 1, 1) \
    + rawkey(0, 0x57) + rawkey(0x99, 0x41)
state, out, held = {}, [], set()
mouse.parse(buf, 6, 1.0, out, held, state)
p = state["prov"]
expect(p["inj_motion"] == 1 and p["inj_button"] == 1, f"software-made movement and clicks counted ({p['inj_motion']}, {p['inj_button']})")
expect(p["motion_devs"] == {0x1234: 1, 0x5678: 1}, f"movement counted per device ({p['motion_devs']})")
expect(p["inj_key"] == 1 and p["inj_keys"] == {"W": 1}, f"software-made keys counted by name ({p['inj_keys']})")
st2 = {}
mouse.parse(rawmouse(0, 3, 1), 1, 1.0, [], set(), st2, False)
expect(st2["prov"]["inj_motion"] == 0, "input read while another window has focus is not counted")

# --------------------------------------------------------------------------------- the verdict
class FakeMouse:
    virtual = False

    def __init__(self):
        self.p = mouse.new_provenance()

    def provenance(self):
        return copy.deepcopy(self.p)


def guard_for(kbm=True, tools=(), hide=True):
    app = types.SimpleNamespace(mouse=FakeMouse(), offscreen=False, cfg={"anticheat": {"hide_capture": hide}, "kbm": {"shoot": "M1", "forward": "W"}},
                                build_state="official", hwnd=None)
    game = types.SimpleNamespace(kbm=kbm)
    real = system.snapshot
    system.snapshot = lambda: {"tools": list(tools), "foreign_modules": [], "debugger": False, "interception": False}
    try:
        g = ac.RunGuard(app, game)
    finally:
        system.snapshot = real
    g.virtual = False
    return app, g


def judge(app, g, tools=()):
    real = system.snapshot
    system.snapshot = lambda: {"tools": list(tools), "foreign_modules": [], "debugger": False, "interception": False}
    try:
        rec = {"binds": {"shoot": "M1", "ads": "M2"}}
        g.judge(rec, None)
        return rec["integrity"]
    finally:
        system.snapshot = real


app, g = guard_for()
app.mouse.p["motion_devs"] = {7: 5000}
i = judge(app, g)
expect(i["status"] == "unverified" and any("no capture" in d for d in i["doubts"]) and not i["flags"],
       f"a clean mouse run with no capture: unverified, nothing flagged ({i['status']})")
app, g = guard_for()
app.mouse.p["motion_devs"] = {7: 5000}
app.mouse.p["inj_motion"] = 400
i = judge(app, g)
expect(i["status"] == "flagged" and "software" in i["flags"][0], "software-made mouse movement flags the run")
app, g = guard_for()
app.mouse.p["motion_devs"] = {7: 5000, 9: 900}
i = judge(app, g)
expect(i["status"] == "flagged" and "two devices" in " ".join(i["flags"]), "a second device moving the aim flags the run")
app, g = guard_for()
app.mouse.p["inj_button"] = 3
i = judge(app, g)
expect(i["status"] == "flagged", "software-made clicks flag the run")
app, g = guard_for()
app.mouse.p["inj_keys"] = {"M1": 0, "W": 30}
app.mouse.p["inj_key"] = 30
i = judge(app, g)
expect(i["status"] == "flagged" and "bound keys" in " ".join(i["flags"]), "a bound key pressed by software flags the run")
app, g = guard_for(tools=[("aimmy", "Aimmy (AI aimbot)", "cheat")])
i = judge(app, g)
expect(i["status"] == "flagged" and "Aimmy" in i["flags"][0], "a cheat program running flags the run")
app, g = guard_for()
i = judge(app, g, tools=[("autohotkey64", "AutoHotkey (scripts)", "macro")])
expect(i["status"] == "unverified" and any("AutoHotkey" in d for d in i["doubts"]), "a macro tool running: unverified, not flagged")
app, g = guard_for(hide=False)
i = judge(app, g)
expect(any("screen-capture protection was off" in d for d in i["doubts"]), "capture protection off: unverified")
app, g = guard_for()
app.build_state = "modified"
i = judge(app, g)
expect(i["status"] == "flagged", "a modified build flags the run")
app, g = guard_for()
g.clocks.a = (g.clocks.a[0] - 10.0, g.clocks.a[1] - 9.0 if g.clocks.a[1] else None, g.clocks.a[2] - 9.0)
i = judge(app, g)
expect(i["status"] == "flagged" and "speed hack" in " ".join(i["flags"]), f"a clock running 10% off flags the run ({i.get('clock_drift')})")

# --------------------------------------------------------------------------------- system checks
expect(system.tools_running(["explorer", "autohotkey_2.0", "aimmy", "discord"]) ==
       [("autohotkey_2.0", "AutoHotkey (scripts)", "macro"), ("aimmy", "Aimmy (AI aimbot)", "cheat")],
       "known tools are recognised by process name, versioned ones too")
fm = system.foreign_modules([r"C:\Windows\System32\kernel32.dll", r"C:\Program Files\NVIDIA Corporation\x\nvoglv64.dll",
                             r"C:\Users\x\AppData\Local\Discord\DiscordHook64.dll", r"C:\Users\x\evil\inject.dll"])
expect(fm == [r"C:\Users\x\evil\inject.dll"], f"only unknown DLLs count as foreign ({fm})")
t0 = time.perf_counter()
snap = system.snapshot()
expect(time.perf_counter() - t0 < 0.5 and not snap["debugger"], f"the system snapshot is quick ({(time.perf_counter() - t0) * 1000:.0f} ms)")

# --------------------------------------------------------------------------------- the sealed log
scratch = tempfile.mkdtemp(prefix="aimtuner-ac-")
log = stats.Log(scratch)
recs = []
for k in range(4):
    r = {"schema": 1, "app": "0.8.0", "run_id": f"2026-{k}", "controller": "Pad", "scenario": "flick",
         "stats": {"score": 1000 + k}, "fingerprint": "abc", "binds": {}, "integrity": {"status": "verified", "flags": [], "doubts": []}}
    log.save(r, {"events": []})
    recs.append(r)
log2 = stats.Log(scratch)
expect([r["_seal"] for r in log2.all] == ["ok"] * 4 and len(log2.runs) == 4, "sealed runs read back intact")
lines = open(os.path.join(scratch, "runs.jsonl"), encoding="utf-8").read().splitlines()
edited = json.loads(lines[1])
edited["stats"]["score"] = 9999
lines[1] = json.dumps(edited, separators=(",", ":"))
del lines[2]
open(os.path.join(scratch, "runs.jsonl"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
log3 = stats.Log(scratch)
expect([r["_seal"] for r in log3.all] == ["ok", "edited", "gap"], f"an edited score and a deleted run are caught ({[r['_seal'] for r in log3.all]})")
expect(len(log3.runs) == 2 and all(r["stats"]["score"] != 9999 for r in log3.runs), "an edited run is not counted")
log3.rename_controller("Pad", "Pad 2")
log4 = stats.Log(scratch)
expect([r["_seal"] for r in log4.all][0] == "ok" and log4.all[0]["controller"] == "Pad 2", "renaming a profile keeps seals intact")
# a run flagged after the fact (its behaviour came in), then counted by its player
r = log4.all[0]
r["integrity"]["status"] = "flagged"
r["integrity"]["flags"] = ["test"]
log4.update(r)
expect(r not in log4.runs and stats.Log(scratch).all[0]["_seal"] == "ok", "a run flagged later drops out of the counts, seal intact")
r["integrity"]["override"] = True
log4.update(r)
expect(any(x is r for x in log4.runs), "...and counts again when its player says it was them")
expect(stats.Log(scratch).all[0]["integrity"]["override"] is True, "...which is written down")
# share codes
code = seal.share_code(r)
body, err = seal.read_code(code)
expect(err is None and body["v"] == "flagged" and body["o"] is True and body["m"]["score"] == r["stats"]["score"],
       "a share code carries the result, the verdict and the override")
bad = seal.read_code(code[:20] + ("A" if code[20] != "A" else "B") + code[21:])
expect(bad[0] is None and bad[1], f"a changed share code is refused ({bad[1]})")
expect(seal.read_code("hello")[0] is None, "something that is not a code is refused")
# a legacy line (before seals) counts; a stripped one does not
old = {"app": "0.7.0", "run_id": "x"}
new = {"app": "0.8.0", "run_id": "y"}
seal.verify([old])
seal.verify([new])
expect(old["_seal"] == "unsealed" and seal.trusted(old) and new["_seal"] == "stripped" and not seal.trusted(new),
       "runs from before seals still count; a removed seal does not")

# --------------------------------------------------------------------------------- Ed25519 + the build
sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
expect(seal.ed_public(sk).hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
       and seal.ed_sign(sk, b"").hex() == "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                                         "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
       "Ed25519 matches RFC 8032's test vector")
built = tempfile.mkdtemp(prefix="aimtuner-build-")
os.makedirs(os.path.join(built, "_internal", "x"))
for rel, data in (("AimTuner.exe", b"exe"), ("_internal/base_library.zip", b"zip"), ("_internal/x/a.pyd", b"pyd")):
    with open(os.path.join(built, rel), "wb") as f:
        f.write(data)
seed = os.urandom(32)
files = {rel: seal.file_hash(os.path.join(built, rel)) for rel in seal.manifest_files(built)}
body = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
with open(os.path.join(built, "_internal", seal.MANIFEST), "w") as f:
    json.dump({"files": files, "sig": seal.ed_sign(seed, body).hex()}, f)
seal.PUBLIC_KEY, was_frozen = seal.ed_public(seed).hex(), getattr(sys, "frozen", False)
sys.frozen = True
try:
    expect(seal.build_state(built) == "official", "a signed, untouched build is official")
    with open(os.path.join(built, "_internal", "x", "a.pyd"), "wb") as f:
        f.write(b"patched")
    expect(seal.build_state(built) == "modified", "a changed file makes it modified")
    with open(os.path.join(built, "_internal", "x", "a.pyd"), "wb") as f:
        f.write(b"pyd")
    with open(os.path.join(built, "_internal", "x", "hook.dll"), "wb") as f:
        f.write(b"dll")
    expect(seal.build_state(built) == "modified", "an added DLL makes it modified")
    os.remove(os.path.join(built, "_internal", "x", "hook.dll"))
    seal.PUBLIC_KEY = seal.ed_public(os.urandom(32)).hex()
    expect(seal.build_state(built) == "modified", "a list signed by any other key is not official")
finally:
    if was_frozen:
        sys.frozen = was_frozen
    else:
        del sys.frozen
    seal.PUBLIC_KEY = None
expect(seal.build_state() == "source", "running from source is 'source'")

# --------------------------------------------------------------------------------- behaviour
# Simulated cheats played through the real game, offscreen, in real time
caps = {}
opts = types.SimpleNamespace(windowed=False, offscreen=(960, 540), bench=False, selftest=True, only=None, repeats=1,
                             shots=None, duration=15.0, cap=240, exit_after=None, logdir=os.path.join(scratch, "g"),
                             config=os.path.join(scratch, "g", "config.json"), fast=True)
gapp = App(opts)
kbm_name = next(p["name"] for p in gapp.cfg["profiles"] if p.get("kind") == "kbm")
pad_name = next(p["name"] for p in gapp.cfg["profiles"] if p.get("kind") != "kbm")
JOBS = [("snap", "kbm", "flick"), ("smooth", "pad", "grid"), ("human_ai", "kbm", "track_strafe"),
        ("ar", "kbm", "r6_recoil_transfer"), ("ar", "pad", "cod_loss_push")]
done = {}


def end_game(rec, game):
    done["cap"], done["rec"] = game.capture, rec
    if gapp.game is game:
        gapp.game = None


gapp.end_game = end_game


class Driver:
    i, cur = -1, None

    def step(self, now):
        if gapp.game is not None:
            return
        if self.cur is not None:
            caps[self.cur] = (done.pop("cap", None), done.pop("rec", None))
            self.cur = None
        self.i += 1
        if self.i >= len(JOBS):
            glfw.set_window_should_close(gapp.win, True)
            return
        mode, kind, sid = self.cur = JOBS[self.i]
        gapp.set_profile(kbm_name if kind == "kbm" else pad_name)
        gapp.bot = Cheat(gapp, mode="smooth" if mode == "ar" else mode, ar=(mode == "ar"), seed=self.i + 3)
        if mode == "ar":            # anti-recoil on its own: the aim a hand's (slow to correct), the pull a script's
            gapp.bot.mode = "hand"
        gapp.start(tracks.BY_ID[sid])


gapp.auto = Driver()
gapp.bot = Cheat(gapp)
gapp.run()


def verdict(cap):
    det = behavior.analyse(cap)["detectors"]
    strong = [k for k, d in det.items() if d["level"] == "flag"]
    weak = [k for k, d in det.items() if d["level"] == "suspect"]
    return ("flagged" if strong or len(weak) >= 2 else ("unverified" if weak else "clean")), strong + weak


for job, (cap, rec) in caps.items():
    if cap is None:
        expect(False, f"{job}: no capture")
        continue
    v, why = verdict(cap)
    expect(v == "flagged", f"cheat {'-'.join(job)}: {v} {why}")
    if job[0] == "ar":
        det = behavior.analyse(cap)["detectors"]
        expect(det.get("anti_recoil", {}).get("level") == "flag",
               f"{'-'.join(job)}: the anti-recoil detector itself fires ({det.get('anti_recoil', {}).get('value')})")
    integ = (rec or {}).get("integrity") or {}
    expect(integ.get("status") in ("unverified", "verified") and integ.get("behaviour") is not None,
           f"{'-'.join(job)}: a self-test run is checked but never thrown out of the self-test's sums ({integ.get('status')})")
    # the ghost (a strong human, by model) plays the same targets: never flagged
    if ghost.playable(cap.head["scenario"]):
        gr = ghost.GhostRun(cap)
        gr.run()
        res = gr.result()
        gv, gwhy = verdict(res["capture"])
        expect(gv != "flagged", f"the ghost on {'-'.join(job)}'s targets: {gv} {gwhy}")
        # ...and the ghost with a recoil script added to its input: flagged
        if job[0] == "ar":
            c = copy.copy(res["capture"])
            c.view = res["capture"].view.copy()
            f = c.view[:, V["fire"]] > 0.5
            c.view[f, V["ay"]] += 3.0 if c.head["kbm"] else -0.15
            det = behavior.analyse(c)["detectors"]
            expect(det.get("anti_recoil", {}).get("level") in ("flag", "suspect"),
                   f"the ghost's sprays with a recoil script added are caught ({det.get('anti_recoil', {}).get('value')})")

# real play: none of it may be flagged (only where there are captures - a fresh copy has none)
real = sorted(glob.glob(os.path.join(ROOT, "logs", "captures", "*.npz")))
for path in real:
    cap = capture.Capture.load(path)
    v, why = verdict(cap)
    expect(v != "flagged", f"real play {os.path.basename(path)}: {v} {why}")
if not real:
    print("(no captures of real play in logs/captures to check against)")

print(f"{checks[0] - len(errors)} of {checks[0]} checks passed")
sys.exit(1 if errors else 0)
