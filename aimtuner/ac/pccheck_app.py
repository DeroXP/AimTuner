"""The PC check as a program of its own - a small window - so it can be used without Core.

    Core PC Check.exe          (built by build_exe.py --pccheck)
    python -m aimtuner.ac.pccheck_app

It is the same check as Core's (pccheck.py): the same sources, the same verdict, the same sealed
record tied to this PC and this version. Passing here also lets Core on this PC skip its own check
(it writes the same pccheck.json), and a passed check here can be shown to others with a result
code and an HTML report.

No numpy, no OpenGL, no game - only Python's own Tkinter. If Tkinter is missing it runs on the
console instead.
"""
from __future__ import annotations

import html
import os
import threading
import time
import webbrowser

from . import pccheck, seal


def _version():
    try:
        from .. import __version__

        return __version__
    except Exception:
        return "0"


APP_VERSION = _version()


def _state_path() -> str:
    """The pccheck.json this check shares with Core: beside a Core install if this exe sits next to
    one, else beside this exe."""
    import sys

    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    # beside this exe, in its parent (the PC check may sit in a folder next to Core.exe), or a Core
    # install in the home folder: wherever there is already a Core, share its verdict
    for base in (here, os.path.dirname(here), os.path.join(os.path.expanduser("~"), "Core")):
        if os.path.exists(os.path.join(base, "config.json")) or os.path.exists(os.path.join(base, "Core.exe")):
            return os.path.join(base, "pccheck.json")
    return os.path.join(here, "pccheck.json")


