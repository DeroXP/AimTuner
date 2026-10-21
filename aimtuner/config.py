"""Settings: one JSON file beside the app, deep-merged over DEFAULTS.

Merging over defaults means a config written by an older build keeps working
when a newer build adds keys. Everything that changes how hard a scenario is
feeds `fingerprint()`, and runs are only ever compared with runs that share it -
otherwise "controller B scored higher" could just mean "I raised the sensitivity".
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import time

# Where config.json, logs/ and the generated sounds live: the project folder - or, for the
# built Core.exe, the folder the exe sits in (its code is unpacked somewhere temporary).
ROOT = (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(ROOT, "config.json")
LOG_DIR = os.path.join(ROOT, "logs")

DEFAULTS = {
    "active_profile": "Controller 1",
    # `devices` holds the USB ids ("3537:103D") seen for that pad, so plugging it
    # in (or pressing a button on it) selects the right profile automatically.
    # A profile with "kind": "kbm" is mouse and keyboard; without it, a controller.
    "profiles": [
        {"name": "Controller 1", "devices": []},
        {"name": "Controller 2", "devices": []},
        {"name": "Mouse & keyboard", "kind": "kbm", "devices": []},
    ],
    "binds": {"shoot": "RB", "ads": "LB"},
    # Mouse and keyboard. Mouse buttons are M1-M5, keys are their names (see mouse.VK).
    # (the last four are used on the Siege track only)
    "kbm": {"shoot": "M1", "ads": "M2", "forward": "W", "back": "S", "left": "A", "right": "D",
            "lean_l": "Q", "lean_r": "E", "crouch": "C", "reload": "R"},
    "mouse": {
        "dpi": 800,               # what the mouse is set to; only used to turn cm into counts and back
        "dpi_auto": True,         # read it from the mouse itself where the mouse can say (see dpi.py)
        "cm360": 30.0,            # Core track: centimetres of mouse travel for one full turn
        "ads_mult": 1.0,          # Core track: 1.00 = the same speed across the screen with the sights up
    },
    # One sensitivity for every track, chosen per input device: "own" leaves each track on its own
    # settings; "core" / "cod" / "r6" makes every track turn the way that track's settings say.
    "sens": {"pad": "own", "kbm": "own"},
    "migrated": {},               # one-off upgrades of an older config that have been applied (see load)
    "aim": {
        "sens_h": 240,            # deg/s at full deflection
        "sens_v": 180,
        "ads_mult": 0.75,
        "deadzone": 0.06,
        "curve": "standard",
        "invert_y": False,
        "stick": "right",
        "trigger_threshold": 0.12,  # analogue trigger travel that counts as a press
    },
    "game": {
        "duration": 45,
        "target_size": "normal",
        "fov": 105,               # horizontal, at 16:9
        "ads_zoom": 1.3,
        "target_color": "red",
        "sounds": True,
    },
    "crosshair": {"style": "cross_dot", "color": "green", "size": 5, "gap": 3, "thickness": 2},
    # Call of Duty track. Sensitivities and deadzones are in the game's own units,
    # so they can be copied straight across from its controller menu.
    "cod": {
        "sens_h": 1.95, "sens_v": 1.95,     # 0.10 - 4.00
        "ads_mult": 0.95,
        # The sensitivity finder's ADS answer, kept as a speed (stick sensitivity x multiplier) because
        # that is what was actually tested. While set, changing stick sensitivity moves the multiplier
        # to keep it; setting the multiplier by hand lets go of it. See hold_ads_speed().
        "ads_hold": None,
        "curve": "dynamic",
        "dz_r_min": 0, "dz_r_max": 99,      # right stick, percent
        "dz_l_min": 0, "dz_l_max": 75,      # left stick, percent
        "fov": 100,
        "ads_fov": "affected",
        # Mouse, on the game's own scale: degrees per count = 0.0066 x sensitivity; the ADS
        # multiplier and monitor distance coefficient as in its "Relative" ADS mode.
        "mouse_sens": 6.0, "mouse_ads": 1.0, "mouse_mdc": 1.33,
        "aa_style": "bo7",                  # or "classic" (MW3 / Black Ops 6) or "mw4" (its beta): see codkit.STYLES
        # The spin test: full-stick turns timed in the real game with a stopwatch.
        # Turn speed in this track is derived from these numbers, not assumed.
        "cal": {"sens": 1.95, "ads_mult": 0.95, "hip_turns": 10, "hip_seconds": 5.59,
                "ads_turns": 5, "ads_seconds": 11.0},
    },
    # Siege track. Sensitivities are in Siege's own units, to copy from its menus. There is no
    # aim-assist setting because there is no aim assist: Siege has none, so neither does this.
    "r6": {
        "sens_h": 50, "sens_v": 50,         # controller: the game's 1-100 stick sensitivity sliders
        "ads": 50,                          # controller: ADS sensitivity, 1-100
        "dz_r": 10, "dz_l": 10,             # stick deadzones, percent
        # The spin test, as on the Call of Duty track: full-stick turns timed in the real game with
        # a stopwatch. The defaults are a placeholder until you time your own.
        "cal": {"sens": 50, "ads": 50, "hip_turns": 5, "hip_seconds": 9.0, "ads_turns": 3, "ads_seconds": 9.0},
        # Mouse: degrees per count = sensitivity x multiplier unit x 0.2865 (r6kit.MOUSE_K)
        "mouse_sens": 7, "mouse_unit": 0.02, "mouse_ads": 50,
        "fov": 84,                          # VERTICAL, 60-90, as in the game
        "weapon": "r4c", "sight": "1x", "barrel": "flash_hider", "grip": "vertical",
        "speed": 2,                         # operator speed 1-3: 3 is the fast, 100-health kind
    },
    # Anti-cheat (ac/). Hiding the window from screen capture during runs keeps a screen-reading
    # ("AI") aimbot blind; it also keeps OBS and other recorders from seeing the game while you play.
    "anticheat": {"hide_capture": True},
    "video": {
        "fullscreen": True,
        "vsync": False,
        "fps_cap": -2,            # -2 = one frame under the display's real rate, 0 = match it, -1 = uncapped
        "quality": "low",
        "reflections": False,     # glossy floor (High only); off because mirrored targets pull the eye
        "show_fps": True,
    },
}

# Settings that change difficulty. Binds are deliberately NOT here: RB/LB versus
# RT/LT is something worth comparing, so it is a grouping dimension instead.
# (aim.trigger_threshold only takes part when a trigger is bound - see fingerprint_of.)
FINGERPRINT_KEYS = (
    "aim.sens_h", "aim.sens_v", "aim.ads_mult", "aim.deadzone", "aim.curve",
    "aim.stick", "aim.trigger_threshold",
    "game.duration", "game.target_size", "game.fov", "game.ads_zoom",
)

SIZE_MULT = {"small": 0.75, "normal": 1.0, "large": 1.3}

TARGET_COLORS = {
    "red": (1.00, 0.20, 0.16),
    "orange": (1.00, 0.52, 0.10),
    "yellow": (1.00, 0.88, 0.16),
    "green": (0.25, 0.95, 0.35),
    "cyan": (0.15, 0.85, 1.00),
    "magenta": (1.00, 0.25, 0.85),
}

CROSSHAIR_COLORS = {
    "green": (0.20, 1.00, 0.35),
    "cyan": (0.20, 0.95, 1.00),
    "white": (1.00, 1.00, 1.00),
    "yellow": (1.00, 0.92, 0.20),
    "magenta": (1.00, 0.30, 0.90),
    "red": (1.00, 0.22, 0.20),
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path: str = CONFIG_PATH) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            _merge(cfg, json.load(f))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:  # unreadable or corrupt: start from defaults
        print(f"config: could not read {path}: {e}")
    if not cfg["profiles"]:
        cfg["profiles"] = copy.deepcopy(DEFAULTS["profiles"])
    # A config written before mouse and keyboard existed has no such profile: add one, once.
    # (Once only, so that deleting it afterwards sticks.)
    if not cfg["migrated"].get("kbm_profile"):
        cfg["migrated"]["kbm_profile"] = True
        if not any(p.get("kind") == "kbm" for p in cfg["profiles"]):
            name, taken = "Mouse & keyboard", [p["name"] for p in cfg["profiles"]]
            while name in taken:
                name += " "
            cfg["profiles"].append({"name": name, "kind": "kbm", "devices": []})
    if cfg["active_profile"] not in [p["name"] for p in cfg["profiles"]]:
        cfg["active_profile"] = cfg["profiles"][0]["name"]
    return cfg


def save(cfg: dict, path: str = CONFIG_PATH) -> bool:
    """Atomic save. Returns False instead of raising: this folder sits under
    OneDrive, which can hold a file open for a moment while it syncs, and a
    settings tweak must never crash the app over that."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        for _ in range(5):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                time.sleep(0.03)
    except OSError as e:
        print(f"config: could not save {path}: {e}")
        return False
    print(f"config: {path} is locked by another program; settings not saved this time")
    return False


