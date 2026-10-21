"""The PC check: a one-off forensic look at the computer, when AimTuner is installed or updated.

Like the "PC checks" competitive communities run (and tools such as detect.ac sell), it looks for
two things: traces of cheats, and traces of someone hiding traces. It passes or fails:
  pass   runs can be verified (each run still has its own checks - ac/__init__.py)
  fail   every run is unverified until a check passes
The verdict is kept, sealed, beside config.json (pccheck.json). It is redone when AimTuner is
updated, and a quick part of it (program history, devices, running programs) is redone at every
start - a cheat installed after a passed check fails it then.

Read-only, local, and narrow. Nothing is sent anywhere. Program histories and file lists are
read only to be matched against the list of cheat and bypass names below; only the matches are
kept, never what else was found.

Where it looks (without admin rights):
  program history   UserAssist (programs started from Explorer), MuiCache (every program that
                    showed a window), the Compatibility Assistant store, recent files
  installed         installed programs, services and drivers
  devices           mice and keyboards connected now (by USB id: a microcontroller board passing
                    itself off as a mouse, a Cronus adapter), and every USB device ever connected
  files             Desktop, Downloads and Documents (three folders deep): cheat-named files,
                    Cronus scripts (.gpc), AutoHotkey scripts that pull the mouse down while
                    firing, Logitech G HUB Lua scripts that do the same
  Python            screen-grab + object-detection + mouse-control packages installed together
                    (the parts of an "AI" aimbot)
  temp folder       programs unpacked into %TEMP% (how one-click cheat loaders run)
  folders opened    ShellBags: every folder opened in Explorer, kept after the folder is deleted
  recycle bin       what is in it, by original name, and whether it was emptied just before
  hiding traces     event logs cleared, the NTFS change journal deleted and recreated, trace
                    services switched off just before the check, cleaning commands in the
                    PowerShell history
With admin rights (Windows asks; the check works without, with less to go on):
  BAM               every program run, with its full path
  Prefetch          the last ~1,000 programs run
  change journal    files deleted or renamed recently, by name - a deleted cheat leaves its name here
  security log      the audit log cleared
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import datetime
import glob
import hashlib
import hmac
import json
import os
import re
import struct
import subprocess
import sys
import time

from . import pcsources

# ------------------------------------------------------------------------------------ names
# (regex on a lower-case file name or program name, what it is, "fail" or "warn")
SIGNATURES = [
    (r"aimmy", "Aimmy (AI aimbot)", "fail"),
    (r"(^|[^a-z])ai[-_ ]m[-_ ]?bot|(^|[^a-z])ai[-_ ]?aim|yolo[-_ ]?aim|aim[-_ ]?yolo", "an AI aimbot", "fail"),
    (r"aim[-_ ]?bot", "an aimbot", "fail"),
    (r"trigger[-_ ]?bot", "a triggerbot", "fail"),
    (r"colou?r[-_ ]?bot|colou?r[-_ ]?aim", "a colour aimbot", "fail"),
    (r"(no|anti)[-_ ]?recoil|recoil[-_ ]?(script|macro|control(ler)?\.(exe|ahk|lua))", "a recoil script", "fail"),
    (r"zen[-_ ]?studio|cronus", "Cronus (controller adapter scripts)", "fail"),
    (r"kmbox|km[-_ ]?net|makcu", "an input box (KMBox / MAKCU)", "fail"),
    (r"xim[-_ ]?(matrix|nexus|apex|manager)", "XIM (input adapter)", "fail"),
    (r"hwid[-_ ]?spoof|spoofer", "an ID spoofer", "fail"),
    (r"(pc|ss)[-_ ]?check[-_ ]?bypass|screenshare[-_ ]?bypass|bypass[-_ ]?(ss|anti[-_ ]?cheat)", "a PC-check bypass", "fail"),
    (r"(prefetch|usn|journal|trace|bam|event[-_ ]?log)[-_ ]?(cleaner|wiper|deleter|clear)", "a trace cleaner", "fail"),
    (r"(dll|cheat|hack)[-_ ]?inject(or)?|injector\.(exe|dll)", "a DLL injector", "fail"),
    # Call of Duty and Siege: what their cheats are called and sold as, and the anti-cheats they dodge
    # (Ricochet for Call of Duty; BattlEye and Mousetrap for Siege)
    (r"wall[-_ ]?hack|silent[-_ ]?aim|(^|[^a-z])chams([^a-z]|$)|radar[-_ ]?hack|(^|[^a-z])esp[-_ ]?(hack|cheat|overlay)|"
     r"(^|[^a-z])(r6|siege|cod|warzone|mw[23]|bo[67])[-_ ]?(esp|hack|cheat|aim(bot)?|wh|external|internal|loader)([^a-z]|$)",
     "a Call of Duty or Siege cheat", "fail"),
    (r"(unlock[-_ ]?all|camo[-_ ]?unlock)[^\\]*\.(exe|dll|zip|rar|7z)$", "a Call of Duty unlock tool", "fail"),
    (r"(^|[^a-z])(ricochet|battl[-_ ]?eye|be|mousetrap|eac|vanguard|faceit)[-_ ]?(bypass|disabler|emulator|spoof)",
     "an anti-cheat bypass", "fail"),
    (r"(r6|siege|cod|warzone)[-_ ]?(no[-_ ]?)?recoil[-_ ]?(macro|script|config|control)|(r6|siege|cod|warzone)[-_ ]?macro",
     "a game recoil macro", "fail"),
    (r"(?<!anti)(?<!anti[-_ ])(^|[-_ .])cheats?([-_ .]|$)(?!.*sheet)|hack[-_ ]?(client|menu|loader)",
     "something named as a cheat", "warn"),
    (r"rewasd", "reWASD (remapper with macros)", "warn"),
    (r"autohotkey", "AutoHotkey (scripts)", "warn"),
    (r"tinytask|macro[-_ ]?(recorder|creator)", "a macro recorder", "warn"),
    (r"cheat[-_ ]?engine", "Cheat Engine (memory editor)", "warn"),
]
_SIG = [(re.compile(p), what, lvl) for p, what, lvl in SIGNATURES]

# USB ids: (vendor, product or None, what, "fail"/"warn", only while connected as a mouse/keyboard)
USB = [
    ("2508", None, "a Cronus controller adapter", "fail", False),
    ("2341", None, "an Arduino board", "fail", True), ("2a03", None, "an Arduino board", "fail", True),
    ("1b4f", None, "a SparkFun Pro Micro board", "fail", True),
    ("16c0", None, "a Teensy board", "fail", True),
    ("1a86", None, "a CH9329/CH340 USB bridge (what KMBox-style input boxes are built on)", "warn", True),
]

# PowerShell / command history: cleaning traces
CLEANING = re.compile(r"fsutil\s+usn\s+deletejournal|wevtutil(\.exe)?\s+cl\b|clear-eventlog|remove-eventlog|"
                      r"sc(\.exe)?\s+(config|stop)\s+(sysmain|pcasvc|eventlog|dps|bam)\b|"
                      r"stop-service\s+(-name\s+)?(sysmain|pcasvc|eventlog|dps)\b|"
                      r"remove-item\s+.*\\prefetch|del(ete)?\s+.*\\prefetch|"
                      r"reg(\.exe)?\s+delete\s+.*(userassist|bam\\state|muicache|compatibility assistant)", re.I)

AHK_PULL = re.compile(r"(mousemove|mouse_event|dllcall\s*\(\s*\"?mouse_event|sendinput)", re.I)
AHK_FIRE = re.compile(r"(lbutton|getkeystate\s*\(\s*\"?lbutton)", re.I)
LUA_PULL = re.compile(r"movemouserelative", re.I)
LUA_FIRE = re.compile(r"ismousebuttonpressed|onevent", re.I)

AI_GRAB = ("bettercam", "dxcam", "dxshot", "d3dshot")                 # game-screen capture
AI_DETECT = ("ultralytics", "onnxruntime_gpu", "onnxruntime", "tensorrt", "yolov5", "torch")
AI_MOVE = ("interception", "pyinterception", "pydirectinput", "vgamepad", "pyserial", "serial", "kmnet")

RUNNABLE = (".exe", ".msi", ".bat", ".cmd", ".ps1", ".ahk", ".lua", ".gpc", ".dll", ".sys", ".zip", ".rar", ".7z")

STATE_FILE = "pccheck.json"
VERSION = 1


def match(name: str):
    """(what, level) for the first signature a name matches, or None."""
    n = name.lower()
    for rx, what, lvl in _SIG:
        if rx.search(n):
            return what, lvl
    return None


def _ft(v) -> datetime.datetime | None:
    try:
        return datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=int(v) // 10)
    except (OverflowError, ValueError, TypeError):
        return None


def _rot13(s: str) -> str:
    return s.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
                                     "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm"))


# ------------------------------------------------------------------------------------ sources
class Scan:
    """One PC check. `findings` is [(level, what, where)]; `coverage` says what could be read."""

    def __init__(self, elevated: bool = False, progress=None, quick: bool = False):
        self.elevated = elevated
        self.quick = quick
        self.progress = progress or (lambda text, frac: None)
        self.findings: list = []
        self.coverage: dict = {}
        self._seen = set()

    def _hit(self, level, what, where):
        # a note is said once per thing found (Windows keeps several entries per program); a failure
        # keeps every place it was found, as evidence
        key = (level, what) if level == "warn" else (level, what, where.lower())
        if key not in self._seen:
            self._seen.add(key)
            self.findings.append((level, what, where))

    def _names(self, source, names):
        """Match program or file names from one source against the signatures."""
        n = 0
        for name in names:
            n += 1
            m = match(os.path.basename(name.rstrip("\\/")) or name)
            if m is None:
                m = match(name)
            if m:
                self._hit(m[1], f"{m[0]} in {source}", name)
        self.coverage[source] = n

    # -- program history (no admin)
    def userassist(self):
        import winreg

        out = []
        base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    g = winreg.EnumKey(k, i)
                    try:
                        with winreg.OpenKey(k, g + "\\Count") as c:
                            for j in range(winreg.QueryInfoKey(c)[1]):
                                out.append(_rot13(winreg.EnumValue(c, j)[0]))
                    except OSError:
                        pass
        except OSError:
            pass
        self._names("program history (UserAssist)", out)
        return out

    def _values(self, root, path):
        import winreg

        out = []
        try:
            with winreg.OpenKey(root, path) as k:
                for j in range(winreg.QueryInfoKey(k)[1]):
                    nm, val, _ = winreg.EnumValue(k, j)
                    out.append(nm)
                    if isinstance(val, str):
                        out.append(val)
        except OSError:
            pass
        return out

    def muicache(self):
        import winreg

        self._names("program history (MuiCache)", self._values(
            winreg.HKEY_CURRENT_USER, r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache"))

    def compat_store(self):
        import winreg

        self._names("program history (Compatibility Assistant)", self._values(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store"))

    def recent(self):
        rec = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent")
        try:
            names = [os.path.splitext(f)[0] for f in os.listdir(rec)]
        except OSError:
            names = []
        self._names("recent files", names)

    def installed(self):
        import winreg

        names = []
        for root, path in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                           (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                           (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")):
            try:
                with winreg.OpenKey(root, path) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        sub = winreg.EnumKey(k, i)
                        names.append(sub)
                        try:
                            with winreg.OpenKey(k, sub) as s:
                                names.append(str(winreg.QueryValueEx(s, "DisplayName")[0]))
                        except OSError:
                            pass
            except OSError:
                pass
        self._names("installed programs", names)

    def services(self):
        """Services and drivers by name, display name and file; and the trace services' state."""
        import winreg

        names = []
        base = r"SYSTEM\CurrentControlSet\Services"
        now = datetime.datetime.utcnow()
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    sub = winreg.EnumKey(k, i)
                    names.append(sub)
                    if self.quick:
                        continue
                    try:
                        with winreg.OpenKey(k, sub) as s:
                            for v in ("DisplayName", "ImagePath"):
                                try:
                                    names.append(str(winreg.QueryValueEx(s, v)[0]))
                                except OSError:
                                    pass
                    except OSError:
                        pass
        except OSError:
            pass
        self._names("services and drivers", names)
        if self.quick:
            return
        # The services that keep the traces: switched off is how traces are hidden. Two of them are
        # commonly switched off by performance guides (SysMain, PcaSvc, DPS): off for months is only
        # less to go on; switched off in the last days before a check is hiding. Nobody switches off
        # the event log or BAM for speed.
        for svc, core in (("EventLog", True), ("bam", True), ("SysMain", False), ("PcaSvc", False), ("DPS", False)):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + svc) as s:
                    start = winreg.QueryValueEx(s, "Start")[0]
                    written = _ft(winreg.QueryInfoKey(s)[2])
            except OSError:
                if core:
                    self._hit("fail", f"the {svc} service is missing", svc)
                continue
            if start != 4:
                continue
            recent = written is not None and (now - written).days < 3
            if core:
                self._hit("fail", f"the {svc} service (it keeps traces) is switched off", svc)
            elif recent:
                self._hit("fail", f"the {svc} service (it keeps traces) was switched off in the last 3 days", svc)
            else:
                self._hit("warn", f"the {svc} service is switched off (since {written:%Y-%m-%d}): less history to check", svc)

    def processes(self):
        from . import system

        self._names("running programs", system.processes())

    # -- devices
    def devices_now(self):
        """Mice and keyboards connected now, by USB vendor and product id."""
        from ..mouse import Mouse

        paths = []
        try:
            u32 = ctypes.WinDLL("user32", use_last_error=True)

            class RIDL(ctypes.Structure):
                _fields_ = [("h", wt.HANDLE), ("type", wt.DWORD)]

            n = wt.UINT(0)
            u32.GetRawInputDeviceList(None, ctypes.byref(n), ctypes.sizeof(RIDL))
            arr = (RIDL * max(n.value, 1))()
            got = u32.GetRawInputDeviceList(arr, ctypes.byref(n), ctypes.sizeof(RIDL))
            m = Mouse(start=False)
            for i in range(max(int(got), 0) if got != 0xFFFFFFFF else 0):
                if arr[i].type in (0, 1):            # mouse, keyboard
                    p = m.device_name(arr[i].h)
                    if p:
                        paths.append((arr[i].type, p))
        except (AttributeError, OSError):
            pass
        for kind, p in paths:
            vm = re.search(r"vid_([0-9a-f]{4})", p, re.I)
            if not vm:
                continue
            vid = vm.group(1).lower()
            for v, pid, what, lvl, _active in USB:
                if v == vid:
                    self._hit(lvl, f"{what} is connected as a {'mouse' if kind == 0 else 'keyboard'}", p)
        self.coverage["mice and keyboards now"] = len(paths)

    def devices_ever(self):
        """Every USB device that has been connected (Windows keeps them all)."""
        import winreg

        ids = []
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB") as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    ids.append(winreg.EnumKey(k, i))
        except OSError:
            pass
        for d in ids:
            vm = re.search(r"vid_([0-9a-f]{4})", d, re.I)
            if not vm:
                continue
            for v, pid, what, lvl, active in USB:
                if v == vm.group(1).lower() and not active:
                    self._hit(lvl, f"{what} has been connected to this PC", d)
        self.coverage["USB devices ever connected"] = len(ids)

    # -- files
    def files(self, budget_s: float = 4.0):
        home = os.path.expanduser("~")
        roots = [os.path.join(home, d) for d in ("Desktop", "Downloads", "Documents")]
        od = os.environ.get("OneDrive")
        if od:
            roots += [os.path.join(od, d) for d in ("Desktop", "Documents")]
        t_end = time.perf_counter() + budget_s
        n = 0
        skip = {"node_modules", ".git", "__pycache__", "site-packages", "AppData", "logs", "captures", "_internal"}
        own = os.path.normcase(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        for root in dict.fromkeys(r for r in roots if os.path.isdir(r)):
            base_depth = root.rstrip("\\/").count(os.sep)
            for d, dirs, fs in os.walk(root):
                if d.count(os.sep) - base_depth >= 3:
                    dirs[:] = []
                dirs[:] = [x for x in dirs if x not in skip and not x.startswith(".")
                           and os.path.normcase(os.path.join(d, x)) != own]          # (AimTuner's own folder: its tests)
                for f in fs:
                    n += 1
                    low = f.lower()
                    path = os.path.join(d, f)
                    m = match(low)
                    # a note needs a program, not someone's own source code that happens to share a name
                    # (a project's macro_recorder.py is not the Macro Recorder app)
                    if m and (m[1] == "fail" or low.endswith(RUNNABLE)):
                        self._hit(m[1], f"{m[0]}: a file", path)
                    if low.endswith(".gpc"):
                        self._hit("fail", "a Cronus script (.gpc)", path)
                    elif low.endswith(".ahk"):
                        self._script(path, AHK_PULL, AHK_FIRE, "an AutoHotkey script that moves the mouse while firing")
                    elif low.endswith(".lua"):
                        self._script(path, LUA_PULL, LUA_FIRE, "a Lua mouse script that moves the mouse while firing")
                if time.perf_counter() > t_end:
                    self.coverage["files (stopped at the time limit)"] = n
                    return
        self.coverage["files"] = n

    def _script(self, path, pull, fire, what):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(65536)
        except OSError:
            return
        if pull.search(text) and fire.search(text):
            self._hit("fail", what, path)

    def temp(self):
        """%TEMP%, two folders deep: one-click loaders unpack and run from here."""
        d = os.path.expandvars("%TEMP%")
        n = 0
        base = os.path.normpath(d).count(os.sep)
        for root, dirs, fs in os.walk(d):
            if root.count(os.sep) - base >= 2:
                dirs[:] = []
            for f in fs:
                n += 1
                low = f.lower()
                if low.endswith((".exe", ".dll", ".sys", ".lua", ".ahk", ".gpc", ".py", ".bat", ".ps1")):
                    m = match(low)
                    if m:
                        self._hit(m[1], f"{m[0]}: a file in the temp folder", os.path.join(root, f))
            if n > 20000:
                break
        self.coverage["temp folder"] = n

    def shellbags(self):
        """Every folder opened in Explorer (ShellBags), by name - kept after the folder is deleted."""
        import winreg

        names = []

        def walk(key, depth):
            if depth > 12 or len(names) > 50000:
                return
            try:
                for j in range(winreg.QueryInfoKey(key)[1]):
                    _, val, typ = winreg.EnumValue(key, j)
                    if typ == winreg.REG_BINARY and isinstance(val, bytes) and len(val) > 8:
                        for m in re.finditer(rb"(?:[ -~]\x00){4,}", val):
                            names.append(m.group().decode("utf-16-le", "ignore"))
                for i in range(winreg.QueryInfoKey(key)[0]):
                    with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                        walk(sub, depth + 1)
            except OSError:
                pass

        for path in (r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\BagMRU",
                     r"Software\Microsoft\Windows\Shell\BagMRU"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                    walk(k, 0)
            except OSError:
                pass
        self._names("folders opened (ShellBags)", names)

    def recycle_bin(self):
        """The recycle bin: each deleted item's original path is kept in its $I file."""
        n = 0
        newest = None
        for root in glob.glob(r"C:\$Recycle.Bin\S-*"):
            try:
                st = os.stat(root)
                newest = max(newest or 0, st.st_mtime)
                for f in os.listdir(root):
                    if not f.startswith("$I"):
                        continue
                    n += 1
                    try:
                        with open(os.path.join(root, f), "rb") as fh:
                            raw = fh.read(4096)
                        ver = struct.unpack_from("<Q", raw, 0)[0]
                        name = (raw[28:].decode("utf-16-le", "ignore") if ver == 2 else raw[24:544].decode("utf-16-le", "ignore")).split("\x00")[0]
                    except (OSError, struct.error):
                        continue
                    m = match(os.path.basename(name)) or match(name)
                    if m:
                        self._hit(m[1], f"{m[0]}: in the recycle bin", name)
            except OSError:
                pass
        self.coverage["recycle bin"] = n
        if newest and n == 0 and time.time() - newest < 600 and not self.quick:
            self._hit("warn", "the recycle bin was emptied in the last 10 minutes", time.strftime("%H:%M", time.localtime(newest)))

    def ghub(self):
        """Logitech G HUB's Lua scripts (its scripting is the usual home of a mouse recoil script)."""
        base = os.path.expandvars(r"%LOCALAPPDATA%\LGHUB")
        if not os.path.isdir(base):
            return
        n = 0
        for p in glob.glob(os.path.join(base, "scripts", "**", "*.lua"), recursive=True):
            n += 1
            self._script(p, LUA_PULL, LUA_FIRE, "a G HUB Lua script that moves the mouse while firing")
        for db in glob.glob(os.path.join(base, "settings.db*")):
            n += 1
            try:
                with open(db, "rb") as f:
                    blob = f.read(32 << 20).decode("utf-8", "ignore")
            except OSError:
                continue
            if LUA_PULL.search(blob) and LUA_FIRE.search(blob):
                self._hit("fail", "a G HUB Lua script that moves the mouse while firing", db)
        self.coverage["G HUB scripts"] = n

    def python_kits(self):
        """Screen grabbing + object detection + mouse control, installed together in one Python."""
        sites = set()
        for pat in (r"%LOCALAPPDATA%\Programs\Python\*\Lib\site-packages", r"%APPDATA%\Python\*\site-packages",
                    r"C:\Python*\Lib\site-packages", r"%USERPROFILE%\anaconda3\Lib\site-packages",
                    r"%USERPROFILE%\miniconda3\Lib\site-packages", r"%USERPROFILE%\*\venv\Lib\site-packages",
                    r"%USERPROFILE%\*\.venv\Lib\site-packages"):
            sites.update(glob.glob(os.path.expandvars(pat)))
        for sp in sites:
            try:
                names = {x.lower().split("-")[0] for x in os.listdir(sp)}
            except OSError:
                continue
            grab = sorted(names & set(AI_GRAB))
            det = sorted(names & set(AI_DETECT))
            move = sorted(names & set(AI_MOVE))
            if grab and det and move:
                self._hit("fail", f"the parts of an AI aimbot in one Python ({', '.join(grab + det + move)})", sp)
            elif grab and det:
                self._hit("warn", f"screen-grab and object-detection packages in one Python ({', '.join(grab + det)})", sp)
        self.coverage["Python installs"] = len(sites)

    # -- hiding traces
    def event_logs(self):
        """Event logs cleared in the last 30 days (System: event 104; Security: 1102, admin only)."""
        days = 30
        for log, eid, admin in (("System", 104, False), ("Security", 1102, True)):
            if admin and not self.elevated:
                continue
            q = f"*[System[(EventID={eid}) and TimeCreated[timediff(@SystemTime) <= {days * 86400000}]]]"
            try:
                r = subprocess.run(["wevtutil", "qe", log, f"/q:{q}", "/c:5", "/rd:true", "/f:text"],
                                   capture_output=True, text=True, timeout=15,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                n = r.stdout.count("Event ID:")
                self.coverage[f"{log} event log"] = "read"
                if n:
                    self._hit("fail", f"the {log} event log was cleared in the last {days} days", f"{log} log, event {eid} x{n}")
            except (OSError, subprocess.SubprocessError):
                self.coverage[f"{log} event log"] = "not readable"

    def journal_age(self):
        """The NTFS change journal records every file change; deleting it (to hide deleted files) makes
        a new one, whose id is the time it was made."""
        try:
            r = subprocess.run(["fsutil", "usn", "queryjournal", "C:"], capture_output=True, text=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            m = re.search(r"Usn Journal ID\s*:\s*0x([0-9a-f]+)", r.stdout, re.I)
        except (OSError, subprocess.SubprocessError):
            m = None
        if not m:
            self.coverage["change journal"] = "not readable"
            return
        made = _ft(int(m.group(1), 16))
        self.coverage["change journal"] = f"made {made:%Y-%m-%d}" if made else "read"
        boot = self._windows_installed()
        if made and (datetime.datetime.utcnow() - made).days < 7 and (boot is None or (made - boot).days > 2):
            self._hit("fail", "the drive's change journal was deleted and made again in the last 7 days", f"journal made {made:%Y-%m-%d %H:%M}")

    def _windows_installed(self):
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
                return datetime.datetime.utcfromtimestamp(int(winreg.QueryValueEx(k, "InstallDate")[0]))
        except (OSError, ValueError):
            return None

    def shell_history(self):
        """Cleaning commands typed into PowerShell (its history file)."""
        p = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt")
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()[-5000:]
        except OSError:
            return
        for ln in lines:
            if CLEANING.search(ln):
                self._hit("fail", "a command that wipes traces, in the PowerShell history", ln.strip()[:120])
        self.coverage["PowerShell history"] = len(lines)

    def history_emptied(self):
        """Program history wiped: UserAssist almost empty on a PC whose recent-files list is full."""
        ua = self.coverage.get("program history (UserAssist)", 0)
        rec = self.coverage.get("recent files", 0)
        if ua < 3 and rec > 50:
            self._hit("fail", "the program history (UserAssist) has been cleared", f"{ua} entries, {rec} recent files")

    # -- with admin rights
    def bam(self):
        import winreg

        out = []
        base = r"SYSTEM\CurrentControlSet\Services\bam\State\UserSettings"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    out += self._values(winreg.HKEY_LOCAL_MACHINE, base + "\\" + winreg.EnumKey(k, i))
        except OSError:
            self.coverage["program history (BAM)"] = "not readable"
            return
        self._names("program history (BAM)", [x for x in out if "\\" in x])

    def prefetch(self):
        d = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Prefetch")
        try:
            pf = [f for f in os.listdir(d) if f.lower().endswith(".pf")]
        except OSError:
            self.coverage["Prefetch"] = "not readable"
            return
        self._names("Prefetch", [f.rsplit("-", 1)[0] for f in pf])

    def change_journal(self, budget_s: float = 8.0):
        """Files deleted or renamed away, from the NTFS change journal, by name."""
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wt.HANDLE
        k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
        k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, wt.LPVOID, wt.DWORD, wt.LPVOID, wt.DWORD,
                                        ctypes.POINTER(wt.DWORD), wt.LPVOID]
        h = k32.CreateFileW("\\\\.\\C:", 0x80000000, 0x3, None, 3, 0, None)
        if not h or h == wt.HANDLE(-1).value:
            self.coverage["deleted files (change journal)"] = "not readable"
            return
        try:
            jd = ctypes.create_string_buffer(64)
            got = wt.DWORD(0)
            if not k32.DeviceIoControl(h, 0x000900F4, None, 0, jd, 64, ctypes.byref(got), None):
                self.coverage["deleted files (change journal)"] = "not readable"
                return
            jid, first, nxt = struct.unpack_from("<Qqq", jd, 0)
            usn = first
            out = ctypes.create_string_buffer(1 << 20)
            t_end = time.perf_counter() + budget_s
            n = 0
            want = (".exe", ".dll", ".sys", ".ahk", ".gpc", ".lua", ".py", ".onnx", ".engine", ".pt", ".zip", ".rar", ".7z")
            while usn < nxt and time.perf_counter() < t_end:
                req = struct.pack("<qIIQQQ", usn, 0x200 | 0x1000, 0, 0, 0, jid)      # FILE_DELETE | RENAME_OLD_NAME
                if not k32.DeviceIoControl(h, 0x000900BB, req, len(req), out, len(out), ctypes.byref(got), None):
                    break
                if got.value <= 8:
                    break
                usn = struct.unpack_from("<q", out, 0)[0]
                raw = out.raw[:got.value]
                off = 8
                while off + 60 <= len(raw):
                    rl = struct.unpack_from("<I", raw, off)[0]
                    if rl < 60:
                        break
                    nl, no = struct.unpack_from("<HH", raw, off + 56)
                    name = raw[off + no:off + no + nl].decode("utf-16-le", "ignore")
                    n += 1
                    low = name.lower()
                    if low.endswith(want):
                        m = match(low)
                        if m:
                            self._hit(m[1], f"{m[0]}: a file deleted or renamed recently", name)
                        elif low.endswith(".gpc"):
                            self._hit("fail", "a Cronus script (.gpc) deleted recently", name)
                    off += rl
            self.coverage["deleted files (change journal)"] = n
        finally:
            k32.CloseHandle(h)

    # -- all of it
    def run(self) -> dict:
        t0 = time.perf_counter()
        steps = [("program history", self.userassist), ("program history", self.muicache),
                 ("program history", self.compat_store), ("recent files", self.recent),
                 ("running programs", self.processes), ("devices", self.devices_now),
                 ("services", self.services), ("G HUB scripts", self.ghub),
                 ("program history (PCA)", lambda: pcsources.pca(self)),
                 ("program history (ShimCache)", lambda: pcsources.shimcache(self)),
                 ("Explorer history", lambda: pcsources.explorer(self))]
        if not self.quick:
            steps += [("installed programs", self.installed), ("USB history", self.devices_ever),
                      ("files", self.files), ("temp folder", self.temp), ("folders opened", self.shellbags),
                      ("recycle bin", self.recycle_bin), ("Python installs", self.python_kits),
                      ("event logs", self.event_logs), ("change journal", self.journal_age),
                      ("PowerShell history", self.shell_history), ("jump lists", lambda: pcsources.jumplists(self)),
                      ("clock changes", lambda: pcsources.clock(self)), ("Prefetch setting", lambda: pcsources.prefetcher(self)),
                      ("Windows install", lambda: pcsources.fresh(self))]
            if self.elevated:
                steps += [("BAM", self.bam), ("Prefetch", self.prefetch), ("deleted files", self.change_journal),
                          ("Windows Defender", lambda: pcsources.defender(self)),
                          ("every file on C:", lambda: pcsources.drive(self))]
        for i, (label, fn) in enumerate(steps):
            self.progress(label, i / len(steps))
            try:
                fn()
            except Exception as e:                      # one unreadable source never stops the rest
                self.coverage[label] = f"error: {type(e).__name__}"
        if not self.quick:
            self.history_emptied()
        self.progress("done", 1.0)
        fails = [f for f in self.findings if f[0] == "fail"]
        return {"v": VERSION, "passed": not fails, "elevated": self.elevated, "quick": self.quick,
                "findings": [list(f) for f in self.findings], "coverage": self.coverage,
                "seconds": round(time.perf_counter() - t0, 2)}


# ------------------------------------------------------------------------------- the verdict
def machine_id() -> str:
    """This Windows installation, hashed (never stored as it is)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            guid = str(winreg.QueryValueEx(k, "MachineGuid")[0])
    except OSError:
        guid = os.environ.get("COMPUTERNAME", "unknown")
    return hashlib.sha256(("aimtuner|" + guid).encode()).hexdigest()[:24]


def _mac(body: dict) -> str:
    from .seal import _key

    raw = json.dumps({k: v for k, v in body.items() if k != "mac"}, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_key(b"pccheck"), raw, hashlib.sha256).hexdigest()[:32]


def save(path: str, result: dict, app_version: str) -> dict:
    body = dict(result, app=app_version, machine=machine_id(), time=time.strftime("%Y-%m-%dT%H:%M:%S"))
    body["mac"] = _mac(body)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=1)
    os.replace(tmp, path)
    return body


def load(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or not hmac.compare_digest(str(body.get("mac", "")), _mac(body)):
        return {"tampered": True}
    return body


def status(path: str, app_version: str) -> tuple:
    """("passed" | "failed" | "needed" | "tampered", the record or None) for this PC and version."""
    body = load(path)
    if body is None:
        return "needed", None
    if body.get("tampered"):
        return "tampered", None
    if body.get("machine") != machine_id() or body.get("app") != app_version:
        return "needed", body
    return ("passed" if body.get("passed") else "failed"), body


# ------------------------------------------------------------------------------- admin rights
def run_elevated(out_path: str, timeout: float = 120.0) -> bool:
    """Run the check in a copy of AimTuner that Windows starts with admin rights (it asks the
    user). True when that copy wrote its result; False if the user said no."""
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, f'--pccheck "{out_path}"'
    else:
        here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        exe, args = sys.executable, f'"{os.path.join(here, "app.py")}" --pccheck "{out_path}"'

    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wt.HWND), ("lpVerb", wt.LPCWSTR),
                    ("lpFile", wt.LPCWSTR), ("lpParameters", wt.LPCWSTR), ("lpDirectory", wt.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wt.HINSTANCE), ("lpIDList", wt.LPVOID),
                    ("lpClass", wt.LPCWSTR), ("hkeyClass", wt.HKEY), ("dwHotKey", wt.DWORD),
                    ("hIcon", wt.HANDLE), ("hProcess", wt.HANDLE)]

    try:
        os.remove(out_path)
    except OSError:
        pass
    sei = SEI()
    sei.cbSize = ctypes.sizeof(SEI)
    sei.fMask = 0x00000040                                   # SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb, sei.lpFile, sei.lpParameters, sei.nShow = "runas", exe, args, 0
    sei.lpDirectory = os.path.dirname(exe)
    sh = ctypes.WinDLL("shell32", use_last_error=True)
    sh.ShellExecuteExW.argtypes = [ctypes.POINTER(SEI)]
    if not sh.ShellExecuteExW(ctypes.byref(sei)) or not sei.hProcess:
        return False
    k32 = ctypes.windll.kernel32
    k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    k32.WaitForSingleObject(sei.hProcess, int(timeout * 1000))
    k32.CloseHandle(sei.hProcess)
    return os.path.exists(out_path)


def elevated_main(out_path: str) -> int:
    """`AimTuner.exe --pccheck OUT` (started with admin rights by run_elevated)."""
    admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    res = Scan(elevated=admin).run()
    res["mac_child"] = _mac(res)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f)
    return 0


def read_elevated(out_path: str) -> dict | None:
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            res = json.load(f)
        os.remove(out_path)
    except (OSError, ValueError):
        return None
    mac = res.pop("mac_child", "")
    return res if hmac.compare_digest(str(mac), _mac(res)) else None
