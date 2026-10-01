#!/usr/bin/env python3
"""Read and check a benchmark config file (KEY=VALUE lines), print shell assignments.

run_benchmarks.sh --config FILE calls this and evaluates the output. Every key has
a default, a check and a description. Unknown keys, duplicate keys and invalid
values stop the run with a clear message, so a typo can never silently fall back
to a default.

File format: one KEY=VALUE per line; blank lines and lines starting with # are
ignored; a value may be quoted; " # comment" after a value is ignored.

Two layers: a measurement file (what to measure, and how much) names a machine profile with
MACHINE=minimal (how the machine is prepared: configs/machine/minimal.config). Values come from
the defaults, then the machine profile, then the measurement file, which can override anything.

Usage:
  python3 tools/bench_config.py bench.config        # checked shell assignments
  python3 tools/bench_config.py --example           # the short example measurement file
  python3 tools/bench_config.py --docs              # docs/CONFIG.md: every setting explained
"""
import difflib
import os
import re
import secrets
import shlex
import sys

GOVERNORS = ("performance", "powersave", "schedutil", "ondemand", "conservative", "unchanged")


def _int(lo=None):
    def check(v):
        if not re.fullmatch(r"-?\d+", v):
            raise ValueError("must be a whole number")
        n = int(v)
        if lo is not None and n < lo:
            raise ValueError(f"must be at least {lo}")
        return str(n)
    return check


def _choice(*options):
    def check(v):
        if v not in options:
            raise ValueError(f"must be one of: {', '.join(options)}")
        return v
    return check


def _optional_number(v):
    if v == "":
        return ""
    try:
        return str(float(v))
    except ValueError:
        raise ValueError("must be a number or empty")


def _optional_int(v):
    return "" if v == "" else _int(0)(v)


def _list(item_check, what):
    """Space-separated list of values, each checked by `item_check`."""
    def check(v):
        items = v.split()
        if not items:
            raise ValueError(f"must list at least one {what}")
        try:
            return " ".join(item_check(x) for x in items)
        except ValueError as e:
            raise ValueError(f"must be {what}s separated by spaces ({e})")
    return check


MEASUREMENT_KINDS = ("static", "dynamic", "websocket", "concurrency", "payload")
SERVER_TYPES = ("static", "dynamic", "websocket")


def _kind(v):
    if v not in MEASUREMENT_KINDS:
        raise ValueError(f"'{v}' is not one of: {', '.join(MEASUREMENT_KINDS)}")
    return v


def _servers(v):
    """Empty (all servers found), or names, each a server folder name or TYPE:IMAGE for an image
    built on this machine without a folder."""
    out = []
    for item in v.split():
        name = item.split(":", 1)[1] if ":" in item else item
        if ":" in item and item.split(":", 1)[0] not in SERVER_TYPES:
            raise ValueError(f"'{item}': the type before ':' must be one of: {', '.join(SERVER_TYPES)}")
        if not re.fullmatch(r"[A-Za-z0-9_.\-/]+(:[A-Za-z0-9_.\-]+)?", name):
            raise ValueError(f"'{item}' is not a container or image name")
        out.append(item)
    return " ".join(out)


def _machine(v):
    if v == "none" or re.fullmatch(r"[A-Za-z0-9_-]+", v) or v.endswith(".config"):
        return v
    raise ValueError("must be a profile name (minimal, tolerable, untouched), a .config file, or none")


def _path(v):
    if any(c.isspace() for c in v):
        raise ValueError("must be a folder path without spaces")
    return v


def _number(lo=0.0):
    def check(v):
        try:
            n = float(v)
        except ValueError:
            raise ValueError(f"'{v}' is not a number")
        if n < lo:
            raise ValueError(f"must be at least {lo:g}")
        return v
    return check


def _workers(v):
    if v.lower() in ("system", "none", "default", "system_default"):
        return "system"
    return _int(1)(v)


def _names(v):
    if v and not re.fullmatch(r"[A-Za-z0-9_.-]+(,[A-Za-z0-9_.-]+)*", v):
        raise ValueError("must be comma-separated container names")
    return v


def _optional_percent(v):
    if v == "":
        return ""
    n = _optional_number(v)
    if not 0 <= float(n) <= 100:
        raise ValueError("must be between 0 and 100, or empty")
    return n


