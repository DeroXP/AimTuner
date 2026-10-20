"""How the view turns for this input device - whichever track's settings say so.

Every track words sensitivity its own way: the Core track in degrees per second and
centimetres per turn, the Call of Duty track on that game's scales, the Siege track on
Siege's. Each of those is a "dialect" here. A track normally speaks its own, but
cfg["sens"] can name one dialect per input device that every track then uses, so the
sensitivity you settled on in one place is the sensitivity everywhere:

    sens.pad(cfg, track)    -> PadSens    full-stick turn speeds, ADS share, stick response
    sens.mouse(cfg, track)  -> MouseSens  degrees per count, and what the sights do to it

Only how the view turns follows the chosen track. Everything else stays the track's own:
its field of view, its zoom, its weapon and recoil, and its aim assist (which only the
Call of Duty track has, only on a controller, and only in the scenarios that say so).

`over` lets the sensitivity finder play a block at a value it is trying, without saving it:
    {"hip": v}        the dialect's own stick-sensitivity setting at v
    {"ads": v}        the dialect's own ADS setting at v
    {"ads_speed": s}  ADS turn speed pinned at s deg/s whatever the hip speed is
    {"cm360": v}      mouse: v centimetres per full turn, whatever the dialect
"""
from __future__ import annotations

import math

from . import codkit, config
from . import pad as padmod

NAMES = {"own": "each track's own", "core": "the Core track", "cod": "the Call of Duty track", "r6": "the Siege track"}


class PadSens:
    """A controller's aim response: stick position in, turn rate out."""

    def __init__(self, src: str, rate_h: float, rate_v: float, ads: float, shape):
        self.src = src
        self.rate_h, self.rate_v = rate_h, rate_v       # deg/s at full stick, from the hip
        self.ads = ads                                  # ADS turn speed as a share of hip speed
        self.shape = shape                              # (x, y) -> (ax, ay, |a|): deadzones and response curve

    def stick_for(self, rate: float, vertical: bool = False, scale: float = 1.0) -> float:
        """Stick deflection that asks for `rate` deg/s (inverts deadzone and curve; for the self-test bots)."""
        full = (self.rate_v if vertical else self.rate_h) * scale
        want = min(abs(rate) / full, 1.0) if full > 0.0 else 0.0
        lo, hi = 0.0, 1.0
        for _ in range(18):
            mid = (lo + hi) * 0.5
            if self.shape(mid, 0.0)[2] < want:
                lo = mid
            else:
                hi = mid
        return math.copysign(hi, rate)


class MouseSens:
    """A mouse's aim response: counts in, degrees out."""

    def __init__(self, src: str, deg: float, ads):
        self.src = src
        self.deg = deg          # degrees per count from the hip
        self._ads = ads         # (horizontal FOV at 16:9, zoom) -> share of `deg` with the sights fully up

    def ads(self, fov: float, zoom: float) -> float:
        return self._ads(fov, zoom)

    def cm360(self, cfg: dict) -> float:
        return 360.0 / self.deg / cfg["mouse"]["dpi"] * 2.54


# ---- the dialects, controller ---------------------------------------------------
def _core_pad(cfg, hip=None, ads=None):
    a = cfg["aim"]
    h = a["sens_h"] if hip is None else hip

    def shape(x, y):
        ax, ay = padmod.shape(x, y, a["deadzone"], a["curve"])
        return ax, ay, math.hypot(ax, ay)

    return h, a["sens_v"] * h / a["sens_h"], (a["ads_mult"] if ads is None else ads), shape


def _cod_pad(cfg, hip=None, ads=None):
    c = cfg["cod"]
    per_unit, ads_ratio = config.cod_rates(cfg)
    h = c["sens_h"] if hip is None else hip
    dz0, dz1, curve = c["dz_r_min"] / 100.0, c["dz_r_max"] / 100.0, c["curve"]
    return (h * per_unit, c["sens_v"] * h / c["sens_h"] * per_unit,
            (c["ads_mult"] if ads is None else ads) * ads_ratio,
            lambda x, y: codkit.shape(x, y, dz0, dz1, curve))


def _r6_pad(cfg, hip=None, ads=None):
    from . import r6kit

    return r6kit.pad_response(cfg, hip, ads)


_PAD = {"core": _core_pad, "cod": _cod_pad, "r6": _r6_pad}


