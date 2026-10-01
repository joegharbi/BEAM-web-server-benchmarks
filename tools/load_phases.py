"""Optional phases around the measured load, shared by measure_docker.py and measure_websocket.py.

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


def charger_unplugged(before, after):
    """True when a laptop ran on battery at the start or end of the load (ON_BATTERY not 'ignore').

    `before`/`after` are run_metadata.ac_power() readings: "yes", "no", or "" without a battery.
    Without a config (no MEASURE_ON_BATTERY) this is never checked, as in earlier releases.
    """
    policy = os.environ.get("MEASURE_ON_BATTERY", "ignore")
    return policy != "ignore" and "no" in (before, after)


def idle_window(seconds):
    """Wait `seconds` without traffic; returns the (start, end) wall-clock window, or None when off."""
    if seconds <= 0:
        return None
    t0 = time.time()
    time.sleep(seconds)
    return t0, time.time()


def idle_fields(raw_json, container_name, container_id, window, warmup_s):
    """CSV columns of the warm-up and idle phases (idle values empty when idle is off)."""
    fields = {"Warm-up (s)": warmup_s, "Idle Time (s)": 0, "Idle Energy (J)": "",
              "Idle Avg Power (W)": "", "Idle Host Avg Power (W)": ""}
    if window is None:
        return fields
    e = compute_window_energy(raw_json, container_name, window[0], window[1], container_id=container_id)
    fields.update({"Idle Time (s)": round(window[1] - window[0], 3),
                   "Idle Energy (J)": round(e["energy_j"], 6),
                   "Idle Avg Power (W)": round(e["avg_power_w"], 6),
                   "Idle Host Avg Power (W)": round(e["host_avg_power_w"], 6)})
    return fields
