"""Mouse and keyboard in the menus: typed values in Settings, rows the pointer can point at,
footer hints as buttons. Drives the screens directly (offscreen: no real pointer)."""
import os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import config, screens
from aimtuner.app import App

scratch = tempfile.mkdtemp(prefix="aimtuner-ui-")
opts = types.SimpleNamespace(windowed=False, offscreen=(1280, 720), bench=False, selftest=False, only=None,
                             repeats=1, shots=None, duration=1.5, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
errors, checks = [], [0]


def expect(cond, msg):
    checks[0] += 1
    if not cond:
        errors.append(msg)
        print("FAIL:", msg)


def step(scr, nav=(), typed="", keys=()):
    app.typed, app.keys = list(typed), set(keys)
    scr.update(set(nav), time.perf_counter())
    app.typed, app.keys = [], set()


def drawn(scr):
    app.screens.append(scr) if app.screens[-1] is not scr else None
    app._draw(time.perf_counter())
    return app._hits if app._hits_of is scr else []


cfg = app.cfg
kbm = next(p["name"] for p in cfg["profiles"] if p.get("kind") == "kbm")
app.set_profile(kbm)

# ---- typing a sensitivity: digits start it, Enter sets it, kept in range
s = screens.Settings(app, "cod.sens_h")
app.push(s)
step(s, typed="1.37")
expect(s.text_mode and s.buf == "1.37", f"digits start typing ({s.buf!r})")
step(s, keys={"enter"}, nav={"accept"})
expect(cfg["cod"]["sens_h"] == 1.37, f"typed COD stick sensitivity set ({cfg['cod']['sens_h']})")
expect(not s.text_mode, "typing ends on Enter")
step(s, typed="99")
step(s, keys={"enter"}, nav={"accept"})
expect(cfg["cod"]["sens_h"] == 4.0, f"typed value kept inside the range ({cfg['cod']['sens_h']})")
step(s, typed="2")
step(s, keys={"backspace"})
step(s, typed="0.8")
step(s, keys={"esc"}, nav={"esc"})
expect(cfg["cod"]["sens_h"] == 4.0 and not s.text_mode, "Esc cancels the typed value")
# Enter on a number opens it empty for typing (mouse and keyboard)
step(s, keys={"enter"}, nav={"accept"})
expect(s.buf == "", "Enter opens a number for typing")
step(s, typed="0,95")
step(s, nav={"down"})
expect(cfg["cod"]["sens_h"] == 0.95 and not s.text_mode, "moving off the line keeps the typed value (comma ok)")

for key, typed, want in (("mouse.dpi", "1600", 1600), ("mouse.cm360", "34.5", 34.5),
                         ("cod.mouse_sens", "4.2", 4.2), ("r6.mouse_sens", "12", 12),
                         ("aim.sens_h", "275", 275), ("r6.sens_h", "40", 40), ("aim.deadzone", "8", 0.08)):
    s2 = screens.Settings(app, key)
    step(s2, typed=typed)
    step(s2, keys={"enter"}, nav={"accept"})
    got = config.get(cfg, key)
    expect(abs(got - want) < 1e-9, f"{key}: typed {typed} -> {got}, want {want}")
expect(cfg["mouse"]["dpi_auto"] is False, "typing the DPI turns automatic reading off, like changing it by hand")

# on a controller profile A does not open an empty number (no keyboard to type it with)
pad = next(p["name"] for p in cfg["profiles"] if p.get("kind") != "kbm")
app.set_profile(pad)
s3 = screens.Settings(app, "cod.sens_h")
step(s3, nav={"accept"})
expect(not s3.text_mode, "pad A on a number does not start typing")
app.set_profile(kbm)

# ---- the pointer: rows and the arrows either side of a value
s4 = screens.Settings(app, "cod.sens_h")
app.screens[-1:] = [s4]
hits = drawn(s4)
rows = [h for h in hits if isinstance(h[4], int)]
expect(len(rows) >= 10, f"settings rows are pointable ({len(rows)})")
expect(any(isinstance(h[4], tuple) and h[4][0] == "step" for h in hits), "value arrows are pointable")
expect(any(isinstance(h[4], tuple) and h[4] == ("nav", "back") for h in hits), "footer Esc  back is a button")
other = next(h[4] for h in rows if h[4] != s4.sel and s4.rows[h[4]][2] == "num")
expect(s4.point(other, False) is False and s4.sel == other, "pointing selects a line without pressing it")
before = config.get(cfg, s4.rows[other][0])
s4.point(("step", other, 1), True)
expect(config.get(cfg, s4.rows[other][0]) != before or before == s4.rows[other][3][1], "clicking › raises the value")
expect(s4.point(other, True) is True, "clicking a line presses A on it")
step(s4, nav={"accept"})
expect(s4.text_mode, "clicking a number opens it for typing")
step(s4, typed="5")
first = next(h[4] for h in rows if s4.rows[h[4]][2] != "#" and h[4] != other)
s4.point(first, True)
expect(not s4.text_mode, "clicking another line ends typing")

# ---- every main screen draws pointable rows
for scr in (screens.MainMenu(app), screens.Controllers(app), screens.KbmBinds(app), screens.Binds(app),
            screens.CodCalibrate(app)):
    app.screens[-1:] = [scr]
    hits = drawn(scr)
    expect(any(not isinstance(h[4], tuple) for h in hits), f"{type(scr).__name__}: rows are pointable")
mm = screens.MainMenu(app)
app.screens[-1:] = [mm]
drawn(mm)
expect(mm.point(3, False) is False and mm.sel == 3, "main menu: pointing selects")

app.screens[-1:] = [screens.MainMenu(app)]
print(f"{checks[0] - len(errors)} of {checks[0]} checks passed")
sys.exit(1 if errors else 0)
