"""Entry point for the standalone PC check (Core PC Check.exe). It uses only aimtuner.ac - none of
the game, numpy, OpenGL or glfw - so it builds small.

    python pccheck_main.py
"""
from __future__ import annotations

import os
import sys
import traceback


def _find_tcl():
    # Python 3.14 keeps the Tcl/Tk script library inside the DLL (zipfs), which a frozen app cannot
    # reach. build_exe.py bundles the extracted library under _internal/_tcl_data; point Tcl at it.
    if not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    tcl = os.path.join(base, "_tcl_data", "tcl_library")
    tk = os.path.join(base, "_tcl_data", "tk_library")
    if os.path.isdir(tcl):
        os.environ["TCL_LIBRARY"] = tcl       # override PyInstaller's own guess (the parent folder)
    if os.path.isdir(tk):
        os.environ["TK_LIBRARY"] = tk


def _elevated_arg():
    # the admin copy re-launches this exe with --pccheck OUT (see ac/pccheck.run_elevated)
    for i, a in enumerate(sys.argv):
        if a == "--pccheck" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main() -> int:
    _find_tcl()
    out = _elevated_arg()
    if out:
        from aimtuner.ac import pccheck

        return pccheck.elevated_main(out)
    from aimtuner.ac import pccheck_app

    return pccheck_app.main()


if __name__ == "__main__":
    windowless = sys.stdout is None or sys.stderr is None
    if windowless:
        log = os.path.join(os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__)),
                           "core_pccheck.log")
        sys.stdout = sys.stderr = open(log, "w", buffering=1, encoding="utf-8")
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        if windowless:
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(None, traceback.format_exc()[-1500:], "Core PC Check crashed", 0x10)
            except Exception:
                pass
        sys.exit(1)
