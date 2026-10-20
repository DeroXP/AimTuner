"""The monitor's exact refresh rate, and the frame cap that sits one frame under it.

GLFW (and most of Windows) reports a whole number - "240 Hz" - for a mode that really runs at
239.99 or 239.76. Windows' display configuration has the exact timing as a fraction; this reads it
for the main monitor (the one at the desktop's top-left corner, where fullscreen opens)."""
from __future__ import annotations

import ctypes
import math
import sys


def exact_refresh() -> float | None:
    """Refresh rate of the main monitor in Hz, to the thousandth, or None where it cannot be read."""
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes as wt

        class LUID(ctypes.Structure):
            _fields_ = [("lo", wt.DWORD), ("hi", wt.LONG)]

        class RATIONAL(ctypes.Structure):
            _fields_ = [("n", ctypes.c_uint32), ("d", ctypes.c_uint32)]

        class SOURCE(ctypes.Structure):
            _fields_ = [("adapter", LUID), ("id", ctypes.c_uint32), ("mode", ctypes.c_uint32), ("flags", ctypes.c_uint32)]

        class TARGET(ctypes.Structure):
            _fields_ = [("adapter", LUID), ("id", ctypes.c_uint32), ("mode", ctypes.c_uint32), ("tech", ctypes.c_uint32),
                        ("rotation", ctypes.c_uint32), ("scaling", ctypes.c_uint32), ("refresh", RATIONAL),
                        ("order", ctypes.c_uint32), ("available", wt.BOOL), ("flags", ctypes.c_uint32)]

        class PATH(ctypes.Structure):
            _fields_ = [("src", SOURCE), ("tgt", TARGET), ("flags", ctypes.c_uint32)]

        class SOURCE_MODE(ctypes.Structure):          # DISPLAYCONFIG_SOURCE_MODE
            _fields_ = [("w", ctypes.c_uint32), ("h", ctypes.c_uint32), ("fmt", ctypes.c_uint32),
                        ("x", ctypes.c_long), ("y", ctypes.c_long)]

        class MODE(ctypes.Structure):
            _fields_ = [("type", ctypes.c_uint32), ("id", ctypes.c_uint32), ("adapter", LUID),
                        ("u", ctypes.c_byte * 48)]

        u32 = ctypes.WinDLL("user32")
        ACTIVE = 2                                     # QDC_ONLY_ACTIVE_PATHS
        n_p, n_m = ctypes.c_uint32(), ctypes.c_uint32()
        if u32.GetDisplayConfigBufferSizes(ACTIVE, ctypes.byref(n_p), ctypes.byref(n_m)):
            return None
        paths, modes = (PATH * n_p.value)(), (MODE * n_m.value)()
        if u32.QueryDisplayConfig(ACTIVE, ctypes.byref(n_p), paths, ctypes.byref(n_m), modes, None):
            return None
        best = None
        for p in paths[:n_p.value]:
            r = p.tgt.refresh
            if not r.d or not r.n:
                continue
            hz = r.n / r.d
            if best is None:
                best = hz
            i = p.src.mode
            if i < n_m.value and modes[i].type == 1:   # DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE
                sm = SOURCE_MODE.from_buffer(modes[i].u)
                if sm.x == 0 and sm.y == 0:            # the main monitor
                    return hz
        return best
    except Exception:
        return None


def cap_under(hz_exact: float | None, hz_whole: int) -> int:
    """One frame under the refresh rate: 239.99 Hz -> 238, 180.00 -> 179, 359.97 -> 358.
    Kept under the real rate so the frame cap never outruns the display (no queued frames with
    V-Sync or G-Sync). With only the whole number to go on, the mode is assumed to be one of the
    usual 'x.9x' timings, as most high-refresh modes are."""
    if hz_exact and abs(hz_exact - hz_whole) < 1.0:
        return max(int(math.floor(hz_exact + 1e-6)) - 1, 30)
    return max(int(hz_whole) - 2, 30)