def own_pad(cfg: dict, track: str) -> PadSens:
    """What `track`'s OWN controller settings come to (whatever it follows)."""
    return PadSens(track, *_PAD[track](cfg))


def pad(cfg: dict, track: str, over: dict | None = None) -> PadSens:
    src = config.sens_source(cfg, "pad", track)
    over = over or {}
    rate_h, rate_v, ads, shape = _PAD[src](cfg, over.get("hip"), over.get("ads"))
    if over.get("ads_speed"):
        ads = over["ads_speed"] / rate_h
    return PadSens(src, rate_h, rate_v, ads, shape)


# ---- the dialects, mouse --------------------------------------------------------
def _core_mouse(cfg, ads=None):
    m = cfg["mouse"]
    mult = m["ads_mult"] if ads is None else ads
    # 1.00 = the same speed across the screen as from the hip: the turn is scaled down by the zoom
    return config.mouse_deg(cfg), lambda fov, zoom: mult / zoom


def _cod_mouse(cfg, ads=None):
    c = cfg["cod"]
    mult, mdc = (c["mouse_ads"] if ads is None else ads), c["mouse_mdc"]
    return config.COD_MOUSE_YAW * c["mouse_sens"], lambda fov, zoom: mult * codkit.mouse_ads_ratio(fov, zoom, mdc)


def _r6_mouse(cfg, ads=None):
    from . import r6kit

    return r6kit.mouse_response(cfg, ads)


_MOUSE = {"core": _core_mouse, "cod": _cod_mouse, "r6": _r6_mouse}


def own_mouse_cm(cfg: dict, track: str) -> float:
    """Centimetres per full turn that `track`'s OWN mouse settings come to (whatever it follows)."""
    return 360.0 / _MOUSE[track](cfg)[0] / cfg["mouse"]["dpi"] * 2.54


def mouse(cfg: dict, track: str, over: dict | None = None) -> MouseSens:
    src = config.sens_source(cfg, "kbm", track)
    over = over or {}
    deg, ads = _MOUSE[src](cfg, over.get("ads"))
    if over.get("cm360"):
        deg = 360.0 / (over["cm360"] / 2.54 * cfg["mouse"]["dpi"])
    return MouseSens(src, deg, ads)


# ---- what the sensitivity finder searches, in each dialect's own units ----------
# hip / ads: (setting, lowest, highest, decimals, format); v: the vertical setting that moves with hip
FINDER = {
    "pad": {
        "core": {"hip": ("aim.sens_h", 40.0, 600.0, 0, "{:.0f}°/s"), "v": "aim.sens_v",
                 "ads": ("aim.ads_mult", 0.20, 1.50, 2, "{:.2f}×"), "scale": "degrees per second at full stick"},
        "cod": {"hip": ("cod.sens_h", 0.10, 4.00, 2, "{:.2f}"), "v": "cod.sens_v",
                "ads": ("cod.ads_mult", 0.10, 2.00, 2, "{:.2f}"), "scale": "the game's own scale"},
        "r6": {"hip": ("r6.sens_h", 1.0, 100.0, 0, "{:.0f}"), "v": "r6.sens_v",
               "ads": ("r6.ads", 1.0, 100.0, 0, "{:.0f}"), "scale": "Siege's own scale"},
    },
    "kbm": {
        "core": {"ads": ("mouse.ads_mult", 0.20, 2.00, 2, "{:.2f}×")},
        "cod": {"ads": ("cod.mouse_ads", 0.10, 3.00, 2, "{:.2f}")},
        "r6": {"ads": ("r6.mouse_ads", 1.0, 200.0, 0, "{:.0f}")},
    },
}


def set_mouse_cm360(cfg: dict, cm: float) -> dict:
    """Put every track's own mouse sensitivity at `cm` centimetres per turn (a distance per turn is
    the same sensitivity in all of them). -> what each came to, in its own words."""
    from . import r6kit

    lo, hi = config.COD_MOUSE_SENS
    cfg["mouse"]["cm360"] = round(cm, 1)
    cfg["cod"]["mouse_sens"] = round(min(max(config.cod_mouse_sens_for(cfg, cm), lo), hi), 2)
    r6kit.set_mouse_cm360(cfg, cm)
    return {"core": cfg["mouse"]["cm360"], "cod": cfg["cod"]["mouse_sens"], "r6": cfg["r6"]["mouse_sens"]}
