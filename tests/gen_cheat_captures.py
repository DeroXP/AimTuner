"""Play the simulated cheats (tests/cheatsim.py) through real scenarios and keep the captures, so
the anti-cheat can be measured on them. Offscreen, scratch config and logs; real time.

    python tests/gen_cheat_captures.py OUT_DIR [seconds] [mode,mode..] [scenario,scenario..]

Writes OUT_DIR/<mode>-<pad|kbm>-<scenario>.npz (+ .json with the run record)."""
import json, os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glfw

from aimtuner import tracks
from aimtuner.app import App
from aimtuner.bot import Bot
from cheatsim import Cheat

OUT = sys.argv[1]
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
MODES = (sys.argv[3].split(",") if len(sys.argv) > 3 else ["snap", "smooth", "human_ai", "ar", "bot"])
SCNS = (sys.argv[4].split(",") if len(sys.argv) > 4 else
        ["flick", "grid", "track_strafe", "spray", "cod_track_jump", "cod_first_burst", "cod_loss_push",
         "r6_recoil_dump", "r6_place_height", "r6_track_run"])
os.makedirs(OUT, exist_ok=True)

scratch = tempfile.mkdtemp(prefix="aimtuner-cheats-")
opts = types.SimpleNamespace(windowed=False, offscreen=(960, 540), bench=False, selftest=True, only=None,
                             repeats=1, shots=None, duration=SECS, cap=240, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
kbm_name = next(p["name"] for p in app.cfg["profiles"] if p.get("kind") == "kbm")
pad_name = next(p["name"] for p in app.cfg["profiles"] if p.get("kind") != "kbm")

jobs = []
for mode in MODES:
    for kind in ("pad", "kbm"):
        for sid in SCNS:
            cls = tracks.BY_ID[sid]
            if kind == "kbm" and getattr(cls, "pad_only", False):
                continue
            jobs.append((mode, kind, sid))

done = {}
orig_end = app.end_game


def end_game(rec, game):
    done["rec"], done["cap"] = rec, game.capture
    if app.game is game:
        app.game = None


app.end_game = end_game


class Driver:
    def __init__(self):
        self.i = -1
        self.cur = None

    def step(self, now):
        if app.game is not None:
            return
        if self.cur is not None:
            mode, kind, sid = self.cur
            cap = done.pop("cap", None)
            rec = done.pop("rec", None)
            if cap is not None:
                name = f"{mode}-{kind}-{sid}"
                cap.save(os.path.join(OUT, name + ".npz"))
                with open(os.path.join(OUT, name + ".json"), "w", encoding="utf-8") as f:
                    json.dump(rec, f, default=str)
                print("saved", name, flush=True)
            else:
                print("no capture for", self.cur, flush=True)
            self.cur = None
        self.i += 1
        if self.i >= len(jobs):
            glfw.set_window_should_close(app.win, True)
            return
        mode, kind, sid = self.cur = jobs[self.i]
        app.set_profile(kbm_name if kind == "kbm" else pad_name)
        if mode == "bot":           # the self-test bot: perfect information, steered like a hand
            app.bot = Bot(app, react=0.25, gain=10.0, noise=0.08, throw=0.06, seed=self.i + 1)
        else:
            m = "smooth" if mode == "ar" else mode
            app.bot = Cheat(app, mode=m, ar=(mode == "ar"), seed=self.i + 1)
        app.start(tracks.BY_ID[sid])


app.auto = Driver()
app.bot = Cheat(app)
app.run()
print("done", len(jobs))
