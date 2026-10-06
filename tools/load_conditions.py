"""Conditions during the load: watched while every measurement runs, the same way for every server.

The readiness check (readiness.py) looks at the machine *before* a load. This module watches it
*during* the load, every 0.5 s, and judges the run by rules fixed before measuring (the config):

  CPU speed   READY_CPU_SPEED: the CPU's speed limit must stay at the expected speed (the firmware
              or a weak charger can cap it, and the cap can come and go)
  throttling  READY_NO_THROTTLING=1: no thermal throttling
  charger     ON_BATTERY not ignore: the laptop stays on the charger

A run that breaks a rule is invalid: it is not added to the results, but kept with its values and
reason in invalid_runs.csv (and its raw log), and run_benchmarks.sh measures it again (exit code
EXIT_INVALID, at most INVALID_RUN_RETRIES times). Without a config (a measurement by hand) nothing
is judged; the values are still recorded. Shared by measure_docker.py and measure_websocket.py.
"""
import csv
import glob
import logging
import os
import sys
import threading

import run_metadata

logger = logging.getLogger()

EXIT_INVALID = 4
INVALID_FILE = "invalid_runs.csv"
INVALID_COLUMNS = ["Measured At (UTC)", "Container Name", "Variant", "Deploy", "Repeat", "Measurement",
                   "Reason", "Host CPU Speed Limit Min (MHz)", "Host CPU Avg Speed (MHz)", "Host Throttled (ms)",
                   "Rate (/s)", "Container Energy (J)", "Host Energy (J)", "Raw Log"]


def cpu_avg_speed_mhz():
    """Average current speed over all cores (MHz), or None."""
    vals = []
    for path in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq"):
        try:
            with open(path, encoding="utf-8") as fh:
                vals.append(int(fh.read()))
        except (OSError, ValueError):
            continue
    return sum(vals) / len(vals) / 1000 if vals else None


class Watch:
    """Samples the machine every `interval` s between start() and stop()."""

    def __init__(self, interval=0.5):
        self.interval = interval
        self.limits, self.speeds, self.power = [], [], []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _sample(self):
        limit, speed = run_metadata.cpu_speed_limit_mhz(), cpu_avg_speed_mhz()
        if limit is not None:
            self.limits.append(limit)
        if speed is not None:
            self.speeds.append(speed)
        self.power.append(run_metadata.ac_power())

    def _run(self):
        while not self._stop.wait(self.interval):
            self._sample()

    def start(self):
        self._sample()
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)
        self._sample()
        return self

    def fields(self):
        """CSV values (Host block)."""
        return {"Host CPU Speed Limit Min (MHz)": min(self.limits) if self.limits else "",
                "Host CPU Avg Speed (MHz)": round(sum(self.speeds) / len(self.speeds)) if self.speeds else ""}


def rules(env=None):
    """The rules from the config (MEASURE_* variables set by run_benchmarks.sh); {} without a config."""
    env = os.environ if env is None else env
    if "MEASURE_READY_ON_TIMEOUT" not in env:
        return {}
    return {"cpu_speed": env.get("MEASURE_READY_CPU_SPEED", "auto"),
            "no_throttling": env.get("MEASURE_READY_NO_THROTTLING", "1") == "1",
            "on_battery": env.get("MEASURE_ON_BATTERY", "wait")}


def problems(watch, throttled_ms, rule, expected_mhz=None):
    """Reasons why the run is invalid ([] when it is valid or nothing is judged)."""
    reasons = []
    setting = rule.get("cpu_speed", "off")
    if setting not in ("off", "", None):
        expected = expected_mhz if expected_mhz is not None else (
            run_metadata.expected_cpu_speed_mhz() if setting == "auto" else int(setting))
        low = min(watch.limits) if watch.limits else None
        if expected and low is not None and low < expected:
            reasons.append(f"CPU speed capped at {low} MHz < {expected} MHz during the load (charger or firmware)")
    if rule.get("no_throttling") and isinstance(throttled_ms, (int, float)) and throttled_ms > 0:
        reasons.append(f"thermal throttling during the load ({throttled_ms} ms)")
    if rule and rule.get("on_battery", "ignore") != "ignore" and "no" in watch.power:
        reasons.append("the laptop ran on battery during the load (charger unplugged)")
    return reasons


def record_invalid(output_csv, values, reasons, measurement):
    """Keep the invalid run: one row in invalid_runs.csv of the results folder (next to the family
    folder of `output_csv`), with its values and reason."""
    folder = os.path.dirname(os.path.dirname(os.path.abspath(output_csv)))
    path = os.path.join(folder, INVALID_FILE)
    rate = values.get("Requests/s", values.get("Messages/s", ""))
    row = {**values, "Measurement": measurement, "Reason": "; ".join(reasons), "Rate (/s)": rate}
    new = not os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(INVALID_COLUMNS)
        w.writerow([row.get(c, "") for c in INVALID_COLUMNS])
    return path


def judge(watch, values, output_csv, measurement):
    """End the tool with EXIT_INVALID when the run broke a rule (after keeping it); else return."""
    reasons = problems(watch, values.get("Host Throttled (ms)"), rules())
    if not reasons:
        return
    path = record_invalid(output_csv, values, reasons, measurement)
    logger.warning("%s | invalid run, not added to the results: %s (kept in %s; it is measured again)",
                   values.get("Container Name", ""), "; ".join(reasons), path)
    sys.exit(EXIT_INVALID)
