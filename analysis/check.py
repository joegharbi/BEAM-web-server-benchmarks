"""Step 1: is a results folder complete and were the conditions the same in every run?

    ./srv/bin/python3 analysis/check.py results/<folder> [results/<folder> ...]
"""
import collections
import datetime
import json
import statistics
import sys

import data

# A run is "odd" when it is this far from the median of its 5 repeats (same container and load).
ODD = {"Execution Time (s)": 0.15, "Host Avg Power (W)": 0.10}
MIN_READINGS = 5     # fewer meter readings than this: the energy of a run is too coarse


def check(folder):
    rows = data.rows(folder)
    manifest = [json.loads(line) for line in open(f"{folder}/manifest.jsonl")]
    ws = data.is_websocket(rows[0])
    print(f"== {folder}: {len(rows)} rows, manifest {len(manifest)} entries, "
          f"{sum(not m['valid'] for m in manifest)} invalid")

    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["Container Name"], data.load(r))].append(r)
    per_group = collections.Counter(len(v) for v in groups.values())
    print(f"   {len({c for c, _ in groups})} containers x {len({l for _, l in groups})} loads; "
          f"rows per container and load: {dict(per_group)}")

    times = sorted(datetime.datetime.fromisoformat(r["Measured At (UTC)"].replace("Z", "+00:00")) for r in rows)
    gap = max((b - a).total_seconds() for a, b in zip(times, times[1:]))
    print(f"   {times[0]:%a %d %b %H:%M} -> {times[-1]:%a %d %b %H:%M} UTC, sessions "
          f"{sorted({r['Session'] for r in rows})}, longest pause {gap / 60:.1f} min")

    failed = sum(int(data.num(r, "Failed Messages" if ws else "Failed Requests")) for r in rows)
    limits = collections.Counter(r["Host CPU Speed Limit Min (MHz)"] for r in rows)
    throttled = sum(1 for r in rows if data.num(r, "Host Throttled (ms)") > 0)
    t_start = [data.num(r, "Host CPU Temp Start (C)") for r in rows]
    t_end = [data.num(r, "Host CPU Temp End (C)") for r in rows]
    coverage = [data.num(r, "Energy Window Coverage") for r in rows]
    over = sum(1 for r in rows if data.num(r, "Container Energy (J)") > data.num(r, "Host Energy (J)"))
    print(f"   failed {failed}; CPU limit {dict(limits)} MHz; throttled rows {throttled}; "
          f"temperature start {min(t_start):.0f}-{max(t_start):.0f} C, end max {max(t_end):.0f} C")
    print(f"   meter coverage {min(coverage):.3f}-{max(coverage):.3f}; ready "
          f"{dict(collections.Counter(r['Ready Check'] for r in rows))}; server > machine energy in {over} rows")
    print(f"   processes {dict(collections.Counter(r['Server Processes'] for r in rows))}")

    odd = []
    for (container, load), group in groups.items():
        for column, limit in ODD.items():
            values = [data.num(r, column) for r in group]
            mid = statistics.median(values)
            odd += [(container, load, r["Repeat"], column, v, mid) for r, v in zip(group, values)
                    if abs(v - mid) / mid > limit]
    print(f"   odd runs (duration >15% or machine power >10% from the median of their repeats): {len(odd)}")
    for o in sorted(odd, key=str):
        print(f"      {o[0]:38} {str(o[1]):>14} repeat {o[2]}: {o[3]} {o[4]:.1f} (median {o[5]:.1f})")

    print("   duration and meter readings per load (median, fewest):")
    for load in sorted({l for _, l in groups}, key=lambda l: (str(type(l)), l)):
        group = [r for r in rows if data.load(r) == load]
        secs = statistics.median(data.num(r, "Execution Time (s)") for r in group)
        samples = [data.num(r, "Energy Samples") for r in group]
        flag = "  <- too few readings to trust" if min(samples) < MIN_READINGS else ""
        print(f"      {str(load):>14}: {secs:6.1f} s, {statistics.median(samples):.0f} readings (fewest {min(samples):.0f}){flag}")

if __name__ == "__main__":
    for folder in sys.argv[1:]:
        check(folder)
