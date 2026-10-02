#!/usr/bin/env python3
"""Progress of a measurement: what is done, what runs now, how long the rest takes.

run_benchmarks.sh writes, in the results folder:
  plan.json    what the whole measurement contains (servers per family, load levels, repeats)
  timing.tsv   one line per finished measurement: end time, duration, pass, family, server,
               level, outcome, CSV file
and prints the panel (`panel`) before every measurement. `make status` prints it on demand.

The time left is estimated from the measurements already done, per kind of measurement: for HTTP,
duration = overhead + requests x seconds per request, fitted to the finished runs; for WebSocket,
the mean duration of each test family. Before enough runs are done, priors from the pilot are used
(67 s overhead, 800 requests per second, 85 s per WebSocket test), and the estimate says so.

Commands:
  panel  RESULTS_DIR [--now TEXT] [--pass N]   the progress panel
  status [RESULTS_DIR]                         the panel of the newest (or given) measurement
"""
import argparse
import csv
import datetime
import glob
import json
import os
import shutil
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PRIOR_OVERHEAD_S = 67.0
PRIOR_REQUESTS_PER_S = 800.0
PRIOR_WS_S = 85.0
HTTP = ("static", "dynamic")
WS = ("websocket", "concurrency", "payload")
COLS = ("end", "duration", "pass", "family", "server", "level", "outcome", "csv")