def _brightness(v):
    if v == "unchanged":
        return v
    n = _int(0)(v)
    if int(n) > 100:
        raise ValueError("must be 'unchanged' or a percentage 0-100")
    return n


def _optional_margin(v):
    if v == "":
        return ""
    n = _optional_number(v)
    if float(n) < 0:
        raise ValueError("must be 0 or more, or empty")
    return n


# Every key: default, check, a one-line explanation, and either the unit of a number
# or the options of a choice (each with a short meaning). bench.config.example is
# generated from this, so the documentation always matches the code.
SCHEMA = {
    # --- Machine profile ---
    "MACHINE": dict(default="minimal", check=_machine, unit="profile name, .config file, or none",
        help="How the machine is prepared: a profile in configs/machine/ (minimal, tolerable, untouched) or\n"
             "the path of a profile file. Any machine setting written in the measurement file overrides the\n"
             "profile. none = no profile, only the built-in defaults (which equal minimal)."),

    # --- What to measure ---
    "MEASURE": dict(default=" ".join(MEASUREMENT_KINDS), check=_list(_kind, "measurement kind"),
        unit="kinds, separated by spaces: static dynamic websocket concurrency payload",
        help="Which kinds of measurement to run. static/dynamic: HTTP load levels (HTTP_REQUESTS) on the\n"
             "static and dynamic HTTP servers; websocket: burst and stream tests; concurrency and payload:\n"
             "the WebSocket client-count and message-size sweeps. A type on the command line\n"
             "(make run-static ...) takes precedence over this setting."),
    "SERVERS": dict(default="", check=_servers, unit="names separated by spaces, or empty = all servers found",
        help="Which servers to measure. Empty = every server found in BENCHMARKS_DIR (a folder with a\n"
             "Dockerfile; its place, static/, dynamic/ or websocket/, gives its type). A name is a server\n"
             "folder name, e.g. st-erlang-cowboy-28-4-3. An image built on this machine without a folder is\n"
             "given with its type: static:my-nginx, dynamic:my-app, websocket:my-ws (port from the image).\n"
             "Every name must exist and every image must be built, or the measurement does not start."),
    "BENCHMARKS_DIR": dict(default="", check=_path, unit="folder path, or empty = benchmarks/",
        help="The folder searched for servers. Empty = benchmarks/ (or BENCHMARKS_DIR from the environment).\n"
             "--bench on the command line takes precedence."),

    # --- Repeats and order ---
    "REPEATS": dict(default="5", check=_int(1), unit="runs, 1 or more",
        help="How many times every measurement is repeated. Each repeat is one full pass over all servers."),
    "SHUFFLE": dict(default="1", check=_choice("0", "1"),
        help="Shuffle the order of the servers in every repeat, so no server always runs first on a cool machine.",
        options={"1": "shuffle (recommended)", "0": "same order in every repeat"}),
    "SHUFFLE_SEED": dict(default="", check=_optional_int, unit="whole number, or empty",
        help="Number that decides the shuffled order. Empty = random; the number used is saved in\n"
             "metadata.json and schedule.txt. Put it here to rerun exactly the same order (same servers)."),

    # --- Machine settings (applied before the measurement, restored after) ---
    "ENV_GOVERNOR": dict(default="performance", check=_choice(*GOVERNORS),
        help="CPU frequency governor during the measurement.",
        options={"performance": "CPU stays at its highest allowed frequency (steadiest, recommended)",
                 "powersave": "CPU lowers its frequency when it can (Linux default on Intel laptops)",
                 "schedutil": "frequency follows the load (only with some CPU drivers, e.g. AMD)",
                 "ondemand": "frequency jumps up under load (only with some CPU drivers)",
                 "conservative": "frequency rises slowly under load (only with some CPU drivers)",
                 "unchanged": "leave the machine's current setting"}),
    "ENV_TURBO": dict(default="off", check=_choice("off", "on", "unchanged"),
        help="Turbo boost: the CPU briefly running above its base frequency when it is cool enough.",
        options={"off": "no turbo; slower but much steadier results (recommended)",
                 "on": "turbo allowed; faster, closer to everyday use, but more variation",
                 "unchanged": "leave the machine's current setting"}),
    "ENV_STOP_CONTAINERS": dict(default="1", check=_choice("0", "1"),
        help="Other Docker containers running during the measurement.",
        options={"1": "stop them before, restart them after (recommended)", "0": "leave them running"}),
    "ENV_KEEP_CONTAINERS": dict(default="", check=_names, unit="container names, comma-separated, or empty",
        help="Containers that must keep running even when ENV_STOP_CONTAINERS=1."),
    "ENV_SCREEN_BRIGHTNESS": dict(default="1", check=_brightness, unit="percent 0-100, or 'unchanged'",
        help="Screen brightness during the measurement (restored after). 1 = dimmest that is still on (0 switches\n"
             "the backlight off on some laptops, and a brightness key pressed then would change it mid-run).\n"
             "It does not change the container's energy (the screen is not part of the CPU), only the whole\n"
             "machine's. The desktop may still dim or switch off the screen by itself. Machines without a\n"
             "screen are not affected."),
    "ENV_KEYBOARD_LIGHT": dict(default="off", check=_choice("off", "unchanged"),
        help="Keyboard backlight during the measurement.",
        options={"off": "switch it off, restored after (recommended)", "unchanged": "leave it as it is"}),
    "ENV_WIFI": dict(default="off", check=_choice("off", "unchanged"),
        help="Wi-Fi radio during the measurement. Off removes background network traffic and updates.\n"
             "The measurement needs no network. A run started over SSH through Wi-Fi refuses to switch it off\n"
             "(it would cut its own connection); set 'unchanged' there.",
        options={"off": "switch it off, restored after (recommended)", "unchanged": "leave it as it is"}),
    "ENV_BLUETOOTH": dict(default="off", check=_choice("off", "unchanged"),
        help="Bluetooth radio during the measurement.",
        options={"off": "switch it off, restored after (recommended)", "unchanged": "leave it as it is"}),
    "ON_BATTERY": dict(default="wait", check=_choice("wait", "stop", "ignore"),
        help="A laptop not on its charger: the CPU can run under other power limits on battery. Checked at\n"
             "the start and before every run; a run during which the charger was unplugged counts as failed.\n"
             "Machines without a battery are never affected.",
        options={"wait": "do not measure on battery; wait until the charger is back (recommended)",
                 "stop": "stop the measurement; continue later with make resume",
                 "ignore": "measure anyway"}),
    "SETTLE_SECONDS": dict(default="60", check=_int(0), unit="seconds",
        help="Wait after applying the machine settings, before the resting state is measured."),
    "RESTING_MEASURE_SECONDS": dict(default="10", check=_int(1), unit="seconds, 1 or more (one reading per second)",
        help="How long the resting temperature and CPU use are measured, after SETTLE_SECONDS. Longer = a more\n"
             "trustworthy resting value on a noisy machine. Used as the reference by the readiness checks."),

    # --- Readiness check before every run ---
    "READY_CHECK_EVERY_SECONDS": dict(default="5", check=_int(1), unit="seconds, 1 or more",
        help="While waiting for the machine to be ready, how often it is checked again."),
    "READY_TEMP_REFERENCE_C": dict(default="", check=_optional_number,
        unit="degrees Celsius, or empty = measured automatically",
        help="Temperature the CPU is compared to. Empty = the resting temperature, measured automatically\n"
             "for RESTING_MEASURE_SECONDS (middle value of the readings). A number = use this fixed temperature."),
    "READY_TEMP_MARGIN_C": dict(default="3", check=_optional_margin, unit="degrees Celsius, or empty = no temperature check",
        help="Ready when CPU temperature <= READY_TEMP_REFERENCE_C + this margin.\n"
             "Examples: reference empty + margin 3 = back within 3 C of today's resting temperature;\n"
             "reference 50 + margin 0 = never start above 50 C. The sensor reads in steps of 1 C."),
    "READY_CPU_BUSY_REFERENCE_PERCENT": dict(default="", check=_optional_percent,
        unit="percent 0-100, or empty = measured automatically",
        help="CPU use the whole machine is compared to. Empty = the resting CPU use, measured automatically\n"
             "(average over RESTING_MEASURE_SECONDS). A number = use this fixed value."),
    "READY_CPU_BUSY_MARGIN_PERCENT": dict(default="5", check=_optional_percent, unit="percent 0-100, or empty = no CPU check",
        help="Ready when whole-machine CPU use <= READY_CPU_BUSY_REFERENCE_PERCENT + this margin.\n"
             "Examples: reference empty + margin 5 = within 5% of the machine's resting use (shared machines);\n"
             "reference 0 + margin 5 = never start above 5% (your own quiet machine)."),
    "READY_NO_THROTTLING": dict(default="1", check=_choice("0", "1"),
        help="Thermal throttling: the CPU slowing itself down because it is too hot.",
        options={"1": "not ready while the CPU is throttling (recommended)", "0": "ignore throttling"}),
    "READY_CONSECUTIVE_CHECKS": dict(default="2", check=_int(1), unit="checks, 1 or more",
        help="All checks must pass this many times in a row, so a short dip does not count."),
    "READY_MIN_WAIT_SECONDS": dict(default="10", check=_int(0), unit="seconds",
        help="Always wait at least this long before a run, even if the machine already looks ready\n"
             "(lets the previous container stop and its connections close)."),
    "READY_MAX_WAIT_SECONDS": dict(default="300", check=_int(0), unit="seconds, at least READY_MIN_WAIT_SECONDS",
        help="After this long without being ready, READY_ON_TIMEOUT decides what happens."),
    "READY_ON_TIMEOUT": dict(default="wait", check=_choice("wait", "stop", "measure"),
        help="What to do when the machine is still not ready after READY_MAX_WAIT_SECONDS.",
        options={"wait": "keep waiting; never measure in a bad state; prints every minute what fails (recommended)",
                 "stop": "stop the whole measurement with an error",
                 "measure": "measure anyway and write the reason in the CSV (cloud or shared servers)"}),

    # --- Failures ---
    "FAILURES_STOP_AFTER": dict(default="5", check=_int(0), unit="failed measurements in a row",
        help="What to do when a measurement fails (server did not start, health check failed, ...).\n"
             "Every failure is written to failures.csv.",
        options={"1": "stop at the first failure",
                 "N": "keep going, but stop after N failures in a row, because then something is broken (default 5)",
                 "0": "never stop; measure everything that works"}),

    # --- Raw data ---
    "RAW_DATA": dict(default="keep", check=_choice("keep", "delete"),
        help="Scaphandre's raw power log of each run, after its energy has been calculated.",
        options={"keep": "keep it as it is (plain JSON) in <results>/raw/ (energy can be recalculated later; recommended)",
                 "delete": "delete it (saves disk space)"}),

    # --- Workloads (full runs; --quick and --super-quick keep their short built-in lists) ---
    "HTTP_REQUESTS": dict(default="100 1000 5000 8000 10000 15000 20000 30000 40000 50000 60000 70000 80000",
        check=_list(_int(1), "request count"), unit="request counts, separated by spaces",
        help="Load levels of the static and dynamic HTTP measurements, measured in this order."),
    "WS_BURST_CLIENTS": dict(default="5 50 100", check=_list(_int(1), "client count"),
        unit="client counts, separated by spaces", help="WebSocket burst: how many clients connect at once."),
    "WS_BURST_SIZES_KB": dict(default="8 1024 65536", check=_list(_int(1), "size"),
        unit="KB per message, separated by spaces", help="WebSocket burst: message sizes."),
    "WS_BURST_BURSTS": dict(default="3", check=_list(_int(1), "count"),
        unit="bursts per client, separated by spaces", help="WebSocket burst: how many bursts each client sends."),
    "WS_BURST_INTERVAL_SECONDS": dict(default="0.5", check=_number(0),
        unit="seconds, 0 or more",
        help="Pause between bursts, also used by the concurrency and payload tests. 0.5 = as in the published\n"
             "results; 0 = back-to-back bursts (as fast as possible, like the Green Metrics Tool)."),
    "WS_STREAM_CLIENTS": dict(default="5 50 100", check=_list(_int(1), "client count"),
        unit="client counts, separated by spaces", help="WebSocket stream: how many clients send at the same time."),
    "WS_STREAM_SIZES_KB": dict(default="8 1024 65536", check=_list(_int(1), "size"),
        unit="KB per message, separated by spaces", help="WebSocket stream: message sizes."),
    "WS_STREAM_RATE_PER_SECOND": dict(default="10", check=_list(_int(1), "rate"),
        unit="messages per second per client, separated by spaces", help="WebSocket stream: sending rate."),
    "WS_STREAM_DURATION_SECONDS": dict(default="5", check=_list(_int(1), "duration"),
        unit="seconds, separated by spaces", help="WebSocket stream: how long each client sends."),
    "WS_CONCURRENCY_CLIENTS": dict(default="100 1000 5000", check=_list(_int(1), "client count"),
        unit="client counts, separated by spaces", help="WebSocket concurrency test: numbers of simultaneous clients."),
    "WS_CONCURRENCY_SIZE_KB": dict(default="8", check=_int(1), unit="KB per message",
        help="WebSocket concurrency test: message size."),
    "WS_PAYLOAD_CLIENTS": dict(default="5", check=_int(1), unit="clients",
        help="WebSocket payload test: number of clients."),
    "WS_PAYLOAD_SIZES_KB": dict(default="8 1024 65536", check=_list(_int(1), "size"),
        unit="KB per message, separated by spaces", help="WebSocket payload test: message sizes."),

    # --- Load and energy measurement ---
    "HTTP_MAX_WORKERS": dict(default="100", check=_workers, unit="threads, 1 or more, or 'system'",
        help="How many requests the HTTP client sends in parallel. 'system' = Python's default (differs per machine)."),
    "HTTP_CONNECTION": dict(default="reuse", check=_choice("reuse", "per-request"),
        help="How the HTTP client uses TCP connections.",
        options={"reuse": "one kept-alive connection per worker, like browsers (recommended)",
                 "per-request": "a new connection for every request, as in the published results;\n"
                                "#                fails above ~28,000 requests when the client runs out of ports"}),
    "SCAPH_STEP_MS": dict(default="500", check=_int(10), unit="milliseconds, 10 or more",
        help="How often Scaphandre reads the power. Smaller = more detail but Scaphandre itself uses more power."),

    # --- Phases around the load ---
    "IDLE_SECONDS": dict(default="0", check=_int(0), unit="seconds, 0 = off",
        help="Measure every server doing nothing for this long, right before its load. Gives its idle\n"
             "energy and power (Idle ... columns in the CSV). 0 = off, as in the published results.\n"
             "Adds this many seconds to every measurement."),
    "WARMUP_SECONDS": dict(default="0", check=_int(0), unit="seconds, 0 = off",
        help="Unmeasured traffic of the same kind as the load (HTTP requests or WebSocket echo messages)\n"
             "for this long after the server starts, so the measurement does not include its very first\n"
             "requests. The readiness check afterwards lets the CPU cool down again. 0 = off, as in the\n"
             "published results."),
}



