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


def driver_cap(exe: str | None = None) -> int | None:
    """The NVIDIA driver's own "Max Frame Rate" for this program - its own profile in the NVIDIA
    Control Panel if it has one (exe: e.g. "AimTuner.exe"), else the global one - or None if
    there is none or this is not an NVIDIA card. Read only - nothing in the driver is changed.

    When the driver caps too, its limiter and ours pace the same frames. The driver's waits
    inside the buffer swap - after AimTuner has read the input and drawn the frame - so every
    frame shows input one wait older (about 4 ms at 240 Hz). Pacing a frame under the driver's
    cap keeps the waiting in AimTuner's limiter, before the input is read."""
    if sys.platform != "win32":
        return None
    try:
        nv = ctypes.WinDLL("nvapi64.dll")
    except OSError:
        return None
    try:
        q = nv.nvapi_QueryInterface
        q.restype, q.argtypes = ctypes.c_void_p, [ctypes.c_uint32]
        P = ctypes.c_void_p

        def fn(iid, *args):
            addr = q(iid)
            if not addr:
                raise OSError(f"nvapi {iid:#x} missing")
            return ctypes.CFUNCTYPE(ctypes.c_int, *args)(addr)

        class SETTING(ctypes.Structure):               # NVDRS_SETTING (version 1)
            _fields_ = [("version", ctypes.c_uint32), ("name", ctypes.c_wchar * 2048), ("id", ctypes.c_uint32),
                        ("type", ctypes.c_uint32), ("location", ctypes.c_uint32), ("is_cur_pre", ctypes.c_uint32),
                        ("is_pre_valid", ctypes.c_uint32), ("pre", ctypes.c_ubyte * 4100),
                        ("cur", ctypes.c_ubyte * 4100)]

        if fn(0x0150E828)():                           # NvAPI_Initialize
            return None
        sess = P()
        if fn(0x0694D52E, ctypes.POINTER(P))(ctypes.byref(sess)):       # NvAPI_DRS_CreateSession
            return None
        try:
            if fn(0x375DBD6B, P)(sess):                # NvAPI_DRS_LoadSettings
                return None
            prof = P()
            if fn(0x617BFF9F, P, ctypes.POINTER(P))(sess, ctypes.byref(prof)):   # GetCurrentGlobalProfile
                return None
            if exe:
                class APP(ctypes.Structure):           # NVDRS_APPLICATION (version 4)
                    _fields_ = [("version", ctypes.c_uint32), ("predefined", ctypes.c_uint32),
                                ("name", ctypes.c_wchar * 2048), ("friendly", ctypes.c_wchar * 2048),
                                ("launcher", ctypes.c_wchar * 2048), ("file_in_folder", ctypes.c_wchar * 2048),
                                ("flags", ctypes.c_uint32), ("cmdline", ctypes.c_wchar * 2048)]

                app, own = APP(), P()
                app.version = ctypes.sizeof(APP) | (4 << 16)
                find = fn(0xEEE566B2, P, ctypes.c_wchar_p, ctypes.POINTER(P), ctypes.POINTER(APP))
                if find(sess, exe, ctypes.byref(own), ctypes.byref(app)) == 0 and own.value:
                    prof = own                         # its own profile (settings it does not set come from global)
            st = SETTING()
            st.version = ctypes.sizeof(SETTING) | (1 << 16)
            get = fn(0x73BF8338, P, P, ctypes.c_uint32, ctypes.POINTER(SETTING))   # NvAPI_DRS_GetSetting
            r = get(sess, prof, 0x10835002, ctypes.byref(st))           # FRL_FPS: "Max Frame Rate"
            if r == -160 and prof is not None:         # NVAPI_SETTING_NOT_FOUND in its own profile
                gp = P()
                fn(0x617BFF9F, P, ctypes.POINTER(P))(sess, ctypes.byref(gp))
                r = get(sess, gp, 0x10835002, ctypes.byref(st))
            if r:
                return None
            fps = int.from_bytes(bytes(st.cur[:4]), "little")
            return fps if 20 <= fps <= 1000 else None  # 0 = off
        finally:
            fn(0xDAD9CFF8, P)(sess)                    # NvAPI_DRS_DestroySession
    except Exception:
        return None
