"""Run capture: everything needed to watch a run again, and for the ghost to play its targets.

Recorded every frame of a run (Game.update):
  the view       time, yaw, pitch, roll, eye position, how far the sights are up
  the inputs     fire and aim held, the aim stick (or this frame's mouse counts)
  the body       where the player stood and how fast they moved (Call of Duty / Siege), how far
                 they leaned and crouched (Siege) - the ghost's body follows this exactly
  the targets    every target's pose (scenario.capture_targets) and whether it could be seen
  the picture    every sphere that was drawn (Game.spheres), for the replay
and as events: shots, hits, kills, and targets removed (killed or timed out).

Saved as logs/captures/<run_id>.npz - compressed numpy arrays plus a JSON header - in the
background once the run is over. Only the most recent captures are kept (KEEP).
"""
from __future__ import annotations

import copy
import json
import os
import threading

import numpy as np

SCHEMA = 1
VIEW = ("t", "yaw", "pitch", "roll", "ex", "ey", "ez", "ads_t", "fire", "ads", "ax", "ay",
        "bx", "bz", "bvx", "bvz", "strafe", "lean", "crouch")
V = {k: i for i, k in enumerate(VIEW)}
POSE = ("x", "y", "z", "a", "b", "c", "d", "flag", "vis")      # meaning of a..d per target kind (see Scenario)
P = {k: i for i, k in enumerate(POSE)}
SPHERE = ("x", "y", "z", "r", "cr", "cg", "cb", "alpha", "light", "hh")
KEEP = 60               # captures kept (with their ghosts); older ones are removed as new ones arrive
NOT_CAPTURED = ("reaction", "sticksnap")     # nothing to aim at: no replay, no ghost


def wanted(game) -> bool:
    """Is this run worth capturing? Not a sensitivity-finder block, and not the two drills without aiming."""
    return (not game.over.get("on_done") and type(game.scn).id not in NOT_CAPTURED
            and game.cfg.get("game", {}).get("capture", True))


class _Grow:
    """A table that grows a chunk at a time, so a frame costs one row assignment."""

    def __init__(self, cols: int, dtype=np.float32, chunk: int = 4096):
        self.cols, self.dtype, self.chunk = cols, dtype, chunk
        self.parts: list = []
        self.cur = np.empty((chunk, cols), dtype)
        self.n = 0

    def add(self, row):
        if self.n == self.chunk:
            self.parts.append(self.cur)
            self.cur = np.empty((self.chunk, self.cols), self.dtype)
            self.n = 0
        self.cur[self.n] = row
        self.n += 1

    def array(self) -> np.ndarray:
        return np.concatenate(self.parts + [self.cur[:self.n]]) if self.parts else self.cur[:self.n].copy()


class Recorder:
    def __init__(self, game):
        self.g = game
        self.view = _Grow(len(VIEW), np.float64)
        self.tgt = _Grow(2 + len(POSE))             # frame, key, pose...
        self.sph = _Grow(1 + len(SPHERE))           # frame, sphere...
        self.keys: dict = {}                        # scenario's key -> index
        self.key_info: list = []                    # per index: {"key", "kind", ...static}
        self.events: list = []
        self.boxes: list = []                       # [frame, boxes] whenever the world's boxes change
        self._boxes = None
        self.frame_i = -1
        self.cfg = copy.deepcopy(game.cfg)          # the settings the run was played with, for the ghost
        self.over = {k: v for k, v in game.over.items() if k in ("sens", "cod", "r6", "endless")}

    # ---- per frame ------------------------------------------------------------
    def frame(self, g):
        self.frame_i += 1
        f = self.frame_i
        kit = g.kit
        if g.kbm:                                   # this frame's mouse counts
            ax = ay = 0.0
            for _, kind, a, b in g.mouse.events:
                if kind == 0:                       # mouse.MOTION
                    ax += a
                    ay += b
        else:
            ax, ay = g.aim_raw
        if kit is not None:
            body = (kit.x, kit.z, kit.vx, kit.vz, kit.strafe, getattr(kit, "lean_t", 0.0), getattr(kit, "crouch_t", 0.0))
        else:
            body = (0.0,) * 7
        e = g.eye
        self.view.add((g.t, g.yaw, g.pitch, g.roll, e[0], e[1], e[2], g.ads_t, float(g.fire_held), float(g.ads_held),
                       ax, ay) + body)
        for key, kind, pose, vis, static in g.scn.capture_targets():
            k = self.keys.get(key)
            if k is None:
                k = self.keys[key] = len(self.key_info)
                self.key_info.append(dict(static or {}, key=repr(key), kind=kind, first=f))
            self.tgt.add((f, k) + tuple(pose) + (float(vis),))
        world = getattr(g.scn, "world", None)
        if world is not None and world.boxes is not self._boxes and list(world.boxes) != (self._boxes or []):
            self._boxes = list(world.boxes)
            self.boxes.append([f, [[list(a), list(b)] for a, b in world.boxes]])

    def spheres(self, out):
        f = self.frame_i
        if f < 0:
            return
        for s in out:
            hh = s[5] if len(s) > 5 else -1.0
            col = s[2]
            self.sph.add((f, s[0][0], s[0][1], s[0][2], s[1], col[0], col[1], col[2], s[3], s[4], hh))

    def event(self, kind: str, **kw):
        kw.update(kind=kind, t=round(self.g.t, 4), f=self.frame_i)
        if "key" in kw:
            kw["key"] = self.keys.get(kw["key"], -1)
        self.events.append(kw)

    # ---- the end ----------------------------------------------------------------
    def finish(self, meta: dict) -> "Capture":
        g = self.g
        w = g.world_args()
        world = {k: v for k, v in w.items() if k in ("room", "boxes", "smokes", "smoke_density", "soft_from")}
        head = dict(meta, schema=SCHEMA, scenario=type(g.scn).id, track=g.scn.track, kbm=g.kbm,
                    fov=g.fov, zoom_max=g.zoom_max, aspect=getattr(g.app.gfx, "aspect", 16 / 9),
                    cfg=self.cfg, over=self.over, keys=self.key_info,
                    events=self.events, boxes=self.boxes, world=_jsonable(world))
        t = self.tgt.array()
        s = self.sph.array()
        return Capture(head, self.view.array(), t[:, 0].astype(np.int32), t[:, 1].astype(np.int32),
                       t[:, 2:].astype(np.float32), s[:, 0].astype(np.int32), s[:, 1:].astype(np.float32))


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    return x


