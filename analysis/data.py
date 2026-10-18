"""Read the measurement rows of a results folder (the per-container CSVs, not the summaries)."""
import csv
import functools
import glob
import json
import os
import re
import statistics
import subprocess

SUFFIX = re.compile(r"(-nobw)?(-native)?$")
CPU_COUNTER = "51efaf4"     # first framework commit that reads the server's exact CPU time (cgroup counter, v1.6.2)
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def rows(folder):
    """Every measurement row of a results folder, one dict per row, values as text."""
    out = []
    for path in sorted(glob.glob(f"{folder}/*/*.csv")):
        if path.endswith("summary.csv"):        # per-container and combined summaries
            continue
        with open(path, newline="") as f:
            out.extend(csv.DictReader(f))
    return out


def num(row, column):
    value = row.get(column)
    return float(value) if value not in (None, "") else None


def server(row):
    """The server a container belongs to: st-erlang-pure-29-1-1-nobw-native -> st-erlang-pure-29-1-1."""
    return SUFFIX.sub("", row["Container Name"])


def busy_wait(row):
    """'on' for the server as built, 'off' for its -nobw variant (BEAM busy-waiting off)."""
    return "off" if row["Variant"] == "nobw" else "on"


def is_echo(row):
    return "Message Size (B)" in row


def is_websocket(row):
    return "Pattern" in row and not is_echo(row)


def load(row):
    """The load level of a row: the request count (HTTP), pattern and message size (WebSocket), or pattern,
    clients and the pattern's own setting (echo)."""
    if is_echo(row):
        if row["Pattern"] == "connect" and float(row["Spacing (s)"] or 0) == 0:     # all at once: the paper's nosleep
            rounds = int(row.get("Rounds") or 1)
            return f'nosleep {row["Num Clients"]} clients' + (f' x{rounds} rounds' if rounds > 1 else '')
        extra = {"connect": f'spacing {row["Spacing (s)"]} s', "burst": f'{row["Messages per Client"]} msg',
                 "stream": f'{row["Rate (msg/s)"]}/s'}[row["Pattern"]]
        return f'{row["Pattern"]} {row["Num Clients"]} clients {extra}'
    if is_websocket(row):
        return f'{row["Pattern"]} {row["Message Size (KB)"]} KB'
    return int(row["Total Requests"])


def median(values):
    return statistics.median(values)


def cv(values):
    """Spread between repeats: standard deviation / mean, in percent."""
    return statistics.pstdev(values) / statistics.mean(values) * 100


@functools.lru_cache(maxsize=None)
def cpu_counter_exact(folder):
    """True when the folder was measured with a framework that reads the server's exact CPU time (commit
    51efaf4 or later): only then is the CPU-time method right. Earlier CPU totals came from docker-stats samples
    and are too low (the static/dynamic campaign: 50-77%)."""
    try:
        version = json.load(open(os.path.join(folder, "metadata.json")))["software_and_machine"]["framework_version"]
        return subprocess.run(["git", "-C", ROOT, "merge-base", "--is-ancestor", CPU_COUNTER, version.split("-")[0]],
                              capture_output=True).returncode == 0
    except (OSError, ValueError, KeyError):
        return False


def energy_by_cpu_time(row, exact=True):
    """The server energy by the CPU-time method (second method, tools/server_box.py): the run's own column, or
    computed from its CPU total, platform energy, load time and CPUs; None when the CPU total is not exact."""
    if not exact:
        return None
    if row.get("Container Energy, CPU-time method (J)"):
        return num(row, "Container Energy, CPU-time method (J)")
    cpu, host, secs, cpus = (num(row, c) for c in ("Container Total CPU (%*s)", "Host Energy (J)",
                                                   "Execution Time (s)", "Host CPUs"))
    if None in (cpu, host, secs, cpus) or secs <= 0 or cpus <= 0:
        return None
    return cpu / 100 * host / (secs * cpus)
