"""Optional phases around the measured load, shared by every measurement (tools/measure_core.py).

Order of one measurement:
  server start -> health check -> warm-up -> readiness check 2 -> Scaphandre start -> idle -> load

  * Warm-up (WARMUP_SECONDS): unmeasured traffic of the same kind as the load (HTTP requests or
    WebSocket echoes), so the load does not include the server's first requests after boot.
    The readiness check that follows lets the CPU cool down again before anything is measured.
  * Idle (IDLE_SECONDS): Scaphandre already runs, no traffic is sent; the server's energy while
    doing nothing is integrated over this window from the same Scaphandre log as the load.

Both are off (0) by default, which is how the published results were measured. run_benchmarks.sh
--config exports MEASURE_WARMUP_SECONDS and MEASURE_IDLE_SECONDS; the tools' --warmup_s and
--idle_s options default to those.
"""
import os
import time

from scaphandre_energy import compute_window_energy


def seconds_from_env(name):
    try:
        return max(0.0, float(os.environ.get(name, "0") or 0))
    except ValueError:
        return 0.0


def default_warmup_s():
    return seconds_from_env("MEASURE_WARMUP_SECONDS")


def default_idle_s():
    return seconds_from_env("MEASURE_IDLE_SECONDS")


def idle_window(seconds):
    """Wait `seconds` without traffic; returns the (start, end) wall-clock window, or None when off."""
    if seconds <= 0:
        return None
    t0 = time.time()
    time.sleep(seconds)
    return t0, time.time()


def idle_fields(raw_json, container_name, container_id, window, warmup_s):
    """CSV columns of the warm-up and idle phases (idle values empty when idle is off)."""
    fields = {"Warm-up (s)": warmup_s, "Idle Time (s)": 0, "Container Idle Energy (J)": "",
              "Container Idle Avg Power (W)": "", "Host Idle Avg Power (W)": ""}
    if window is None:
        return fields
    e = compute_window_energy(raw_json, container_name, window[0], window[1], container_id=container_id,
                              zero_is_normal=True)
    fields.update({"Idle Time (s)": round(window[1] - window[0], 3),
                   "Container Idle Energy (J)": round(e["energy_j"], 6),
                   "Container Idle Avg Power (W)": round(e["avg_power_w"], 6),
                   "Host Idle Avg Power (W)": round(e["host_avg_power_w"], 6)})
    return fields