def read_plan(folder):
    try:
        with open(os.path.join(folder, "plan.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_timing(folder):
    rows = []
    try:
        with open(os.path.join(folder, "timing.tsv"), encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == len(COLS):
                    r = dict(zip(COLS, parts))
                    r["end"], r["duration"] = float(r["end"]), float(r["duration"])
                    rows.append(r)
    except OSError:
        pass
    return rows


def planned(plan):
    """[(family, level, count)] of every measurement in the plan."""
    out = []
    rep = plan["repeats"]
    for fam in HTTP:
        n = plan["servers"].get(fam, 0)
        out += [(fam, str(lv), n * rep) for lv in plan["http_levels"]] if n else []
    n = plan["servers"].get("websocket", 0)
    for fam in WS:
        steps = plan["ws_steps"].get(fam, 0)
        if n and steps:
            out.append((fam, "*", n * rep * steps))
    return out


def model(rows):
    """Duration estimators from the finished runs: (http(requests) -> s, ws(family) -> s, measured?)."""
    http = [(float(r["level"]), r["duration"]) for r in rows if r["family"] in HTTP and r["level"].isdigit()]
    if len({x for x, _ in http}) >= 2:
        n = len(http)
        mx = sum(x for x, _ in http) / n
        my = sum(y for _, y in http) / n
        sxx = sum((x - mx) ** 2 for x, _ in http)
        b = sum((x - mx) * (y - my) for x, y in http) / sxx
        a = my - b * mx
        if b <= 0 or a < 0:
            a, b = PRIOR_OVERHEAD_S, 1 / PRIOR_REQUESTS_PER_S
    elif http:
        b = 1 / PRIOR_REQUESTS_PER_S
        a = max(0.0, statistics.mean(y - x * b for x, y in http))
    else:
        a, b = PRIOR_OVERHEAD_S, 1 / PRIOR_REQUESTS_PER_S
    ws_means = {}
    for fam in WS:
        d = [r["duration"] for r in rows if r["family"] == fam]
        if d:
            ws_means[fam] = statistics.mean(d)
    ws_all = statistics.mean(ws_means.values()) if ws_means else PRIOR_WS_S
    measured = len(http) >= 3 or len([r for r in rows if r["family"] in WS]) >= 3
    return (lambda req: a + b * req), (lambda fam: ws_means.get(fam, ws_all)), measured


def remaining_seconds(plan, rows):
    http_est, ws_est, measured = model(rows)
    done = {}
    for r in rows:
        k = (r["family"], r["level"] if r["family"] in HTTP else "*")
        done[k] = done.get(k, 0) + 1
    left = 0.0
    for fam, level, count in planned(plan):
        n = max(0, count - done.get((fam, level), 0))
        left += n * (http_est(float(level)) if fam in HTTP else ws_est(fam))
    return left, measured


def human(seconds):
    s = int(round(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d}d {h}h{m:02d}m"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m" if m else f"{s}s"


def bar(fraction, width=28):
    full = int(round(fraction * width))
    return "█" * full + "░" * (width - full)


def last_result(row):
    """One line about the last finished measurement, from its CSV row."""
    if not row:
        return ""
    base = f"{row['server']} · {level_text(row['family'], row['level'])}"
    if row["outcome"] != "ok":
        return f"{base} · FAILED ({row['outcome']})"
    try:
        import csv_columns
        r = csv_columns.read(row["csv"])[1][-1]
    except (OSError, IndexError, csv.Error):
        return f"{base} · {row['duration']:.0f} s"
    parts = [base]
    rate = r.get("Requests/s") or r.get("Messages/s")
    if r.get("Execution Time (s)"):
        parts.append(f"load {float(r['Execution Time (s)']):.1f} s")
    if rate:
        parts.append(f"{float(rate):.0f} {'req' if r.get('Requests/s') else 'msg'}/s")
    if r.get("Container Energy (J)"):
        parts.append(f"container {float(r['Container Energy (J)']):.1f} J")
    if r.get("Host Energy (J)"):
        parts.append(f"machine {float(r['Host Energy (J)']):.0f} J")
    failed = r.get("Failed Requests") or r.get("Failed Messages")
    parts.append("ok" if failed in ("0", "", None) else f"{failed} failed requests")
    return " · ".join(parts)


def level_text(family, level):
    if family in HTTP and level.isdigit():
        return f"{int(level):,} requests"
    return level.replace("_", " ")


def panel(folder, now="", current_pass=None, running=True):
    plan = read_plan(folder)
    rows = read_timing(folder)
    if not plan:
        return f"No plan.json in {folder}: not a measurement started with a config."
    total = plan["total"]
    done = len(rows)
    failed = sum(r["outcome"] != "ok" for r in rows)
    left, measured = remaining_seconds(plan, rows)
    spent = sum(r["duration"] for r in rows)
    frac = done / total if total else 0
    finish = datetime.datetime.now() + datetime.timedelta(seconds=left)
    rule = "─" * 74
    pass_text = f"repeat {current_pass} of {plan['repeats']} · " if current_pass else ""
    number = done + 1 if running and done < total else done
    lines = [rule,
             f" {plan.get('name', '')} · {pass_text}measurement {number} of {total} "
             f"({100 * frac:.0f}% done)  {bar(frac)}"]
    if done < total:
        lines.append(f" Time    spent {human(spent)} · left ~{human(left)}"
                     f"{'' if measured else ' (rough, from the pilot)'} · done around "
                     f"{finish.strftime('%a %d %b %H:%M')}")
    if now:
        lines.append(f" Now     {now}")
    last = last_result(rows[-1] if rows else None)
    if last:
        lines.append(f" Last    {last}")
    health = [f"{failed} failed" if failed else "no failures"]
    try:
        import run_metadata
        t = run_metadata.cpu_package_temp_c()
        if t != "":
            health.append(f"CPU {t:.0f} °C")
    except Exception:  # noqa: BLE001 - the panel must never stop a measurement
        pass
    try:
        health.append(f"disk {shutil.disk_usage(folder).free / 1e9:.1f} GB free")
    except OSError:
        pass
    lines += [f" Health  {' · '.join(health)}", rule]
    return "\n".join(lines)


def state(folder):
    """'finished', 'running' or 'stopped' for a results folder."""
    meta_path = os.path.join(folder, "metadata.json")
    try:
        with open(meta_path, encoding="utf-8") as fh:
            if "finished_at_utc" in json.load(fh):
                return "finished"
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(folder, ".running"), encoding="utf-8") as fh:
            os.kill(int(fh.read().strip()), 0)
            return "running"
    except (OSError, ValueError):
        return "stopped"


def status(folder=None):
    if not folder:
        candidates = [os.path.dirname(p) for p in glob.glob(os.path.join("results", "*", "plan.json"))]
        if not candidates:
            return "No measurement with a config found in results/."
        folder = max(candidates, key=lambda f: os.path.getmtime(os.path.join(f, "plan.json")))
    st = state(folder)
    head = {"running": f"Running: {folder}",
            "finished": f"Finished: {folder}",
            "stopped": f"Stopped: {folder} (continue with: make resume RESUME={folder})"}[st]
    rows = read_timing(folder)
    current_pass = rows[-1]["pass"] if rows else None
    return head + "\n" + panel(folder, current_pass=current_pass, running=(st == "running"))


def main():
    ap = argparse.ArgumentParser(description="Progress of a measurement")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("panel")
    p.add_argument("folder")
    p.add_argument("--now", default="")
    p.add_argument("--pass", dest="current_pass", default=None)
    s = sub.add_parser("status")
    s.add_argument("folder", nargs="?")
    args = ap.parse_args()
    if args.cmd == "panel":
        print(panel(args.folder, args.now, args.current_pass))
    else:
        print(status(args.folder))


if __name__ == "__main__":
    main()
