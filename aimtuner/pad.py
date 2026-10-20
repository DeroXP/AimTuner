"""Controller input.

XInput is read on its own thread at ~2 kHz, far faster than the render loop, and
every state change is stamped with QueryPerformanceCounter. Reaction and
time-to-kill numbers therefore carry the input thread's resolution (about
+/-0.25 ms) instead of being rounded to a 4 ms frame at 240 Hz.

The driver's packet counter is also sampled, which gives an honest reading of
how many reports per second the pad is really delivering - an "8K" controller
that has fallen back to 1000 Hz shows up as ~1000 here.

A pad that is not in XInput mode is picked up through GLFW's gamepad mapping
instead; that path is polled once per frame, so its timestamps are frame-quantised.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import threading
import time
from collections import deque

BTN_BITS = (
    ("DPAD_UP", 0x0001), ("DPAD_DOWN", 0x0002), ("DPAD_LEFT", 0x0004), ("DPAD_RIGHT", 0x0008),
    ("START", 0x0010), ("BACK", 0x0020), ("LS", 0x0040), ("RS", 0x0080),
    ("LB", 0x0100), ("RB", 0x0200),
    ("A", 0x1000), ("B", 0x2000), ("X", 0x4000), ("Y", 0x8000),
)
BIT = dict(BTN_BITS)

# What a bind may be set to. START/BACK stay reserved for pause.
BINDABLE = ("RT", "LT", "RB", "LB", "A", "B", "X", "Y", "RS", "LS",
            "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT")
LABEL = {
    "RT": "RT", "LT": "LT", "RB": "RB", "LB": "LB", "A": "A", "B": "B", "X": "X", "Y": "Y",
    "RS": "R3", "LS": "L3", "DPAD_UP": "D-pad up", "DPAD_DOWN": "D-pad down",
    "DPAD_LEFT": "D-pad left", "DPAD_RIGHT": "D-pad right", "START": "Start", "BACK": "Back",
}

VIRTUAL_SLOT = 9      # synthetic pad used by the self-test bot
FALLBACK_SLOT = 8     # non-XInput pad read through GLFW


class _Gamepad(ctypes.Structure):
    _fields_ = [("b", wt.WORD), ("lt", ctypes.c_ubyte), ("rt", ctypes.c_ubyte),
                ("lx", ctypes.c_short), ("ly", ctypes.c_short),
                ("rx", ctypes.c_short), ("ry", ctypes.c_short)]


class _State(ctypes.Structure):
    _fields_ = [("pkt", wt.DWORD), ("gp", _Gamepad)]


class _Caps(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ubyte), ("sub", ctypes.c_ubyte), ("flags", wt.WORD),
                ("gp", _Gamepad), ("vl", wt.WORD), ("vr", wt.WORD)]


class _CapsEx(ctypes.Structure):
    _fields_ = [("caps", _Caps), ("vid", wt.WORD), ("pid", wt.WORD), ("rev", wt.WORD),
                ("u1", wt.WORD), ("u2", wt.DWORD)]


def _load_xinput():
    for name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            return ctypes.WinDLL(name), name
        except OSError:
            continue
    return None, None


class _Poller(threading.Thread):
    """Polls every connected XInput slot and queues each changed state."""

    def __init__(self, dll, dll_name):
        super().__init__(name="xinput-poll", daemon=True)
        self._get = dll.XInputGetState
        self._get.argtypes = [wt.DWORD, ctypes.POINTER(_State)]
        self._get.restype = wt.DWORD
        # Ordinal 108 is the undocumented XInputGetCapabilitiesEx, the only XInput
        # call that reveals VID/PID. It exists on xinput1_4 only.
        self._caps_ex = None
        if dll_name == "xinput1_4.dll":
            try:
                self._caps_ex = dll[108]
            except (AttributeError, OSError):
                pass
        self.q: deque = deque()
        self.devq: deque = deque()
        self.connected = [False] * 4
        self.scan = True          # look for newly plugged pads (off during a run)
        self.running = True
        self.polls = 0
        # Controller Lab: (slot, rows) while recording. The loop then stops sleeping
        # and spins, so every report is stamped to a few microseconds instead of 0.5 ms.
        self.capture = None

    def _identify(self, slot: int) -> dict:
        info = {"backend": "xinput", "slot": slot, "id": None, "name": f"XInput pad {slot + 1}"}
        if self._caps_ex is not None:
            c = _CapsEx()
            try:
                if self._caps_ex(1, slot, 0, ctypes.byref(c)) == 0 and (c.vid or c.pid):
                    info["id"] = f"{c.vid:04X}:{c.pid:04X}"
                    info["revision"] = f"{c.rev:04X}"
            except OSError:
                pass
        return info

    def run(self):
        get, st = self._get, _State()
        ref, gp = ctypes.byref(st), st.gp
        q, conn = self.q, self.connected
        pc, sleep = time.perf_counter, time.sleep
        last_pkt = [None] * 4
        t_prev = pc()
        next_scan = 0.0
        cap_seen = None
        try:    # wake promptly after each sleep even when the render thread is busy
            k32 = ctypes.windll.kernel32
            k32.GetCurrentThread.restype = wt.HANDLE
            k32.SetThreadPriority.argtypes = [wt.HANDLE, ctypes.c_int]
            k32.SetThreadPriority(k32.GetCurrentThread(), 2)      # THREAD_PRIORITY_HIGHEST
        except (AttributeError, OSError):
            pass
        while self.running:
            t = pc()
            cap = self.capture
            if cap is not cap_seen:
                cap_seen = cap
                if cap is not None and 0 <= cap[0] < 4:
                    last_pkt[cap[0]] = None       # record the state the capture starts from
            if t >= next_scan and (self.scan or not any(conn)):
                next_scan = t + 0.5
                for i in range(4):
                    if not conn[i] and get(i, ref) == 0:
                        conn[i] = True
                        last_pkt[i] = None
                        self.devq.append((i, True, self._identify(i)))
                t = pc()
            # A change seen now happened somewhere since the previous poll; the
            # midpoint is the unbiased estimate of when.
            tm = (t_prev + t) * 0.5
            for i in range(4):
                if conn[i]:
                    if get(i, ref) != 0:
                        conn[i] = False
                        self.devq.append((i, False, None))
                        continue
                    p = st.pkt
                    if p != last_pkt[i]:
                        last_pkt[i] = p
                        row = (i, tm, p, gp.b, gp.lt, gp.rt, gp.lx, gp.ly, gp.rx, gp.ry)
                        q.append(row)
                        if cap is not None and cap[0] == i:
                            cap[1].append(row)
            t_prev = t
            self.polls += 1
            if cap is None:
                sleep(0.0002)   # the OS rounds this up to ~0.5 ms, i.e. ~2 kHz


def shape(x: float, y: float, deadzone: float, curve: str):
    """Radial deadzone + response curve. Returns the shaped (x, y), |v| <= 1.

    The curves are the usual families, not copies of any one game's:
    linear, squared ("standard"), and an S-curve ("dynamic").
    """
    m = math.hypot(x, y)
    if m <= deadzone or m == 0.0:
        return 0.0, 0.0
    u = min((m - deadzone) / (0.98 - deadzone), 1.0)
    if curve == "standard":
        f = u * u
    elif curve == "dynamic":
        f = u * u * (3.0 - 2.0 * u)
    else:
        f = u
    k = f / m
    return x * k, y * k


class Pad:
    """Main-thread view of the active controller. Call update() once per frame."""

    NAV_DELAY = 0.34
    NAV_REPEAT = 0.085

    def __init__(self):
        dll, name = _load_xinput()
        self.poller = _Poller(dll, name) if dll else None
        self.q = self.poller.q if self.poller else deque()
        if self.poller:
            self.poller.start()
        self.devices: dict[int, dict] = {}
        self.active: int | None = None
        self.locked = False        # during a run: never hop to another pad
        self.changed = False       # active device changed this frame
        self.lost = False          # active device vanished this frame
        self.trigger_thr = 0.12
        self.events: list = []     # (t, name, down) for this frame, active pad only
        self.samples: list = []    # (t, lx, ly, rx, ry) for this frame
        self.btn = 0
        self.lt = self.rt = 0.0
        self.lt_down = self.rt_down = False
        self.lx = self.ly = self.rx = self.ry = 0.0
        self.hz: float | None = None       # peak report rate over the last ~3 s
        self.hz_log: list | None = None
        self._hz_recent: deque = deque(maxlen=12)
        self._hz_ref = None
        self._last_sample_t = 0.0
        self._other_btn: dict[int, int] = {}
        self._nav_dir = None
        self._nav_next = 0.0
        self._poll_ref = (time.perf_counter(), 0)
        self.poll_hz: float | None = None
        self._fb_pkt = 0
        self._cap = None

    # ---- device bookkeeping -------------------------------------------------
    def _activate(self, slot):
        self.active = slot
        self.changed = True
        self.btn = 0
        self.lt = self.rt = 0.0
        self.lt_down = self.rt_down = False
        self.lx = self.ly = self.rx = self.ry = 0.0
        self.hz = None
        self._hz_ref = None
        self._hz_recent.clear()

    def info(self) -> dict | None:
        return self.devices.get(self.active) if self.active is not None else None

    def use_virtual(self, name="bot"):
        self.devices[VIRTUAL_SLOT] = {"backend": "bot", "slot": VIRTUAL_SLOT, "id": None, "name": name}
        self._activate(VIRTUAL_SLOT)
        self.locked = True

    def inject(self, t, btn, lt, rt, lx, ly, rx, ry):
        self._fb_pkt += 1
        row = (VIRTUAL_SLOT, t, self._fb_pkt, btn, lt, rt, lx, ly, rx, ry)
        self.q.append(row)
        if self._cap is not None and self._cap[0] == VIRTUAL_SLOT:
            self._cap[1].append(row)

    def capture_start(self) -> list:
        """Start a Controller Lab recording of the active pad and return the live list
        of rows (slot, t, packet, buttons, lt, rt, lx, ly, rx, ry - raw driver values)."""
        rows: list = []
        self._cap = (self.active, rows)
        if self.poller:
            self.poller.scan = False       # probing empty slots would put holes in the timing
            self.poller.capture = self._cap
        return rows

    def capture_stop(self):
        self._cap = None
        if self.poller:
            self.poller.capture = None
            self.poller.scan = True

    def set_scanning(self, on: bool):
        if self.poller:
            self.poller.scan = on

    def poll_fallback(self, glfw, now):
        """Read one non-XInput gamepad via GLFW. Only used when XInput sees nothing."""
        if self.poller and any(self.poller.connected):
            return
        if self.active == VIRTUAL_SLOT:
            return
        for jid in range(glfw.JOYSTICK_1, glfw.JOYSTICK_LAST + 1):
            try:
                if not (glfw.joystick_present(jid) and glfw.joystick_is_gamepad(jid)):
                    continue
                st = glfw.get_gamepad_state(jid)
            except Exception:
                continue
            if not st:
                continue
            if FALLBACK_SLOT not in self.devices:
                guid = glfw.get_joystick_guid(jid) or b""
                guid = guid.decode() if isinstance(guid, bytes) else guid
                name = glfw.get_gamepad_name(jid) or b"Gamepad"
                name = name.decode() if isinstance(name, bytes) else name
                dev_id = None
                # SDL-style GUID: bus type, then little-endian vendor and product ids.
                # (GLFW's own XInput devices use a made-up GUID with no ids in it.)
                if len(guid) >= 20 and guid[:8] in ("03000000", "05000000"):
                    dev_id = f"{(guid[10:12] + guid[8:10]).upper()}:{(guid[18:20] + guid[16:18]).upper()}"
                self.devices[FALLBACK_SLOT] = {"backend": "glfw", "slot": FALLBACK_SLOT,
                                               "id": dev_id, "name": name}
                if self.active is None:
                    self._activate(FALLBACK_SLOT)
            b, ax = st.buttons, st.axes
            order = ("A", "B", "X", "Y", "LB", "RB", "BACK", "START", None, "LS", "RS",
                     "DPAD_UP", "DPAD_RIGHT", "DPAD_DOWN", "DPAD_LEFT")
            bits = 0
            for i, nm in enumerate(order):
                if nm and b[i]:
                    bits |= BIT[nm]
            s16 = lambda v: int(max(-1.0, min(1.0, v)) * 32767)
            self._fb_pkt += 1
            self.q.append((FALLBACK_SLOT, now, self._fb_pkt, bits,
                           int((ax[4] + 1) * 127.5), int((ax[5] + 1) * 127.5),
                           s16(ax[0]), s16(-ax[1]), s16(ax[2]), s16(-ax[3])))
            return
        if FALLBACK_SLOT in self.devices:
            del self.devices[FALLBACK_SLOT]
            if self.active == FALLBACK_SLOT:
                self.active, self.lost = None, True

    # ---- per-frame ----------------------------------------------------------
    def update(self, now: float):
        self.events.clear()
        self.samples.clear()
        self.changed = self.lost = False
        p = self.poller
        if p:
            while p.devq:
                slot, on, info = p.devq.popleft()
                if on:
                    self.devices[slot] = info
                    if self.active is None:
                        self._activate(slot)
                else:
                    self.devices.pop(slot, None)
                    if slot == self.active:
                        self.active, self.lost = None, True
                        if not self.locked:
                            for other in self.devices:
                                self._activate(other)
                                break
            t0, n0 = self._poll_ref
            if now - t0 >= 1.0:
                self.poll_hz = (p.polls - n0) / (now - t0)
                self._poll_ref = (now, p.polls)

        q, act = self.q, self.active
        while q:
            s = q.popleft()
            slot = s[0]
            if slot != act:
                # A fresh button press on another pad makes it the active one, so
                # swapping controllers is just "pick it up and press something".
                prev = self._other_btn.get(slot, 0)
                self._other_btn[slot] = s[3]
                if not self.locked and slot in self.devices and (s[3] & ~prev):
                    self._activate(slot)
                    act = slot
                    self.btn = s[3]     # swallow the press that woke it
                continue
            self._apply(s)

        if self.hz is not None and now - self._last_sample_t > 3.0:
            self.hz = None
            self._hz_recent.clear()

    def _apply(self, s):
        _, t, pkt, b, lt, rt, lx, ly, rx, ry = s
        chg = b ^ self.btn
        if chg:
            ev = self.events
            for name, bit in BTN_BITS:
                if chg & bit:
                    ev.append((t, name, bool(b & bit)))
            self.btn = b
        thr = self.trigger_thr
        rel = max(thr - 0.04, thr * 0.5)   # hysteresis so a resting finger can't chatter
        self.lt = lt = lt / 255.0
        self.rt = rt = rt / 255.0
        if self.lt_down:
            if lt < rel:
                self.lt_down = False
                self.events.append((t, "LT", False))
        elif lt >= thr:
            self.lt_down = True
            self.events.append((t, "LT", True))
        if self.rt_down:
            if rt < rel:
                self.rt_down = False
                self.events.append((t, "RT", False))
        elif rt >= thr:
            self.rt_down = True
            self.events.append((t, "RT", True))
        self.lx = max(-1.0, lx / 32767.0)
        self.ly = max(-1.0, ly / 32767.0)
        self.rx = max(-1.0, rx / 32767.0)
        self.ry = max(-1.0, ry / 32767.0)
        self.samples.append((t, self.lx, self.ly, self.rx, self.ry))
        self._last_sample_t = t

        ref = self._hz_ref
        if ref is None:
            self._hz_ref = (t, pkt)
        elif t - ref[0] >= 0.25:
            if self.active not in (VIRTUAL_SLOT, FALLBACK_SLOT):
                hz = ((pkt - ref[1]) & 0xFFFFFFFF) / (t - ref[0])
                if self.hz_log is not None:
                    self.hz_log.append(hz)
                # The packet counter only moves when the state changes, so a pad lying
                # still under-reads. The recent peak is the honest "what it can do" figure.
                self._hz_recent.append(hz)
                self.hz = max(self._hz_recent)
            self._hz_ref = (t, pkt)

    # ---- queries ------------------------------------------------------------
    def down(self, name: str) -> bool:
        if name == "RT":
            return self.rt_down
        if name == "LT":
            return self.lt_down
        return bool(self.btn & BIT.get(name, 0))

    def stick(self, which: str):
        return (self.lx, self.ly) if which == "left" else (self.rx, self.ry)

    def first_press(self):
        """Name of the first bindable button pressed this frame, if any."""
        for _, name, down in self.events:
            if down and name in BINDABLE:
                return name
        return None

    def nav(self, now: float) -> set:
        """Menu actions for this frame: edges for buttons, auto-repeat for directions."""
        out = set()
        for _, name, down in self.events:
            if not down:
                continue
            if name == "A":
                out.add("accept")
            elif name == "B":
                out.add("back")
            elif name in ("X", "Y", "LB", "RB", "START"):
                out.add(name.lower())
        b = self.btn
        d = None
        if b & 0x0001 or self.ly > 0.6:
            d = "up"
        elif b & 0x0002 or self.ly < -0.6:
            d = "down"
        elif b & 0x0004 or self.lx < -0.6:
            d = "left"
        elif b & 0x0008 or self.lx > 0.6:
            d = "right"
        if d != self._nav_dir:
            self._nav_dir = d
            if d:
                out.add(d)
                self._nav_next = now + self.NAV_DELAY
        elif d and now >= self._nav_next:
            out.add(d)
            self._nav_next = now + self.NAV_REPEAT
        return out

    def stop(self):
        if self.poller:
            self.poller.running = False
