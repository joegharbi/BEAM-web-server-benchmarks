#!/usr/bin/env python3
"""Results of the load tools experiment (run.py, README.md). Per server and tool, over the repeats:

  1. breaking point: the highest rate the tool held in each repeat (held = at least 95 % of the target, at most 1 %
     failed, per-second rate CV at most 5 %), and what it reached at the next step
  2. per rate: rate error, per-second steadiness, latency p50/p99 (each tool's own), the tool's CPU and energy per
     1,000 requests, the server's CPU and energy, the laptop's energy above idle
  3. the plan's decision rule: a tool passes when it holds 2,000 req/s within 2 % with 0 failed requests in every
     repeat; the passing tools ranked by the tool's energy per 1,000 requests at 2,000 req/s
  4. does the tool change the server's measured energy? At the rates every tool held, each tool's server energy
     against wrk2's (the cheapest tool): ratio of medians and the exact two-sided Mann-Whitney p (5 vs 5: the
     smallest possible p is 0.008); and the server's CPU time, to tell real extra work from a different attribution
  5. busy-waiting on vs off: the Erlang server's energy on/off per tool and rate

Medians over the repeats, with [min, max]. The laptop's energy above idle = laptop energy - idle laptop power (the
idle run of the same server and repeat) x run time.

    ./srv/bin/python3 experiments/load-tools/analyze.py results/<folder>      (writes <folder>/load_tools.json)
"""
import collections
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "analysis"))
sys.path.insert(0, HERE)
import stats  # noqa: E402
from run import MAX_FAILED_SHARE, MAX_RATE_CV_PCT, MIN_RATE_SHARE  # noqa: E402

TOOL_ORDER = ["wrk2", "oha", "vegeta", "k6", "k6-paced", "python"]
SERVER_ORDER = ["erlang-nobw", "erlang-bw", "java"]
LABEL = {"erlang-nobw": "Erlang, busy-waiting off", "erlang-bw": "Erlang, busy-waiting on", "java": "Java (Netty)"}


