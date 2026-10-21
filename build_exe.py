"""Build Core.exe: no console window, the crosshair icon.

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
NAME = "Core"
# what Explorer shows under Properties > Details
VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={n}, prodvers={n}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'Core - aim trainer and benchmark'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('ProductName', 'Core'),
      StringStruct('ProductVersion', '{v}'),
      StringStruct('OriginalFilename', 'Core.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


# The publisher's signing key (Ed25519 seed, hex). It lives here, outside the project folder and
# out of OneDrive and git: whoever holds it can make a build Core calls "official".
KEY = os.path.join(os.path.expanduser("~"), ".aimtuner-signing", "ed25519.key")


def sign(built: str) -> bool:
    """List every file of the build with its hash and sign the list (aimtuner/ac/seal.py checks it
    at start-up). False when there is no key, or it is not the one the code trusts."""
    import json

    from aimtuner.ac import seal

    if not os.path.exists(KEY):
        return False
    seed = bytes.fromhex(open(KEY, encoding="ascii").read().strip())
    if seal.ed_public(seed).hex() != seal.PUBLIC_KEY:
        print("The signing key does not match aimtuner/ac/seal.py's PUBLIC_KEY.")
        return False
    files = {rel: seal.file_hash(os.path.join(built, rel)) for rel in seal.manifest_files(built)}
    body = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with open(os.path.join(built, "_internal", seal.MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"files": files, "sig": seal.ed_sign(seed, body).hex()}, f)
    return True


SECRET = os.path.join(os.path.dirname(KEY), "seal.secret")


def write_secret() -> str | None:
    """The published build's sealing secret (ac/seal.py), written into the source for the length
    of the build only. Made once, kept beside the signing key."""
    if not os.path.exists(KEY):
        return None
    if not os.path.exists(SECRET):
        with open(SECRET, "w", encoding="ascii") as f:
            f.write(os.urandom(32).hex())
    sec = open(SECRET, encoding="ascii").read().strip()
    path = os.path.join(HERE, "aimtuner", "ac", "_secret.py")
    with open(path, "w", encoding="ascii") as f:
        f.write("# written by build_exe.py for one build, then removed - never commit\n")
        f.write(f"SECRET = bytes.fromhex({sec!r})\n")
    return path


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
    secret = write_secret()
    try:
        subprocess.check_call(args, cwd=WORK)
    finally:
        if secret and os.path.exists(secret):
            os.remove(secret)               # in the build only: never left in the (public) source
    built = os.path.join(dist, NAME)
    print("Signed: the published build." if sign(built) else
          f"Not signed (no key at {KEY}): this build will call itself unofficial, which is right for anyone's but the publisher's.")
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
        print(f"\n{exe} is in use - close Core and build again.")
        return 1
    files = [os.path.join(d, f) for d, _, fs in os.walk(internal) for f in fs]
    mb = (os.path.getsize(exe) + sum(os.path.getsize(f) for f in files)) / 1e6
    print(f"\nDone in {time.perf_counter() - t0:.0f} s: {exe} (+ _internal, {len(files)} files, {mb:.0f} MB together)")
    if "--no-pccheck" not in sys.argv:
        build_pccheck(ico, ver)
    return 0


def extract_tcl():
    """Unzip Python 3.14's Tcl/Tk script libraries (libtcl*.zip, libtk*.zip) next to the build, so
    the frozen PC check can set TCL_LIBRARY/TK_LIBRARY to real folders. Returns the folder holding
    tcl_library/ and tk_library/, or None (older Pythons keep these on disk already)."""
    import glob
    import zipfile

    tcl_dir = os.path.join(os.path.dirname(sys.executable), "tcl")
    zips = glob.glob(os.path.join(tcl_dir, "libtcl*.zip")) + glob.glob(os.path.join(tcl_dir, "libtk*.zip"))
    if not zips:
        return None
    out = os.path.join(WORK, "tcl_data")
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            zf.extractall(out)
    if os.path.isdir(os.path.join(out, "tcl_library")) and os.path.isdir(os.path.join(out, "tk_library")):
        return out
    return None


def build_pccheck(ico: str, ver: str) -> int:
    """The standalone PC check (Core PC Check.exe): aimtuner.ac only, with Tkinter, no game, no
    numpy/OpenGL/glfw. Lands in the pccheck/ folder beside Core, as its own small app."""
    from aimtuner import __version__

    out_dir = os.path.join(HERE, "pccheck")
    dist = os.path.join(WORK, "dist_pc")
    name = "Core PC Check"
    running = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}.exe"], capture_output=True, text=True).stdout
    if f"{name}.exe" in running:
        print(f"{name}.exe is running - close it to rebuild it.")
        return 1
    tcl_data = extract_tcl()                 # Python 3.14 hides the Tcl library in the DLL; ship it unzipped
    args = [
        sys.executable, "-m", "PyInstaller", os.path.join(HERE, "pccheck_main.py"),
        "--name", name, "--onedir", "--windowed", "--icon", ico, "--version-file", ver,
        "--noconfirm", "--clean", "--log-level", "WARN",
        "--distpath", dist, "--workpath", os.path.join(WORK, "work_pc"), "--specpath", WORK, "--paths", HERE,
        # everything the game needs and the check does not
        "--exclude-module", "numpy", "--exclude-module", "OpenGL", "--exclude-module", "glfw",
        "--exclude-module", "PIL", "--exclude-module", "unittest", "--exclude-module", "pydoc",
    ]
    if tcl_data:
        for sub in ("tcl_library", "tk_library"):
            args += ["--add-data", os.path.join(tcl_data, sub) + os.pathsep + os.path.join("_tcl_data", sub)]
    print(f"Building {name}.exe {__version__} (standalone PC check)...")
    t0 = time.perf_counter()
    secret = write_secret()
    try:
        subprocess.check_call(args, cwd=WORK)
    finally:
        if secret and os.path.exists(secret):
            os.remove(secret)
    built = os.path.join(dist, name)
    print("  signed." if sign(built) else "  not signed (no key): it will call itself unofficial.")
    try:
        if os.path.isdir(out_dir):
            if not os.path.exists(os.path.join(out_dir, "_internal", "base_library.zip")):
                print(f"\n{out_dir} is not a PyInstaller folder - not touching it.")
                return 1
            shutil.rmtree(out_dir)
        shutil.copytree(built, out_dir)
    except PermissionError:
        print(f"\n{out_dir} is in use.")
        return 1
    files = [os.path.join(d, f) for d, _, fs in os.walk(out_dir) for f in fs]
    mb = sum(os.path.getsize(f) for f in files) / 1e6
    print(f"Done in {time.perf_counter() - t0:.0f} s: {os.path.join(out_dir, name + '.exe')} ({len(files)} files, {mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    os.makedirs(WORK, exist_ok=True)
    sys.exit(main())
