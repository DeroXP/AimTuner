"""Window, main loop and frame pacing; glue between the pad, the game and the menus.

Per frame: wait for the frame slot, THEN read the controller, simulate, draw and
swap. Sampling input after the wait rather than before it means the stick
position on screen is at most one render (well under a millisecond) old.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import os
import sys
import threading
import time

import glfw

from . import __version__, codscn, config, dpi, scenarios, stats
from .game import Game
from .gfx import Camera, Renderer
from .mouse import BUTTON, Mouse
from .pad import Pad
from .screens import Controllers, MainMenu, Results
from .sound import Sound
from .theme import ACCENT, FAINT

TITLE = "AimTuner"

_KEYS = {
    glfw.KEY_UP: "up", glfw.KEY_DOWN: "down", glfw.KEY_LEFT: "left", glfw.KEY_RIGHT: "right",
    glfw.KEY_ENTER: "enter", glfw.KEY_KP_ENTER: "enter", glfw.KEY_SPACE: "space",
    glfw.KEY_ESCAPE: "esc", glfw.KEY_BACKSPACE: "backspace",
    glfw.KEY_X: "x", glfw.KEY_Y: "y", glfw.KEY_Q: "q", glfw.KEY_E: "e",
    glfw.KEY_W: "w", glfw.KEY_A: "a", glfw.KEY_S: "s", glfw.KEY_D: "d",
    glfw.KEY_F11: "f11", glfw.KEY_F12: "f12",
    glfw.KEY_C: "c", glfw.KEY_V: "v", glfw.KEY_I: "i",
}


def _tune_process():
    """1 ms timer granularity and a small priority bump, so the frame limiter's
    sleeps are accurate and a background task is less likely to steal a frame."""
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00008000)   # ABOVE_NORMAL_PRIORITY_CLASS
    except (AttributeError, OSError):
        pass
    try:
        # No power throttling ("EcoQoS"): on a CPU with performance and efficiency cores, a
        # throttled thread is steered to the slow cores and wakes from its sleeps later.
        # PROCESS_POWER_THROTTLING_STATE {Version 1, ControlMask EXECUTION_SPEED | IGNORE_TIMER_RESOLUTION, StateMask 0}
        k32 = ctypes.windll.kernel32
        state = (ctypes.c_ulong * 3)(1, 0x1 | 0x4, 0)
        k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        k32.SetProcessInformation(k32.GetCurrentProcess(), 4, state, ctypes.sizeof(state))   # ProcessPowerThrottling
        tstate = (ctypes.c_ulong * 3)(1, 0x1, 0)
        k32.GetCurrentThread.restype = wt.HANDLE
        k32.SetThreadInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        k32.SetThreadInformation(k32.GetCurrentThread(), 3, tstate, ctypes.sizeof(tstate))   # ThreadPowerThrottling
    except (AttributeError, OSError):
        pass
    try:
        # The render thread (this one) joins the Multimedia Class Scheduler's "Games" task, as a
        # game's render thread does: it is then scheduled ahead of ordinary work on the machine,
        # which is what keeps a frame from starting late when something else wakes up.
        avrt = ctypes.WinDLL("avrt")
        avrt.AvSetMmThreadCharacteristicsW.restype = wt.HANDLE
        avrt.AvSetMmThreadCharacteristicsW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
        task = wt.DWORD(0)
        h = avrt.AvSetMmThreadCharacteristicsW("Games", ctypes.byref(task))
        if h:
            avrt.AvSetMmThreadPriority.argtypes = [wt.HANDLE, ctypes.c_int]
            avrt.AvSetMmThreadPriority(h, 1)                        # AVRT_PRIORITY_HIGH
    except (AttributeError, OSError):
        pass
    # The input thread must get the GIL promptly when it wakes; 5 ms (the default) is a whole frame.
    sys.setswitchinterval(0.0005)


class App:
    def __init__(self, opts):
        self.opts = opts
        self.cfg = config.load(opts.config or config.CONFIG_PATH)
        self.log = stats.Log(opts.logdir or config.LOG_DIR)
        self.session_id = time.strftime("%Y%m%d-%H%M%S")
        self.offscreen = bool(opts.offscreen)
        self.fullscreen = False
        self.keys: set = set()
        self.typed: list = []
        self._keybuf: set = set()
        self._charbuf: list = []
        self.clicks: set = set()         # "L" / "R": window clicks this frame (menus only use these)
        self._clickbuf: set = set()
        self._hits: list = []            # what the last frame drew that the pointer can point at
        self._hits_of = None             # ... and which screen drew it
        self._ptr = None
        self._cursor = None
        self.focused = True
        self.game: Game | None = None
        self.screens: list = []
        self.bot = None
        self.auto = None
        self.pending_shot = None
        self._toast = None
        self._last_dev = None
        self.fps = 0.0
        self.cpu_ms = 0.0
        self.t_start = self.now = time.perf_counter()
        # the PC check (ac/pccheck.py): its verdict for this PC and this version of AimTuner
        self.pc_path = os.path.join(os.path.dirname(os.path.abspath(opts.config or config.CONFIG_PATH)), "pccheck.json")
        self.pc_status, self.pc_record = "needed", None
        self.build_state = "checking"  # ac/seal.py: official / modified / source, worked out in the background
        threading.Thread(target=self._check_build, name="build-check", daemon=True).start()
        self.ac_pending: list = []   # (guard, record) of runs whose play is still being checked (ac/)
        self.pace = None             # tools/frametest.py: per-frame (start, late, work, swap) in seconds
        self.capturing = False       # Controller Lab is recording: keep this thread out of the way

        _tune_process()
        self._open_window()
        w, h = opts.offscreen if self.offscreen else glfw.get_framebuffer_size(self.win)
        self.gfx = Renderer(w, h, offscreen=self.offscreen)
        self.ui = self.gfx.ui
        self.menu_cam = Camera()
        self.pad = Pad()
        # Raw mouse and keyboard, on their own thread. Not started for offscreen runs (tests,
        # --bench): those feed a virtual mouse and have no business listening to the real one.
        self.mouse = Mouse(start=not self.offscreen)
        self.dpi = dpi.Reader()         # asks the mouse for its DPI, in the background (see _dpi_tick)
        self._dpi_next = 0.0
        self._dpi_startup = False
        self._pad_profile = None        # the controller profile to go back to when a pad button is pressed
        self._grabbed = False
        self.sfx = Sound(config.ROOT, enabled=self.cfg["game"]["sounds"] and not self.offscreen)
        self.apply_input()
        self.apply_video()
        self.screens.append(MainMenu(self))
        if not self.offscreen:
            self._pc_start()
        print(f"AimTuner {__version__}  |  {self.gfx.info['renderer']}  |  GL {self.gfx.info['version']}"
              f"  |  {self.gfx.w}x{self.gfx.h} @ {self.refresh} Hz")
        print(f"settings {opts.config or config.CONFIG_PATH}  |  logs {self.log.dir}")

    # ---- window -------------------------------------------------------------
    def _open_window(self):
        if not glfw.init():
            raise RuntimeError("GLFW failed to initialise")
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, True)
        # No depth, stencil or MSAA planes: nothing here needs them, and at 4K
        # they would be several hundred MB of framebuffer to clear and swap.
        glfw.window_hint(glfw.DEPTH_BITS, 0)
        glfw.window_hint(glfw.STENCIL_BITS, 0)
        glfw.window_hint(glfw.SAMPLES, 0)
        self.monitor = glfw.get_primary_monitor()
        self.mode = mode = glfw.get_video_mode(self.monitor)
        self.refresh = int(mode.refresh_rate) or 60
        from . import display

        self.refresh_exact = None if self.offscreen else display.exact_refresh()   # 239.99, where GLFW says 240
        self.driver_cap = None if self.offscreen else display.driver_cap(os.path.basename(sys.executable))   # NVIDIA "Max Frame Rate"
        self.auto_cap = display.cap_under(self.refresh_exact, self.refresh)
        if self.driver_cap and self.driver_cap - 1 < self.auto_cap:
            self.auto_cap = max(self.driver_cap - 1, 30)        # under the driver's: see display.driver_cap
        self.win_size = (int(mode.size.width * 0.6), int(mode.size.height * 0.6))
        if self.offscreen:
            glfw.window_hint(glfw.VISIBLE, False)
            self.win = glfw.create_window(320, 180, TITLE, None, None)
        elif self.cfg["video"]["fullscreen"] and not self.opts.windowed:
            glfw.window_hint(glfw.RED_BITS, mode.bits.red)
            glfw.window_hint(glfw.GREEN_BITS, mode.bits.green)
            glfw.window_hint(glfw.BLUE_BITS, mode.bits.blue)
            glfw.window_hint(glfw.REFRESH_RATE, mode.refresh_rate)
            self.win = glfw.create_window(mode.size.width, mode.size.height, TITLE, self.monitor, None)
            self.fullscreen = True
        else:
            self.win = glfw.create_window(*self.win_size, TITLE, None, None)
        if not self.win:
            glfw.terminate()
            raise RuntimeError("could not create an OpenGL 3.3 window")
        glfw.make_context_current(self.win)
        self.hwnd = None
        if not self.offscreen:
            try:                                    # the anti-cheat hides this window from screen capture during runs
                self.hwnd = glfw.get_win32_window(self.win)
            except Exception:
                self.hwnd = None
        if not self.offscreen:
            try:                                    # the crosshair icon in the title bar and on the taskbar
                from . import icon

                glfw.set_window_icon(self.win, 2, [icon.image(32), icon.image(48)])
            except Exception as e:                  # cosmetic: never worth failing to start over
                print(f"window icon: {e}")
        glfw.set_key_callback(self.win, self._on_key)
        glfw.set_char_callback(self.win, self._on_char)
        glfw.set_mouse_button_callback(self.win, self._on_button)
        glfw.set_framebuffer_size_callback(self.win, self._on_resize)
        glfw.set_window_focus_callback(self.win, self._on_focus)

    def _on_key(self, win, key, scancode, action, mods):
        if action != glfw.RELEASE and key in _KEYS:
            self._keybuf.add(_KEYS[key])
        if action == glfw.PRESS and key == glfw.KEY_V and mods & glfw.MOD_CONTROL:
            self._keybuf.add("paste")

    def _on_char(self, win, codepoint):
        self._charbuf.append(chr(codepoint))

    def _on_button(self, win, button, action, mods):
        if action == glfw.PRESS:
            if button == glfw.MOUSE_BUTTON_LEFT:
                self._clickbuf.add("L")
            elif button == glfw.MOUSE_BUTTON_RIGHT:
                self._clickbuf.add("R")

    def _on_resize(self, win, w, h):
        if not self.offscreen and w > 0 and h > 0:
            self.gfx.resize(w, h)

    def _on_focus(self, win, focused):
        self.focused = bool(focused)
        if not focused and self.game and not self.offscreen:
            self.game.pause()

    def apply_video(self):
        v = self.cfg["video"]
        if self.offscreen:
            return
        want = bool(v["fullscreen"]) and not self.opts.windowed
        if want != self.fullscreen:
            m = self.mode
            if want:
                glfw.set_window_monitor(self.win, self.monitor, 0, 0, m.size.width, m.size.height, m.refresh_rate)
            else:
                mx, my = glfw.get_monitor_pos(self.monitor)
                w, h = self.win_size
                glfw.set_window_monitor(self.win, None, mx + (m.size.width - w) // 2,
                                        my + (m.size.height - h) // 2, w, h, 0)
            self.fullscreen = want
        glfw.swap_interval(1 if v["vsync"] else 0)

    def grab_mouse(self, on: bool):
        """While a mouse run is live the pointer is hidden and held inside the window, so a flick
        cannot land on the taskbar or another monitor. Movement itself never comes from the
        pointer - it comes from the raw reports - so this changes nothing about the aim."""
        if self.offscreen or on == self._grabbed:
            return
        self._grabbed = on
        self._cursor_tick()

    def _cursor_tick(self):
        """The pointer shows in the menus, is hidden over a run, and is held in the window
        during a mouse run (grab_mouse)."""
        if self.offscreen:
            return
        mode = (glfw.CURSOR_DISABLED if self._grabbed else
                glfw.CURSOR_HIDDEN if self.game else glfw.CURSOR_NORMAL)
        if mode != self._cursor:
            self._cursor = mode
            glfw.set_input_mode(self.win, glfw.CURSOR, mode)

    def _pointer(self, nav: set):
        """Menus answer the mouse pointer: pointing at a line selects it, a left click presses
        A on it, a right click is B, and the footer's hints are buttons."""
        self._ptr, prev = None, self._ptr
        scr = self.screens[-1]
        if self.offscreen or self.game or not self.focused or self._hits_of is not scr:
            return
        if "R" in self.clicks:
            nav.add("back")
        if getattr(scr, "capture", None):           # a bind is waiting for a key or button
            return
        ww, wh = glfw.get_window_size(self.win)
        if ww <= 0 or wh <= 0:
            return
        cx, cy = glfw.get_cursor_pos(self.win)
        ui = self.ui
        ux, uy = cx * ui.fb_w / ww / ui.s, cy * ui.fb_h / wh / ui.s
        self._ptr = (ux, uy)
        left = "L" in self.clicks
        moved = prev is not None and (abs(ux - prev[0]) > 0.5 or abs(uy - prev[1]) > 0.5)
        hit = next((what for x, y, w, h, what in reversed(self._hits)
                    if x <= ux < x + w and y <= uy < y + h), None)
        if hit is None or not (moved or left):
            return
        if isinstance(hit, tuple) and hit[0] == "nav":
            if left:
                nav.add(hit[1])
        elif scr.point(hit, left) and left:
            nav.add("accept")

    def apply_input(self):
        self.pad.trigger_thr = self.cfg["aim"]["trigger_threshold"]

    def frame_cap(self):
        """Frames per second the limiter enforces, or None when nothing is limiting."""
        if self.capturing:
            return 30        # the input thread is spinning to time every report; drawing fast would starve it
        if self.opts.cap is not None:
            return self.opts.cap if self.opts.cap > 0 else None
        v = self.cfg["video"]
        cap = v["fps_cap"]
        if cap == -2:
            cap = self.auto_cap
        elif cap == 0:
            cap = self.refresh
        if cap < 0 or (v["vsync"] and not self.offscreen and cap >= self.refresh):
            return None      # V-Sync is already pacing the swap
        return cap

    # ---- config / profiles --------------------------------------------------
    def save(self):
        config.save(self.cfg, self.opts.config or config.CONFIG_PATH)

    def owner_of(self, dev_id):
        if dev_id:
            for p in self.cfg["profiles"]:
                if dev_id in p["devices"]:
                    return p["name"]
        return None

    def set_profile(self, name: str, link: bool = False):
        self.cfg["active_profile"] = name
        if link and config.kind(self.cfg, name) != "kbm":
            dev = (self.pad.info() or {}).get("id")
            if dev and not self.owner_of(dev):
                config.profile(self.cfg, name)["devices"].append(dev)
        self.save()

    def link_device(self, name: str):
        dev = (self.pad.info() or {}).get("id")
        if not dev:
            return
        for p in self.cfg["profiles"]:
            if dev in p["devices"]:
                p["devices"].remove(dev)
        config.profile(self.cfg, name)["devices"].append(dev)
        self.save()

    def _sync_device(self):
        """When a linked pad becomes the active one, switch to its profile."""
        info = self.pad.info()
        dev = info.get("id") if info else None
        if dev == self._last_dev:
            return
        self._last_dev = dev
        owner = self.owner_of(dev)
        # (not while the mouse profile is in use: a pad merely being plugged in is no reason to
        # leave it - pressing a button on the pad is, see _sync_input)
        if owner and owner != self.cfg["active_profile"] and not self.game and config.kind(self.cfg) != "kbm":
            self.cfg["active_profile"] = owner
            self.save()
            self.toast(f"Controller: {owner}")

    def _sync_input(self):
        """Use it and it is selected: a click switches to the mouse-and-keyboard profile, a button
        on a controller switches back to that controller's. Never during a run - a run keeps the
        device it started with, and the other one is not read."""
        if self.game or self.screens[-1].text_mode:
            return
        cfg = self.cfg
        if config.kind(cfg) == "kbm":
            if any(down for _, _, down in self.pad.events):
                owner = self.owner_of((self.pad.info() or {}).get("id")) or self._pad_profile
                names = [p["name"] for p in cfg["profiles"] if p.get("kind") != "kbm"]
                if owner not in names:
                    owner = names[0] if names else None
                if owner:
                    self.set_profile(owner)
                    self.toast(f"Controller: {owner}")
                    self.pad.events.clear()     # that press chose the device; it should not also press "select"
        elif any(k == BUTTON and a == "M1" and b for _, k, a, b in self.mouse.events):
            name = next((p["name"] for p in cfg["profiles"] if p.get("kind") == "kbm"), None)
            if name and self.mouse.available:
                self._pad_profile = cfg["active_profile"]
                self.set_profile(name)
                self.toast(f"Input: {name}")

    def _pc_start(self):
        """At start-up: the PC check if this PC or this version has not had one (it asks first);
        otherwise its quick part, in the background - a cheat installed since fails it now."""
        from .ac import pccheck

        self.pc_status, self.pc_record = pccheck.status(self.pc_path, __version__)
        if self.pc_status in ("needed", "tampered"):
            from .acscreens import PCCheckScreen

            self.push(PCCheckScreen(self, first=True))
        elif self.pc_status == "passed":
            threading.Thread(target=self._pc_quick, name="pccheck-quick", daemon=True).start()

    def _pc_quick(self):
        from .ac import pccheck

        try:
            res = pccheck.Scan(quick=True).run()
        except Exception:
            return
        if not res["passed"]:
            rec = dict(self.pc_record or {})
            rec.update(passed=False, findings=(rec.get("findings") or []) + res["findings"],
                       failed_at_start=time.strftime("%Y-%m-%dT%H:%M:%S"))
            for k in ("mac", "app", "machine", "time"):
                rec.pop(k, None)
            self.pc_record = pccheck.save(self.pc_path, rec, __version__)
            self.pc_status = "failed"
            self.toast("PC check failed at start-up - runs are unverified (Settings > Anti-cheat)", 6.0)

    def _check_build(self):
        from .ac import seal

        try:
            self.build_state = seal.build_state()
        except Exception:
            self.build_state = "modified"

    def _ac_tick(self):
        """A run's behavioural check has finished: fold it into the record and rewrite its log line."""
        if not self.ac_pending or self.game is not None:
            return
        keep = []
        for guard, rec in self.ac_pending:
            if guard.poll(rec):
                try:
                    self.log.update(rec)
                except OSError as e:
                    print(f"log: could not update run {rec.get('run_id')}: {e}")
                st = rec["integrity"]["status"]
                if st == "flagged":
                    self.toast("Anti-cheat flagged that run - it is not counted (see its results)", 5.0)
            elif rec.get("integrity", {}).get("behaviour") == "pending":
                keep.append((guard, rec))
        self.ac_pending = keep

    def _dpi_tick(self, now: float):
        """Keep the DPI setting equal to what the mouse itself says, where the mouse can say.

        Only between runs (a run has the mouse to itself), only for a real mouse, and only while
        the setting is on Automatic. On a controller profile once at start-up is enough; on the
        mouse profile it is asked again every few seconds, which is what catches the DPI button."""
        m = self.cfg["mouse"]
        res = self.dpi.poll()
        if res and res.get("dpi") and m["dpi_auto"] and res["dpi"] != m["dpi"] and not self.game:
            old, m["dpi"] = m["dpi"], res["dpi"]
            self.save()
            self.toast(f"Mouse DPI read from {res['name']}: {res['dpi']} (it was set to {old})", 5.0)
        if self.offscreen or self.game or not m["dpi_auto"] or now < self._dpi_next:
            return
        if config.kind(self.cfg) != "kbm" and self._dpi_startup:
            return
        self._dpi_startup = True
        self._dpi_next = now + 4.0
        self.dpi.ask(self.mouse.device_path())

    def toast(self, text: str, secs: float = 2.5):
        self._toast = (text, time.perf_counter() + secs)

    # ---- screens / runs -----------------------------------------------------
    def push(self, screen):
        self.screens.append(screen)

    def pop(self):
        if len(self.screens) > 1:
            self.screens.pop()

    def _pad_matches_profile(self) -> bool:
        """Refuse to start a run that would be logged under the wrong controller.

        The first real session produced exactly that: pads were swapped, the
        profile was not, and a run went into the other pad's history.
        """
        if config.kind(self.cfg) == "kbm":
            if not self.mouse.available:
                self.toast("Raw mouse input is not available: " + (self.mouse.error or "unknown reason"), 7.0)
                return False
            return True                       # nothing to mix up: there is one mouse-and-keyboard profile per name
        dev = (self.pad.info() or {}).get("id")
        if not dev:
            return True                       # no USB id to go on
        prof = config.profile(self.cfg)
        owner = self.owner_of(dev)
        if owner == prof["name"]:
            return True
        if owner is None and not prof["devices"]:
            prof["devices"].append(dev)       # a profile's first run defines its pad
            self.save()
            return True
        self.toast(f"This pad is linked to '{owner}', not '{prof['name']}'" if owner else
                   f"This pad ({dev}) is not the one linked to '{prof['name']}' - pick its profile", 7.0)
        self.push(Controllers(self))
        return False

    def playable(self, scn_cls) -> bool:
        """Can the active profile's input device play this scenario? (Stick Snap times a thumbstick.)"""
        return not (getattr(scn_cls, "pad_only", False) and config.kind(self.cfg) == "kbm")

    def start(self, scn_cls, playlist=None, over=None):
        if not self.playable(scn_cls):
            self.toast(f"{scn_cls.name} measures a thumbstick - it needs a controller")
            return False
        if (playlist is None or playlist["index"] == 0) and not self._pad_matches_profile():
            return False
        self.game = Game(self, scn_cls, playlist, over)
        return True

    def start_playlist(self, classes, label="track", **extra):
        """Play several scenarios back to back; each is still logged as its own run.

        extra: then="back" returns to the screen underneath after the last one instead of
        opening Compare; duel={...} is copied into every run's record (guided comparison)."""
        classes = [c for c in classes if self.playable(c)]
        if not classes:
            return None
        pl = dict({"id": f"{label}-" + time.strftime("%Y%m%d-%H%M%S"), "items": classes, "index": 0}, **extra)
        return pl if self.start(classes[0], pl) else None

    def end_game(self, rec, game):
        if self.game is game:
            self.game = None
        if rec:
            self.push(Results(self, rec, game.playlist, capture=game.capture))

    def quit(self):
        glfw.set_window_should_close(self.win, True)

    # ---- frame --------------------------------------------------------------
    def _nav(self, now) -> set:
        nav = self.pad.nav(now)
        self._pointer(nav)
        k = self.keys
        for name in ("up", "down", "left", "right"):
            if name in k:
                nav.add(name)
        typing = self.game is None and self.screens[-1].text_mode
        if not typing:
            if "enter" in k or "space" in k:
                nav.add("accept")
            if "esc" in k or "backspace" in k:
                nav.add("back")
            for key, act in (("x", "x"), ("y", "y"), ("q", "lb"), ("e", "rb"), ("c", "c"), ("v", "v"), ("i", "i")):
                if key in k:
                    nav.add(act)
            if self.game is None:       # in the menus W A S D are arrows too, and the wheel moves up and down
                for key, act in (("w", "up"), ("s", "down"), ("a", "left"), ("d", "right")):
                    if key in k:
                        nav.add(act)
                if self.mouse.wheel:
                    nav.add("up" if self.mouse.wheel > 0 else "down")
        if "esc" in k:
            nav.add("esc")
        return nav

    def _menu_scene(self, now):
        t = now - self.t_start
        cam = self.menu_cam
        cam.set(13.0 * math.sin(t * 0.13), 1.5 + 2.0 * math.sin(t * 0.09), 100.0, self.gfx.aspect)
        col = config.TARGET_COLORS[self.cfg["game"]["target_color"]]
        out = []
        for k in range(5):
            yaw = math.radians(-34.0 + 17.0 * k + 5.0 * math.sin(t * 0.37 + k * 1.3))
            pitch = math.radians(2.0 + 7.0 * math.sin(t * 0.29 + k * 1.9))
            d = 15.0 + 2.5 * math.sin(k * 2.1)
            cp = math.cos(pitch)
            out.append(((d * math.sin(yaw) * cp, 5.0 + d * math.sin(pitch), -d * math.cos(yaw) * cp),
                        0.62, col, 1.0, 1.0))
        return cam, out

    def _draw(self, now):
        gfx, ui, game = self.gfx, self.ui, self.game
        video = self.cfg["video"]
        kbm = ui.kbm = config.kind(self.cfg) == "kbm"       # the footers word their hints for whichever is in use
        ui.hits = [] if not game else None                  # filled by the screen's rows (uikit._hit)
        quality = (2 if video["reflections"] else 1) if video["quality"] == "high" else 0
        gfx.begin()
        if game:
            dim = 0.45 if game.state == "paused" else 1.0
            gfx.world(game.cam, game.spheres(), quality, dim, **game.world_args())
            game.draw(ui)
        elif hasattr(self.screens[-1], "scene"):        # a replay: a run drawn again from its capture
            cam, spheres, world = self.screens[-1].scene()
            gfx.world(cam, spheres, quality, 1.0, **world)
            self.screens[-1].draw(ui)
        else:
            cam, spheres = self._menu_scene(now)
            gfx.world(cam, spheres if self.screens[-1].decor else (), quality, 0.6)
            ui.rect(0, 0, ui.W, ui.H, (0.0, 0.0, 0.0, 0.45))    # keep menu text readable over the arena
            self.screens[-1].draw(ui)
        if ui.hits is not None:
            self._hits, self._hits_of = ui.hits, self.screens[-1]
            ui.hits = None
        if self.cfg["video"]["show_fps"]:
            bits = [f"{self.fps:.0f} fps", f"GPU {gfx.gpu_ms:.2f} ms", f"CPU {self.cpu_ms:.2f} ms"]
            if kbm:
                if self.mouse.hz:
                    bits.append(f"mouse {self.mouse.hz:,.0f} Hz")
            elif self.pad.hz:
                bits.append(f"pad {self.pad.hz:,.0f} Hz")
            ui.text(56, 1054, "   ·   ".join(bits), "s", FAINT)
        if self._toast:
            text, until = self._toast
            if now < until:
                w = ui.width(text, "l") + 60
                ui.rect(ui.W / 2 - w / 2, 136, w, 52, (0.0, 0.0, 0.0, 0.7))
                ui.text(ui.W / 2, 146, text, "l", ACCENT, 1)
            else:
                self._toast = None
        gfx.end()
        if self.pending_shot:
            gfx.screenshot(self.pending_shot)
            self.pending_shot = None

    def run(self):
        pc = time.perf_counter
        win, pad, gfx = self.win, self.pad, self.gfx
        last = next_t = pc()
        acc_t, acc_n, acc_cpu = last, 0, 0.0
        # How long before a frame's slot the limiter stops sleeping and spins. Sleeps wake late by
        # a varying amount (Windows' timer, the scheduler): the margin follows the latest of the
        # last few hundred wake-ups, so it is as small as this machine allows - less spinning -
        # and never so small that a frame starts late.
        margin, overs = 0.0013, []
        probe = [] if self.opts.exit_after else None      # --exit-after: frame-time report on exit
        while not glfw.window_should_close(win):
            if probe is not None and pc() - self.t_start > self.opts.exit_after:
                break
            # --- pace: sleep most of the wait, spin the last fraction of a millisecond
            cap = self.frame_cap()
            if not self.focused and not self.offscreen:
                cap = 60
            if cap:
                next_t += 1.0 / cap
                now = pc()
                if next_t < now - 0.05:
                    next_t = now          # fell behind (alt-tab, hitch): don't try to catch up
                rem = next_t - now
                # sleep() overshoots by up to ~1 ms on Windows; the margin (above) adapts to it.
                if rem > margin + 0.0002:
                    want = next_t - margin
                    time.sleep(rem - margin)
                    overs.append(pc() - want)
                    if len(overs) >= 480:
                        overs.sort()
                        margin = min(max(overs[int(len(overs) * 0.998)] + 0.00015, 0.0003), 0.002)
                        overs = []
                while pc() < next_t:
                    pass
            now = self.now = pc()
            raw_dt = now - last
            last = now
            dt = min(raw_dt, 0.1)

            glfw.poll_events()
            self.keys, self._keybuf = self._keybuf, set()
            self.typed, self._charbuf = self._charbuf, []
            self.clicks, self._clickbuf = self._clickbuf, set()
            if not self.offscreen and glfw.get_window_attrib(win, glfw.ICONIFIED):
                time.sleep(0.05)
                continue
            if "f11" in self.keys and not self.offscreen:
                self.cfg["video"]["fullscreen"] = not self.fullscreen
                self.apply_video()
                self.save()
            if "f12" in self.keys:
                self.pending_shot = os.path.join(self.log.dir, "shots", time.strftime("%Y%m%d-%H%M%S") + ".png")

            if self.bot:
                self.bot.step(now)
            else:
                pad.poll_fallback(glfw, now)
            pad.update(now)
            # Last thing before simulating: everything the mouse and keyboard have reported up to
            # this instant. (Reports made while the window is not focused are dropped unread.)
            self.mouse.update(pc(), self.focused and not self.offscreen)
            self._sync_device()
            self._sync_input()
            self._ac_tick()
            self._dpi_tick(now)
            if self.auto:
                self.auto.step(now)
            nav = self._nav(now)
            if self.game:
                self.game.update(dt, now, nav, raw_dt)
            else:
                self.screens[-1].update(nav, now)

            self._cursor_tick()
            self._draw(now)
            t_work = pc()
            if self.offscreen:
                gfx.finish()
            else:
                glfw.swap_buffers(win)
            if self.game:
                self.game.presented(pc())
            if self.pace is not None:
                self.pace.append((now, (now - next_t) if cap else 0.0, t_work - now, pc() - t_work))

            acc_n += 1
            acc_cpu += t_work - now
            if probe is not None and now - self.t_start > 1.0:
                probe.append((raw_dt, t_work - now))
            if now - acc_t >= 0.5:
                self.fps = acc_n / (now - acc_t)
                self.cpu_ms = acc_cpu / acc_n * 1000.0
                acc_t, acc_n, acc_cpu = now, 0, 0.0
        if probe:
            dts = sorted(p[0] for p in probe)
            n = len(dts)
            worst = dts[int(n * 0.99):] or dts[-1:]
            print(f"frames {n}  avg {n / sum(dts):.1f} fps  1% low {len(worst) / sum(worst):.1f} fps  "
                  f"p99 {dts[min(int(n * 0.99), n - 1)] * 1000:.3f} ms  max {dts[-1] * 1000:.3f} ms  "
                  f"cpu {sum(p[1] for p in probe) / n * 1000:.3f} ms/frame  gpu {self.gfx.gpu_ms:.3f} ms/frame  "
                  f"pad {self.pad.hz and round(self.pad.hz)} Hz  poll {self.pad.poll_hz and round(self.pad.poll_hz)} Hz  "
                  f"raw mouse {'ok' if self.mouse.available and self.mouse.thread and self.mouse.thread.ok else self.mouse.error or 'off'}"
                  f" ({self.mouse.reports} reports, {self.mouse.hz and round(self.mouse.hz)} Hz)  "
                  f"fullscreen {self.fullscreen}  {self.gfx.w}x{self.gfx.h}  build {self.build_state}")
        self.close()

    def close(self):
        self.pad.stop()
        self.mouse.stop()
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except (AttributeError, OSError):
            pass
        glfw.terminate()

    # ---- GPU benchmark ------------------------------------------------------
    def bench(self, frames: int = 3000) -> dict:
        """Render a busy representative frame flat out, offscreen, and time it.

        glFinish after every frame forces the GPU to actually complete the work,
        so the figure is real throughput at this resolution rather than how fast
        commands can be queued.
        """
        pc = time.perf_counter
        out = {}
        # the Core arena, then the two heaviest Call of Duty scenes: cover boxes, and smoke
        cases = (("core, high", scenarios.Spray, "high", False), ("core, low", scenarios.Spray, "low", False),
                 ("core, high + reflections", scenarios.Spray, "high", True),
                 ("cod cover, high", codscn.CoverBreak, "high", False),
                 ("cod smoke, high", codscn.SmokeLine, "high", False),
                 ("cod cover, low", codscn.CoverBreak, "low", False),
                 ("cod cover, high + reflections", codscn.CoverBreak, "high", True))
        for label, cls, quality, reflections in cases:
            self.cfg["video"]["quality"] = quality
            self.cfg["video"]["reflections"] = reflections
            game = self.game = Game(self, cls)
            game.started = True
            game.state = "run"
            game.scn.begin(0.0)
            for i in range(frames + 200):
                if i == 200:                   # after warm-up: shader compiles, font cache, first spawns
                    t0 = pc()
                game.yaw = 20.0 * math.sin(i * 0.02)
                game.pitch = 4.0 * math.sin(i * 0.013)
                game.t += 1 / 240
                game._update_cam()
                game.scn.update(1 / 240, game.t)
                self._draw(pc())
                self.gfx.finish()
            dt = (pc() - t0) / frames
            out[label] = {"ms_per_frame": round(dt * 1000.0, 3), "fps": round(1.0 / dt),
                          "gpu_ms": round(self.gfx.gpu_ms, 3)}
        self.game = None
        return out
