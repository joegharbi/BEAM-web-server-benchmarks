"""Independent check of the server energy: is anything of the server missed, counted twice, or wrong?

For every run it re-reads Scaphandre's raw log and the run's window file (the server's process and thread IDs
saved at measurement time, the load start and end) and checks, over the load window only:

  1. recomputed  the server energy, recomputed here from the raw log with its own small code, equals the CSV value
  2. no doubles  over the load, a process's energy equals the total of its own thread entries (Linux reports a
                 process's CPU time as the sum of its threads), so counting the process alone and skipping the
                 threads is right; counting both would double it. Single 0.5 s samples differ a little (timing)
  3. no misses   every other entry of a server program (beam.smp, erl_child_setup, inet_gethost, epmd, java) in the
                 window: with a command line = a separate process that was NOT counted (a real miss); with an empty
                 command line = a thread that started during the load (already inside its process, not a miss)
  4. no gaps     the server's process is in Scaphandre's list in every sample of the load (it lists the top programs)
  5. fits        the server energy is below the platform energy

    ./srv/bin/python3 analysis/check_attribution.py results/<folder> [results/<folder> ...]
"""
import gzip
import json
import os
import statistics
import sys

import data

SERVER_PROGRAMS = {"beam.smp", "erl_child_setup", "inet_gethost", "epmd", "java"}


def load_entries(path):
    raw = (gzip.open(path, "rt") if path.endswith(".gz") else open(path)).read()
    try:
        return json.loads(raw)
    except ValueError:
        return [json.loads(line) for line in raw.splitlines() if line.strip()]


def window_file(raw_path):
    for ext in (".json.gz", ".json"):
        if raw_path.endswith(ext):
            return raw_path[: -len(ext)] + ".window.json"
    return raw_path + ".window.json"


def check_run(folder, row):
    raw = os.path.join(folder, row["Raw Log"])
    win_path = window_file(raw)
    if not (os.path.exists(raw) and os.path.exists(win_path)):
        return None
    win = json.load(open(win_path))
    t0, t1 = win["load_start_epoch"], win["load_end_epoch"]
    procs, threads, pids = set(win["process_ids"]), set(win["thread_ids"]), set(win["pids"])
    r = dict(name=row["Container Name"], energy=0.0, thread_energy=0.0, missed=0.0, missed_by={}, late_threads=0.0,
             host=0.0, samples=0, gaps=0)
    prev = None
    for en in load_entries(raw):
        t = en["host"]["timestamp"]
        if prev is not None:
            a, b = max(prev, t0), min(t, t1)
            if b > a:
                dt = b - a
                r["samples"] += 1
                cons = en["consumers"]
                proc_w = [c["consumption"] / 1e6 for c in cons if c["pid"] in procs]
                if not proc_w:
                    r["gaps"] += 1
                r["energy"] += sum(proc_w) * dt
                r["thread_energy"] += sum(c["consumption"] for c in cons if c["pid"] in threads) / 1e6 * dt
                r["host"] += en["host"]["consumption"] / 1e6 * dt
                for c in cons:
                    exe = os.path.basename(c.get("exe") or "")
                    if exe in SERVER_PROGRAMS and c["pid"] not in pids and c["consumption"] > 0:
                        e = c["consumption"] / 1e6 * dt
                        if c.get("cmdline"):
                            r["missed"] += e
                            r["missed_by"][exe] = r["missed_by"].get(exe, 0.0) + e
                        else:
                            r["late_threads"] += e
        prev = t
    r["csv_energy"] = data.num(row, "Container Energy (J)")
    return r


def report(folder):
    runs = [x for x in (check_run(folder, row) for row in data.rows(folder)) if x]
    print(f"\n=== {folder}: {len(runs)} runs checked")
    diff = [abs(x["energy"] - x["csv_energy"]) / max(x["csv_energy"], 1e-9) * 100 for x in runs]
    print(f"1 recomputed  server energy vs CSV: median difference {statistics.median(diff):.3f}%, largest {max(diff):.2f}%")
    ratio = [x["energy"] / x["thread_energy"] for x in runs if x["thread_energy"] > 0]
    print(f"2 no doubles  process energy / its threads' energy over the load: median {statistics.median(ratio):.3f}, "
          f"range {min(ratio):.3f}-{max(ratio):.3f} ({len(ratio)} runs; 1 = the process holds its threads)")
    by = {}
    for x in runs:
        for k, v in x["missed_by"].items():
            by[k] = by.get(k, 0.0) + v
    late = [x for x in runs if x["late_threads"] > 0]
    print(f"3 no misses   separate server processes not counted: {sum(1 for x in runs if x['missed'] > 0)} runs"
          f"{' (' + ', '.join(f'{k} {v:.1f} J' for k, v in by.items()) + ')' if by else ''}; threads started during the "
          f"load (inside their process): {len(late)} runs ({', '.join(sorted({x['name'] for x in late})[:2]) or 'none'})")
    gaps = sum(x["gaps"] for x in runs)
    print(f"4 no gaps     load samples without the server's process: {gaps} of {sum(x['samples'] for x in runs)}")
    over = sum(1 for x in runs if x["energy"] > x["host"])
    share = [x["energy"] / x["host"] * 100 for x in runs if x["host"]]
    print(f"5 fits        runs with server > platform: {over}; server share of the platform: "
          f"median {statistics.median(share):.1f}%, largest {max(share):.1f}%")


if __name__ == "__main__":
    for folder in sys.argv[1:]:
        report(folder)
