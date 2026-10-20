"""Mouse DPI: read from the mouse where the mouse can say, measured with a ruler where it cannot.

Windows has no way to ask a mouse for its DPI - a mouse reports movement in counts and
never says how many of them make an inch. So there is no universal "detect DPI", in this
program or any other. What there is:

  * Mice whose maker's own protocol can be asked. Razer's is documented by the OpenRazer
    project, and this module speaks just enough of it to ask one question - "what is your
    DPI?" (command class 0x04, id 0x85, a GET). It never sends a command that changes
    anything on the mouse. Checked here against a DeathAdder V2; other Razer mice use the
    same question, and the answer is validated before it is believed.
  * Every other mouse: `measure()` turns a slide of known length into a DPI, which is how
    the web's "DPI analyzers" do it. The DPI screen (dpiscreen.py) walks you through it.

Nothing here runs during a scenario: a query is a USB exchange, and a run should have the
mouse to itself.
"""
from __future__ import annotations

import ctypes
import re
import struct
import threading
import time
from ctypes import wintypes as wt

RAZER = 0x1532
_TRANSACTIONS = (0x3F, 0x1F, 0xFF)      # which the mouse answers to depends on its generation
STEPS = (400, 450, 500, 600, 800, 900, 1000, 1200, 1600, 1800, 2000, 2400, 3200, 4000, 4800, 6400, 8000, 12000, 16000)


def vid_pid(path: str | None):
    """(vendor id, product id) out of a Windows HID device path, or None."""
    m = re.search(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})", (path or "").lower())
    return (int(m.group(1), 16), int(m.group(2), 16)) if m else None


def razer_request(transaction: int) -> bytes:
    """The 90-byte Razer report that asks for the DPI. A GET: it sets nothing."""
    rep = bytearray(90)
    rep[1] = transaction
    rep[5] = 0x07               # size of the answer's payload
    rep[6] = 0x04               # command class: device settings
    rep[7] = 0x85               # command: get DPI (x, y)
    crc = 0
    for b in rep[2:88]:
        crc ^= b
    rep[88] = crc
    return bytes(rep)


def razer_parse(reply: bytes):
    """(dpi_x, dpi_y) from a reply to razer_request, or None if it is not a good answer to it.

    Other software talks to the mouse too (Razer Synapse does, constantly), and its exchanges
    can land between our question and our read - so a reply only counts if it is marked as
    successful, is an answer to this very command, and carries a believable number."""
    if len(reply) < 90 or reply[0] != 0x02 or reply[6] != 0x04 or reply[7] != 0x85:
        return None
    x, y = (reply[9] << 8) | reply[10], (reply[11] << 8) | reply[12]
    return (x, y) if 50 <= x <= 60000 and 50 <= y <= 60000 else None


class _GUID(ctypes.Structure):
    _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]


class _IFACE(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("guid", _GUID), ("flags", wt.DWORD), ("reserved", ctypes.c_void_p)]


class _ATTR(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT), ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]


class _CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT), ("InputLen", wt.USHORT), ("OutputLen", wt.USHORT),
                ("FeatureLen", wt.USHORT), ("Reserved", wt.USHORT * 17), ("rest", wt.USHORT * 10)]


def _libs():
    s = ctypes.WinDLL("setupapi", use_last_error=True)
    h = ctypes.WinDLL("hid", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    s.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    s.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
    s.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_GUID), wt.DWORD,
                                              ctypes.POINTER(_IFACE)]
    s.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_IFACE), ctypes.c_void_p, wt.DWORD,
                                                   ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    s.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    k.CreateFileW.restype = ctypes.c_void_p
    k.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    h.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ATTR)]
    h.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    h.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    h.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_CAPS)]
    h.HidD_SetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.ULONG]
    h.HidD_GetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.ULONG]
    h.HidD_GetProductString.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.ULONG]
    return s, h, k


def _hid_paths(s, h):
    g = _GUID()
    h.HidD_GetHidGuid(ctypes.byref(g))
    info = s.SetupDiGetClassDevsW(ctypes.byref(g), None, None, 0x12)        # present devices, by interface
    if not info or info == ctypes.c_void_p(-1).value:
        return
    try:
        i = 0
        while True:
            d = _IFACE()
            d.cbSize = ctypes.sizeof(d)
            if not s.SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(g), i, ctypes.byref(d)):
                break
            i += 1
            need = wt.DWORD()
            s.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(d), None, 0, ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(max(need.value, 8))
            struct.pack_into("<I", buf, 0, 8)       # cbSize of the detail structure on 64-bit Windows
            if s.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(d), buf, need, None, None):
                yield ctypes.wstring_at(ctypes.addressof(buf) + 4)
    finally:
        s.SetupDiDestroyDeviceInfoList(info)


