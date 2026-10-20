"""Run every check script and say which passed.

    python tests/run_checks.py            all of them (about fifteen minutes)
    python tests/run_checks.py codkit lab only those whose name contains one of the words

None of them touches the real config or the real logs: the ones that start the
app do it offscreen on a scratch directory, with a synthetic pad. check_lab also
reads the connected controller for three seconds if there is one.
The full bot playthrough of every scenario is separate: python app.py --selftest
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    words = sys.argv[1:]
    scripts = sorted(glob.glob(os.path.join(HERE, "check_*.py")))
    if words:
        scripts = [s for s in scripts if any(w in os.path.basename(s) for w in words)]
    failed = []
    for path in scripts:
        name = os.path.basename(path)
        t0 = time.perf_counter()
        try:
            r = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=900,
                               encoding="utf-8", errors="replace")
            ok, out = r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            ok, out = False, "timed out"
        print(f"{'pass' if ok else 'FAIL'}  {name:<26} {time.perf_counter() - t0:5.0f} s", flush=True)
        if not ok:
            failed.append(name)
            for line in out.strip().splitlines()[-25:]:
                print("      " + line, flush=True)
    print(f"\n{len(scripts) - len(failed)} of {len(scripts)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