# Settings that describe how the machine is prepared; the only ones a machine profile may contain
MACHINE_KEYS = [k for k in SCHEMA if k.startswith(("ENV_", "READY_"))] + [
    "ON_BATTERY", "SETTLE_SECONDS", "RESTING_MEASURE_SECONDS"]
PROFILES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs", "machine")


class ConfigError(Exception):
    pass


def _read(text, allowed=None, where=""):
    """{key: (line, checked value)} of the keys set in `text`, and the errors."""
    seen = {}
    errors = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append(f"line {n}: expected KEY=VALUE, got '{raw}'")
            continue
        key, value = (x.strip() for x in line.split("=", 1))
        value = re.sub(r"\s+#.*$", "", value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in SCHEMA:
            hint = difflib.get_close_matches(key, SCHEMA, n=1)
            errors.append(f"{where}line {n}: unknown key '{key}'" + (f" (did you mean {hint[0]}?)" if hint else ""))
            continue
        if allowed is not None and key not in allowed:
            errors.append(f"{where}line {n}: {key} is not a machine setting; put it in the measurement file")
            continue
        if key in seen:
            errors.append(f"{where}line {n}: {key} is set twice (first on line {seen[key][0]})")
            continue
        try:
            seen[key] = (n, SCHEMA[key]["check"](value))
        except ValueError as e:
            errors.append(f"{where}line {n}: {key}={value} {e}")
    return seen, errors


def parse(text, machine_text=""):
    """Return {key: checked value}: the defaults, then the machine profile `machine_text`, then
    the measurement file `text`. Raises ConfigError."""
    machine, errors = _read(machine_text, allowed=MACHINE_KEYS, where="machine profile ")
    seen, more = _read(text)
    errors += more
    cfg = {k: seen[k][1] if k in seen else machine[k][1] if k in machine else SCHEMA[k]["default"]
           for k in SCHEMA}
    if not errors and int(cfg["READY_MAX_WAIT_SECONDS"]) < int(cfg["READY_MIN_WAIT_SECONDS"]):
        errors.append("READY_MAX_WAIT_SECONDS must be at least READY_MIN_WAIT_SECONDS")
    if errors:
        raise ConfigError("\n".join(errors))
    if cfg["SHUFFLE_SEED"] == "":
        cfg["SHUFFLE_SEED"] = str(secrets.randbelow(2**31))
    return cfg


def machine_file(machine, config_path):
    """The profile file MACHINE refers to ("" for none). BENCH_MACHINE_FILE (set when resuming) wins:
    a resumed measurement uses the copy kept in its results folder."""
    override = os.environ.get("BENCH_MACHINE_FILE")
    if override:
        return override
    if machine == "none":
        return ""
    if machine.endswith(".config"):
        here = os.path.join(os.path.dirname(os.path.abspath(config_path)), machine)
        return here if os.path.isfile(here) else machine
    return os.path.join(PROFILES_DIR, f"{machine}.config")


def load(path):
    """Read the measurement file at `path` and its machine profile; returns (cfg, profile path)."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    seen, _ = _read(text)
    machine = seen["MACHINE"][1] if "MACHINE" in seen else SCHEMA["MACHINE"]["default"]
    profile = machine_file(machine, path)
    machine_text = ""
    if profile:
        try:
            with open(profile, encoding="utf-8") as fh:
                machine_text = fh.read()
        except OSError:
            names = sorted(f[:-7] for f in os.listdir(PROFILES_DIR) if f.endswith(".config")) \
                if os.path.isdir(PROFILES_DIR) else []
            raise ConfigError(f"MACHINE={machine}: no profile {profile}" +
                              (f" (profiles: {', '.join(names)})" if names else ""))
    return parse(text, machine_text), profile


# One line per setting, for the example files (the full text is in docs/CONFIG.md)
SHORT = {
    "MACHINE": "machine profile: minimal, tolerable, untouched (configs/machine/), a .config file, or none",
    "MEASURE": "kinds: static dynamic websocket concurrency payload",
    "SERVERS": "server folder names, or TYPE:IMAGE; empty = every server found",
    "BENCHMARKS_DIR": "folder searched for servers; empty = benchmarks/",
    "REPEATS": "runs of every measurement (each repeat = one pass over all servers)",
    "SHUFFLE": "1 = shuffle the server order in every repeat, 0 = same order",
    "SHUFFLE_SEED": "number that decides the order; empty = random (saved, to rerun the same order)",
    "ENV_GOVERNOR": "CPU governor: performance | powersave | schedutil | ondemand | conservative | unchanged",
    "ENV_TURBO": "turbo boost: off | on | unchanged",
    "ENV_STOP_CONTAINERS": "1 = stop other Docker containers (restarted after), 0 = leave them",
    "ENV_KEEP_CONTAINERS": "containers to keep running anyway, comma-separated",
    "ENV_SCREEN_BRIGHTNESS": "screen brightness in % (1 = dimmest still on), or unchanged",
    "ENV_KEYBOARD_LIGHT": "keyboard light: off | unchanged",
    "ENV_WIFI": "Wi-Fi: off | unchanged (off cuts a remote SSH-over-Wi-Fi session, so it refuses then)",
    "ENV_BLUETOOTH": "Bluetooth: off | unchanged",
    "ON_BATTERY": "laptop on battery: wait | stop | ignore",
    "SETTLE_SECONDS": "seconds to let the machine settle after the settings are applied",
    "RESTING_MEASURE_SECONDS": "seconds the resting temperature and CPU use are measured",
    "READY_CHECK_EVERY_SECONDS": "seconds between readiness checks",
    "READY_TEMP_REFERENCE_C": "reference temperature in C; empty = the measured resting temperature",
    "READY_TEMP_MARGIN_C": "ready when CPU temperature <= reference + this (C); empty = no check",
    "READY_CPU_BUSY_REFERENCE_PERCENT": "reference CPU use in %; empty = the measured resting use",
    "READY_CPU_BUSY_MARGIN_PERCENT": "ready when CPU use <= reference + this (%); empty = no check",
    "READY_NO_THROTTLING": "1 = not ready while the CPU throttles, 0 = ignore",
    "READY_CONSECUTIVE_CHECKS": "checks that must pass in a row",
    "READY_MIN_WAIT_SECONDS": "always wait at least this long before a run",
    "READY_MAX_WAIT_SECONDS": "after this long not ready, READY_ON_TIMEOUT decides",
    "READY_ON_TIMEOUT": "still not ready: wait | stop | measure (and record why)",
    "FAILURES_STOP_AFTER": "stop after this many failed measurements in a row (1 = first failure, 0 = never)",
    "RAW_DATA": "Scaphandre's raw power logs: keep | delete",
    "HTTP_REQUESTS": "HTTP load levels (request counts)",
    "WS_BURST_CLIENTS": "burst test: client counts",
    "WS_BURST_SIZES_KB": "burst test: message sizes (KB)",
    "WS_BURST_BURSTS": "burst test: bursts per client",
    "WS_BURST_INTERVAL_SECONDS": "pause between bursts (s); 0 = back to back",
    "WS_STREAM_CLIENTS": "stream test: client counts",
    "WS_STREAM_SIZES_KB": "stream test: message sizes (KB)",
    "WS_STREAM_RATE_PER_SECOND": "stream test: messages per second per client",
    "WS_STREAM_DURATION_SECONDS": "stream test: seconds each client sends",
    "WS_CONCURRENCY_CLIENTS": "concurrency test: client counts",
    "WS_CONCURRENCY_SIZE_KB": "concurrency test: message size (KB)",
    "WS_PAYLOAD_CLIENTS": "payload test: client count",
    "WS_PAYLOAD_SIZES_KB": "payload test: message sizes (KB)",
    "HTTP_MAX_WORKERS": "parallel HTTP requests (threads), or system",
    "HTTP_CONNECTION": "reuse = one kept-alive connection per worker, per-request = a new one each time",
    "SCAPH_STEP_MS": "Scaphandre sampling step (ms)",
    "IDLE_SECONDS": "measure each server idle this long before its load; 0 = off",
    "WARMUP_SECONDS": "unmeasured traffic this long after the server starts; 0 = off",
}

# The example measurement file; machine settings live in the profiles (configs/machine/)
EXAMPLE_LAYOUT = [
    ("Machine profile", ["MACHINE"]),
    ("What to measure", ["MEASURE", "SERVERS", "BENCHMARKS_DIR"]),
    ("How much", ["REPEATS", "HTTP_REQUESTS", "IDLE_SECONDS", "WARMUP_SECONDS"]),
    ("Failures and raw data", ["FAILURES_STOP_AFTER", "RAW_DATA"]),
    ("Order of the runs", ["SHUFFLE", "SHUFFLE_SEED"]),
    ("Load and energy measurement", ["HTTP_MAX_WORKERS", "HTTP_CONNECTION", "SCAPH_STEP_MS"]),
    ("WebSocket workloads (full runs; --quick and --super-quick keep their short lists)",
     ["WS_BURST_CLIENTS", "WS_BURST_SIZES_KB", "WS_BURST_BURSTS", "WS_BURST_INTERVAL_SECONDS",
      "WS_STREAM_CLIENTS", "WS_STREAM_SIZES_KB", "WS_STREAM_RATE_PER_SECOND", "WS_STREAM_DURATION_SECONDS",
      "WS_CONCURRENCY_CLIENTS", "WS_CONCURRENCY_SIZE_KB", "WS_PAYLOAD_CLIENTS", "WS_PAYLOAD_SIZES_KB"]),
]

# docs/CONFIG.md: every setting, the machine ones grouped as in the profiles
DOCS_LAYOUT = EXAMPLE_LAYOUT[:2] + [
    ("Machine settings (configs/machine/ profiles)",
     ["ENV_GOVERNOR", "ENV_TURBO", "ENV_STOP_CONTAINERS", "ENV_KEEP_CONTAINERS", "ENV_SCREEN_BRIGHTNESS",
      "ENV_KEYBOARD_LIGHT", "ENV_WIFI", "ENV_BLUETOOTH", "ON_BATTERY", "SETTLE_SECONDS", "RESTING_MEASURE_SECONDS"]),
    ("Readiness check before every run (configs/machine/ profiles)",
     ["READY_CHECK_EVERY_SECONDS", "READY_TEMP_REFERENCE_C", "READY_TEMP_MARGIN_C",
      "READY_CPU_BUSY_REFERENCE_PERCENT", "READY_CPU_BUSY_MARGIN_PERCENT", "READY_NO_THROTTLING",
      "READY_CONSECUTIVE_CHECKS", "READY_MIN_WAIT_SECONDS", "READY_MAX_WAIT_SECONDS", "READY_ON_TIMEOUT"]),
] + EXAMPLE_LAYOUT[2:]


def example():
    out = ["# Measurement file:  make run CONFIG=bench.config",
           "# Every line is optional; the value shown is the default. Every setting is explained in",
           "# docs/CONFIG.md. The machine settings (CPU, turbo, screen, radios, readiness check) come from",
           "# the MACHINE profile; any of them written here overrides the profile for this measurement.", ""]
    for title, keys in EXAMPLE_LAYOUT:
        out += [f"# ===== {title} ====="]
        for key in keys:
            out += [f"# {SHORT[key]}", f"{key}={SCHEMA[key]['default']}"]
        out.append("")
    return "\n".join(out).rstrip()


def docs():
    out = ["# Benchmark configuration", "",
           "<!-- Generated by: python3 tools/bench_config.py --docs > docs/CONFIG.md (do not edit by hand) -->", "",
           "A measurement is described by a short **measurement file** (`make run CONFIG=my.config`) that names a "
           "**machine profile**:", "",
           "```", "MACHINE=minimal", "MEASURE=static", "HTTP_REQUESTS=1000 20000 80000", "```", "",
           "Values come from the built-in defaults, then the machine profile (`configs/machine/<name>.config`), "
           "then the measurement file, which can override any setting. Every value actually used is written to "
           "`bench.config.resolved` in the results folder.", "",
           "| Profile | For |", "|---|---|",
           "| `minimal` | A machine you control, left alone while it measures: CPU at a fixed speed, turbo off, other "
           "containers stopped, screen at 1%, keyboard light, Wi-Fi and Bluetooth off |",
           "| `tolerable` | As minimal, but Wi-Fi stays on (reached over the network, or must stay online) |",
           "| `untouched` | A machine you do not control: nothing is changed; if it never becomes calm, it measures "
           "anyway and records why |", ""]
    for title, keys in DOCS_LAYOUT:
        out += [f"## {title}", ""]
        for key in keys:
            spec = SCHEMA[key]
            out += [f"### `{key}`", "", f"Default: `{spec['default'] or '(empty)'}`", ""]
            out += [" ".join(spec["help"].split("\n")), ""]
            if "options" in spec:
                out += ["| Option | Meaning |", "|---|---|"]
                out += [f"| `{o}` | {' '.join(m.replace('#', '').split())} |" for o, m in spec["options"].items()]
                out.append("")
            else:
                out += [f"Unit: {spec['unit']}", ""]
    return "\n".join(out).rstrip() + "\n"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--example":
        print(example())
        return
    if sys.argv[1] == "--docs":
        print(docs(), end="")
        return
    try:
        cfg, profile = load(sys.argv[1])
    except OSError as e:
        sys.exit(f"Config: cannot read {sys.argv[1]}: {e}")
    except ConfigError as e:
        sys.exit(f"Config {sys.argv[1]} is invalid:\n{e}")
    for k, v in cfg.items():
        print(f"CFG_{k}={shlex.quote(v)}")
    print(f"BENCH_MACHINE_FILE={shlex.quote(profile)}")


if __name__ == "__main__":
    main()
