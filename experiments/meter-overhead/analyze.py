#!/usr/bin/env python3
"""Results of the meter-overhead experiment (README.md): per server and deployment, the laptop's power and energy
over the load with Scaphandre on vs off (both from the same psys counter, read by psys_sampler.py), the difference
with its bootstrap 95% range and the exact Mann-Whitney p, and Scaphandre's real CPU time (kernel counter) next to
what Scaphandre reports about itself.

    ./srv/bin/python3 experiments/meter-overhead/analyze.py results/<folder>      (writes <folder>/meter_overhead.json)
"""
import collections
import csv
import glob
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "analysis"))
import check_attribution  # noqa: E402
import data  # noqa: E402
import stats  # noqa: E402

LABEL = {"ec-erlang-pure-29-1-1": "Erlang, busy-waiting on", "ec-erlang-pure-29-1-1-nobw": "Erlang, off",
         "ec-java-pure-27": "Java", "ec-c-pure-14": "C"}


def self_report_j(folder, raw_log, t0, t1):
    """Scaphandre's own consumption in its log over [t0, t1] (what it reports about itself), in J."""
    path = os.path.join(folder, raw_log)
    if not raw_log or not os.path.exists(path):
        return None
    total, prev = 0.0, None
    for e in check_attribution.load_entries(path):
        t = e["host"]["timestamp"]
        if prev is not None and min(t, t1) > max(prev, t0):
            w = sum(c["consumption"] for c in e["consumers"] if "scaphandre" in (c.get("exe") or "")) / 1e6
            total += w * (min(t, t1) - max(prev, t0))
        prev = t
    return total


def rows_by_run(folder):
    """CSV rows by (server, meter, repeat): failed messages, the server's CPU, the raw log."""
    out = {}
    for path in glob.glob(os.path.join(folder, "echo", "*_meter-*.csv")):
        meter = path.rsplit("_meter-", 1)[1][:-4]
        for r in csv.DictReader(open(path)):
            out[(r["Container Name"], meter, r["Repeat"])] = r
    return out


def main(folder):
    records = [json.loads(line) for line in open(os.path.join(folder, "meter_overhead.jsonl"))]
    csv_rows = rows_by_run(folder)
    g = collections.defaultdict(lambda: collections.defaultdict(list))
    for rec in records:
        row = csv_rows.get((rec["server"], rec["meter"], rec["repeat"]), {})
        k = (rec["deploy"], rec["server"].replace("-native", ""), rec["meter"])
        g[k]["power_w"].append(rec["psys_j"] / rec["load_s"])
        g[k]["energy_j"].append(rec["psys_j"])
        g[k]["load_s"].append(rec["load_s"])
        g[k]["failed"].append(int(float(row.get("Failed Messages") or 0)))
        g[k]["server_cpus"].append(float(row.get("Container Total CPU (%*s)") or 0) / 100 / rec["load_s"])
        if rec["meter"] == "on":
            g[k]["meter_cpus"].append((rec["scaphandre_cpu_s"] or 0) / rec["load_s"])
            raw = row.get("Raw Log", "")
            win = check_attribution.window_file(os.path.join(folder, raw)) if raw else ""
            if win and os.path.exists(win):
                w = json.load(open(win))
                rep = self_report_j(folder, raw, w["load_start_epoch"], w["load_end_epoch"])
                if rep is not None:
                    platform_w = rec["psys_j"] / rec["load_s"]
                    g[k]["self_report_cpus"].append(rep / rec["load_s"] / (platform_w / os.cpu_count()))
    rng = random.Random(stats.SEED)
    out = []
    print(f"{'deploy':<10}{'server':<26}{'power W off -> on':>20}{'extra W (95% range)':>26}{'p':>8}"
          f"{'energy J off -> on':>22}{'meter CPUs real / self-report':>32}{'failed':>8}")
    for deploy in ("container", "native"):
        for server, label in LABEL.items():
            on, off = g.get((deploy, server, "on")), g.get((deploy, server, "off"))
            if not on or not off:
                continue
            diff = lambda a, b: data.median(a) - data.median(b)
            row = dict(deploy=deploy, server=server, label=label, runs_on=len(on["power_w"]), runs_off=len(off["power_w"]),
                       power_off_w=data.median(off["power_w"]), power_on_w=data.median(on["power_w"]),
                       extra_w=diff(on["power_w"], off["power_w"]),
                       extra_w_ci=stats.bootstrap(on["power_w"], off["power_w"], diff, rng),
                       p=stats.mann_whitney_p(on["power_w"], off["power_w"]),
                       energy_off_j=data.median(off["energy_j"]), energy_on_j=data.median(on["energy_j"]),
                       load_off_s=data.median(off["load_s"]), load_on_s=data.median(on["load_s"]),
                       meter_cpus=data.median(on["meter_cpus"]) if on["meter_cpus"] else None,
                       meter_self_report_cpus=data.median(on["self_report_cpus"]) if on["self_report_cpus"] else None,
                       server_cpus_off=data.median(off["server_cpus"]), server_cpus_on=data.median(on["server_cpus"]),
                       failed=sum(on["failed"]) + sum(off["failed"]))
            out.append(row)
            lo, hi = row["extra_w_ci"]
            print(f"{deploy:<10}{label:<26}{row['power_off_w']:>9.2f} -> {row['power_on_w']:<7.2f}"
                  f"{row['extra_w']:>+10.2f} ({lo:+.2f} to {hi:+.2f}){row['p']:>9.3f}"
                  f"{row['energy_off_j']:>11.0f} -> {row['energy_on_j']:<7.0f}"
                  f"{(row['meter_cpus'] or 0):>17.2f} / {(row['meter_self_report_cpus'] or 0):<10.2f}{row['failed']:>8}")
    with open(os.path.join(folder, "meter_overhead.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"\nwritten: {os.path.join(folder, 'meter_overhead.json')}")
    print("extra W = median power with Scaphandre on minus off (psys counter, same reading in both); p = exact "
          "Mann-Whitney test, on vs off; meter CPUs = Scaphandre's CPU-seconds per second of load, from the kernel "
          "(real) and from its own log (self-report: its watts / (platform watts / CPUs)).")


if __name__ == "__main__":
    main(sys.argv[1])
