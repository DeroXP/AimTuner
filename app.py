"""Core - controller aim benchmark.

    python app.py                 play (fullscreen at the desktop mode)
    python app.py --windowed      play in a window
    python app.py --bench         offscreen GPU throughput test at 4K
    python app.py --selftest      bots play every scenario offscreen (scratch logs)
    python analyze.py             read the logs and compare controllers
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import traceback

# (built as Core.exe, the folder the exe is in: aimtuner.log goes beside config.json and logs/)
ROOT = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))


def _size(text: str):
    w, h = text.lower().split("x")
    return int(w), int(h)


def main() -> int:
    ap = argparse.ArgumentParser(description="Core - controller aim benchmark")
    ap.add_argument("--windowed", action="store_true", help="run in a window instead of fullscreen")
    ap.add_argument("--offscreen", type=_size, metavar="WxH", help="render to an offscreen buffer (no window)")
    ap.add_argument("--bench", action="store_true", help="GPU throughput benchmark, then exit")
    ap.add_argument("--selftest", action="store_true", help="bots play every scenario, then exit")
    ap.add_argument("--only", help="--selftest: comma-separated scenario ids and/or tracks (core, cod, r6)")
    ap.add_argument("--mouse", action="store_true", help="--selftest: the bots play on mouse and keyboard")
    ap.add_argument("--repeats", type=int, default=2, help="runs per bot per scenario in --selftest")
    ap.add_argument("--shots", metavar="DIR", help="save screenshots here during --selftest")
    ap.add_argument("--duration", type=float, help="override the round length (seconds)")
    ap.add_argument("--cap", type=int, help="override the frame cap (0 = uncapped)")
    ap.add_argument("--exit-after", type=float, metavar="SEC", help="quit after SEC seconds and print frame timing")
    ap.add_argument("--logdir", help="write run logs here instead of ./logs")
    ap.add_argument("--config", help="use this config file instead of ./config.json")
    ap.add_argument("--pccheck", metavar="OUT", help=argparse.SUPPRESS)     # the PC check, with admin rights
    opts = ap.parse_args()
    opts.fast = False
    if opts.pccheck:
        from aimtuner.ac import pccheck

        return pccheck.elevated_main(opts.pccheck)

    if opts.bench and not opts.offscreen:
        opts.offscreen = (3840, 2160)
    if opts.selftest or opts.bench:
        # Never let a test touch the real config or the real run history.
        scratch = opts.logdir or tempfile.mkdtemp(prefix="aimtuner-test-")
        opts.logdir = scratch
        opts.config = opts.config or os.path.join(scratch, "config.json")
        opts.offscreen = opts.offscreen or (1920, 1080)
        opts.fast = True

    from aimtuner.app import App

    app = App(opts)
    if opts.bench:
        res = app.bench()
        app.close()
        w, h = opts.offscreen
        for q, r in res.items():
            print(f"{w}x{h} {q:>16}: {r['ms_per_frame']:.3f} ms/frame = {r['fps']} fps "
                  f"(GPU {r['gpu_ms']:.3f} ms)  ->  {r['fps'] / 240:.1f}x a 240 Hz display")
        return 0
    if opts.selftest:
        from aimtuner.bot import Autopilot, Bot

        app.bot = Bot(app)
        app.auto = Autopilot(app, opts.shots, opts.repeats, opts.only.split(",") if opts.only else None, kbm=opts.mouse)
        app.run()
        print(f"selftest: {len(app.log.runs)} runs logged to {opts.logdir}")
        for e in app.auto.errors:
            print("  ERROR", e)
        return 1 if app.auto.errors else 0
    app.run()
    return 0


if __name__ == "__main__":
    # Under pythonw there is no console: send output to a log file, and put a
    # crash where the user will actually see it.
    windowless = sys.stdout is None or sys.stderr is None
    if windowless:
        sys.stdout = sys.stderr = open(os.path.join(ROOT, "aimtuner.log"), "w", buffering=1, encoding="utf-8")
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        if windowless:
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(None, traceback.format_exc()[-1500:], "Core crashed", 0x10)
            except Exception:
                pass
        sys.exit(1)
