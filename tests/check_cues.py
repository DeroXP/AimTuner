"""The warning tone for dummies that shoot back: is the sound file what it should be, and is it
played once per armed dummy, from the correct side, and never for a dummy that cannot shoot?"""
import math, os, struct, sys, tempfile, time, types, wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import codscn, sound
from aimtuner.app import App
from aimtuner.bot import Bot

scratch = tempfile.mkdtemp(prefix="aimtuner-cues-")
errors = []


def expect(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


# ---- the files ---------------------------------------------------------------
def levels(path):
    with wave.open(path, "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    vals = struct.unpack("<" + "h" * (len(raw) // 2), raw)
    rms = lambda xs: math.sqrt(sum(x * x for x in xs) / max(len(xs), 1))
    return ch, width, rate, 1000.0 * n / rate, rms(vals[0::2]), rms(vals[1::2]), max(abs(v) for v in vals)


want_ms = sum(ms for _, ms in sound._THREAT_NOTES)
seen = {}
for name, (left, right) in sound._THREAT_PAN.items():
    p = os.path.join(scratch, name + ".wav")
    sound._write_threat(p, left, right)
    ch, width, rate, ms, l, r, peak = levels(p)
    seen[name] = (l, r)
    expect(ch == 2 and width == 2 and rate == 44100, f"{name}: 16-bit stereo at 44.1 kHz")
    expect(abs(ms - want_ms) < 1.0, f"{name}: {ms:.0f} ms long")
    expect(8000 < peak < 32767, f"{name}: loud but not clipping (peak {peak})")
expect(seen["threat_l"][0] > 4 * seen["threat_l"][1], "left version is clearly in the left ear")
expect(seen["threat_r"][1] > 4 * seen["threat_r"][0], "right version is clearly in the right ear")
expect(abs(seen["threat_c"][0] - seen["threat_c"][1]) < 1.0, "centre version is the same in both ears")
expect(len([n for n in sound._THREAT_NOTES if n[0] > 0]) == 2, "two notes, unlike every single-blip sound")
blip_peak = 32767 * max(spec[3] for spec in sound._SOUNDS.values())
expect(max(seen["threat_l"]) > 0 and levels(os.path.join(scratch, "threat_l.wav"))[6] > blip_peak,
       "peaks above the loudest of the other sounds")

# ---- when it is played -------------------------------------------------------
opts = types.SimpleNamespace(windowed=False, offscreen=(1280, 720), bench=False, selftest=True, only=None,
                             repeats=1, shots=None, duration=None, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
app.bot = Bot(app)
expect(all(os.path.exists(app.sfx.paths[n]) for n in sound._THREAT_PAN), "the app generated all three files")
log = []                      # ("spawn", armed, side) and ("play", name), in the order they happened


class Recorder:
    def play(self, name):
        log.append(("play", name))


app.sfx = Recorder()


def watch(cls):
    inner = cls.spawn

    def spawn(self, now):
        d = inner(self, now)
        rel = (math.degrees(math.atan2(d.x - self.kit.x, -(d.z - self.kit.z))) - self.g.yaw + 180.0) % 360.0 - 180.0
        log.append(("spawn", bool(d.shooter), "l" if rel < -codscn.AHEAD else ("r" if rel > codscn.AHEAD else "c")))
        return d

    cls.spawn = spawn


def wait(pred, secs=60.0, what=""):
    end = time.perf_counter() + secs
    while not pred():
        if time.perf_counter() > end:
            expect(False, "timeout: " + what)
            return
        yield


def play(cls, seconds):
    del log[:]
    done = []
    app.start(cls, None, {"duration": seconds, "on_done": done.append, "on_abort": lambda: done.append(None)})
    yield from wait(lambda: bool(done), secs=seconds + 30.0, what=cls.__name__)
    yield from wait(lambda: app.game is None, secs=10.0, what=cls.__name__ + " to close")


def script():
    for cls in (codscn.CheckSix, codscn.StrafeDuel, codscn.DoorWatch, codscn.OpeningBurst, codscn.SnapShot):
        watch(cls)
    for cls, seconds in ((codscn.CheckSix, 10.0), (codscn.StrafeDuel, 8.0), (codscn.DoorWatch, 18.0),
                         (codscn.OpeningBurst, 8.0), (codscn.SnapShot, 6.0)):
        yield from play(cls, seconds)
        spawns = [e for e in log if e[0] == "spawn"]
        armed = [e for e in spawns if e[1]]
        cues = [e[1] for e in log if e[0] == "play" and e[1].startswith("threat_")]
        sides = {s: sum(1 for e in armed if e[2] == s) for s in "lcr"}
        print(f"   {cls.name}: {len(spawns)} dummies, {len(armed)} armed {sides}, {len(cues)} warnings")
        expect(len(cues) == len(armed), f"{cls.name}: one warning per armed dummy")
        expect(cues == ["threat_" + e[2] for e in armed], f"{cls.name}: each from the side the dummy was on")
        # the warning comes with the spawn, before anything else is heard
        order_ok = all(log[i + 1] == ("play", "threat_" + e[2]) for i, e in enumerate(log[:-1]) if e[0] == "spawn" and e[1])
        expect(order_ok, f"{cls.name}: played at the moment of the spawn")
        # (which dummies are armed and which side they appear on is random, so nothing here
        # depends on a particular mix turning up - only on every one that did being right)
        if cls is codscn.SnapShot:
            expect(spawns and not cues, "Snap Shot (nobody shoots back): no warning at all")
        elif cls is codscn.CheckSix:
            expect(armed and all(e[2] in "lr" for e in armed), "Check Your Six: always from one side, never centre")


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
expect(not os.path.exists(os.path.join(scratch, "runs.jsonl")), "nothing was written to a run log")
print("cues:", "all passed" if not errors else f"FAILED {errors}")
sys.exit(1 if errors else 0)
