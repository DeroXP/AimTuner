"""More places the PC check looks (ac/pccheck.py), added after a second round of research into
what forensic PC checks and incident responders read. Each takes the Scan and reports into it.

Without admin rights:
  pca          Windows 11's Program Compatibility Assistant launch log (C:\\Windows\\appcompat\\pca):
               programs started from Explorer, with their full path - it outlives the program
  shimcache    AppCompatCache: executables Windows has come across, run or not
  jumplists    the jump lists behind every taskbar and Start entry: files and programs opened
  explorer     Explorer's Run box, typed paths, open/save and recent-documents lists
  clock        the system clock set back (to make traces look old)
  prefetcher   Prefetch switched off in the registry (not only by its service)
  fresh        Windows installed in the last few days (little history to check)
With admin rights:
  defender     Windows Defender's exclusions (a cheat folder excluded from scanning) and its
               detection history (a cheat it caught, even if deleted since)
  drive        every file name on drive C:, from the file table itself - nothing is hidden from
               it by being in an odd folder or marked hidden
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import datetime
import glob
import os
import re
import struct
import subprocess
import time

_UTF16 = re.compile(rb"(?:[\x20-\x7e]\x00){5,}")


def _strings16(blob: bytes) -> list:
    return [m.group().decode("utf-16-le", "ignore") for m in _UTF16.finditer(blob)]


def _no_window():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ----------------------------------------------------------------------------- no admin
def pca(scan):
    d = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "appcompat", "pca")
    names = []
    for f in ("PcaAppLaunchDic.txt", "PcaGeneralDb0.txt", "PcaGeneralDb1.txt"):
        try:
            with open(os.path.join(d, f), "r", encoding="cp1252", errors="ignore") as fh:
                for line in fh.read(8 << 20).splitlines():
                    path = line.split("|", 1)[0].strip()
                    if path:
                        names.append(path)
        except OSError:
            continue
    scan._names("program history (PCA)", names)


def shimcache(scan):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager\AppCompatCache") as k:
            blob = winreg.QueryValueEx(k, "AppCompatCache")[0]
    except OSError:
        scan.coverage["program history (ShimCache)"] = "not readable"
        return
    scan._names("program history (ShimCache)", [s for s in _strings16(blob) if "\\" in s])


def jumplists(scan, budget_s: float = 2.0):
    base = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Recent")
    names = []
    t_end = time.perf_counter() + budget_s
    for p in glob.glob(os.path.join(base, "AutomaticDestinations", "*")) + glob.glob(os.path.join(base, "CustomDestinations", "*")):
        if time.perf_counter() > t_end:
            break
        try:
            with open(p, "rb") as f:
                blob = f.read(4 << 20)
        except OSError:
            continue
        names += [s for s in _strings16(blob) if "\\" in s or s.lower().endswith((".exe", ".ahk", ".lua", ".gpc"))]
    scan._names("jump lists", names)


def explorer(scan):
    import winreg

    names = []

    def walk(key, depth):
        if depth > 4 or len(names) > 20000:
            return
        try:
            for j in range(winreg.QueryInfoKey(key)[1]):
                _, val, _typ = winreg.EnumValue(key, j)
                if isinstance(val, str):
                    names.append(val)
                elif isinstance(val, bytes):
                    names.extend(_strings16(val))
            for i in range(winreg.QueryInfoKey(key)[0]):
                with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                    walk(sub, depth + 1)
        except OSError:
            pass

    base = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
    for sub in ("RunMRU", "TypedPaths", r"ComDlg32\OpenSavePidlMRU", r"ComDlg32\LastVisitedPidlMRU", "RecentDocs",
                "WordWheelQuery"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + "\\" + sub) as k:
                walk(k, 0)
        except OSError:
            pass
    scan._names("Explorer history (Run box, typed paths, open/save, searches)", names)


def clock(scan, days: int = 14):
    """The system clock changed by hand (Kernel-General event 1 with a big jump). Small corrections
    are Windows keeping time; a dual-boot with Linux shows as whole hours. Back by a day or more,
    just before a check, is making traces look old."""
    q = (f"*[System[Provider[@Name='Microsoft-Windows-Kernel-General'] and (EventID=1) and "
         f"TimeCreated[timediff(@SystemTime) <= {days * 86400000}]]]")
    try:
        r = subprocess.run(["wevtutil", "qe", "System", f"/q:{q}", "/c:200", "/rd:true", "/f:xml"],
                           capture_output=True, text=True, timeout=15, creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        scan.coverage["clock changes"] = "not readable"
        return
    n = 0
    for ev in re.findall(r"<Event .*?</Event>", r.stdout, re.S):
        new = re.search(r"Name='NewTime'>([^<]+)<", ev)
        old = re.search(r"Name='OldTime'>([^<]+)<", ev)
        if not (new and old):
            continue
        n += 1
        try:
            tn = datetime.datetime.fromisoformat(new.group(1)[:26].rstrip("Z"))
            to = datetime.datetime.fromisoformat(old.group(1)[:26].rstrip("Z"))
        except ValueError:
            continue
        back = (to - tn).total_seconds()
        if back >= 86400:
            scan._hit("fail", f"the system clock was set back {back / 86400:.0f} day(s) in the last {days} days",
                      f"{to:%Y-%m-%d %H:%M} -> {tn:%Y-%m-%d %H:%M}")
        elif back >= 600 and abs(back - round(back / 3600) * 3600) > 300:
            scan._hit("warn", "the system clock was set back by hand recently", f"{to:%Y-%m-%d %H:%M} -> {tn:%Y-%m-%d %H:%M}")
    scan.coverage["clock changes"] = n


def prefetcher(scan):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters") as k:
            val = winreg.QueryValueEx(k, "EnablePrefetcher")[0]
            written = datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=winreg.QueryInfoKey(k)[2] // 10)
    except OSError:
        return
    if val == 0:
        if (datetime.datetime.utcnow() - written).days < 3:
            scan._hit("fail", "Prefetch was switched off in the registry in the last 3 days", "EnablePrefetcher = 0")
        else:
            scan._hit("warn", f"Prefetch is switched off in the registry (since {written:%Y-%m-%d}): less history to check",
                      "EnablePrefetcher = 0")


def fresh(scan):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            installed = datetime.datetime.utcfromtimestamp(int(winreg.QueryValueEx(k, "InstallDate")[0]))
    except (OSError, ValueError):
        return
    if (datetime.datetime.utcnow() - installed).days < 3:
        scan._hit("warn", f"Windows was installed on {installed:%Y-%m-%d}: there is little history to check",
                  "a fresh install")


# ----------------------------------------------------------------------------- admin
def defender(scan):
    import winreg

    base = r"SOFTWARE\Microsoft\Windows Defender\Exclusions"
    n = 0
    for kind in ("Paths", "Processes", "Extensions"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + kind) as k:
                for j in range(winreg.QueryInfoKey(k)[1]):
                    name = winreg.EnumValue(k, j)[0]
                    n += 1
                    m = scan_match(name)
                    if m and m[1] == "fail":
                        scan._hit("fail", f"{m[0]}: excluded from Windows Defender's scans", name)
                    elif kind == "Paths" and re.fullmatch(r"[a-z]:\\?", name.lower()):
                        scan._hit("warn", "a whole drive is excluded from Windows Defender's scans", name)
        except OSError:
            continue
    hist = r"C:\ProgramData\Microsoft\Windows Defender\Scans\History\Service\DetectionHistory"
    found = 0
    for p in glob.glob(os.path.join(hist, "**", "*"), recursive=True):
        if not os.path.isfile(p):
            continue
        found += 1
        try:
            with open(p, "rb") as f:
                strs = _strings16(f.read(1 << 20))
        except OSError:
            continue
        threat = next((s for s in strs if re.search(r"(gamehack|hacktool:.*(cheat|aim|game)|cheat|aimbot|trainer)", s, re.I)), None)
        if threat:
            scan._hit("fail", "Windows Defender caught a game cheat on this PC", threat)
        for s in strs:
            m = scan_match(os.path.basename(s)) if "\\" in s else None
            if m and m[1] == "fail":
                scan._hit("fail", f"{m[0]}: caught by Windows Defender", s)
    scan.coverage["Windows Defender"] = f"{n} exclusions, {found} detections"


def drive(scan, budget_s: float = 12.0):
    """Every file name on C:, read from the file table (FSCTL_ENUM_USN_DATA) - like the "Everything"
    search tool checkers use: no folder is too deep or too hidden for it."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wt.HANDLE
    k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
    k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, wt.LPVOID, wt.DWORD, wt.LPVOID, wt.DWORD,
                                    ctypes.POINTER(wt.DWORD), wt.LPVOID]
    h = k32.CreateFileW("\\\\.\\C:", 0x80000000, 0x3, None, 3, 0, None)
    if not h or h == wt.HANDLE(-1).value:
        scan.coverage["every file on C:"] = "not readable"
        return
    try:
        out = ctypes.create_string_buffer(1 << 20)
        got = wt.DWORD(0)
        start = 0
        n = 0
        mask = 0x0000FFFFFFFFFFFF                   # file reference -> its record number (drop the sequence)
        dirs = {}                                   # record -> (parent record, name), for every folder
        hits = []                                   # (what, name, parent record)
        t_end = time.perf_counter() + budget_s
        done = False
        while time.perf_counter() < t_end:
            req = struct.pack("<Qqq", start, 0, 0x7FFFFFFFFFFFFFFF)
            if not k32.DeviceIoControl(h, 0x000900B3, req, len(req), out, len(out), ctypes.byref(got), None) \
                    or got.value <= 8:
                done = True
                break
            raw = out.raw[:got.value]
            start = struct.unpack_from("<Q", raw, 0)[0]
            off = 8
            while off + 60 <= len(raw):
                rl = struct.unpack_from("<I", raw, off)[0]
                if rl < 60:
                    break
                ref, parent = struct.unpack_from("<QQ", raw, off + 8)
                attrs = struct.unpack_from("<I", raw, off + 52)[0]
                nl, no = struct.unpack_from("<HH", raw, off + 56)
                name = raw[off + no:off + no + nl].decode("utf-16-le", "ignore")
                n += 1
                if attrs & 0x10:
                    dirs[ref & mask] = (parent & mask, name)
                low = name.lower()
                if low.endswith(".gpc"):
                    hits.append(("a Cronus script (.gpc) on drive C:", name, parent & mask))
                else:
                    m = scan_match(low)
                    if m and m[1] == "fail":
                        hits.append((f"{m[0]}: a file on drive C:", name, parent & mask))
                off += rl

        def path_of(name, parent):
            # the folders above it, from the drive's own table (the root folder is record 5)
            parts, seen = [name], set()
            while parent in dirs and parent != 5 and parent not in seen:
                seen.add(parent)
                parent, nm = dirs[parent]
                parts.append(nm)
            return "C:\\" + "\\".join(reversed(parts))

        for what, name, parent in hits:
            scan._hit("fail", what, path_of(name, parent))
        scan.coverage["every file on C:" if done else "every file on C: (stopped at the time limit)"] = n
    finally:
        k32.CloseHandle(h)


