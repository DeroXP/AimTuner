"""The PC check (aimtuner/ac/pccheck.py) and the holes found in the anti-cheat and patched.

Every source is pointed at a scratch folder holding planted traces, so the test never depends on
what is (or is not) on the computer running it - except one timing check of the real quick scan.

    python tests/check_pccheck.py
"""
import json, os, shutil, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import ac
from aimtuner.ac import pccheck, seal, system

errors, checks = [], [0]


def expect(cond, msg):
    checks[0] += 1
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        errors.append(msg)


tmp = tempfile.mkdtemp(prefix="aimtuner-pc-")

# ------------------------------------------------------------------ names
for name, want in (("aimbot_v2.exe", "fail"), ("Aimmy.exe", "fail"), ("norecoil.ahk", "fail"), ("zen studio", "fail"),
                   ("kmbox_net.dll", "fail"), ("hwid_spoofer.exe", "fail"), ("prefetch_cleaner.exe", "fail"),
                   ("dll_injector.exe", "fail"), ("AutoHotkey64.exe", "warn"), ("reWASDEngine.exe", "warn")):
    m = pccheck.match(name)
    expect(m is not None and m[1] == want, f"'{name}' -> {m}")
for name, want in (("r6_esp.exe", "fail"), ("warzone-hack-loader.exe", "fail"), ("silent_aim.dll", "fail"),
                   ("cod_unlock_all.exe", "fail"), ("ricochet_bypass.sys", "fail"), ("battleye-bypass.exe", "fail"),
                   ("mw3_external.exe", "fail"), ("chams.dll", "fail"), ("r6 no recoil macro.ahk", "fail"),
                   ("siege_cheat.zip", "fail")):
    m = pccheck.match(name)
    expect(m is not None and m[1] == want, f"Call of Duty / Siege: '{name}' -> {m}")
# names found on a real gaming PC that must not match (Siege's own files, a COD community client, AimTuner's
# own scenarios and captures, a hobby microcontroller)
for name in ("Easy Anti-Cheat", "Ubisoft Anti-Cheat Service", "cheatsheet.pdf", "discord.exe", "obs64.exe",
             "steam.exe", "AimTuner.exe", "aim_trainer.exe", "Logitech G HUB",
             "datapc64_r6_menuworld_playgo.forge", "RainbowSix_BE.exe", "BEService.exe", "r6_who_deactivates.py",
             "plutonium_unlockall_warning.lua", "20261017-215856-r6_recoil_dump.npz", "cod_track_jump", "r6kit.py",
             "esp32_flash.py", "Call of Duty.exe", "maybe-bypass.txt", "chamsfield.txt"):
    expect(pccheck.match(name) is None, f"'{name}' is not a cheat")


# ------------------------------------------------------------------ labels: what kind of cheat, for which game
for what, where, want in (
        ("a Call of Duty or Siege cheat: a file", r"C:\x\r6_esp.exe", ("wallhack / ESP", "Siege")),
        ("a Call of Duty or Siege cheat: a file", r"C:\x\warzone-hack-loader.exe", ("cheat loader / injector", "Call of Duty")),
        ("an anti-cheat bypass: a file", r"C:\x\ricochet_bypass.sys", ("anti-cheat bypass", "Call of Duty")),
        ("a Cronus script (.gpc)", r"C:\x\R6 Siege\ash_antirecoil.gpc", ("controller-adapter script (recoil / aim)", "Siege")),
        ("an AutoHotkey script that moves the mouse while firing", r"C:\x\mw3 recoil.ahk", ("recoil macro", "Call of Duty")),
        ("the parts of an AI aimbot in one Python (bettercam, ultralytics, pydirectinput)", r"C:\Py\site-packages",
         ("aimbot (AI, reads the screen)", "game unknown")),
        ("an Arduino board is connected as a mouse", r"\\?\HID#VID_2341&PID_8036", ("input device / adapter", "any game")),
        ("the System event log was cleared in the last 30 days", "System log", ("hiding traces", "any game")),
        ("something named as a cheat: a file", r"C:\x\my_cheats.dll", ("unknown cheat", "game unknown")),
        ("a Call of Duty unlock tool: a file", r"C:\x\unlockall.zip", ("unlock tool", "Call of Duty")),
        ("reWASD (remapper with macros) in program history (MuiCache)", "reWASD", ("macro tool", "any game"))):
    got = pccheck.classify(what, where)
    expect(got == want, f"labelled {want}: {got}")
