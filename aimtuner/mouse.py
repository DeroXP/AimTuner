"""Mouse and keyboard input, read the way a shooter reads it.

What "as little lag as possible" means here, step by step:

  * Raw Input, not the pointer. The mouse's own counts come straight from the
    driver (WM_INPUT / RAWMOUSE): no pointer acceleration, no "enhance pointer
    precision", no Windows sensitivity slider, no clamping at the screen edge.
    One count is one count, whatever the desktop is set to.
  * Its own thread, woken by the device. A message-only window on a dedicated
    thread owns the registration, and the thread sleeps in
    MsgWaitForMultipleObjects until the mouse or keyboard actually reports. It is
    not polled: an 8 kHz mouse wakes it 8,000 times a second, a still one never.
  * Buffered reads. Each wake drains everything pending with GetRawInputBuffer in
    one call instead of pumping one window message per report - that per-message
    path is what makes high-polling-rate mice stutter in some games.
  * Every report is stamped with QueryPerformanceCounter as it is read, so a
    click is timed to a fraction of a millisecond rather than to a 4 ms frame,
    and the game can replay motion and clicks in the order they happened (a shot
    is judged where the crosshair was at the click, not at the end of the frame).
  * The render thread takes whatever has arrived at the last possible moment:
    after the frame-pacing wait, right before it simulates and draws.
  * Nothing is filtered. No smoothing, no interpolation, no acceleration, and -
    in the game code - no aim assist of any kind for this input, ever.

Keyboard reports come down the same pipe so a key bound to Shoot is timed like a
click. Reports that arrive while Core is not the focused window are thrown
away unread; nothing typed elsewhere is kept or logged.

The self-test bot drives the same queue through inject(), so everything
downstream of the Windows calls is exercised without touching the real pointer.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct
import threading
import time
from collections import deque

MOTION, BUTTON, WHEEL = 0, 1, 2       # kinds of row in the queue: (t, kind, a, b)

MOUSE_BUTTONS = ("M1", "M2", "M3", "M4", "M5")
# usButtonFlags bit -> (button, pressed)
_BTN_FLAGS = ((0x0001, "M1", True), (0x0002, "M1", False), (0x0004, "M2", True), (0x0008, "M2", False),
              (0x0010, "M3", True), (0x0020, "M3", False), (0x0040, "M4", True), (0x0080, "M4", False),
              (0x0100, "M5", True), (0x0200, "M5", False))
_RI_WHEEL = 0x0400

# virtual-key code -> the name a bind is stored under
VK = {0x20: "SPACE", 0x10: "SHIFT", 0x11: "CTRL", 0x12: "ALT", 0x09: "TAB", 0x14: "CAPS",
      0x25: "LEFT", 0x26: "UP", 0x27: "RIGHT", 0x28: "DOWN", 0x0D: "ENTER", 0x1B: "ESC"}
VK.update({c: chr(c) for c in range(0x41, 0x5B)})       # A-Z
VK.update({c: chr(c) for c in range(0x30, 0x3A)})       # 0-9
NAME_VK = {v: k for k, v in VK.items()}
NAME_VK.update({"M1": 0x01, "M2": 0x02, "M3": 0x04, "M4": 0x05, "M5": 0x06})
RESERVED = ("ESC", "ENTER")           # pause and confirm: never bindable
LABELS = {"M1": "Left click", "M2": "Right click", "M3": "Middle click", "M4": "Mouse 4", "M5": "Mouse 5",
          "SPACE": "Space", "SHIFT": "Shift", "CTRL": "Ctrl", "ALT": "Alt", "TAB": "Tab", "CAPS": "Caps Lock",
          "LEFT": "Left arrow", "UP": "Up arrow", "RIGHT": "Right arrow", "DOWN": "Down arrow"}
ACTIONS = (("shoot", "Shoot"), ("ads", "Aim  (ADS)"), ("forward", "Move forward"), ("back", "Move back"),
           ("left", "Strafe left"), ("right", "Strafe right"),
           ("lean_l", "Lean left  (Siege)"), ("lean_r", "Lean right  (Siege)"), ("crouch", "Crouch  (Siege)"),
           ("reload", "Reload  (Siege)"))
DEFAULT_BINDS = {"shoot": "M1", "ads": "M2", "forward": "W", "back": "S", "left": "A", "right": "D",
                 "lean_l": "Q", "lean_r": "E", "crouch": "C", "reload": "R"}


def label(name: str) -> str:
    return LABELS.get(name, name)


def new_provenance() -> dict:
    """Running counts of where input came from (see parse)."""
    return {"inj_motion": 0, "inj_button": 0, "inj_key": 0, "inj_keys": {}, "absolute": 0, "motion_devs": {}}


def parse(buf, count: int, t: float, out, held: set, state: dict, count_prov: bool = True) -> int:
    """Turn `count` RAWINPUT records at the start of `buf` into queue rows appended to `out`.

    Returns how many of them were mouse-motion reports. `held` is the set of keys already
    down (a held key repeats its "down" forever; only the first is an event) and `state`
    carries the last absolute position for the rare device that reports positions rather
    than movement (tablets, remote desktop).

    Layout on 64-bit Windows: a 24-byte RAWINPUTHEADER (type, size, device, wParam), then
    RAWMOUSE (flags, [pad], button flags, button data, raw buttons, dx, dy, extra) or
    RAWKEYBOARD (make code, flags, reserved, virtual key, message, extra). Records in a
    buffered read are padded to 8 bytes.
    """
    unpack = struct.unpack_from
    off = moved = 0
    prov = state.get("prov")
    if prov is None:
        prov = state["prov"] = new_provenance()
    if not count_prov:                      # not ours to read (another window has focus): not counted
        prov = new_provenance()
    for _ in range(count):
        typ, size = unpack("<II", buf, off)
        # Which device sent it. Input made up by software (SendInput, mouse_event, keybd_event - what
        # macro tools and most cheats use) arrives with no device at all: handle 0. Counted for the
        # anti-cheat (ac/): an injected movement or click during a run is never a hand's.
        hdev = unpack("<Q", buf, off + 8)[0]
        if typ == 0:
            flags, bflags, bdata, _raw, dx, dy = unpack("<HxxHHIii", buf, off + 24)
            if (dx or dy) and not (flags & 0x01):
                if hdev == 0:
                    prov["inj_motion"] += 1
                else:
                    md = prov["motion_devs"]
                    md[hdev] = md.get(hdev, 0) + 1
            elif flags & 0x01:
                prov["absolute"] += 1
            if bflags & 0x03FF and hdev == 0:
                prov["inj_button"] += 1
            if flags & 0x01:                    # absolute coordinates: difference them
                last = state.get("abs")
                state["abs"] = (dx, dy)
                dx, dy = (dx - last[0], dy - last[1]) if last else (0, 0)
                dx, dy = round(dx * state.get("w", 1920) / 65535.0), round(dy * state.get("h", 1080) / 65535.0)
            if dx or dy:
                out.append((t, MOTION, dx, dy))
                moved += 1
                state["dev"] = hdev                 # which mouse moved (dpi.py asks it for its DPI)
            if bflags:
                for bit, name, down in _BTN_FLAGS:
                    if bflags & bit:
                        out.append((t, BUTTON, name, down))
                if bflags & _RI_WHEEL:
                    out.append((t, WHEEL, (bdata - 0x10000 if bdata & 0x8000 else bdata) / 120.0, 0))
        elif typ == 1:
            _make, flags, _res, vkey, _msg = unpack("<HHHHI", buf, off + 24)
            name = VK.get(vkey)
            if hdev == 0 and not (flags & 0x01) and name:
                prov["inj_key"] += 1
                ik = prov["inj_keys"]
                ik[name] = ik.get(name, 0) + 1
            if name:
                if flags & 0x01:                # break = released
                    if name in held:
                        held.discard(name)
                        out.append((t, BUTTON, name, False))
                elif name not in held:
                    held.add(name)
                    out.append((t, BUTTON, name, True))
        off = (off + size + 7) & ~7
    return moved


class _RawThread(threading.Thread):
    """Owns the Raw Input registration and feeds the queue. Windows only."""

    BUF = 1 << 16

    def __init__(self, q: deque):
        super().__init__(name="raw-input", daemon=True)
        self.q = q
        self.running = True
        self.listening = False      # the app sets this to "our window has focus"
        self.ok = None              # None until the thread has tried to register; then True / False
        self.error = ""
        self.packets = 0            # mouse-motion reports seen, for the report-rate readout
        self.wakes = 0
        self.state: dict = {}       # parse()'s scratch; "dev" is the handle of the mouse that last moved

    def run(self):
        try:
            self._loop()
        except Exception as e:      # never take the app down with it; the mouse profile just says so
            self.ok, self.error = False, f"{type(e).__name__}: {e}"

    def _loop(self):
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            self.ok, self.error = False, "mouse input needs 64-bit Python"
            return
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u32.CreateWindowExW.restype = wt.HWND
        u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        u32.MsgWaitForMultipleObjects.restype = wt.DWORD
        u32.MsgWaitForMultipleObjects.argtypes = [wt.DWORD, wt.LPVOID, wt.BOOL, wt.DWORD, wt.DWORD]
        u32.GetRawInputBuffer.restype = wt.UINT
        u32.GetRawInputBuffer.argtypes = [wt.LPVOID, ctypes.POINTER(wt.UINT), wt.UINT]
        u32.GetQueueStatus.restype = wt.DWORD
        u32.GetQueueStatus.argtypes = [wt.UINT]
        u32.PeekMessageW.argtypes = [wt.LPVOID, wt.HWND, wt.UINT, wt.UINT, wt.UINT]
        u32.DestroyWindow.argtypes = [wt.HWND]

        class RID(ctypes.Structure):
            _fields_ = [("page", wt.USHORT), ("usage", wt.USHORT), ("flags", wt.DWORD), ("target", wt.HWND)]

        u32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RID), wt.UINT, wt.UINT]
        u32.RegisterRawInputDevices.restype = wt.BOOL

        # A message-only window of a stock class: it exists only so reports have somewhere to
        # go, and it needs no window procedure of ours (so no Python callback per message).
        hwnd = u32.CreateWindowExW(0, "STATIC", "Core raw input", 0, 0, 0, 0, 0, wt.HWND(-3), None, None, None)
        if not hwnd:
            self.ok, self.error = False, f"could not create the input window (error {ctypes.get_last_error()})"
            return
        # INPUTSINK: deliver to this window although it is never the foreground one. Generic
        # desktop page (1), mouse (2) and keyboard (6).
        devs = (RID * 2)(RID(1, 2, 0x100, hwnd), RID(1, 6, 0x100, hwnd))
        if not u32.RegisterRawInputDevices(devs, 2, ctypes.sizeof(RID)):
            self.ok, self.error = False, f"Raw Input registration was refused (error {ctypes.get_last_error()})"
            u32.DestroyWindow(hwnd)
            return
        try:        # be scheduled the moment a report arrives, even while the render thread is busy
            k32.GetCurrentThread.restype = wt.HANDLE
            k32.SetThreadPriority.argtypes = [wt.HANDLE, ctypes.c_int]
            k32.SetThreadPriority(k32.GetCurrentThread(), 2)        # THREAD_PRIORITY_HIGHEST
        except (AttributeError, OSError):
            pass
        state = self.state = {"w": u32.GetSystemMetrics(0), "h": u32.GetSystemMetrics(1)}
        buf = ctypes.create_string_buffer(self.BUF)
        size = wt.UINT(0)
        msg = ctypes.create_string_buffer(64)
        held: set = set()
        scratch: list = []
        wait, drain, status = u32.MsgWaitForMultipleObjects, u32.GetRawInputBuffer, u32.GetQueueStatus
        pc, q = time.perf_counter, self.q
        self.ok = True
        while self.running:
            r = wait(0, None, False, 8, 0x0400)             # QS_RAWINPUT
            if r == 0xFFFFFFFF:
                self.ok, self.error = False, f"waiting for input failed (error {ctypes.get_last_error()})"
                break
            if r == 258:
                # Timed out: nothing was announced. Look anyway (a report that slipped in unannounced
                # is then at most 8 ms late instead of waiting for the next one), check `running`, and
                # throw away any other message that strays in - never WM_INPUT (0xFF), that is the data.
                while u32.PeekMessageW(msg, None, 0x0100, 0xFFFF, 1) or u32.PeekMessageW(msg, None, 0, 0x00FE, 1):
                    pass
            else:
                status(0x0400)      # mark the input as seen, or the next wait would return at once
                self.wakes += 1
            while True:
                size.value = self.BUF
                n = drain(buf, ctypes.byref(size), 24)
                if n == 0 or n == 0xFFFFFFFF:
                    break
                t = pc()
                if self.listening:
                    self.packets += parse(buf, n, t, q, held, state)
                else:               # not ours to read: keep only which keys are down, so repeats stay filtered
                    scratch.clear()
                    parse(buf, n, t, scratch, held, state, False)
        devs = (RID * 2)(RID(1, 2, 0x1, None), RID(1, 6, 0x1, None))        # RIDEV_REMOVE
        u32.RegisterRawInputDevices(devs, 2, ctypes.sizeof(RID))
        u32.DestroyWindow(hwnd)


class Mouse:
    """Main-thread view of the mouse and keyboard. Call update() once per frame, as late as possible."""

    def __init__(self, start: bool = True):
        self.q: deque = deque()
        self.thread = None
        self.virtual = not start
        if start:
            try:
                self.thread = _RawThread(self.q)
                self.thread.start()
            except Exception:           # not Windows, or no user32: the profile will say it is unavailable
                self.thread = None
        self.events: list = []          # this frame's rows, oldest first: (t, kind, a, b)
        self.dx = self.dy = 0           # this frame's summed counts
        self.wheel = 0.0
        self.held: set = set()          # buttons and keys down now
        self.hz: float | None = None    # peak report rate over the last few seconds of movement
        self.age_ms: float | None = None    # how old the newest report was when this frame took it
        # Diagnostic for a run's log: motion reports seen, and how many of them were read together
        # with another one (same timestamp). A few is normal at 4-8 kHz; a third or more at 1 kHz
        # would mean Windows is handing reports over late and in pairs.
        self.reports = self.paired = 0
        self._hz_recent: deque = deque(maxlen=12)
        self._hz_ref = None
        self._moved_t = 0.0
        self._packets = 0

    # ---- state --------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.virtual or bool(self.thread and self.thread.ok is not False)

    @property
    def error(self) -> str:
        return self.thread.error if self.thread else ""

    def use_virtual(self):
        """Self-test: stop listening to the real devices; rows come from inject() only."""
        self.virtual = True
        if self.thread:
            self.thread.listening = False
        self.q.clear()
        self.held.clear()

    def inject(self, t: float, kind: int, a, b=0):
        self.q.append((t, kind, a, b))
        if kind == MOTION:
            self._packets += 1

    def resync(self, names=()):
        """Forget what was down and re-read these binds from the hardware. Used when a run starts
        or resumes: a key held across the countdown, or released while the window was not focused,
        would otherwise be remembered wrongly."""
        self.held.clear()
        if self.virtual:
            return
        try:
            get = ctypes.windll.user32.GetAsyncKeyState
            for name in names:
                vk = NAME_VK.get(name)
                if vk and get(vk) & 0x8000:
                    self.held.add(name)
        except (AttributeError, OSError):
            pass

    def provenance(self) -> dict:
        """Where this program's input has come from so far (counts since start; see parse). A run
        takes one copy at its start and one at its end and keeps the difference."""
        st = self.thread.state if self.thread else {}
        p = st.get("prov") or new_provenance()
        return {"inj_motion": p["inj_motion"], "inj_button": p["inj_button"], "inj_key": p["inj_key"],
                "inj_keys": dict(p["inj_keys"]), "absolute": p["absolute"], "motion_devs": dict(p["motion_devs"])}

    def device_name(self, hdev: int) -> str | None:
        """Windows' device path for a raw-input device handle (vendor and product ids are in it)."""
        try:
            u32 = ctypes.WinDLL("user32", use_last_error=True)
            u32.GetRawInputDeviceInfoW.argtypes = [wt.HANDLE, wt.UINT, wt.LPVOID, ctypes.POINTER(wt.UINT)]
            u32.GetRawInputDeviceInfoW.restype = wt.UINT
            size = wt.UINT(512)
            buf = ctypes.create_unicode_buffer(512)
            n = u32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), 0x20000007, buf, ctypes.byref(size))
            return buf.value if 0 < n < 0xFFFFFFFF else None
        except (AttributeError, OSError):
            return None

    def device_path(self) -> str | None:
        """The Windows device path of the mouse that last moved (it carries the USB vendor and
        product ids), or None if nothing has moved yet."""
        dev = self.thread.state.get("dev") if self.thread else None
        if not dev:
            return None
        try:
            u32 = ctypes.WinDLL("user32", use_last_error=True)
            u32.GetRawInputDeviceInfoW.argtypes = [wt.HANDLE, wt.UINT, wt.LPVOID, ctypes.POINTER(wt.UINT)]
            u32.GetRawInputDeviceInfoW.restype = wt.UINT
            size = wt.UINT(512)
            buf = ctypes.create_unicode_buffer(512)
            n = u32.GetRawInputDeviceInfoW(wt.HANDLE(dev), 0x20000007, buf, ctypes.byref(size))     # RIDI_DEVICENAME
            return buf.value if 0 < n < 0xFFFFFFFF else None
        except (AttributeError, OSError):
            return None

    def down(self, name: str) -> bool:
        return name in self.held

    def first_press(self):
        """The first key or mouse button pressed this frame that may be bound, if any."""
        for _, kind, a, b in self.events:
            if kind == BUTTON and b and a not in RESERVED:
                return a
        return None

    # ---- per frame ----------------------------------------------------------
    def update(self, now: float, listening: bool = True):
        th = self.thread
        if th and not self.virtual:
            th.listening = listening
        q = self.q
        ev = self.events = []
        dx = dy = 0
        wheel = 0.0
        last = None
        while q:
            row = q.popleft()
            if not listening and not self.virtual:
                continue
            ev.append(row)
            kind = row[1]
            if kind == MOTION:
                dx += row[2]
                dy += row[3]
                self.reports += 1
                if row[0] == last:
                    self.paired += 1
                last = row[0]
            elif kind == BUTTON:
                if row[3]:
                    self.held.add(row[2])
                else:
                    self.held.discard(row[2])
            else:
                wheel += row[2]
        self.dx, self.dy, self.wheel = dx, dy, wheel
        if not listening and not self.virtual:
            self.held.clear()
        if last is not None:
            self.age_ms = max(now - last, 0.0) * 1000.0
            self._moved_t = now
        # report rate: reports per second over quarter-second windows, while it is moving
        packets = (th.packets if th and not self.virtual else self._packets)
        ref = self._hz_ref
        if ref is None:
            self._hz_ref = (now, packets)
        elif now - ref[0] >= 0.25:
            n = packets - ref[1]
            if n > 8 and not self.virtual:      # a still mouse reports nothing; that is not "0 Hz"
                self._hz_recent.append(n / (now - ref[0]))
                self.hz = max(self._hz_recent)
            self._hz_ref = (now, packets)
        if self.hz is not None and now - self._moved_t > 3.0:
            self.hz = None
            self._hz_recent.clear()

    def stop(self):
        if self.thread:
            self.thread.running = False
