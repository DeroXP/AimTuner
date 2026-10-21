"""The machine a run was played on: what else was running, what was loaded into AimTuner, and
whether the game could be seen by anything but the screen. Windows only; read-only throughout -
nothing here changes, closes or blocks anything except AimTuner's own window.

  tools         programs known to be aim cheats, recoil scripts / input remappers, or debuggers
                and memory editors, running at the time (by process name)
  modules       DLLs loaded into AimTuner that are neither Windows', AimTuner's own, nor a known
                graphics driver, overlay or antivirus - the way code gets into a game uninvited
  debugger      a debugger attached to AimTuner
  input driver  the Interception filter driver (it lets a program make input that Windows reports
                as coming from the real mouse - used by many hardware-emulating cheats)
  clock         the game's clock against two independent Windows clocks over the run: a
                "speedhack" slows one down and not the others
  capture       AimTuner's window hidden from screen capture during runs (SetWindowDisplayAffinity),
                so a program reading the screen - an "AI" aimbot - sees black
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

# process names (lower case, no .exe) -> (what it is, how serious). "cheat": made for cheating.
# "macro": can run recoil scripts or remap input - legitimate uses too, so on its own it only
# stops a run counting as verified. "tamper": debuggers and memory editors.
TOOLS = {
    "aimmy": ("Aimmy (AI aimbot)", "cheat"), "aimmylauncher": ("Aimmy (AI aimbot)", "cheat"),
    "ai-m-bot": ("AI-M-BOT (AI aimbot)", "cheat"), "aimbot": ("an aimbot", "cheat"),
    "zenstudio": ("Cronus Zen Studio (controller adapter scripts)", "cheat"),
    "cronusmax": ("CronusMAX (controller adapter scripts)", "cheat"),
    "xim matrix manager": ("XIM Matrix manager", "cheat"), "ximnexus": ("XIM Nexus manager", "cheat"),
    "kmbox": ("KMBox (input box)", "cheat"), "kmboxnet": ("KMBox Net (input box)", "cheat"),
    "rewasdengine": ("reWASD (remapper with macros)", "macro"), "rewasdtray": ("reWASD (remapper with macros)", "macro"),
    "rewasd": ("reWASD (remapper with macros)", "macro"),
    "autohotkey": ("AutoHotkey (scripts)", "macro"), "autohotkeyu64": ("AutoHotkey (scripts)", "macro"),
    "autohotkey64": ("AutoHotkey (scripts)", "macro"), "autohotkeyu32": ("AutoHotkey (scripts)", "macro"),
    "autohotkey32": ("AutoHotkey (scripts)", "macro"), "tinytask": ("TinyTask (macro recorder)", "macro"),
    "macrocreator": ("Pulover's Macro Creator", "macro"), "macrorecorder": ("Macro Recorder", "macro"),
    "cheatengine-x86_64": ("Cheat Engine (memory editor)", "tamper"), "cheatengine": ("Cheat Engine (memory editor)", "tamper"),
    "cheatengine-x86_64-sse4-avx2": ("Cheat Engine (memory editor)", "tamper"),
    "x64dbg": ("x64dbg (debugger)", "tamper"), "x32dbg": ("x32dbg (debugger)", "tamper"),
    "ollydbg": ("OllyDbg (debugger)", "tamper"), "ida64": ("IDA (disassembler)", "tamper"), "ida": ("IDA (disassembler)", "tamper"),
    "processhacker": ("Process Hacker", "tamper"), "systeminformer": ("System Informer", "tamper"),
    "artmoney": ("ArtMoney (memory editor)", "tamper"), "wemod": ("WeMod (trainer)", "tamper"),
}

# DLLs that are expected in a game's process although they are not Windows' own: graphics
# drivers, overlays, recorders, input software and antivirus hooks (matched as substrings)
KNOWN_MODULES = (
    "nvoglv", "nvd3d", "nvldumd", "nvwgf", "nvapi", "nvspcap", "nvcuda", "nvppe", "nvgpucomp", "nvtelemetry",
    "nvinitx", "nvcamera", "nvshim", "nvdlist", "nvmessagebus", "nvlowlatency", "nvrtx", "nvofapi", "nvml",
    "atio6axx", "atiadlxx", "amdxx", "amdihk", "aticfx", "atiumd", "amdvlk", "amdenc", "ig9icd", "ig7icd", "igxelp",
    "igdgmm", "igc64", "intelocl", "igdml",
    "gameoverlayrenderer", "discordhook", "graphics-hook", "rtsshooks", "obs-vulkan", "nahimic", "sscore",
    "overwolf", "medal", "xsplit", "bdcam", "action_x64", "mirillis", "fraps", "bandicam",
    "aswhook", "atcuf", "bdhkm", "bitdefender", "avghook", "avast", "sophos", "eset", "eamsi", "kaspersky", "klsihk",
    "mbae", "malwarebytes", "cylance", "crowdstrike", "sentinel", "mcafee", "norton", "symantec", "trend",
    "webroot", "fsecure", "f-secure", "panda", "comodo", "cmdguard", "avira",
    "logi", "lghub", "razer", "rzchroma", "steelseries", "corsair", "icue", "wooting",
    "msvcp", "vcruntime", "ucrtbase", "concrt", "vccorlib", "api-ms-", "ext-ms-", "python", "glfw", "libopenblas",
    "libffi", "libcrypto", "libssl", "sqlite3", "tcl", "tk86",
)


def _k32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def processes() -> list:
    """Names of the programs running now (lower case, without .exe)."""
    if sys.platform != "win32":
        return []

    class PE32(ctypes.Structure):
        _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wt.DWORD), ("threads", wt.DWORD), ("parent", wt.DWORD), ("pri", ctypes.c_long),
                    ("flags", wt.DWORD), ("exe", ctypes.c_wchar * 260)]

    k = _k32()
    k.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PE32)]
    k.CloseHandle.argtypes = [wt.HANDLE]
    snap = k.CreateToolhelp32Snapshot(0x2, 0)        # TH32CS_SNAPPROCESS
    if not snap or snap == wt.HANDLE(-1).value:
        return []
    out = []
    try:
        pe = PE32()
        pe.size = ctypes.sizeof(PE32)
        ok = k.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            name = pe.exe.lower()
            out.append(name[:-4] if name.endswith(".exe") else name)
            ok = k.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k.CloseHandle(snap)
    return out


def tools_running(names=None) -> list:
    """[(process, what it is, kind)] for every known tool running now."""
    names = processes() if names is None else names
    seen, out = set(), []
    for n in names:
        hit = TOOLS.get(n)
        if hit is None:
            for key, val in TOOLS.items():          # versioned names: "autohotkey_2.0", "cheatengine-i386"
                if n.startswith(key) and len(key) >= 6:
                    hit = val
                    break
        if hit and hit[0] not in seen:
            seen.add(hit[0])
            out.append((n, hit[0], hit[1]))
    return out


def modules() -> list:
    """Full paths of every DLL loaded into this process."""
    if sys.platform != "win32":
        return []
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        k = _k32()
        k.GetCurrentProcess.restype = wt.HANDLE
        h = k.GetCurrentProcess()
        arr = (wt.HMODULE * 2048)()
        need = wt.DWORD(0)
        psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HMODULE), wt.DWORD, ctypes.POINTER(wt.DWORD), wt.DWORD]
        if not psapi.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr), ctypes.byref(need), 0x03):
            return []
        n = min(need.value // ctypes.sizeof(wt.HMODULE), 2048)
        psapi.GetModuleFileNameExW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
        out = []
        buf = ctypes.create_unicode_buffer(1024)
        for i in range(n):
            if psapi.GetModuleFileNameExW(h, arr[i], buf, 1024):
                out.append(buf.value)
        return out
    except (AttributeError, OSError):
        return []


def foreign_modules(paths=None) -> list:
    """Loaded DLLs that are not Windows', not AimTuner's or Python's own, and not a known driver,
    overlay or antivirus (see KNOWN_MODULES)."""
    paths = modules() if paths is None else paths
    win = os.environ.get("SystemRoot", r"C:\Windows").lower()
    own = [os.path.dirname(os.path.abspath(sys.executable)).lower(), os.path.dirname(os.__file__).lower(),
           os.path.dirname(os.path.dirname(os.path.abspath(__file__))).lower()]
    try:
        import site
        own += [p.lower() for p in site.getsitepackages()]
    except Exception:
        pass
    base = getattr(sys, "_MEIPASS", None)
    if base:
        own.append(base.lower())
    out = []
    for p in paths:
        lp = p.lower()
        if lp.startswith(win) or any(lp.startswith(o) for o in own if o):
            continue
        name = os.path.basename(lp)
        if any(k in name for k in KNOWN_MODULES) or "program files" in lp and any(k in lp for k in ("nvidia", "amd", "intel")):
            continue
        out.append(p)
    return out


def debugger() -> bool:
    if sys.platform != "win32":
        return False
    try:
        k = _k32()
        if k.IsDebuggerPresent():
            return True
        flag = wt.BOOL(0)
        k.GetCurrentProcess.restype = wt.HANDLE
        k.CheckRemoteDebuggerPresent.argtypes = [wt.HANDLE, ctypes.POINTER(wt.BOOL)]
        k.CheckRemoteDebuggerPresent(k.GetCurrentProcess(), ctypes.byref(flag))
        return bool(flag.value) or sys.gettrace() is not None and "pydevd" in sys.modules
    except (AttributeError, OSError):
        return False


def interception_driver() -> bool:
    """Is the Interception filter driver installed on the mouse or keyboard class?"""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        for cls in ("{4D36E96F-E325-11CE-BFC1-08002BE10318}", "{4D36E96B-E325-11CE-BFC1-08002BE10318}"):
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Class" + "\\" + cls) as k:
                try:
                    filt, _ = winreg.QueryValueEx(k, "UpperFilters")
                except OSError:
                    continue
                # Interception installs itself as the upper filter named "mouse" / "keyboard"
                if any(f.lower() in ("mouse", "keyboard") for f in (filt or [])):
                    return True
        return False
    except Exception:
        return False


class Clocks:
    """The game's clock against Windows' tick count and the wall clock, over one run."""

    def __init__(self):
        self.a = self._now()

    @staticmethod
    def _now():
        try:
            tick = ctypes.windll.kernel32.GetTickCount64
            tick.restype = ctypes.c_ulonglong
            t64 = tick() / 1000.0
        except (AttributeError, OSError):
            t64 = None
        return time.perf_counter(), t64, time.time()

    def drift(self):
        """How far the game's clock ran from the other two, as a share (0.0 = together), or None."""
        b = self._now()
        dq = b[0] - self.a[0]
        if dq < 5.0:
            return None
        ref = [b[2] - self.a[2]]
        if b[1] is not None and self.a[1] is not None:
            ref.append(b[1] - self.a[1])
        return max(abs(dq - r) / dq for r in ref)


def hide_from_capture(hwnd, on: bool) -> bool:
    """Keep AimTuner's window out of screen captures (on) or let it be captured again (off).
    Returns True when Windows confirms the window is now excluded."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        u32 = ctypes.windll.user32
        u32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
        u32.GetWindowDisplayAffinity.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u32.SetWindowDisplayAffinity(hwnd, 0x11 if on else 0x0)      # WDA_EXCLUDEFROMCAPTURE / WDA_NONE
        cur = wt.DWORD(0)
        u32.GetWindowDisplayAffinity(hwnd, ctypes.byref(cur))
        return cur.value == 0x11 if on else False
    except (AttributeError, OSError):
        return False


def snapshot() -> dict:
    """Everything above that is read at a point in time (the start and the end of a run)."""
    return {"tools": tools_running(), "foreign_modules": foreign_modules(), "debugger": debugger(),
            "interception": interception_driver()}