def get(cfg: dict, dotted: str):
    node = cfg
    for part in dotted.split("."):
        node = node[part]
    return node


def put(cfg: dict, dotted: str, value) -> None:
    *path, leaf = dotted.split(".")
    node = cfg
    for part in path:
        node = node[part]
    node[leaf] = value


def kind(cfg: dict, name: str | None = None) -> str:
    """"kbm" when the (active) profile is mouse and keyboard, else "pad"."""
    return "kbm" if profile(cfg, name).get("kind") == "kbm" else "pad"


def mouse_deg(cfg: dict) -> float:
    """Core track: degrees the view turns per mouse count, from cm per full turn and the mouse's DPI."""
    m = cfg["mouse"]
    return 360.0 / (m["cm360"] / 2.54 * m["dpi"])


COD_MOUSE_YAW = 0.0066      # degrees per count per 1.00 of the game's mouse sensitivity (see codkit)


COD_MOUSE_SENS = (0.01, 100.0)      # the values Settings accepts for it


def cod_mouse_cm360(cfg: dict) -> float:
    """What the Call of Duty mouse sensitivity comes to, in centimetres per full turn at this DPI."""
    return 360.0 / (COD_MOUSE_YAW * cfg["cod"]["mouse_sens"] * cfg["mouse"]["dpi"]) * 2.54


