"""Read the Core run log and say which controller is doing better.

    python analyze.py                 compare controllers on the current settings, every track
    python analyze.py --track cod     only the Call of Duty track (or: core, r6)
    python analyze.py --all           ignore the settings fingerprint (mixes difficulties!)
    python analyze.py --last 5        only each controller's 5 most recent runs per scenario
    python analyze.py --since 2026-10-05
    python analyze.py --runs          list every run instead of comparing
    python analyze.py --lab           show the Controller Lab (speed test) results
    python analyze.py --json          machine-readable output

A difference is only called "clear" when it is bigger than run-to-run noise
(Welch's t-test, p < 0.05, at least four runs per controller). Everything else
is reported as what it is: not enough evidence yet. Runs played in a guided
comparison also get the paired verdict (see aimtuner/duel.py), which is the one
to trust: it is immune to getting better as you play.
"""
from __future__ import annotations

import argparse
import json
import sys

from aimtuner import config, lab, stats, tracks


def _cell(c, dec, unit):
    if not c:
        return "-"
    s = stats.fmt(c["mean"], dec, unit)
    if c["sd"] is not None:
        s += f" +/- {c['sd']:.{dec}f}"
    return s


def _verdict_text(v, labels):
    if not v:
        return ""
    if v["winner"] is None:
        return "tie"
    pct = f"{v['pct']:.1f}%" if v["pct"] is not None else "?"
    p = f", p={v['p']:.3f}" if v["p"] is not None else ""
    return f"{labels[v['winner']]} better by {pct} ({stats.CONF_TEXT[v['conf']]}{p})"


def _track_of(r) -> str:
    return r.get("track") or "core"


def _filter(runs, args):
    if args.since:
        runs = [r for r in runs if r.get("time", "") >= args.since]
    if args.track:
        runs = [r for r in runs if _track_of(r) == args.track]
    if args.last:
        keep, seen = [], {}
        for r in reversed(runs):
            k = (r.get("controller"), r.get("scenario"), r.get("binds", {}).get("shoot"),
                 r.get("binds", {}).get("ads"), r.get("fingerprint"))
            seen[k] = seen.get(k, 0) + 1
            if seen[k] <= args.last:
                keep.append(r)
        runs = list(reversed(keep))
    return runs


def list_runs(runs):
    print(f"{'time':19}  {'controller':18} {'binds':9} {'track':5} {'scenario':18} {'fp':6}  primary")
    for r in runs:
        scn = tracks.BY_ID.get(r.get("scenario"))
        if not scn:
            continue
        key, label, unit, _, dec = next(m for m in scn.metrics if m[0] == scn.primary)
        b = r.get("binds", {})
        dev = r.get("device", {}).get("id") or "-"
        hz = r.get("input", {}).get("report_hz_max")
        print(f"{r.get('time', ''):19}  {r.get('controller', '?')[:18]:18} "
              f"{str(b.get('shoot')) + '/' + str(b.get('ads')):9} {_track_of(r):5} {scn.name[:18]:18} "
              f"{r.get('fingerprint', ''):6}  "
              f"{label}: {stats.fmt(r['stats'].get(key), dec, unit)}   [{dev}, {hz or '-'} Hz, "
              f"{r.get('display', {}).get('fps_avg', '-')} fps]")


def _warnings(runs, pool):
    out = []
    ids: dict = {}
    for r in pool:
        dev = r.get("device", {}).get("id")
        if dev:
            ids.setdefault(r.get("controller"), set()).add(dev)
    for name, devs in ids.items():
        if len(devs) > 1:
            out.append(f"'{name}' was logged with more than one USB id ({', '.join(sorted(devs))}) - "
                       "some runs may be under the wrong profile.")
    seen: dict = {}
    for name, devs in ids.items():
        for d in devs:
            seen.setdefault(d, set()).add(name)
    for d, names in seen.items():
        if len(names) > 1:
            out.append(f"USB id {d} appears under several profiles ({', '.join(sorted(names))}).")
    slow = [r for r in pool if (r.get("display", {}).get("fps_1pct_low") or 1e9) <
            0.8 * (r.get("display", {}).get("refresh_hz") or 0)]
    if slow:
        out.append(f"{len(slow)} run(s) had 1% low frame rate under 80% of the refresh rate - "
                   "frame hitches may have affected those scores.")
    paused = sum(1 for r in pool if r.get("pauses"))
    if paused:
        out.append(f"{paused} run(s) were paused mid-run.")
    other = len(runs) - len(pool)
    if other:
        out.append(f"{other} run(s) were made with different settings and are not included "
                   "(use --all to mix them in, or --runs to list them).")
    return out


