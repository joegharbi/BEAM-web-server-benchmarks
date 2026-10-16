"""Read the measurement rows of a results folder (the per-container CSVs, not the summaries)."""
import csv
import glob
import re
import statistics

SUFFIX = re.compile(r"(-nobw)?(-native)?$")


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