def cod_mouse_sens_for(cfg: dict, cm360: float) -> float:
    """The other way: the Call of Duty mouse sensitivity that gives `cm360` centimetres per turn at this DPI."""
    return 360.0 * 2.54 / (COD_MOUSE_YAW * cfg["mouse"]["dpi"] * cm360)


# With a mouse the stick settings mean nothing and the mouse ones do. The first entry keeps a
# mouse run from ever sharing a fingerprint with a controller run, whatever else matches.
KBM_KEYS = ("mouse.cm360", "mouse.ads_mult", "aim.invert_y",
            "game.duration", "game.target_size", "game.fov", "game.ads_zoom")
# The game's sensitivity is per count, so how far the hand moves depends on DPI as well.
COD_KBM_KEYS = ("cod.mouse_sens", "cod.mouse_ads", "cod.mouse_mdc", "mouse.dpi", "aim.invert_y",
                "cod.fov", "cod.ads_fov", "game.duration")


# ---- one sensitivity for every track --------------------------------------------
# Each track words sensitivity its own way (a "dialect"). cfg["sens"] can make one of them the
# sensitivity every track uses, separately for controllers and for mouse and keyboard; the tracks
# then build their aim from that dialect (sens.py) and their fingerprints carry its settings
# instead of their own, so "the same settings" still means "the same turn".
DIALECTS = ("core", "cod", "r6")
# the settings that make up each dialect's sensitivity, per input device
SENS_KEYS = {
    "pad": {"core": ("aim.sens_h", "aim.sens_v", "aim.ads_mult", "aim.deadzone", "aim.curve"),
            "cod": ("cod.sens_h", "cod.sens_v", "cod.ads_mult", "cod.curve", "cod.dz_r_min", "cod.dz_r_max"),
            "r6": ("r6.sens_h", "r6.sens_v", "r6.ads", "r6.dz_r")},
    "kbm": {"core": ("mouse.cm360", "mouse.ads_mult"),
            "cod": ("cod.mouse_sens", "cod.mouse_ads", "cod.mouse_mdc", "mouse.dpi"),
            "r6": ("r6.mouse_sens", "r6.mouse_unit", "r6.mouse_ads", "mouse.dpi")},
}
# ...and what the Call of Duty and Siege dialects derive from their spin tests (cod_rates, r6_rates)
_COD_DERIVED = ("cod.turn_rate_per_unit", "cod.ads_ratio", "r6.turn_rate_per_unit", "r6.ads_ratio")
_ALL_SENS = {k for per in SENS_KEYS.values() for keys in per.values() for k in keys} | set(_COD_DERIVED) | {"sens.source"}