def report(all_runs, cfg, args):
    doc = {"tracks": []}
    for track in tracks.TRACKS:
        runs = [r for r in all_runs if _track_of(r) == track.id]
        if not runs:
            continue
        fp = None if args.all else (args.fingerprint or config.fingerprint_for(cfg, track.id))
        note = None
        if fp and not any(r.get("fingerprint") == fp for r in runs):
            counts: dict = {}
            for r in runs:           # nothing matches the current settings: use the most-played ones
                counts[r.get("fingerprint")] = counts.get(r.get("fingerprint"), 0) + 1
            best = max(counts, key=counts.get)
            note = f"no runs with the current settings {fp}; showing the most-used settings {best} instead"
            fp = best
        pool = [r for r in runs if fp is None or r.get("fingerprint") == fp]
        sect = {"track": track.id, "name": track.name, "fingerprint": fp, "runs": len(pool), "note": note,
                "scenarios": [], "overall": None, "warnings": _warnings(runs, pool)}
        for scn in track.scenarios:
            tab = stats.metric_table(runs, scn.id, fp)
            if not tab["labels"]:
                continue
            sect["scenarios"].append({
                "id": scn.id, "name": scn.name, "group": getattr(scn, "group", ""),
                "labels": tab["labels"], "counts": tab["counts"],
                "rows": [{"key": r["key"], "label": r["label"], "unit": r["unit"], "better": r["better"],
                          "dec": r["dec"], "primary": r["primary"], "cells": r["cells"], "verdict": r["verdict"]}
                         for r in tab["rows"]],
            })
        sb = stats.scoreboard(runs, fp, track.id)
        if sb:
            sect["overall"] = {"labels": sb["labels"], "wins": sb["wins"], "clear": sb["clear"]}
        # guided comparisons (A-B-B-A blocks): the paired verdict for every pair of pads that played one
        sect["guided"] = []
        if fp:
            pads = sorted({r.get("controller") for r in pool if r.get("duel")})
            for i, a in enumerate(pads):
                for b in pads[i + 1:]:
                    sm = stats.duel_summary(stats.duel_cells(pool, track.id, fp, a, b))
                    if sm:
                        sm["rows"] = [dict(r, scenario=r["scenario"].id, name=r["scenario"].name) for r in sm["rows"]]
                        sect["guided"].append(dict(sm, labels=[a, b]))
        doc["tracks"].append(sect)

    if args.json:
        print(json.dumps(doc, indent=1))
        return
    if not doc["tracks"]:
        print("No runs found.")
        return
    for sect in doc["tracks"]:
        print("=" * 104)
        print(f"{sect['name'].upper()} TRACK - {sect['runs']} runs"
              + (f", settings fingerprint {sect['fingerprint']}" if sect["fingerprint"] else ", ALL settings mixed"))
        if sect["note"]:
            print(f"({sect['note']})")
        print("=" * 104)
        group = None
        for s in sect["scenarios"]:
            labels = s["labels"]
            if s["group"] and s["group"] != group:
                group = s["group"]
                print(f"\n--- {group} ---")
            print(f"\n{s['name'].upper()}")
            print(f"  {'':30}" + "".join(f"{(lb[:20] + ' (n=' + str(n) + ')'):>28}" for lb, n in zip(labels, s["counts"])))
            for r in s["rows"]:
                arrow = "higher=better" if r["better"] > 0 else "lower=better"
                name = ("* " if r["primary"] else "  ") + r["label"]
                line = f"{name:32}" + "".join(f"{_cell(c, r['dec'], r['unit']):>28}" for c in r["cells"])
                v = _verdict_text(r["verdict"], labels)
                print(line + (f"   {v}" if v else "") + f"   [{arrow}]")
        if sect["overall"]:
            o = sect["overall"]
            a, b = o["labels"]
            print("\nOVERALL (primary metric of each scenario played, marked * above)")
            print(f"  {a}: ahead in {o['wins'][0]} ({o['clear'][0]} clear)")
            print(f"  {b}: ahead in {o['wins'][1]} ({o['clear'][1]} clear)")
            if not any(o["clear"]):
                print("  No clear differences yet - keep alternating controllers until each has 4+ runs per scenario"
                      " (Guided comparison in the menu does the alternating for you).")
        elif sect["scenarios"]:
            print("\nOnly one controller has runs on this track so far - nothing to compare against yet.")
        else:
            print("\nNo runs found for these settings.")
        for g in sect["guided"]:
            a, b = g["labels"]
            lead = "level" if g["winner"] is None else f"{g['labels'][g['winner']]} ahead by {abs(g['mean']):.1f}%"
            words = {"clear": "a real difference", "leaning": "leaning, not settled", "noise": "inside run-to-run noise",
                     "few": "too little to say"}[g["conf"]]
            p = "" if g["p"] is None else (f", p={g['p']:.3f}" if g["p"] >= 0.001 else ", p<0.001")
            sd = "" if g["sd"] is None else f", spread +/-{g['sd']:.1f}%"
            print(f"\nGUIDED COMPARISON  {a} vs {b}  (pairs inside A-B-B-A blocks; warm-up passes left out)")
            print(f"  {lead} - {words}  [{g['blocks']} block(s), {g['cells']} paired results{sd}{p}]")
            if g["more"]:
                print("  a gap this size would need " + ("more than 20" if g["more"] > 20 else f"about {g['more']}")
                      + " more block(s) to settle")
            print(f"    {'':20} {a[:14]:>14} {b[:14]:>14}   {'difference':26} blocks won")
            for r in g["rows"]:
                side = "level" if r["rel"] == 0 else f"{g['labels'][0 if r['rel'] > 0 else 1]} +{abs(r['rel']):.1f}%"
                print(f"    {r['name']:20} {r['a']:>14.1f} {r['b']:>14.1f}   {side:26} {r['wins'][0]} - {r['wins'][1]}")
        if sect["warnings"]:
            print("\nNOTES")
            for w in sect["warnings"]:
                print("  - " + w)
        print()