sc = pccheck.Scan()
sc._hit("fail", "a Call of Duty or Siege cheat: a file", r"C:\x\r6_esp.exe")
expect(sc.findings[0][3:] == ("wallhack / ESP", "Siege"), "every finding carries its kind and game")

# ------------------------------------------------------------------ planted traces
def planted(files: dict, env: dict):
    """A fake user profile with these files, and the environment pointing at it."""
    home = os.path.join(tmp, "home%d" % len(os.listdir(tmp)))
    for rel, text in files.items():
        p = os.path.join(home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    old = {k: os.environ.get(k) for k in list(env) + ["USERPROFILE", "OneDrive"]}
    os.environ.update({k: v.replace("~", home) for k, v in env.items()})
    os.environ["USERPROFILE"] = home
    os.environ.pop("OneDrive", None)
    real = os.path.expanduser
    os.path.expanduser = lambda p: p.replace("~", home, 1) if p.startswith("~") else real(p)

    def undo():
        os.path.expanduser = real
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return home, undo


RECOIL_AHK = "~LButton::\nwhile GetKeyState(\"LButton\", \"P\") {\n DllCall(\"mouse_event\", uint, 1, int, 0, int, 3)\n Sleep 10\n}\n"
PLAIN_AHK = "^j::\nSend, My First Script\nreturn\n"
RECOIL_LUA = "function OnEvent(e, a)\n if IsMouseButtonPressed(1) then MoveMouseRelative(0, 4) end\nend\n"
# (Files named like cheats are never written to disk: deleting them afterwards would leave their
# names in the drive's change journal, which the admin check reads - the test would fail this PC's
# own check. They are added to the folder listings the scan sees instead.)
home, undo = planted({"Downloads/rec.ahk": RECOIL_AHK, "Documents/hello.ahk": PLAIN_AHK, "Desktop/g.lua": RECOIL_LUA},
                     {"TEMP": "~/Temp", "APPDATA": "~/AppData/Roaming", "LOCALAPPDATA": "~/AppData/Local"})
real_walk = os.walk
VIRTUAL = {os.path.join(home, "Desktop"): ["aimbot.exe"], os.path.join(home, "Documents"): ["zen.gpc"],
           os.path.join(home, "Temp"): ["loader_triggerbot.exe"]}


def walk(top, *a, **kw):
    for d, dirs, fs in real_walk(top, *a, **kw):
        yield d, dirs, fs + VIRTUAL.get(os.path.normpath(d), [])


try:
    os.makedirs(os.path.join(home, "Temp"), exist_ok=True)
    pccheck.os.walk = walk
    sc = pccheck.Scan()
    sc.files()
    sc.temp()
    pccheck.os.walk = real_walk
    got = {(w, os.path.basename(p)) for lvl, w, p, *_ in sc.findings if lvl == "fail"}
    expect(any(p == "aimbot.exe" for _, p in got), "a cheat-named file on the Desktop fails it")
    expect(any(p == "rec.ahk" for _, p in got), "an AutoHotkey script pulling the mouse down while firing fails it")
    expect(not any(p == "hello.ahk" for _, p in got), "an ordinary AutoHotkey script does not")
    expect(any(p == "zen.gpc" for _, p in got), "a Cronus script fails it")
    expect(any(p == "g.lua" for _, p in got), "a Lua mouse script pulling down while firing fails it")
    expect(any(p == "loader_triggerbot.exe" for _, p in got), "a cheat unpacked into the temp folder fails it")
finally:
    pccheck.os.walk = real_walk
    undo()

home, undo = planted({"AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt":
                      "cd games\nfsutil usn deletejournal /d C:\nwevtutil cl System\n",
                      "AppData/Local/LGHUB/scripts/1/script.lua": RECOIL_LUA,
                      "AppData/Local/Programs/Python/Python312/Lib/site-packages/bettercam/__init__.py": "",
                      "AppData/Local/Programs/Python/Python312/Lib/site-packages/ultralytics/__init__.py": "",
                      "AppData/Local/Programs/Python/Python312/Lib/site-packages/pydirectinput/__init__.py": "",
                      "AppData/Local/Programs/Python/Python311/Lib/site-packages/ultralytics/__init__.py": ""},
                     {"APPDATA": "~/AppData/Roaming", "LOCALAPPDATA": "~/AppData/Local"})
try:
    sc = pccheck.Scan()
    sc.shell_history()
    sc.ghub()
    sc.python_kits()
    whats = [w for lvl, w, p, *_ in sc.findings if lvl == "fail"]
    expect(sum("wipes traces" in w for w in whats) == 2, f"cleaning commands in the PowerShell history fail it ({whats})")
    expect(any("G HUB" in w for w in whats), "a G HUB recoil script fails it")
    expect(sum("AI aimbot" in w for w in whats) == 1, "screen-grab + detection + mouse control in one Python fails it (and "
                                                     "detection alone, in another, does not)")
finally:
    undo()

# unsigned programs, judged by what they can do (made-up programs with innocent names: the Windows
# functions they name are what matters)
from aimtuner.ac import pcsources

home, undo = planted({}, {"TEMP": "~/Temp", "APPDATA": "~/AppData/Roaming", "LOCALAPPDATA": "~/AppData/Local"})
progs = {"Downloads/helper.exe": b"MZ..user32.dll SendInput gdi32.dll BitBlt ...",
         "Downloads/cod/helper2.exe": b"MZ..kernel32.dll OpenProcess WriteProcessMemory CreateRemoteThread ...",
         "Documents/tool/viewer.exe": b"MZ..kernel32.dll ReadProcessMemory ...",
         "Desktop/calc.exe": b"MZ..user32.dll MessageBoxW ...",
         "Desktop/readme.exe": b"not a program at all, just named .exe SendInput BitBlt"}
for rel, data in progs.items():
    p = os.path.join(home, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
os.makedirs(os.path.join(home, "Temp"), exist_ok=True)
real_signed = pcsources.signed
try:
    pcsources.signed = lambda path: os.path.basename(path) == "viewer.exe"     # this one is signed
    sc = pccheck.Scan()
    pcsources.unsigned(sc)
    got = {os.path.basename(f[2]): f for f in sc.findings}
    expect("helper.exe" in got and got["helper.exe"][0] == "warn" and got["helper.exe"][3] == "possible aimbot (by what it can do)",
           f"unsigned + reads the screen + fakes input: a note, 'possible aimbot' ({got.get('helper.exe')})")
    expect("helper2.exe" in got and got["helper2.exe"][0] == "fail" and got["helper2.exe"][4] == "Call of Duty",
           f"unsigned + writes into other programs + in a Call of Duty folder: fails ({got.get('helper2.exe')})")
    expect("viewer.exe" not in got, "a signed program is left alone, whatever it can do")
    expect("calc.exe" not in got and "readme.exe" not in got, "a program without those abilities, or not a program, is left alone")
finally:
    pcsources.signed = real_signed
    undo()
expect(pcsources.signed(sys.executable), "Windows' signature check knows a signed program (Python itself)")

# history wiped
sc = pccheck.Scan()
sc.coverage.update({"program history (UserAssist)": 0, "recent files": 120})
sc.history_emptied()
expect(sc.findings and sc.findings[0][0] == "fail", "program history wiped on a PC in daily use fails it")

# ------------------------------------------------------------------ the real machine: quick enough to run every start
t0 = time.perf_counter()
q = pccheck.Scan(quick=True).run()
dt = time.perf_counter() - t0
expect(dt < 1.0, f"the quick check (every start, every run) takes {dt * 1000:.0f} ms")
t0 = time.perf_counter()
full = pccheck.Scan().run()
expect(time.perf_counter() - t0 < 15.0, f"the full check takes {time.perf_counter() - t0:.1f} s (detect.ac: under 60)")
expect(set(full["coverage"]) > set(q["coverage"]), "the full check looks at more than the quick one")

# ------------------------------------------------------------------ the sealed verdict
path = os.path.join(tmp, "pccheck.json")
expect(pccheck.status(path, "0.8.0")[0] == "needed", "no record: a check is needed")
pccheck.save(path, {"v": 1, "passed": True, "findings": [], "coverage": {}}, "0.8.0")
expect(pccheck.status(path, "0.8.0")[0] == "passed", "a passed check reads back")
expect(pccheck.status(path, "0.9.0")[0] == "needed", "an update needs a new check")
body = json.load(open(path))
body["machine"] = "someone-elses-pc"
json.dump(body, open(path, "w"))
expect(pccheck.status(path, "0.8.0")[0] == "tampered", "a record copied from another PC (machine changed) is refused")
pccheck.save(path, {"v": 1, "passed": False, "findings": [["fail", "x", "y"]], "coverage": {}}, "0.8.0")
body = json.load(open(path))
body["passed"] = True
json.dump(body, open(path, "w"))
expect(pccheck.status(path, "0.8.0")[0] == "tampered", "a failed check edited to 'passed' is refused")
# the admin copy's result file: a forged one is refused
out = os.path.join(tmp, "admin.json")
json.dump({"v": 1, "passed": True, "findings": [], "coverage": {}, "mac_child": "0" * 32}, open(out, "w"))
expect(pccheck.read_elevated(out) is None, "a forged result from the admin check is refused")
pccheck.elevated_main(out)
r = pccheck.read_elevated(out)
expect(r is not None and "coverage" in r, "a genuine result from the admin check reads back")

# ------------------------------------------------------------------ runs: the PC check's verdict, and the patched holes
from aimtuner import mouse


class FakeMouse:
    virtual = False

    def provenance(self):
        return mouse.new_provenance()


def judge(pc_status, snap=None, quick=None):
    app = types.SimpleNamespace(mouse=FakeMouse(), offscreen=False, cfg={"anticheat": {"hide_capture": True}, "kbm": {}},
                                build_state="official", hwnd=None, pc_status=pc_status)
    snap = snap or {"tools": [], "foreign_modules": [], "debugger": False, "interception": False, "capture_hooks": []}
    real_snap, real_scan = system.snapshot, pccheck.Scan
    system.snapshot = lambda: snap
    if quick is not None:
        pccheck.Scan = lambda **kw: types.SimpleNamespace(run=lambda: quick)
    try:
        g = ac.RunGuard(app, types.SimpleNamespace(kbm=False))
        g.virtual = False
        rec = {"binds": {}}
        g.judge(rec, None)
        return rec["integrity"]
    finally:
        system.snapshot, pccheck.Scan = real_snap, real_scan


i = judge("failed")
expect(any("failed its PC check" in d for d in i["doubts"]) and i["status"] != "verified", "a failed PC check: runs unverified")
i = judge("needed")
expect(any("not had its PC check" in d for d in i["doubts"]), "no PC check yet: runs unverified")
i = judge("passed", quick={"passed": False, "findings": [["fail", "an aimbot in running programs", "x.exe"]]})
expect(i["status"] == "flagged" and "found during the run" in i["flags"][0],
       "hole 2 (a cheat started after the check passed): the quick check at the end of the run catches it")
i = judge("passed", snap={"tools": [], "foreign_modules": [], "debugger": False, "interception": False,
                          "capture_hooks": [r"C:\Program Files\obs-studio\data\obs-plugins\win-capture\graphics-hook64.dll"]},
          quick={"passed": True, "findings": []})
expect(any("game-capture hook" in d for d in i["doubts"]),
       "hole 3 (a frame-copying hook gets round capture protection): the run is unverified")
expect(system.capture_hooks([r"C:\x\graphics-hook64.dll", r"C:\Windows\System32\kernel32.dll"]) == [r"C:\x\graphics-hook64.dll"],
       "game-capture hooks are recognised")
# hole 1: the sealing keys are in the public source
code = seal.share_code({"run_id": "r", "stats": {"score": 1}, "integrity": {"status": "verified"}})
body, err = seal.read_code(code)
expect(err is None and body["u"] is True, "hole 1: a code made by a copy run from source says it is not the published build")
if seal.OFFICIAL:
    expect(seal._key(b"share") != seal._key(b"share", "dev"), "the published build seals with its own secret")
else:
    expect(seal._key(b"share", "official") is None, "a copy run from source has no published secret to seal with")
expect(not os.path.exists(os.path.join(ROOT, "aimtuner", "ac", "_secret.py")) or seal.OFFICIAL,
       "the published secret is never left in the source folder")

shutil.rmtree(tmp, ignore_errors=True)
print(f"{checks[0] - len(errors)} of {checks[0]} checks passed")
sys.exit(1 if errors else 0)