def med(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def spread(values):
    values = [v for v in values if v is not None]
    return {"median": med(values), "min": min(values), "max": max(values), "n": len(values)} if values else None


def held(r):
    total = r["ok"] + r["failed"]
    return (r["exit"] == 0 and r["ok"] > 0 and r["rate"] >= MIN_RATE_SHARE * r["target_rate"]
            and r["failed"] <= MAX_FAILED_SHARE * total
            and (r.get("rate_cv_pct") is None or r["rate_cv_pct"] <= MAX_RATE_CV_PCT))


def fmt(s, digits=1, scale=1.0):
    if s is None:
        return "-"
    return f"{s['median'] * scale:.{digits}f} [{s['min'] * scale:.{digits}f}, {s['max'] * scale:.{digits}f}]"


def main(folder):
    rows = [json.loads(line) for line in open(os.path.join(folder, "runs.jsonl"))]
    idle_w = {(r["server"], r["repeat"]): r["host_power_w"] for r in rows if r["tool"] == "idle"}
    for r in rows:
        if r["tool"] != "idle" and (r["server"], r["repeat"]) in idle_w:
            r["laptop_above_idle_j"] = r["host_energy_j"] - idle_w[(r["server"], r["repeat"])] * r["wall_s"]
    servers = [s for s in SERVER_ORDER if any(r["server"] == s for r in rows)]
    tools = [t for t in TOOL_ORDER if any(r["tool"] == t for r in rows)]
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["server"], r["tool"], r["target_rate"])].append(r)
    result = {"folder": os.path.basename(folder), "idle": {}, "breaking_point": {}, "per_rate": {},
              "decision_at_2000": {}, "server_energy_by_tool": {}, "busy_wait": {}}

    print("== Idle (server running, no load)")
    for s in servers:
        idle = [r for r in rows if r["server"] == s and r["tool"] == "idle"]
        result["idle"][s] = {"laptop_power_w": spread([r["host_power_w"] for r in idle]),
                             "server_energy_j": spread([r["server_energy_j"] for r in idle]),
                             "server_cpu_s": spread([r["server_cpu_s"] for r in idle])}
        print(f"  {LABEL[s]:26} laptop {fmt(result['idle'][s]['laptop_power_w'], 2)} W, "
              f"server {fmt(result['idle'][s]['server_energy_j'], 2)} J, CPU {fmt(result['idle'][s]['server_cpu_s'], 3)} s")

    print("\n== 1. Breaking point: highest rate held per repeat, and what the tool reached at the next step")
    for s in servers:
        print(f"  {LABEL[s]}")
        result["breaking_point"][s] = {}
        for t in tools:
            if t == "python":
                py = [r for r in rows if r["server"] == s and r["tool"] == "python"]
                bp = {"own_pace_rate": spread([r["rate"] for r in py])}
                print(f"    {t:9} own pace {fmt(bp['own_pace_rate'], 0)} req/s")
            else:
                reps = sorted({r["repeat"] for r in rows if r["server"] == s and r["tool"] == t})
                highest, reached = [], []
                for rep in reps:
                    mine = sorted((r for r in rows if r["server"] == s and r["tool"] == t and r["repeat"] == rep),
                                  key=lambda r: r["target_rate"])
                    ok = [r["target_rate"] for r in mine if held(r)]
                    highest.append(max(ok) if ok else 0)
                    broke = [r for r in mine if not held(r)]
                    reached.append(broke[0]["rate"] if broke else None)
                broke_at = sorted({r["target_rate"] for r in rows if r["server"] == s and r["tool"] == t and not held(r)})
                bp = {"highest_held_per_repeat": highest, "broke_at": broke_at, "reached_when_broken": spread(reached)}
                print(f"    {t:9} held {sorted(set(highest))} req/s in the repeats {highest}; at {broke_at} it "
                      f"reached {fmt(bp['reached_when_broken'], 0)} req/s")
            result["breaking_point"][s][t] = bp

    print("\n== 2. Per rate (medians [min, max] over the repeats)")
    for s in servers:
        print(f"  {LABEL[s]}")
        print(f"    {'tool':9} {'rate':>6}  {'held':4} {'error %':>17} {'CV %':>5} {'p50 ms':>8} {'p99 ms':>9} "
              f"{'tool J/1k req':>22} {'tool CPU ms/1k':>8} {'server J':>23} {'server CPU s':>12} {'laptop>idle J':>13}")
        result["per_rate"][s] = {}
        for t in tools:
            for rate in sorted({k[2] for k in by if k[0] == s and k[1] == t}, key=lambda x: x or 0):
                rs = by[(s, t, rate)]
                row = {"n": len(rs), "held": sum(held(r) for r in rs) if rate else None,
                       "rate": spread([r["rate"] for r in rs]),
                       "rate_error_pct": spread([r.get("rate_vs_target_pct") for r in rs]),
                       "rate_cv_pct": spread([r.get("rate_cv_pct") for r in rs]),
                       "p50_ms": spread([r["p50_ms"] for r in rs]), "p99_ms": spread([r["p99_ms"] for r in rs]),
                       "tool_energy_per_1000_req_j": spread([r.get("tool_energy_per_1000_req_j") for r in rs]),
                       "tool_cpu_per_1000_req_ms": spread([r.get("tool_cpu_per_1000_req_ms") for r in rs]),
                       "server_energy_j": spread([r["server_energy_j"] for r in rs]),
                       "server_cpu_s": spread([r["server_cpu_s"] for r in rs]),
                       "laptop_above_idle_j": spread([r.get("laptop_above_idle_j") for r in rs])}
                result["per_rate"][s][f"{t}@{rate or 'own'}"] = row
                held_txt = f"{row['held']}/{row['n']}" if rate else "-"
                print(f"    {t:9} {str(rate or 'own'):>6}  {held_txt:4} {fmt(row['rate_error_pct'], 1):>17} "
                      f"{row['rate_cv_pct']['median']:5.1f} {row['p50_ms']['median']:8.2f} {row['p99_ms']['median']:9.2f} "
                      f"{fmt(row['tool_energy_per_1000_req_j'], 3):>22} {row['tool_cpu_per_1000_req_ms']['median']:8.0f} "
                      f"{fmt(row['server_energy_j'], 1):>23} {row['server_cpu_s']['median']:12.1f} "
                      f"{row['laptop_above_idle_j']['median']:13.0f}")

    print("\n== 3. Decision rule: holds 2,000 req/s within 2 % with 0 failed requests in every repeat; "
          "cheapest tool energy per 1,000 requests at 2,000 req/s")
    for s in servers:
        passing = []
        for t in tools:
            rs = by.get((s, t, 2000), [])
            if not rs:
                continue
            ok = all(abs(r["rate_vs_target_pct"]) <= 2 and r["failed"] == 0 and held(r) for r in rs)
            cost = med([r["tool_energy_per_1000_req_j"] for r in rs])
            result["decision_at_2000"].setdefault(s, {})[t] = {"pass": ok, "tool_energy_per_1000_req_j": cost}
            if ok:
                passing.append((cost, t))
        ranking = ", ".join(f"{t} {c:.3f} J" for c, t in sorted(passing))
        print(f"  {LABEL[s]:26} passing, cheapest first: {ranking}")

    print("\n== 4. Does the tool change the server's measured energy? Rates every tool held; each tool vs wrk2")
    for s in servers:
        result["server_energy_by_tool"][s] = {}
        rate_tools = [t for t in tools if t != "python"]
        common = [rate for rate in sorted({k[2] for k in by if k[0] == s and k[2]})
                  if all(by.get((s, t, rate)) and all(held(r) for r in by[(s, t, rate)]) for t in rate_tools)]
        print(f"  {LABEL[s]} (rates {common})")
        for rate in common:
            ref = [r["server_energy_j"] for r in by[(s, "wrk2", rate)]]
            ref_cpu = [r["server_cpu_s"] for r in by[(s, "wrk2", rate)]]
            parts = []
            for t in rate_tools:
                e = [r["server_energy_j"] for r in by[(s, t, rate)]]
                cpu = [r["server_cpu_s"] for r in by[(s, t, rate)]]
                entry = {"server_energy_j": med(e), "server_cpu_s": med(cpu),
                         "energy_vs_wrk2": med(e) / med(ref), "cpu_vs_wrk2": med(cpu) / med(ref_cpu),
                         "energy_per_server_cpu_s": med(e) / med(cpu),
                         "p_vs_wrk2": None if t == "wrk2" else stats.mann_whitney_p(e, ref)}
                result["server_energy_by_tool"][s][f"{t}@{rate}"] = entry
                p = "" if t == "wrk2" else f" p={entry['p_vs_wrk2']:.3f}"
                parts.append(f"{t} {entry['server_energy_j']:.1f} J (x{entry['energy_vs_wrk2']:.2f}{p}, "
                             f"CPU x{entry['cpu_vs_wrk2']:.2f})")
            print(f"    {rate:>6}/s: " + "; ".join(parts))

    if "erlang-bw" in servers and "erlang-nobw" in servers:
        print("\n== 5. Busy-waiting on vs off (Erlang server energy, on/off, medians)")
        for t in tools:
            parts = []
            for rate in sorted({k[2] for k in by if k[1] == t and k[0] == "erlang-bw"}, key=lambda x: x or 0):
                on, off = by.get(("erlang-bw", t, rate)), by.get(("erlang-nobw", t, rate))
                if not on or not off:
                    continue
                if rate and not (all(held(r) for r in on) and all(held(r) for r in off)):
                    continue
                eon, eoff = [r["server_energy_j"] for r in on], [r["server_energy_j"] for r in off]
                lon = [r["laptop_above_idle_j"] for r in on]
                loff = [r["laptop_above_idle_j"] for r in off]
                entry = {"server_on_off": med(eon) / med(eoff), "p": stats.mann_whitney_p(eon, eoff),
                         "laptop_above_idle_on_off": med(lon) / med(loff)}
                result["busy_wait"][f"{t}@{rate or 'own'}"] = entry
                parts.append(f"{rate or 'own'}: server x{entry['server_on_off']:.2f} (p={entry['p']:.3f}), "
                             f"laptop above idle x{entry['laptop_above_idle_on_off']:.2f}")
            if parts:
                print(f"  {t:9} " + "; ".join(parts))

    with open(os.path.join(folder, "load_tools.json"), "w") as fh:
        json.dump(result, fh, indent=1)
    print(f"\nwritten: {os.path.join(folder, 'load_tools.json')}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