def lab_report(log_dir, as_json):
    recs = lab.load(log_dir)
    if as_json:
        print(json.dumps(recs, indent=1))
        return
    if not recs:
        print("No Controller Lab results yet - run the Controller lab from the main menu.")
        return
    latest = lab.latest_by_controller(recs)
    names = list(latest)

    def g(r, *path):
        for p in path:
            r = (r or {}).get(p) if isinstance(r, dict) else None
        return r

    rows = [
        ("New data arrives (Hz)", ("report", "rate_hz")), ("Typical interval (us)", ("report", "interval_us")),
        ("Jitter (us)", ("report", "jitter_us")), ("Intervals on time (%)", ("report", "steady_pct")),
        ("Slowest 1% of intervals (us)", ("report", "p99_us")), ("Worst gap (ms)", ("report", "worst_ms")),
        ("Reports not timed individually (%)", ("report", "untimed_pct")),
        ("Aim stick rest offset (%)", ("aim_stick", "rest", "offset_pct")),
        ("Aim stick rest noise (%)", ("aim_stick", "rest", "noise_pct")),
        ("Aim stick lowest safe min deadzone", ("aim_stick", "rest", "min_deadzone")),
        ("Aim stick weakest direction (%)", ("aim_stick", "rim", "reach_min_pct")),
        ("Aim stick highest safe max deadzone", ("aim_stick", "rim", "max_deadzone")),
        ("Aim stick roundness error (%)", ("aim_stick", "rim", "circularity_err_pct")),
        ("Aim stick resolution (bit)", ("aim_stick", "resolution", "bits")),
        ("Aim stick snap-back avg (%)", ("aim_stick", "flick", "overshoot_avg_pct")),
        ("Aim stick snap-back worst (%)", ("aim_stick", "flick", "overshoot_max_pct")),
        ("Aim stick settle (ms)", ("aim_stick", "flick", "settle_avg_ms")),
        ("Aim stick fastest throw (ms)", ("aim_stick", "flick", "throw_fastest_ms")),
        ("Move stick rest offset (%)", ("move_stick", "rest", "offset_pct")),
        ("Move stick lowest safe min deadzone", ("move_stick", "rest", "min_deadzone")),
        ("Move stick weakest direction (%)", ("move_stick", "rim", "reach_min_pct")),
        ("Move stick highest safe max deadzone", ("move_stick", "rim", "max_deadzone")),
        ("Shoot taps per second", ("shoot", "per_second")), ("Shoot shortest press (ms)", ("shoot", "press_shortest_ms")),
        ("Shoot switch bounces", ("shoot", "bounces")), ("Aim->Shoot gap pressed together (ms)", ("chord", "mean_ms")),
        ("LT fastest pull (ms)", ("triggers", "LT", "pull_fastest_ms")),
        ("RT fastest pull (ms)", ("triggers", "RT", "pull_fastest_ms")),
    ]
    print(f"CONTROLLER LAB - latest result per controller ({len(recs)} tests on file)")
    print(f"{'':40}" + "".join(f"{n[:22]:>24}" for n in names))
    print(f"{'tested':40}" + "".join(f"{latest[n]['time'][:16]:>24}" for n in names))
    print(f"{'USB id':40}" + "".join(f"{(latest[n]['device'].get('id') or '-'):>24}" for n in names))
    for label, path in rows:
        vals = [g(latest[n], *path) for n in names]
        if any(v is not None for v in vals):
            print(f"{label:40}" + "".join(f"{('-' if v is None else v):>24}" for v in vals))
    print("\nThese are what software can see. True press-to-PC latency needs a hardware rig and is not measured.")


def main():
    ap = argparse.ArgumentParser(description="Compare controllers from the Core run log")
    ap.add_argument("--logdir", default=config.LOG_DIR)
    ap.add_argument("--config", default=config.CONFIG_PATH)
    ap.add_argument("--track", choices=[t.id for t in tracks.TRACKS], help="only this track")
    ap.add_argument("--all", action="store_true", help="include runs made with any settings")
    ap.add_argument("--fingerprint", help="compare runs with this settings fingerprint instead of the current one")
    ap.add_argument("--last", type=int, help="only the N most recent runs per controller and scenario")
    ap.add_argument("--since", help="only runs on or after this date/time (ISO, e.g. 2026-10-05)")
    ap.add_argument("--runs", action="store_true", help="list individual runs")
    ap.add_argument("--lab", action="store_true", help="show Controller Lab results")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if args.lab:
        return lab_report(args.logdir, args.json)
    runs = _filter(stats.load_runs(args.logdir), args)
    if args.runs:
        return list_runs(runs)
    report(runs, config.load(args.config), args)


if __name__ == "__main__":
    main()
