#!/usr/bin/env python3
"""Checks a results folder of experiments/load-tools/run.py (by default the newest): is everything there that the
long runs need, and do the numbers agree with each other? Prints one line per check and ends with exit code 1
when one fails. run-all.sh runs it on its short check before it starts the long runs.

    ./srv/bin/python3 experiments/erlang-variants/verify_check.py [results folder] [--cpu-speed 1800]
"""
import glob
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
speed = int(sys.argv[sys.argv.index("--cpu-speed") + 1]) if "--cpu-speed" in sys.argv else 1800
folder = args[0] if args else sorted(glob.glob(os.path.join(ROOT, "results", "*_load-tools")))[-1]
rows = [json.loads(line) for line in open(os.path.join(folder, "runs.jsonl"))]
failed = 0


def check(ok, text):
    global failed
    failed += not ok
    print(f"  {'ok    ' if ok else 'FAILED'} {text}")


def series_of(r):
    name = f"{r['server']}_{r['deploy']}_{r['repeat']}_{r.get('level') or 'own'}_{r['tool']}" + \
           (f"_curve{r['load_pct']:g}" if "load_pct" in r else "")
    path = os.path.join(folder, "raw", name + ".series.json")
    return json.load(open(path)) if os.path.exists(path) else None


print(f"{os.path.relpath(folder, ROOT)}: {len(rows)} runs")
check(bool(rows) and all(r["exit"] == 0 for r in rows), "every run ended without an error")
loaded = [r for r in rows if r["tool"] != "idle"]
check(bool(loaded) and all(r.get("held") for r in loaded), "every loaded run held its level")
check(any(r["tool"] == "idle" for r in rows), "there is an idle run (the laptop's baseline)")
for r in rows:
    what = f"{r['server']} {r['tool']} {r.get('level') or ''}".strip()
    s = series_of(r)
    check("server_energy_j" in r and "host_energy_j" in r and r.get("host_energy_j", 0) > 0, f"{what}: energy of server and laptop")
    check(r.get("meter_coverage", 0) >= 0.99, f"{what}: the meter covers the run ({r.get('meter_coverage')})")
    every, recorded = r.get("record_every_s", 1), set(r.get("recorded", []))
    check(bool(s) and len(s) >= 0.9 * r["wall_s"] / every,
          f"{what}: a reading every {every} s ({len(s or [])} in {r['wall_s']:.0f} s)")
    if not s:
        continue
    keys = {"server": ("server_cpus_used", "server_memory_mb", "server_threads"), "cpu": ("cpu_busy_pct",),
            "frequency": ("cpu_mhz",), "temperature": ("temp_c",), "fan": ("fan_rpm",), "throttle": ("throttle_events",),
            "sockets": ("tcp_sockets",), "switches": ("context_switches_per_s",)}
    for key in [k for group in keys if group in recorded for k in keys[group]]:      # only what RECORD asked for
        check(all(e.get(key) is not None for e in s), f"{what}: {key} in every reading")
    ts = [r["start_epoch"]] + [e["t"] for e in s]
    cpu = sum(e["server_cpus_used"] * (b - a) for e, a, b in zip(s, ts, ts[1:]) if e.get("server_cpus_used") is not None)
    if r["server_cpu_s"] > 1 and "server" in recorded:
        check(abs(cpu - r["server_cpu_s"]) <= 0.03 * r["server_cpu_s"],
              f"{what}: server CPU time, readings {cpu:.1f} s against the run's total {r['server_cpu_s']:.1f} s")
    power = [(e, a, b) for e, a, b in zip(s, ts, ts[1:]) if "laptop_w" in e]
    if "power" in recorded:
        check(len(power) >= 0.9 * len(s), f"{what}: power of server and laptop in the readings ({len(power)} of {len(s)})")
    if power and "host_energy_j" in r:
        inside = [(e, max(a, r["start_epoch"]), min(b, r["end_epoch"])) for e, a, b in power]
        joules = sum(e["laptop_w"] * (b - a) for e, a, b in inside if b > a)
        check(abs(joules - r["host_energy_j"]) <= 0.05 * r["host_energy_j"],
              f"{what}: laptop energy, readings {joules:.0f} J against the run's total {r['host_energy_j']:.0f} J")
    mhz = r.get("server_cpus_mhz_max")
    if "frequency" in recorded:
        check(mhz is not None and mhz <= speed * 1.03, f"{what}: server CPUs never above {speed} MHz (highest {mhz})")
    if "throttle" in recorded:
        check(r.get("throttle_events_in_run") == 0, f"{what}: no thermal throttling during the run")
print("all checks passed" if not failed else f"{failed} checks FAILED")
sys.exit(1 if failed else 0)