def _ask_razer(h, handle, size: int):
    """One mouse, a few tries. -> (dpi_x, dpi_y) or None."""
    for transaction in _TRANSACTIONS:
        for _ in range(3):
            reply = bytes(90)
            out = (ctypes.c_ubyte * size)(*(b"\x00" + razer_request(transaction)).ljust(size, b"\x00"))
            if not h.HidD_SetFeature(handle, out, size):
                break
            for wait in (0.004, 0.012, 0.03):       # the mouse needs a moment; it says "busy" (1) until it has
                time.sleep(wait)
                back = (ctypes.c_ubyte * size)()
                if not h.HidD_GetFeature(handle, back, size):
                    break
                reply = bytes(back)[1:91]
                got = razer_parse(reply)
                if got:
                    return got
                if reply[0] != 0x01:                # not "busy": refused, or someone else's exchange - ask again
                    break
            if reply[0] == 0x05 and reply[6] == 0x04 and reply[7] == 0x85:     # "not supported" under this id
                break
    return None


def read(prefer: str | None = None) -> dict:
    """Ask the mouse for its DPI. -> {"dpi": int, "dpi_y": int, "name": str, "how": "razer"} when it
    answered, else {"dpi": None, "name": str | None, "why": str}.

    prefer: the device path of the mouse in use (mouse.Mouse.device_path), so that with two
    mice plugged in the one being moved is the one asked."""
    want = vid_pid(prefer)
    seen = None
    try:
        s, h, k = _libs()
    except (AttributeError, OSError) as e:
        return {"dpi": None, "name": None, "why": f"this needs Windows ({type(e).__name__})"}
    found = []
    for path in _hid_paths(s, h):
        ids = vid_pid(path)
        if not ids or (want and ids != want):
            continue
        handle = k.CreateFileW(path, 0, 3, None, 3, 0, None)        # no access rights: all a feature report needs,
        if not handle or handle == ctypes.c_void_p(-1).value:       # and all Windows grants on a mouse it owns
            continue
        try:
            caps, pp = _CAPS(), ctypes.c_void_p()
            if h.HidD_GetPreparsedData(handle, ctypes.byref(pp)):
                h.HidP_GetCaps(pp, ctypes.byref(caps))
                h.HidD_FreePreparsedData(pp)
            if (caps.UsagePage, caps.Usage) != (1, 2):              # the mouse collection itself, nothing else
                continue
            name = ctypes.create_unicode_buffer(128)
            h.HidD_GetProductString(handle, name, 256)
            seen = name.value or seen
            if ids[0] == RAZER and caps.FeatureLen == 91:
                got = _ask_razer(h, handle, 91)
                if got:
                    found.append({"dpi": got[0], "dpi_y": got[1], "name": name.value or "Razer mouse", "how": "razer"})
        finally:
            k.CloseHandle(handle)
    if found:
        return found[0]
    if want and want[0] != RAZER or (seen and not want):
        return {"dpi": None, "name": seen, "why": "this mouse does not tell programs its DPI - measure it instead"}
    return {"dpi": None, "name": seen, "why": "the mouse did not answer - measure it instead"}


class Reader:
    """Asks in the background, so a menu frame never waits on USB. poll() hands over each answer once."""

    def __init__(self):
        self._busy = False
        self._out = None
        self.last = None            # the most recent answer, kept for the screens

    def ask(self, prefer: str | None = None) -> bool:
        if self._busy:
            return False
        self._busy = True

        def work():
            try:
                res = read(prefer)
            except Exception as e:      # never let a strange device take the app down
                res = {"dpi": None, "name": None, "why": f"{type(e).__name__}: {e}"}
            self._out, self._busy = res, False

        threading.Thread(target=work, name="dpi-read", daemon=True).start()
        return True

    def poll(self):
        res, self._out = self._out, None
        if res is not None:
            self.last = res
        return res


def measure(counts: float, cm: float) -> float:
    """DPI from a slide: how many counts the mouse sent while it moved `cm` centimetres."""
    return abs(counts) / (cm / 2.54) if cm > 0 else 0.0


def nearest_step(dpi: float) -> int:
    """The DPI setting a measurement most likely is: mice are set in round steps, and a hand on a
    ruler is good to a few percent. Within 6% of a common step it is called that step; otherwise
    it is rounded to the nearest 50."""
    best = min(STEPS, key=lambda s: abs(s - dpi))
    return best if abs(best - dpi) <= 0.06 * best else int(round(dpi / 50.0) * 50)