def sens_source(cfg: dict, kind_: str, track: str) -> str:
    """Whose settings decide how the view turns on `track` for this input device."""
    s = cfg.get("sens", {}).get(kind_, "own")
    return s if s in DIALECTS else track


def sens_settings(cfg: dict, kind_: str, src: str) -> dict:
    """The settings behind dialect `src`'s sensitivity, as they go into a fingerprint."""
    out = {k: get(cfg, k) for k in SENS_KEYS[kind_][src]}
    if kind_ == "pad" and src == "cod":
        per_unit, ads_ratio = cod_rates(cfg)
        out["cod.turn_rate_per_unit"] = round(per_unit, 1)
        out["cod.ads_ratio"] = round(ads_ratio, 3)
    elif kind_ == "pad" and src == "r6":
        per_unit, ads_ratio = r6_rates(cfg)
        out["r6.turn_rate_per_unit"] = round(per_unit, 2)
        out["r6.ads_ratio"] = round(ads_ratio, 4)
    return out


def r6_rates(cfg: dict):
    """Siege, controller: (hip deg/s per point of stick sensitivity, ADS-to-hip speed ratio per point
    of ADS sensitivity), from its spin test - the game publishes neither."""
    c = cfg["r6"]["cal"]
    hip = 360.0 * c["hip_turns"] / c["hip_seconds"]
    ads = 360.0 * c["ads_turns"] / c["ads_seconds"]
    return hip / c["sens"], (ads / hip) / c["ads"]


def _follow(cfg: dict, out: dict, kind_: str, track: str) -> dict:
    """A track's comparable settings, with its own sensitivity swapped for the one it follows.
    (Nothing changes while it follows itself, so fingerprints from before this existed still match.)"""
    src = sens_source(cfg, kind_, track)
    if src != track:
        for k in SENS_KEYS[kind_][track] + _COD_DERIVED:
            out.pop(k, None)
        out.update(sens_settings(cfg, kind_, src))
        out["sens.source"] = src
    return out


def comparable(cfg: dict) -> dict:
    if kind(cfg) == "kbm":
        return _follow(cfg, dict({k: get(cfg, k) for k in KBM_KEYS}, input="kbm"), "kbm", "core")
    return _follow(cfg, {k: get(cfg, k) for k in FINGERPRINT_KEYS}, "pad", "core")


def fingerprint_of(settings: dict, binds: dict) -> str:
    """Hash of the settings that can actually change a run's difficulty.

    Trigger actuation only counts when Shoot or Aim is on a trigger: bumpers and
    face buttons are digital, so with them it changes nothing. (Hashing it
    unconditionally once split a bumper-only comparison in two when the value was
    nudged between benchmark passes.)
    """
    if settings.get("input") == "kbm":
        keep = dict(settings)
    else:
        keep = {k: v for k, v in settings.items() if k in FINGERPRINT_KEYS or k in _ALL_SENS}
        if not ({binds.get("shoot"), binds.get("ads")} & {"RT", "LT"}):
            keep.pop("aim.trigger_threshold", None)
    return hashlib.sha1(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:6]


def fingerprint(cfg: dict) -> str:
    return fingerprint_of(comparable(cfg), cfg["binds"])


def binds(cfg: dict) -> dict:
    """The Shoot / Aim (and, for a keyboard, movement) binds of the active profile."""
    return cfg["kbm"] if kind(cfg) == "kbm" else cfg["binds"]


# ---- Call of Duty track -------------------------------------------------------
# Bump when the aim-assist, recoil or damage tuning changes, so runs made under the
# old tuning are not averaged together with runs made under the new one.
#   1  first release        2  heavier recoil; dummies that shoot back announce themselves
#   3  recoil follows the player's photographed spray pattern; Coach no longer judges reaction time
COD_MODEL = 3

