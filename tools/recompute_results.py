#!/usr/bin/env python3
"""Recalculate the container energy of a finished measurement from its kept raw logs.

For when the energy calculation in scaphandre_energy.py is corrected after a measurement:
every row of every measurement CSV that has a raw Scaphandre log (and its .window.json)
gets Container Energy (J) and Container Avg Power (W) recalculated with the current code.
Nothing is measured again, and the raw logs are not changed.

  * the CSVs as they were are kept in <results>/superseded/<UTC time>/<family>/
  * the per-server and per-family summaries are rebuilt (aggregate_repeats.py)
  * metadata.json gets a "recomputed" entry: when, why, framework version, rows changed

Usage:
  python3 tools/recompute_results.py results/2026-10-02_181305 --reason "thread entries counted twice"
  python3 tools/recompute_results.py results/<folder> --dry-run      # show the changes only
"""
import argparse
import csv
import datetime
import glob
import json
import logging
import os
import shutil
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import scaphandre_energy as se  # noqa: E402

FAMILIES = ("static", "dynamic", "websocket")
CHANGED = ("Container Energy (J)", "Container Avg Power (W)")


def measurement_csvs(folder):
    return sorted(f for f in glob.glob(os.path.join(folder, "*.csv"))
                  if not f.endswith("_summary.csv") and os.path.basename(f) not in ("summary.csv", "failures.csv"))


def recompute_csv(results_dir, path):
    """(header, new rows, changed rows, rows without a raw log, largest old/new ratio)."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header, rows = reader.fieldnames, list(reader)
    changed = missing = 0
    ratios = []
    for r in rows:
        raw = r.get("Raw Log", "")
        raw_path = os.path.join(results_dir, raw) if raw else ""
        if not raw_path or not os.path.isfile(raw_path) or not os.path.isfile(se.window_path(raw_path)):
            missing += 1
            continue
        e = se.recompute(raw_path)
        old = float(r.get(CHANGED[0]) or 0)
        if e["energy_j"] > 0 and old > 0:
            ratios.append(old / e["energy_j"])
        r[CHANGED[0]] = e["energy_j"]
        r[CHANGED[1]] = e["avg_power_w"]
        changed += 1
    return header, rows, changed, missing, ratios


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results_dir")
    ap.add_argument("--reason", default="energy calculation corrected")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.disable(logging.WARNING)
    rd = args.results_dir.rstrip("/")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    total = missing_total = 0
    all_ratios = []
    for fam in FAMILIES:
        files = measurement_csvs(os.path.join(rd, fam))
        for path in files:
            header, rows, changed, missing, ratios = recompute_csv(rd, path)
            total += changed
            missing_total += missing
            all_ratios += ratios
            if args.dry_run or not changed:
                continue
            keep = os.path.join(rd, "superseded", stamp, fam)
            os.makedirs(keep, exist_ok=True)
            shutil.copy2(path, keep)
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=header)
                w.writeheader()
                w.writerows(rows)
        if files and not args.dry_run:
            agg = os.path.join(TOOLS, "aggregate_repeats.py")
            for path in files:
                subprocess.run([sys.executable, agg, path], check=True, stdout=subprocess.DEVNULL)
            subprocess.run([sys.executable, agg, *files, "--output", os.path.join(rd, fam, "summary.csv")],
                           check=True, stdout=subprocess.DEVNULL)
    all_ratios.sort()
    med = all_ratios[len(all_ratios) // 2] if all_ratios else 0
    print(f"{rd}: {total} rows recalculated, {missing_total} without a raw log; "
          f"old/new container energy: median {med:.2f}"
          + (f", range {all_ratios[0]:.2f}-{all_ratios[-1]:.2f}" if all_ratios else ""))
    if args.dry_run or not total:
        return
    meta_path = os.path.join(rd, "metadata.json")
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        meta = {}
    version = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=TOOLS).stdout.strip()
    meta.setdefault("recomputed", []).append({
        "time_utc": stamp, "reason": args.reason, "framework_version": version,
        "rows": total, "old_over_new_median": round(med, 3),
        "superseded_csvs": os.path.join("superseded", stamp)})
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


if __name__ == "__main__":
    main()
