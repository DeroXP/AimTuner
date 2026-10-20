"""Mouse DPI: the one question asked of a Razer mouse (and how its answer is vetted), the ruler
test, and how the app keeps the setting in step - all without touching a real mouse.

    python tests/check_dpi.py [folder for screenshots]

(Nothing here talks to hardware. dpi.read() against the mouse on the desk was tried by hand
while this was written: a Razer DeathAdder V2 answered 800.)
"""
import os, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aimtuner import dpi, dpiscreen, mouse, screens

errors = []


def expect(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        errors.append(msg)


print("the question")
req = dpi.razer_request(0x3F)
x = 0
for b in req[2:88]:
    x ^= b
expect(len(req) == 90 and req[1] == 0x3F and (req[6], req[7]) == (0x04, 0x85) and req[5] == 7 and req[88] == x,
       "90 bytes: device-settings class, command 0x85, checksum over the payload")
expect(req[7] & 0x80 == 0x80 and not any(req[8:88]), "command ids with the top bit set are reads: this one asks, and carries no values to set")


def reply(status=2, cls=4, cmd=0x85, x=800, y=800):
    r = bytearray(90)
    r[0], r[6], r[7] = status, cls, cmd
    r[9], r[10], r[11], r[12] = x >> 8, x & 255, y >> 8, y & 255
    return bytes(r)


print("the answer is only believed when it is an answer")
expect(dpi.razer_parse(reply()) == (800, 800) and dpi.razer_parse(reply(x=1600, y=1200)) == (1600, 1200), "a good reply gives the DPI, each axis")
expect(dpi.razer_parse(reply(status=1)) is None, "'busy' is not an answer")
expect(dpi.razer_parse(reply(status=5)) is None, "'not supported' is not an answer")
expect(dpi.razer_parse(reply(cls=0x0F, cmd=0x02)) is None, "a reply to someone else's question (the maker's own software talks to the mouse too) is ignored")
expect(dpi.razer_parse(reply(x=0)) is None and dpi.razer_parse(reply(x=65000)) is None and dpi.razer_parse(b"\x02" * 10) is None,
       "nor is a number no mouse has, or a short read")
expect(dpi.vid_pid(r"\\?\HID#VID_1532&PID_0084&MI_00#7&c2d60fd&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}") == (0x1532, 0x0084)
       and dpi.vid_pid("") is None and dpi.vid_pid(None) is None, "the mouse in use is recognised by its USB ids")

print("the ruler test")
expect(round(dpi.measure(3150, 10.0)) == 800 and round(dpi.measure(-6299, 20.0)) == 800, "3,150 counts over 10 cm is 800 DPI, in either direction")
for got, want in ((812, 800), (771, 800), (1570, 1600), (3260, 3200), (737, 750), (2710, 2700), (26000, 26000)):
    if dpi.nearest_step(got) != want:
        expect(False, f"{got} should be taken as {want}, not {dpi.nearest_step(got)}")
expect(True, "a measurement within 6% of a common setting is taken as that setting; otherwise it is rounded to 50")

from aimtuner.app import App

scratch = tempfile.mkdtemp(prefix="aimtuner-dpi-")
shots = sys.argv[1] if len(sys.argv) > 1 else None
opts = types.SimpleNamespace(windowed=False, offscreen=(1920, 1080), bench=False, selftest=True, only=None,
                             repeats=1, shots=None, duration=3.0, cap=0, exit_after=None,
                             logdir=scratch, config=os.path.join(scratch, "config.json"), fast=True)
app = App(opts)
app.pad.use_virtual("bot")
app.mouse.use_virtual()


def frames(n=3):
    for _ in range(n):
        yield


def key(*names):
    app.keys.update(names)
    yield


def shot(name):
    if shots:
        yield from frames(3)
        app.pending_shot = os.path.join(shots, name + ".png")
        yield from frames(2)


def slide(counts, dy=0, steps=40):
    """Hold the left button, move `counts` across in small reports, let go."""
    now = time.perf_counter
    app.mouse.inject(now(), mouse.BUTTON, "M1", True)
    yield from frames(2)
    done = 0
    for i in range(steps):
        part = round(counts * (i + 1) / steps) - done
        done += part
        app.mouse.inject(now(), mouse.MOTION, part, round(dy / steps))
        yield
    app.mouse.inject(now(), mouse.BUTTON, "M1", False)
    yield from frames(3)


def script():
    cfg = app.cfg
    m = cfg["mouse"]
    yield from frames(5)
    asked = []
    app.dpi.ask = lambda prefer=None: asked.append(prefer) or True       # nothing in this check may reach a real mouse

    print("keeping the setting in step with the mouse")
    expect(m["dpi_auto"] is True and m["dpi"] == 800, "reading the DPI from the mouse is on by default")
    app.dpi._out = {"dpi": 1600, "dpi_y": 1600, "name": "Test mouse", "how": "razer"}
    yield from frames(2)
    expect(m["dpi"] == 1600 and "1600" in app._toast[0] and "Test mouse" in app._toast[0], f"the mouse says 1600: the setting follows, and says so ({app._toast[0]})")
    app.dpi._out = {"dpi": None, "name": "Some mouse", "why": "this mouse does not tell programs its DPI - measure it instead"}
    yield from frames(2)
    expect(m["dpi"] == 1600, "a mouse that cannot say leaves the setting alone")
    m["dpi_auto"] = False
    app.dpi._out = {"dpi": 3200, "dpi_y": 3200, "name": "Test mouse", "how": "razer"}
    yield from frames(2)
    expect(m["dpi"] == 1600, "with automatic reading off, an answer changes nothing")
    m["dpi_auto"], m["dpi"] = True, 800
    expect(not asked, "an offscreen run (a test, the benchmark) never asks a mouse anything")

    print("setting it by hand")
    st = screens.Settings(app, start="mouse.dpi")
    app.push(st)
    yield from key("right")
    expect(m["dpi"] == 850 and m["dpi_auto"] is False, "changing the DPI yourself turns automatic reading off, so it is not read back over you")
    yield from key("left")
    m["dpi_auto"] = True
    while st.rows[st.sel][0] != "@dpi":
        st._move(1)
    yield from key("enter")
    D = app.screens[-1]
    expect(isinstance(D, dpiscreen.DpiScreen), "Settings opens the DPI screen")

    print("the DPI screen")
    D.sel = D._rows().index("read")
    yield from key("enter")
    expect(len(asked) == 1, "'Ask the mouse now' asks")
    app.dpi._out = {"dpi": 800, "dpi_y": 800, "name": "Razer DeathAdder V2", "how": "razer"}
    yield from frames(3)
    yield from shot("dpi_read")
    expect(app.dpi.last and app.dpi.last["dpi"] == 800, "and shows the answer")
    yield from slide(40)
    expect(D.passes == [], "a click is not a slide")
    for counts in (3120, -3185, 3160):                  # about 800 DPI over 10 cm, by a human hand
        yield from slide(counts, dy=60)
    res = D.result()
    expect(len(D.passes) == 3 and res[1] == 800 and abs(res[0] - 800) < 12, f"three 10 cm slides: measured {res[0]:.0f}, taken as {res[1]}")
    yield from shot("dpi_measure")
    D.sel = D._rows().index("length")
    yield from key("right")
    expect(D.length == 15.0 and D.passes == [], "changing the distance starts the slides again")
    for counts in (9460, 9440, 9475):                   # 1600 DPI over 15 cm
        yield from slide(counts)
    expect(D.result()[1] == 1600, f"three 15 cm slides at 1,600: taken as {D.result()[1]}")
    D.sel = D._rows().index("use")
    yield from key("enter")
    expect(m["dpi"] == 1600 and m["dpi_auto"] is False, "'Use the measured value' sets it, and stops automatic reading from undoing it")
    yield from key("esc")
    expect(app.screens[-1] is st, "back to Settings")


class Auto:
    def __init__(self):
        self.gen = script()

    def step(self, now):
        try:
            next(self.gen)
        except StopIteration:
            app.quit()


app.auto = Auto()
app.run()
print("dpi:", "all passed" if not errors else f"FAILED {errors}")
sys.exit(1 if errors else 0)
