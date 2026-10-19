#!/usr/bin/env python3
"""One measurement of the meter-overhead experiment: the framework's own measuring core (tools/measure_core.py)
with the echo workload, and one extra meter choice for this experiment only:

  --meter scaphandre   as in the campaigns (Scaphandre every 0.5 s)
  --meter off          no Scaphandre at all; the CSV's Host Energy is the psys counter read by psys_sampler.py,
                       the server energy is not measured (0)

In both cases the laptop's energy over exactly the load window is also taken from psys_sampler.py's file (the same
counter, read the same way) and appended, with Scaphandre's real CPU time, to METER_OVERHEAD_SIDECAR (one JSON line
per run). The framework's files are not changed: the extra meter is registered only inside this process.

Environment: METER_OVERHEAD_SAMPLES (the sampler's file), METER_OVERHEAD_SIDECAR (the JSON-lines file).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import measure_core  # noqa: E402
import measure_failure  # noqa: E402
import plugins  # noqa: E402
from plugins.meter import Meter  # noqa: E402
from plugins.meter.scaphandre import Plugin as Scaphandre  # noqa: E402


def read_samples(path):
    """[(time, energy_uj, range_uj, scaphandre_ticks)] and the clock ticks per second."""
    rows, clk = [], 100
    with open(path) as fh:
        for line in fh:
            if line.startswith("# clk_tck"):
                clk = int(line.split()[2])
            elif line.strip() and not line.startswith("#"):
                t, e, rng, ticks = line.split()
                rows.append((float(t), int(e), int(rng), int(ticks)))
    return rows, clk


def unwrapped(rows):
    """The energy counter as one rising series (it wraps around at its range)."""
    out, offset = [], 0
    for i, (t, e, rng, ticks) in enumerate(rows):
        if i and e < rows[i - 1][1]:
            offset += rng
        out.append((t, e + offset, ticks))
    return out


def at(series, t, index):
    """Linear interpolation of column `index` at time t."""
    for (ta, *a), (tb, *b) in zip(series, series[1:]):
        if ta <= t <= tb:
            f = (t - ta) / (tb - ta) if tb > ta else 0.0
            return a[index - 1] + (b[index - 1] - a[index - 1]) * f
    raise ValueError(f"time {t:.3f} outside the sampled range")


def window(path, t0, t1):
    """The laptop's energy (J) and Scaphandre's CPU time (s) over [t0, t1], and the number of samples."""
    rows, clk = read_samples(path)
    s = unwrapped(rows)
    ticks = [r for r in s if t0 - 0.2 <= r[0] <= t1 + 0.2]
    same_pid = all(b[2] >= a[2] for a, b in zip(ticks, ticks[1:]))   # Scaphandre's counter only rises within one run
    return {"psys_j": (at(s, t1, 1) - at(s, t0, 1)) / 1e6,
            "scaphandre_cpu_s": (at(s, t1, 2) - at(s, t0, 2)) / clk if same_pid else None,
            "samples": sum(1 for r in s if t0 <= r[0] <= t1)}


def sidecar(name, mode, t0, t1):
    w = window(os.environ["METER_OVERHEAD_SAMPLES"], t0, t1)
    record = {"server": name, "meter": mode, "deploy": "native" if name.endswith("-native") else "container",
              "repeat": os.environ.get("MEASURE_REPEAT", ""), "load_s": t1 - t0, "t0": t0, "t1": t1, **w}
    with open(os.environ["METER_OVERHEAD_SIDECAR"], "a") as fh:
        fh.write(json.dumps(record) + "\n")
    return w


class On(Scaphandre):
    def energy(self, name, energy_id, t0, t1):
        e = super().energy(name, energy_id, t0, t1)
        sidecar(name, "on", t0, t1)
        return e


class Off(Meter):
    """No meter: nothing records while the server runs except the 0.1 s psys sampler (also there when on)."""

    def prepare(self):
        subprocess.run(["sudo", "-n", "pkill", "-9", "scaphandre"], capture_output=True, check=False)

    def start(self):
        pass

    def stop(self):
        pass

    def energy(self, name, energy_id, t0, t1):
        w = sidecar(name, "off", t0, t1)
        return {"energy_j": 0.0, "avg_power_w": 0.0, "host_energy_j": w["psys_j"],
                "host_avg_power_w": w["psys_j"] / (t1 - t0), "samples": w["samples"], "step_ms": 100,
                "coverage": 1.0}

    def finish(self, name, energy_id, t0, t1, energy, idle, warmup_s):
        return ({"Warm-up (s)": warmup_s, "Idle Time (s)": 0, "Container Idle Energy (J)": "",
                 "Container Idle Avg Power (W)": "", "Host Idle Avg Power (W)": ""}, "")


_names, _load = plugins.names, plugins.load
plugins.names = lambda kind: _names(kind) + (["off"] if kind == "meter" else [])
plugins.load = lambda kind, name: {("meter", "off"): Off, ("meter", "scaphandre"): On}.get((kind, name)) \
    or _load(kind, name)

if __name__ == "__main__":
    measure_failure.run(lambda: measure_core.main(__file__, "echo", "Meter-overhead experiment: one echo measurement"))