COD_KEYS = (
    "cod.sens_h", "cod.sens_v", "cod.ads_mult", "cod.curve",
    "cod.dz_r_min", "cod.dz_r_max", "cod.dz_l_min", "cod.dz_l_max",
    "cod.fov", "cod.ads_fov", "cod.aa_style", "aim.stick", "game.duration",
)


def cod_rates(cfg: dict):
    """(hip turn speed in deg/s per 1.00 of sensitivity, ADS-to-hip speed ratio per
    1.00 of ADS multiplier), both taken from the spin test."""
    c = cfg["cod"]["cal"]
    hip = 360.0 * c["hip_turns"] / c["hip_seconds"]
    ads = 360.0 * c["ads_turns"] / c["ads_seconds"]
    return hip / c["sens"], (ads / hip) / c["ads_mult"]


def hold_ads_speed(cfg: dict):
    """After stick sensitivity has changed: if the finder's ADS speed is being held, move the ADS
    multiplier to whatever gives that speed now (within the game's 0.10-2.00). Returns the
    multiplier if it moved, else None."""
    c = cfg["cod"]
    if not c.get("ads_hold"):
        return None
    mult = round(min(max(c["ads_hold"] / c["sens_h"], 0.10), 2.00), 2)
    if mult == c["ads_mult"]:
        return None
    c["ads_mult"] = mult
    return mult


def comparable_cod(cfg: dict) -> dict:
    if kind(cfg) == "kbm":      # no aim-assist style in it: a mouse gets none under any of them
        out = dict({k: get(cfg, k) for k in COD_KBM_KEYS}, **{"cod.model": COD_MODEL, "input": "kbm"})
        return _follow(cfg, out, "kbm", "cod")
    out = {k: get(cfg, k) for k in COD_KEYS}
    per_unit, ads_ratio = cod_rates(cfg)
    out["cod.turn_rate_per_unit"] = round(per_unit, 1)
    out["cod.ads_ratio"] = round(ads_ratio, 3)
    out["cod.model"] = COD_MODEL
    return _follow(cfg, out, "pad", "cod")


def fingerprint_cod(cfg: dict) -> str:
    return hashlib.sha1(json.dumps(comparable_cod(cfg), sort_keys=True).encode()).hexdigest()[:6]


# ---- Siege track ----------------------------------------------------------------
# Bump when the recoil, damage or movement tuning changes (as COD_MODEL above).
R6_MODEL = 1

# What changes how hard a Siege run is, besides the sensitivity (SENS_KEYS above): the gun and
# what is on it, the field of view, the operator's speed, round length.
R6_KEYS = ("r6.weapon", "r6.sight", "r6.barrel", "r6.grip", "r6.fov", "r6.speed", "r6.dz_l",
           "aim.stick", "game.duration")
R6_KBM_KEYS = ("r6.weapon", "r6.sight", "r6.barrel", "r6.grip", "r6.fov", "r6.speed", "aim.invert_y", "game.duration")


def comparable_r6(cfg: dict) -> dict:
    k = kind(cfg)
    out = {key: get(cfg, key) for key in (R6_KBM_KEYS if k == "kbm" else R6_KEYS)}
    out.update(sens_settings(cfg, k, "r6"))
    out["r6.model"] = R6_MODEL
    if k == "kbm":
        out["input"] = "kbm"
    return _follow(cfg, out, k, "r6")


def fingerprint_r6(cfg: dict) -> str:
    return hashlib.sha1(json.dumps(comparable_r6(cfg), sort_keys=True).encode()).hexdigest()[:6]


def comparable_for(cfg: dict, track: str) -> dict:
    return {"cod": comparable_cod, "r6": comparable_r6}.get(track, comparable)(cfg)


def fingerprint_for(cfg: dict, track: str) -> str:
    return {"cod": fingerprint_cod, "r6": fingerprint_r6}.get(track, fingerprint)(cfg)


def fingerprints(cfg: dict) -> dict:
    """Current fingerprint of every track, for filtering the run log."""
    return {"core": fingerprint(cfg), "cod": fingerprint_cod(cfg), "r6": fingerprint_r6(cfg)}


def profile(cfg: dict, name: str | None = None) -> dict:
    name = name or cfg["active_profile"]
    for p in cfg["profiles"]:
        if p["name"] == name:
            return p
    return cfg["profiles"][0]
