"""Build AimTuner.exe: no console window, the crosshair icon.

    python build_exe.py            (or double-click build_exe.bat)

The exe lands in this folder, beside config.json and logs/, and uses them - the same
settings and history as `python app.py`. It carries its own copy of Python and every
library in the `_internal` folder next to it (the two go together), so nothing needs
installing to run it. Build it again after the code changes.

Why a folder and not one self-contained file: a one-file exe unpacks everything to the
temp folder on every launch - measured here, 3.1 s to open against 1.1 s.
PyInstaller's working files go to the temp folder, not into OneDrive.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(tempfile.gettempdir(), "aimtuner-build")
NAME = "AimTuner"
# what Explorer shows under Properties > Details
VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={n}, prodvers={n}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'AimTuner - aim trainer and benchmark'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('ProductName', 'AimTuner'),
      StringStruct('ProductVersion', '{v}'),
      StringStruct('OriginalFilename', 'AimTuner.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
    sys.path.insert(0, HERE)
    from aimtuner import __version__, icon

    os.makedirs(os.path.join(HERE, "assets"), exist_ok=True)
    ico = os.path.join(HERE, "assets", "icon.ico")
    icon.save_ico(ico)
    dist = os.path.join(WORK, "dist")
    nums = tuple(int(x) for x in __version__.split(".")[:3]) + (0,)
    ver = os.path.join(WORK, "version.txt")
    with open(ver, "w", encoding="utf-8") as f:
        f.write(VERSION_INFO.format(n=nums, v=__version__))
    args = [
        sys.executable, "-m", "PyInstaller", os.path.join(HERE, "app.py"),
        "--name", NAME, "--onedir", "--windowed", "--icon", ico, "--version-file", ver,
        "--noconfirm", "--clean", "--log-level", "WARN",
        "--distpath", dist, "--workpath", os.path.join(WORK, "work"), "--specpath", WORK,
        "--paths", HERE,
        "--collect-binaries", "glfw",           # glfw3.dll sits inside the glfw package
        "--exclude-module", "tkinter", "--exclude-module", "unittest", "--exclude-module", "pydoc",
    ]
    running = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {NAME}.exe"], capture_output=True, text=True).stdout
    if f"{NAME}.exe" in running:
        print(f"{NAME}.exe is running - close it and build again.")
        return 1
    print(f"Building {NAME}.exe {__version__} (about half a minute)...")
    t0 = time.perf_counter()
    subprocess.check_call(args, cwd=WORK)
    built = os.path.join(dist, NAME)
    exe, internal = os.path.join(HERE, NAME + ".exe"), os.path.join(HERE, "_internal")
    try:
        # the old support folder goes first - but only if it is one of ours
        if os.path.isdir(internal):
            if not os.path.exists(os.path.join(internal, "base_library.zip")):
                print(f"\n{internal} exists and is not a PyInstaller folder - not touching it.")
                return 1
            shutil.rmtree(internal)
        shutil.copytree(os.path.join(built, "_internal"), internal)
        shutil.copy2(os.path.join(built, NAME + ".exe"), exe)
    except PermissionError:
        print(f"\n{exe} is in use - close AimTuner and build again.")
        return 1
    files = [os.path.join(d, f) for d, _, fs in os.walk(internal) for f in fs]
    mb = (os.path.getsize(exe) + sum(os.path.getsize(f) for f in files)) / 1e6
    print(f"\nDone in {time.perf_counter() - t0:.0f} s: {exe} (+ _internal, {len(files)} files, {mb:.0f} MB together)")
    return 0


if __name__ == "__main__":
    os.makedirs(WORK, exist_ok=True)
    sys.exit(main())
