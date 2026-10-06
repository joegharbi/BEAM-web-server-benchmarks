"""The columns of a measurement CSV: blocks, names, and the names of earlier releases.

One definition used by measure_docker.py and measure_websocket.py (writing), and by
aggregate_repeats.py, progress.py and the GUI (reading). Every measured value says whose it is:

  1. What was measured     server, variant, repeat, session, time, workload
  2. Performance           what the load generator saw
  3. Container             the server's container: its energy share, CPU, memory, CPU limit
  4. Host                  the whole machine: energy, power, CPU temperature, throttling
  5. Idle                  the server doing nothing, right before its load (IDLE_SECONDS)
  6. How the run went      warm-up, readiness waits and result, energy sampling, raw log

Files written by earlier releases use older names (e.g. "Total Energy (J)" for the container's
energy). canonical() maps them to the current names, so old and new results load side by side;
old files are never rewritten.
"""
import csv
import os

RUN = ["Container Name", "Variant", "Deploy", "Repeat", "Session", "Measured At (UTC)"]
HTTP_WORKLOAD = ["Type", "Total Requests", "HTTP Max Workers", "HTTP Connection Mode"]
WS_WORKLOAD = ["Test Type", "Pattern", "Num Clients", "Message Size (KB)", "Rate (msg/s)", "Bursts",
               "Interval (s)", "Duration (s)"]
HTTP_PERFORMANCE = ["Successful Requests", "Failed Requests", "Execution Time (s)", "Requests/s"]
WS_PERFORMANCE = ["Total Messages", "Successful Messages", "Failed Messages", "Execution Time (s)", "Messages/s",
                  "Throughput (MB/s)", "Avg Latency (ms)", "Min Latency (ms)", "Max Latency (ms)"]
CONTAINER = ["Container CPU Limit", "Container Energy (J)", "Container Avg Power (W)", "Container Avg CPU (%)",
             "Container Peak CPU (%)", "Container Total CPU (%*s)", "Container Avg Mem (MB)",
             "Container Peak Mem (MB)", "Container Total Mem (MB*s)"]
HOST = ["Host CPUs", "Host Energy (J)", "Host Avg Power (W)", "Host CPU Temp Start (C)", "Host CPU Temp End (C)",
        "Host Throttled (ms)"]
IDLE = ["Idle Time (s)", "Container Idle Energy (J)", "Container Idle Avg Power (W)", "Host Idle Avg Power (W)"]
RUN_QUALITY = ["Warm-up (s)", "Waited Before Start (s)", "Waited Before Load (s)", "Ready Check", "Energy Samples",
               "Energy Sampling Step (ms)", "Energy Window Coverage", "Raw Log"]

HTTP_COLUMNS = RUN + HTTP_WORKLOAD + HTTP_PERFORMANCE + CONTAINER + HOST + IDLE + RUN_QUALITY
WS_COLUMNS = RUN + WS_WORKLOAD + WS_PERFORMANCE + CONTAINER + HOST + IDLE + RUN_QUALITY

# Names of earlier releases -> current names
RENAMED = {
    "Num CPUs": "Host CPUs",
    "Total Energy (J)": "Container Energy (J)",
    "Avg Power (W)": "Container Avg Power (W)",
    "Avg CPU (%)": "Container Avg CPU (%)",
    "Peak CPU (%)": "Container Peak CPU (%)",
    "Total CPU (%*s)": "Container Total CPU (%*s)",
    "Avg Mem (MB)": "Container Avg Mem (MB)",
    "Peak Mem (MB)": "Container Peak Mem (MB)",
    "Total Mem (MB*s)": "Container Total Mem (MB*s)",
    "CPU Temp Start (C)": "Host CPU Temp Start (C)",
    "CPU Temp End (C)": "Host CPU Temp End (C)",
    "Throttled (ms)": "Host Throttled (ms)",
    "Idle Energy (J)": "Container Idle Energy (J)",
    "Idle Avg Power (W)": "Container Idle Avg Power (W)",
    "Idle Host Avg Power (W)": "Host Idle Avg Power (W)",
    "Samples": "Energy Samples",
    "Sampling Step (ms)": "Energy Sampling Step (ms)",
    "Window Coverage": "Energy Window Coverage",
}

# Columns that describe a run rather than measure it: never summarised as statistics
NOT_MEASURED = {"Variant", "Deploy", "Repeat", "Session", "Measured At (UTC)", "Container CPU Limit", "Host CPUs",
                "Ready Check", "Raw Log", "Energy Sampling Step (ms)"}

SCOPES = ("Container", "Host")


def canonical(name):
    """The current name of a column (old names are translated, current ones returned as they are)."""
    return RENAMED.get(name, name)


def scope_of(name):
    """'Container', 'Host' or '' (performance, workload, run information)."""
    name = canonical(name)
    for scope in SCOPES:
        if name.startswith(scope + " "):
            return scope
    return ""


def read(path):
    """(header, rows) of a measurement CSV with current column names, whatever release wrote it."""
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        header = [canonical(h) for h in (reader.fieldnames or [])]
        rows = [{canonical(k): v for k, v in r.items() if k is not None} for r in reader]
    return header, rows


def append(path, columns, values):
    """Append one row (a dict by current name) in the order of `columns`.

    A file written by an earlier release (other names or order) is rewritten once with the current
    header, its rows translated by canonical(); values a column did not have stay empty.
    """
    row = [values.get(c, "") for c in columns]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    existing = []
    if os.path.isfile(path) and os.stat(path).st_size > 0:
        with open(path, newline="", encoding="utf-8") as fh:
            existing = list(csv.reader(fh))
    if existing and existing[0] == columns:
        with open(path, "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(row)
        return
    old_header = [canonical(h) for h in existing[0]] if existing else []
    migrated = [[dict(zip(old_header, r)).get(c, "") for c in columns] for r in existing[1:]]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        w.writerows(migrated)
        w.writerow(row)


def run_fields(deploy="container"):
    """Block 1 values set by run_benchmarks.sh for every measurement (empty when run by hand), and
    how the server ran (`deploy`: container or native, from the tool's --deploy)."""
    import datetime
    return {"Variant": os.environ.get("MEASURE_VARIANT", ""), "Deploy": deploy,
            "Repeat": os.environ.get("MEASURE_REPEAT", ""),
            "Session": os.environ.get("MEASURE_SESSION", ""),
            "Measured At (UTC)": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def raw_log_field(raw_path):
    """Where the raw Scaphandre log of a run is kept, relative to the results folder ("" if deleted)."""
    if not raw_path:
        return ""
    raw_dir = os.environ.get("MEASURE_RAW_DIR")
    if raw_dir and os.path.abspath(raw_path).startswith(os.path.abspath(raw_dir) + os.sep):
        return os.path.join("raw", os.path.relpath(raw_path, raw_dir))
    return raw_path


def container_cpu_limit(docker_path, container_name):
    """The container's CPU limit: 'none' (may use every host CPU), '2 CPUs', or 'cpuset 0-3'."""
    import subprocess
    try:
        out = subprocess.run([docker_path, "inspect", "--format", "{{.HostConfig.NanoCpus}} {{.HostConfig.CpusetCpus}}",
                              container_name], capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return ""
    if not out:
        return ""
    nano = int(out[0]) if out[0].isdigit() else 0
    if nano:
        return f"{nano / 1e9:g} CPUs"
    if len(out) > 1:
        return f"cpuset {out[1]}"
    return "none"