# --------------------------------------------------------------------- unsigned programs
# What a program can do, read from the names of the Windows functions it uses (imported, or looked up
# by name at run time - both leave the name in the file). A renamed cheat keeps these.
CAPS = {
    "fakes input": (b"SendInput", b"mouse_event", b"keybd_event", b"interception_send", b"kmNet_mouse_move"),
    "reads the screen": (b"BitBlt", b"DuplicateOutput", b"AcquireNextFrame", b"CreateDXGIFactory", b"PrintWindow",
                         b"Direct3DCreate9", b"D3D11CreateDevice"),
    "reads other programs' memory": (b"ReadProcessMemory", b"NtReadVirtualMemory"),
    "writes into other programs": (b"WriteProcessMemory", b"NtWriteVirtualMemory", b"VirtualAllocEx",
                                   b"CreateRemoteThread", b"NtCreateThreadEx", b"QueueUserAPC"),
    "loads a driver": (b"NtLoadDriver", b"ZwLoadDriver"),
}
GAME_WORDS = re.compile(r"(^|[^a-z])(r6s?|siege|rainbow|cod|warzone|mw[23]|bo[67]|black[-_ ]?ops|modern[-_ ]?warfare)([^a-z]|$)")


def signed(path: str) -> bool:
    """Is the file's Authenticode signature valid? (WinVerifyTrust; cached revocation data only, no network)"""
    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    class FILEINFO(ctypes.Structure):
        _fields_ = [("cbStruct", wt.DWORD), ("pcwszFilePath", wt.LPCWSTR), ("hFile", wt.HANDLE), ("pgKnownSubject", ctypes.c_void_p)]

    class DATA(ctypes.Structure):
        _fields_ = [("cbStruct", wt.DWORD), ("pPolicyCallbackData", ctypes.c_void_p), ("pSIPClientData", ctypes.c_void_p),
                    ("dwUIChoice", wt.DWORD), ("fdwRevocationChecks", wt.DWORD), ("dwUnionChoice", wt.DWORD),
                    ("pFile", ctypes.POINTER(FILEINFO)), ("dwStateAction", wt.DWORD), ("hWVTStateData", wt.HANDLE),
                    ("pwszURLReference", wt.LPCWSTR), ("dwProvFlags", wt.DWORD), ("dwUIContext", wt.DWORD),
                    ("pSignatureSettings", ctypes.c_void_p)]

    action = GUID(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    fi = FILEINFO(ctypes.sizeof(FILEINFO), path, None, None)
    data = DATA()
    data.cbStruct = ctypes.sizeof(DATA)
    data.dwUIChoice, data.fdwRevocationChecks, data.dwUnionChoice = 2, 0, 1      # no UI, no revocation, a file
    data.pFile = ctypes.pointer(fi)
    data.dwProvFlags = 0x1000                                                       # cache only: never the network
    wv = ctypes.WinDLL("wintrust").WinVerifyTrust
    wv.argtypes = [wt.HWND, ctypes.POINTER(GUID), ctypes.POINTER(DATA)]
    wv.restype = ctypes.c_long
    try:
        return wv(wt.HWND(-1), ctypes.byref(action), ctypes.byref(data)) == 0
    finally:
        data.dwStateAction = 2                                                      # WTD_STATEACTION_CLOSE
        wv(wt.HWND(-1), ctypes.byref(action), ctypes.byref(data))


def capabilities(path: str, limit: int = 16 << 20) -> set:
    try:
        with open(path, "rb") as f:
            blob = f.read(limit)
    except OSError:
        return set()
    if not blob.startswith(b"MZ"):
        return set()
    return {cap for cap, names in CAPS.items() if any(n in blob for n in names)}


def unsigned(scan, budget_s: float = 8.0, max_files: int = 600):
    """Programs that are not signed by anyone, in the places downloaded and unpacked programs live
    (Desktop, Downloads, Documents, the temp folder), judged by what they can do rather than their
    name: one that can both read the screen and fake input is built like an aimbot; one that writes
    into other programs or loads drivers, like a cheat loader. Mostly notes - plenty of honest small
    tools are unsigned - but a fail when it is also named for Call of Duty or Siege."""
    home = os.path.expanduser("~")
    roots = [(os.path.join(home, d), 3) for d in ("Desktop", "Downloads", "Documents")]
    od = os.environ.get("OneDrive")
    if od:
        roots += [(os.path.join(od, d), 3) for d in ("Desktop", "Documents")]
    roots.append((os.path.expandvars("%TEMP%"), 2))
    t_end = time.perf_counter() + budget_s
    looked = unsigned_n = 0
    for root, depth in dict.fromkeys(roots):
        if not os.path.isdir(root):
            continue
        base = os.path.normpath(root).count(os.sep)
        for d, dirs, fs in os.walk(root):
            if os.path.normpath(d).count(os.sep) - base >= depth:
                dirs[:] = []
            dirs[:] = [x for x in dirs if x not in ("node_modules", ".git", "__pycache__", "site-packages") and not x.startswith(".")]
            for f in fs:
                if not f.lower().endswith((".exe", ".dll", ".sys")):
                    continue
                if time.perf_counter() > t_end or looked >= max_files:
                    scan.coverage["unsigned programs (stopped at the limit)"] = f"{looked} looked at, {unsigned_n} unsigned"
                    return
                path = os.path.join(d, f)
                looked += 1
                caps = capabilities(path)
                if not caps or signed(path):
                    continue
                unsigned_n += 1
                gamey = bool(GAME_WORDS.search(path.lower()))
                if "fakes input" in caps and "reads the screen" in caps:
                    what = "an unsigned program that can read the screen and fake mouse input (how an aimbot is built)"
                elif "writes into other programs" in caps or "loads a driver" in caps:
                    what = "an unsigned program that can write into other programs or load a driver (how a cheat loader is built)"
                elif "reads other programs' memory" in caps:
                    what = "an unsigned program that can read other programs' memory (how an ESP is built)"
                else:
                    continue
                scan._hit("fail" if gamey else "warn", what + (" - named for Call of Duty or Siege" if gamey else ""), path)
    scan.coverage["unsigned programs"] = f"{looked} looked at, {unsigned_n} unsigned with those abilities"


def scan_match(name):
    from .pccheck import match

    return match(name)