# --------------------------------------------------------------------------- the report
def write_report(result: dict, path: str) -> str:
    code = seal.pc_code(result)
    passed = result.get("passed")
    rows = []
    for f in result.get("findings") or []:
        lvl, what, where = f[0], f[1], f[2]
        kind, game = (f[3], f[4]) if len(f) >= 5 else ("", "")
        tag = "FAIL" if lvl == "fail" else "note"
        label = html.escape(kind.upper()) + ("" if game in ("", "any game", None) else f"  ·  {html.escape(game)}")
        rows.append(f'<tr class="{lvl}"><td class="tag">{tag}</td><td class="lab">{label}</td>'
                    f'<td class="what">{html.escape(what)}</td><td class="where">{html.escape(str(where))}</td></tr>')
    cov = "".join(f"<li>{html.escape(k)}: {html.escape(str(v))}</li>" for k, v in (result.get("coverage") or {}).items())
    verdict = "PASSED" if passed else "FAILED"
    colour = "#38d66b" if passed else "#ff5a5a"
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Core PC check</title>
<style>
 body{{background:#0d0f12;color:#d8dde4;font:15px/1.5 system-ui,Segoe UI,sans-serif;max-width:980px;margin:40px auto;padding:0 20px}}
 h1{{font-size:46px;margin:0;color:{colour}}}
 .sub{{color:#8a929c;margin:6px 0 30px}}
 table{{border-collapse:collapse;width:100%;margin:20px 0}}
 td{{padding:9px 10px;border-top:1px solid #1d2127;vertical-align:top}}
 .tag{{width:48px;color:#ff5a5a}} tr.warn .tag{{color:#f2b23a}}
 .lab{{color:#ff5a5a;font-weight:600;white-space:nowrap}} tr.warn .lab{{color:#f2b23a}}
 .what{{color:#d8dde4}} .where{{color:#6d757f;font-size:13px;word-break:break-all}}
 code{{display:block;background:#15181d;border:1px solid #262b32;border-radius:6px;padding:12px;margin:12px 0;
       word-break:break-all;color:#9fd0ff;font:13px ui-monospace,Consolas,monospace}}
 .foot{{color:#6d757f;font-size:13px;margin-top:30px}} ul{{color:#8a929c;font-size:13px;columns:2}}
</style></head><body>
<h1>{verdict}</h1>
<div class="sub">Core PC check {html.escape(str(result.get('app')))} &middot; {html.escape(str(result.get('time')))}
 &middot; {'with' if result.get('elevated') else 'without'} admin rights &middot; {result.get('seconds')} s</div>
{"<p>Nothing was found.</p>" if not rows else "<table>" + "".join(rows) + "</table>"}
<p>Result code (paste into Core &rarr; Check a share code, or the PC check app, to see a genuine, unedited result):</p>
<code>{html.escape(code)}</code>
<div class="foot"><b>What was looked at</b><ul>{cov}</ul>
<p>This is a look at the PC for cheats and for traces of hiding them. It can be fooled by a cheat it has no
trace of, and anyone who takes it apart can get round it. Nothing left this PC; only what matched the list of
cheat names was kept.</p></div></body></html>"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(doc)
    os.replace(tmp, path)
    return code


# --------------------------------------------------------------------------- running the check
def run_check(admin_wanted: bool, progress, done):
    """Run the check (optionally in an admin copy), save it, and call done(result, code, report_path)."""
    def work():
        res = None
        if admin_wanted:
            out = os.path.join(os.environ.get("TEMP", "."), "core_pccheck_admin.json")
            progress("asking Windows for admin rights…", 0.0)
            if pccheck.run_elevated(out):
                res = pccheck.read_elevated(out)
        if res is None:
            res = pccheck.Scan(elevated=False, progress=progress).run()
        saved = pccheck.save(_state_path(), res, APP_VERSION)
        report = os.path.join(os.path.dirname(_state_path()), "Core PC check.html")
        try:
            code = write_report(saved, report)
        except OSError:
            code, report = seal.pc_code(saved), None
        done(saved, code, report)

    threading.Thread(target=work, name="pccheck", daemon=True).start()


# --------------------------------------------------------------------------- the window
def main() -> int:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return console_main()

    root = tk.Tk()
    root.title("Core PC Check")
    root.configure(bg="#0d0f12")
    root.geometry("760x620")
    try:
        root.call("tk", "scaling", 1.3)
    except tk.TclError:
        pass
    fg, dim, accent = "#d8dde4", "#8a929c", "#6cc6ff"

    def lbl(parent, text, color=fg, font=("Segoe UI", 11), **kw):
        return tk.Label(parent, text=text, fg=color, bg="#0d0f12", font=font, justify="left", anchor="w", **kw)

    lbl(root, "Core PC Check", accent, ("Segoe UI Semibold", 22)).pack(anchor="w", padx=24, pady=(20, 2))
    lbl(root, "A look at this PC for cheats and for traces of hiding them.", dim).pack(anchor="w", padx=24)

    body = tk.Frame(root, bg="#0d0f12")
    body.pack(fill="both", expand=True, padx=24, pady=16)
    state = {"result": None, "code": None, "report": None}

    def show_intro():
        for w in body.winfo_children():
            w.destroy()
        lbl(body, "It passes or it fails. On another machine you can show the result with a code and a saved "
                  "report.\n\nWindows will ask for admin rights so the check can see more (deleted files, the full "
                  "program history, every file on the drive). It also works without them.\n\nNothing leaves this "
                  "PC, and nothing is kept except what matches the list of cheat names. It takes a few seconds.",
            dim, wraplength=680).pack(anchor="w", pady=(0, 20))
        btns = tk.Frame(body, bg="#0d0f12")
        btns.pack(anchor="w")
        tk.Button(btns, text="Check (with admin rights)", command=lambda: begin(True), bg="#1d2a36", fg=fg,
                  activebackground="#24323f", relief="flat", padx=16, pady=8, font=("Segoe UI", 11)).pack(side="left")
        tk.Button(btns, text="Check without admin", command=lambda: begin(False), bg="#15181d", fg=dim,
                  activebackground="#1d2127", relief="flat", padx=16, pady=8, font=("Segoe UI", 11)).pack(side="left", padx=10)

    def begin(admin):
        for w in body.winfo_children():
            w.destroy()
        step = lbl(body, "Checking…", fg, ("Segoe UI", 13))
        step.pack(anchor="w", pady=(10, 14))
        bar = ttk.Progressbar(body, length=460, mode="determinate", maximum=1.0)
        bar.pack(anchor="w")

        def progress(text, frac):
            root.after(0, lambda: (step.config(text="Checking: " + text), bar.config(value=frac)))

        def done(result, code, report):
            state.update(result=result, code=code, report=report)
            root.after(0, show_result)

        run_check(admin, progress, done)

    def show_result():
        for w in body.winfo_children():
            w.destroy()
        r = state["result"]
        passed = r.get("passed")
        head = tk.Frame(body, bg="#0d0f12")
        head.pack(anchor="w", fill="x")
        lbl(head, "PASSED" if passed else "FAILED", "#38d66b" if passed else "#ff5a5a",
            ("Segoe UI Semibold", 26)).pack(side="left")
        lbl(head, f"   {'with' if r.get('elevated') else 'without'} admin  ·  {r.get('seconds')} s", dim).pack(side="left", pady=12)

        canvas = tk.Canvas(body, bg="#0d0f12", highlightthickness=0, height=300)
        sb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg="#0d0f12")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True, pady=10)
        sb.pack(side="right", fill="y")
        finds = r.get("findings") or []
        if not finds:
            lbl(inner, "Nothing found.", "#38d66b", ("Segoe UI", 13)).pack(anchor="w")
        for f in finds:
            lvl, what, where = f[0], f[1], f[2]
            kind, game = (f[3], f[4]) if len(f) >= 5 else ("", "")
            row = tk.Frame(inner, bg="#0d0f12")
            row.pack(anchor="w", fill="x", pady=3)
            c = "#ff5a5a" if lvl == "fail" else "#f2b23a"
            lbl(row, "FAIL" if lvl == "fail" else "note", c, ("Segoe UI", 10), width=6).pack(side="left")
            txt = (kind.upper() + ("" if game in ("", "any game", None) else f"  ·  {game}") + "   ") if kind else ""
            lbl(row, txt, c, ("Segoe UI Semibold", 10)).pack(side="left")
            lbl(row, what, fg, ("Segoe UI", 10)).pack(side="left")
        for f in finds:
            if f[0] == "fail":
                lbl(inner, "", dim).pack()
                where = f[2]
                lbl(inner, "   " + (where if len(str(where)) < 100 else "…" + str(where)[-98:]), "#6d757f",
                    ("Consolas", 9)).pack(anchor="w")

        foot = tk.Frame(body, bg="#0d0f12")
        foot.pack(anchor="w", fill="x", pady=(12, 0))
        ent = tk.Entry(foot, font=("Consolas", 9), width=64, bg="#15181d", fg="#9fd0ff", relief="flat")
        ent.insert(0, state["code"] or "")
        ent.config(state="readonly")
        ent.pack(side="left", pady=4)

        def copy():
            root.clipboard_clear()
            root.clipboard_append(state["code"] or "")
        tk.Button(foot, text="Copy code", command=copy, bg="#15181d", fg=dim, relief="flat", padx=10).pack(side="left", padx=8)
        if state["report"]:
            tk.Button(foot, text="Open report", command=lambda: webbrowser.open(state["report"]), bg="#15181d",
                      fg=dim, relief="flat", padx=10).pack(side="left")
        tk.Button(foot, text="Check again", command=show_intro, bg="#15181d", fg=dim, relief="flat", padx=10).pack(side="left", padx=8)

    show_intro()
    root.mainloop()
    return 0


def console_main() -> int:
    print("Core PC Check", APP_VERSION)
    admin = input("Check with admin rights? Windows will ask. [Y/n] ").strip().lower() != "n"
    res = None
    if admin:
        out = os.path.join(os.environ.get("TEMP", "."), "core_pccheck_admin.json")
        if pccheck.run_elevated(out):
            res = pccheck.read_elevated(out)
    if res is None:
        res = pccheck.Scan(elevated=False, progress=lambda t, f: print(f"  {int(f * 100):3d}%  {t}")).run()
    saved = pccheck.save(_state_path(), res, APP_VERSION)
    print("\n" + ("PASSED" if res["passed"] else "FAILED"), f"({'with' if res['elevated'] else 'without'} admin, {res['seconds']} s)")
    for f in res["findings"]:
        kind = f[3] if len(f) >= 5 else ""
        print(f"  {'FAIL' if f[0] == 'fail' else 'note'}  {kind:<34} {f[1]}")
        if f[0] == "fail":
            print(f"        {f[2]}")
    report = os.path.join(os.path.dirname(_state_path()), "Core PC check.html")
    try:
        code = write_report(saved, report)
        print(f"\nResult code: {code}\nReport saved: {report}")
    except OSError:
        print(f"\nResult code: {seal.pc_code(saved)}")
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