class Capture:
    """One captured run (the player's, or the ghost's playing the same targets)."""

    def __init__(self, head, view, tf, tk, tp, sf, sp):
        self.head, self.view = head, view
        self.tf, self.tk, self.tp = tf, tk, tp
        self.sf, self.sp = sf, sp
        self.t = view[:, V["t"]] if len(view) else np.zeros(0)
        # where each frame's rows start, for quick lookups by frame
        n = len(view)
        self._t_start = np.searchsorted(tf, np.arange(n + 1)) if n else np.zeros(1, int)
        self._s_start = np.searchsorted(sf, np.arange(n + 1)) if n else np.zeros(1, int)

    @property
    def frames(self) -> int:
        return len(self.view)

    @property
    def duration(self) -> float:
        return float(self.t[-1]) if len(self.t) else 0.0

    def col(self, name):
        return self.view[:, V[name]]

    def frame_at(self, t: float) -> int:
        """The last frame at or before game time t."""
        i = int(np.searchsorted(self.t, t, side="right")) - 1
        return min(max(i, 0), max(self.frames - 1, 0))

    def targets(self, f: int):
        """[(key index, pose row)] for frame f."""
        a, b = self._t_start[f], self._t_start[f + 1]
        return list(zip(self.tk[a:b].tolist(), self.tp[a:b]))

    def spheres(self, f: int) -> list:
        a, b = self._s_start[f], self._s_start[f + 1]
        out = []
        for row in self.sp[a:b]:
            x, y, z, r, cr, cg, cb, al, li, hh = row.tolist()
            inst = ((x, y, z), r, (cr, cg, cb), al, li)
            out.append(inst + ((hh,) if hh >= 0.0 else ()))
        return out

    def track_of(self, k: int):
        """(frames, poses) of one target over its life."""
        m = self.tk == k
        return self.tf[m], self.tp[m]

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp.npz"
        np.savez_compressed(tmp, head=np.frombuffer(json.dumps(self.head).encode("utf-8"), np.uint8),
                            view=self.view, tf=self.tf, tk=self.tk, tp=self.tp, sf=self.sf, sp=self.sp)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "Capture":
        with np.load(path) as z:
            head = json.loads(bytes(z["head"]).decode("utf-8"))
            return cls(head, z["view"], z["tf"], z["tk"], z["tp"], z["sf"], z["sp"])


def folder(log_dir: str) -> str:
    return os.path.join(log_dir, "captures")


def path_for(log_dir: str, run_id: str, ghost: bool = False) -> str:
    return os.path.join(folder(log_dir), run_id + (".ghost" if ghost else "") + ".npz")


def save_async(cap: Capture, log_dir: str, run_id: str, done=None) -> threading.Thread:
    """Write a capture without holding up the results screen, then trim old ones."""
    def work():
        try:
            cap.save(path_for(log_dir, run_id))
            prune(log_dir)
        except OSError as e:
            print(f"capture: could not save {run_id}: {e}")
        if done:
            done()
    th = threading.Thread(target=work, name="capture-save", daemon=True)
    th.start()
    return th


def prune(log_dir: str, keep: int = KEEP):
    """Keep the newest `keep` captures (and their ghosts); remove the rest. These files are this
    program's own replay data - the run records in runs.jsonl are never touched."""
    d = folder(log_dir)
    try:
        runs = sorted((f for f in os.listdir(d) if f.endswith(".npz") and not f.endswith(".ghost.npz")
                       and not f.endswith(".tmp.npz")), reverse=True)
    except FileNotFoundError:
        return
    for f in runs[keep:]:
        for name in (f, f[:-4] + ".ghost.npz"):
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass
