"""The measuring core: one measurement of one server, the same steps in the same order for every
workload and every deploy. What differs comes from plugins (tools/plugins/):

  deploy    where the server runs (container, native)
  workload  what load it gets (http, websocket)
  meter     how its energy is measured (scaphandre)

measure_docker.py (HTTP) and measure_websocket.py (WebSocket) are its entry points, with their
command lines as before. The steps of measure():

   1. check the tools; the port must be free; nothing may still be recording energy
   2. start the server (deploy), wait until it answers (workload.probe)
   3. warm-up (unmeasured), then readiness check 2 (the CPU is calm again)
   4. start the meter; the idle window (measured, no load)
   5. the load, with CPU/memory statistics, the conditions watch and the temperatures around it
   6. stop the meter; the energy of the load window; the processes in the server's box; stop the server
   7. judge the conditions (an invalid run is kept apart and measured again), write the CSV row and
      the results-index line
"""
import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

import csv_columns
import load_conditions
import load_phases
import measure_failure
import plugins
import readiness
import results_index
import run_metadata
import server_box
from scaphandre_energy import raw_json_path

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()

# [MEASURE] uses magenta so it is distinct from bash [PROGRESS] (cyan).
_M_MAGENTA = "\033[0;35m"
_M_GREEN = "\033[0;32m"
_M_NC = "\033[0m"


def is_measure_quiet():
    """Match scripts/run_benchmarks.sh: full logs only when BENCH_MEASURE_QUIET is exactly 0.

    Default when unset is quiet (same as bash ``${BENCH_MEASURE_QUIET:-1}``). Any value other than ``0``
    is treated as quiet so bash and Python stay aligned (e.g. typos do not flip to verbose).
    """
    return (os.environ.get("BENCH_MEASURE_QUIET") or "1").strip() != "0"


def measure_quiet_msg(body: str) -> None:
    print(f"{_M_MAGENTA}[MEASURE]{_M_NC} {body}", flush=True)


def measure_quiet_heartbeat_interval_sec():
    try:
        return max(10, int(os.environ.get("MEASURE_HEARTBEAT_SEC", "60")))
    except ValueError:
        return 60


# A measurement started by hand (not by run_benchmarks.sh) writes here
MANUAL_DIR = os.path.join("results", "manual")

# Required tools and how to install them (shown when missing)
REQUIRED_TOOLS = {
    "docker": "Install Docker (e.g. apt install docker.io) and ensure the docker daemon is running.",
    "scaphandre": "Install Scaphandre (e.g. cargo install scaphandre) and ensure it is in PATH.",
}


def check_prerequisites():
    """Check all required tools are available; exit with error before any measurement if not."""
    missing = []
    for name, install_hint in REQUIRED_TOOLS.items():
        result = subprocess.run(["which", name], capture_output=True, text=True, check=False)
        if result.returncode != 0 or not (result.stdout or "").strip():
            missing.append((name, install_hint))
    if missing:
        logger.error("The following required tools are missing. Please install them before running measurements.")
        for name, install_hint in missing:
            logger.error("  - %s: %s", name, install_hint)
        sys.exit(measure_failure.SETUP_BROKEN)


def thermal_reading():
    """CPU package temperature and cumulative throttle milliseconds, read just outside the load window."""
    return run_metadata.cpu_package_temp_c(), run_metadata.throttle_counters()[1]


def thermal_fields(before, after, args, pre_load=(None, "not checked")):
    """Per-run CSV columns: temperatures around the load, throttling during it, and both readiness checks.

    `args.waited_s`/`args.ready_check` come from check 1 (run_benchmarks.sh, before the container
    starts); `pre_load` is (waited_s, result) of check 2 (right before the load).
    """
    ms0, ms1 = before[1], after[1]
    return {
        "Host CPU Temp Start (C)": before[0],
        "Host CPU Temp End (C)": after[0],
        "Host Throttled (ms)": ms1 - ms0 if ms0 != "" and ms1 != "" else "",
        "Waited Before Start (s)": "" if args.waited_s is None else args.waited_s,
        "Waited Before Load (s)": "" if pre_load[0] is None else pre_load[0],
        "Ready Check": readiness.combine(args.ready_check, pre_load[1]),
    }


def pre_load_check(stop_server):
    """Readiness check 2, with the server booted; stops the server if the campaign must stop."""
    try:
        return readiness.pre_load_gate()
    except SystemExit:
        stop_server()
        raise


def wait_until_ready(workload, url):
    """The server's boot time, then health probes until one passes. BEAM/Elixir apps often need
    15-60 s to boot, especially after long runs."""
    startup_wait = int(os.environ.get("MEASURE_STARTUP_WAIT", "15"))
    retries = int(os.environ.get("MEASURE_HEALTH_RETRIES", "25"))
    delay = int(os.environ.get("MEASURE_HEALTH_DELAY", "2"))
    logger.info("Waiting up to %ds for the server at %s (initial %ds, then %d probes every %ds)...",
                startup_wait + retries * delay, url, startup_wait, retries, delay)
    time.sleep(startup_wait)
    for attempt in range(1, retries + 1):
        if workload.probe(url):
            logger.info("Server ready after %d probe(s).", attempt)
            return True
        if attempt < retries:
            time.sleep(delay)
    return False


def add_common_arguments(parser):
    """The options every measurement has (each workload adds its own)."""
    parser.add_argument('--server_image', type=str, required=True, help="Docker image of the server (e.g., nginx-deb)")
    parser.add_argument('--container_name', type=str, default=None, help="Name of the Docker container (defaults to server_image)")
    parser.add_argument('--port_mapping', type=str, default='8001:80', help="Port mapping (default: 8001:80)")
    parser.add_argument('--network', type=str, default='bridge', choices=['bridge', 'host'], help="Network mode (default: bridge)")
    parser.add_argument('--output_csv', type=str, default=None, help="Output CSV file path (default: results/manual/<container_name>.csv)")
    parser.add_argument('--output_json', type=str, default=None, help="Scaphandre's raw log (default: results/manual/raw/<time>.json)")
    parser.add_argument('--verbose', action='store_true', help="Enable verbose logging")
    parser.add_argument('--deploy', choices=plugins.names("deploy"), default='container',
                        help="where the server runs (tools/plugins/deploy/): container = the image in Docker (default); "
                             "native = the image's own program without Docker, in a systemd user scope")
    parser.add_argument('--meter', choices=plugins.names("meter"), default='scaphandre',
                        help="how its energy is measured (tools/plugins/meter/; default: scaphandre)")
    parser.add_argument('--waited_s', type=float, default=None, help="Seconds the readiness gate waited before this run (set by run_benchmarks.sh --config)")
    parser.add_argument('--ready_check', type=str, default="not checked", help="Result of the readiness gate before this run (set by run_benchmarks.sh --config)")
    parser.add_argument('--repeat', type=int, default=1, help="Quick manual check only: run this measurement N times with a fixed --cooldown, without machine settings or readiness checks. For real measurements use: make run CONFIG=bench.config (default: 1)")
    parser.add_argument('--cooldown', type=int, default=30, help="Seconds to rest between repeated runs (default: 30; only applies when --repeat > 1)")


def run_repeats(args, script, workload):
    """Run the measurement args.repeat times, each as a fresh process, then summarise.

    Each repeat is a separate run of the same script with --repeat 1, so it boots its own server
    and does its own load. All runs append to one CSV, and then tools/aggregate_repeats.py turns
    those rows into an average with a give-or-take.
    """
    container_name = args.container_name or args.server_image
    if args.output_csv:
        target_csv = args.output_csv
    else:
        os.makedirs(MANUAL_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        target_csv = os.path.join(MANUAL_DIR, f"{container_name}_{stamp}_repeats.csv")
    base_cmd = [sys.executable, os.path.abspath(script), "--server_image", args.server_image,
                "--port_mapping", args.port_mapping, "--network", args.network, "--output_csv", target_csv,
                "--repeat", "1", *workload.repeat_args(args)]
    if args.container_name:
        base_cmd += ["--container_name", args.container_name]
    base_cmd += ["--deploy", args.deploy]
    if args.verbose:
        base_cmd += ["--verbose"]

    logger.warning("Quick repeat mode (no machine settings or readiness checks; "
                   "for real measurements use: make run CONFIG=bench.config)")
    logger.warning("Repeat mode: %d runs of '%s', %ds cooldown between runs -> %s",
                   args.repeat, container_name, args.cooldown, target_csv)
    meta_path = os.path.splitext(target_csv)[0] + "_metadata.json"
    tool = os.path.splitext(os.path.basename(script))[0]
    run_metadata.write_start(meta_path, {"tool": tool, "repeat": args.repeat, "cooldown_s": args.cooldown,
                                          "command": " ".join(base_cmd[2:])})
    completed = 0
    any_failed = False
    for i in range(1, args.repeat + 1):
        logger.warning("--- run %d of %d ---", i, args.repeat)
        if subprocess.run(base_cmd).returncode != 0:
            logger.error("Run %d failed; stopping repeats.", i)
            any_failed = True
            break
        completed += 1
        if i < args.repeat and args.cooldown > 0:
            logger.warning("Cooldown %ds ...", args.cooldown)
            time.sleep(args.cooldown)

    run_metadata.write_end(meta_path, [target_csv])
    if completed == 0:
        logger.error("No runs completed; nothing to summarise.")
        return 1
    aggregator = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aggregate_repeats.py")
    logger.warning("Summarising %d run(s) ...", completed)
    rc = subprocess.run([sys.executable, aggregator, target_csv]).returncode
    if rc != 0:
        logger.error("Summary step failed (aggregate_repeats.py exited %d).", rc)
        return 1
    return 1 if any_failed else 0


def main(script, workload_name, description):
    """Entry point of a measuring script: its command line, then one measurement (or --repeat)."""
    workload = plugins.load("workload", workload_name)()
    parser = argparse.ArgumentParser(description=description)
    add_common_arguments(parser)
    workload.add_arguments(parser)
    args = parser.parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    elif is_measure_quiet():
        logger.setLevel(logging.WARNING)
    # Repeat mode: run the whole measurement several times (each a fresh run), then
    # summarise with tools/aggregate_repeats.py. Handled before any measurement setup.
    if args.repeat and args.repeat > 1:
        sys.exit(run_repeats(args, script, workload))
    measure(args, workload)


def port_in_use(host_port):
    """Stop before anything starts when the port is taken (and say by whom)."""
    if f":{host_port} " not in subprocess.run(["ss", "-ltn"], capture_output=True, text=True).stdout:
        return
    logger.error(f"[ERROR] Port {host_port} is already in use. Please stop the process or container using it before running the benchmark.")
    users = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
    logger.error("[INFO] The following processes are using port %s:\n%s", host_port,
                 '\n'.join(line for line in users.splitlines() if f":{host_port} " in line))
    containers = subprocess.run(["docker", "ps", "--filter", f"publish={host_port}"], capture_output=True, text=True).stdout
    logger.error("[INFO] Docker containers using this port:\n%s", containers)
    measure_failure.fail(f"port {host_port} already in use")


def measure(args, workload):
    """One measurement of one server (the steps in the module docstring)."""
    quiet = is_measure_quiet() and not args.verbose
    say = measure_quiet_msg if quiet else (lambda body: None)

    # 1. tools, paths, port, meter
    check_prerequisites()  # Exit with error before any measurement if anything is missing
    docker_path = subprocess.run(["which", "docker"], capture_output=True, text=True, check=True).stdout.strip()
    num_cores = os.cpu_count()
    name = args.container_name or args.server_image
    output_json = args.output_json or raw_json_path(name, args.measurement_type)
    output_csv = args.output_csv or os.path.join(MANUAL_DIR, f"{name}.csv")
    if os.path.dirname(output_csv):
        os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    port_in_use(args.port_mapping.split(":")[0])
    meter = plugins.load("meter", args.meter)(output_json)
    meter.prepare()

    # 2. the server, until it answers
    deploy = plugins.load("deploy", args.deploy)(args.server_image, name, args.port_mapping, args.network, docker_path)
    say(f"{name} | {deploy.label} + {workload.name} readiness wait …")
    logger.info(f"Starting container '{name}'...")
    deploy.start()
    url = workload.url(args)
    if not wait_until_ready(workload, url):
        logger.error("Server '%s' failed its %s health check.", name, workload.name)
        logger.error("Server output (last 50 lines):\n%s", deploy.log_tail(50))
        logger.error("To allow more boot time: MEASURE_STARTUP_WAIT=25 MEASURE_HEALTH_RETRIES=30 make run")
        measure_failure.fail(workload.health_failure())

    # 3. warm-up, then readiness check 2: booting the server warms the CPU, so wait again right before the load
    if args.warmup_s > 0:
        say(f"{name} | warm-up {args.warmup_s:g}s (not measured) …")
        workload.warm_up(args, url)
    pre_load = pre_load_check(deploy.stop)

    # 4. the meter, and the idle window
    say(f"{name} | Scaphandre power sampling + {workload.name} load | {workload.describe(args, url)}")
    logger.info("Starting Scaphandre...")
    meter.start()
    time.sleep(2)
    if args.idle_s > 0:
        say(f"{name} | idle {args.idle_s:g}s (measured, {workload.idle_note}) …")
    idle = load_phases.idle_window(args.idle_s)

    # 5. the load, with statistics, the conditions watch, the temperatures around it, a heartbeat
    stop_event = threading.Event()
    resources = {'cpu': {}, 'mem': {}}

    def collect():
        resources['cpu'], resources['mem'] = deploy.collect_stats(stop_event)
    resource_thread = threading.Thread(target=collect)
    resource_thread.start()
    logger.info("Sleeping 1s to let the statistics stabilize...")
    time.sleep(1)

    hb_stop = threading.Event()
    hb_thread = None
    load_t0 = time.time()
    if quiet:
        iv = measure_quiet_heartbeat_interval_sec()

        def heartbeat():
            while not hb_stop.wait(iv):
                measure_quiet_msg(f"{name} | {workload.progress(args)} ({int(time.time() - load_t0)}s elapsed)")
        hb_thread = threading.Thread(target=heartbeat, daemon=True)
        hb_thread.start()

    thermal_before = thermal_reading()
    # Conditions during the load (CPU speed, throttling, charger), judged before the row is written
    watch = load_conditions.Watch().start()
    start_time = time.time()
    try:
        workload.run(args, url)
    finally:
        if hb_thread is not None:
            hb_stop.set()
            hb_thread.join(timeout=3)
    end_time = time.time()
    watch.stop()
    thermal_after = thermal_reading()
    runtime = end_time - start_time
    time.sleep(3)
    stop_event.set()
    resource_thread.join()

    # 6. the energy of the load window, the processes in the server's box, stop the server
    say(f"{name} | stopping Scaphandre + appending CSV …")
    logger.info("Waiting for Scaphandre...")
    time.sleep(5)
    meter.stop()
    energy_id = deploy.energy_id()
    energy = meter.energy(name, energy_id, start_time, end_time)
    phases, output_json = meter.finish(name, energy_id, start_time, end_time, energy, idle, args.warmup_s)
    cpu_limit = deploy.cpu_limit()
    # Which processes ran in the server's box (README server contract: only the server runs)
    box_processes = server_box.processes(deploy.box())
    for helper in server_box.helpers(box_processes):
        logger.warning("%s | not only the server ran in its box: %s (README: server contract)", name, helper)
    deploy.stop()

    # 7. judge, then the CSV row and the results-index line
    cpu, mem = resources['cpu'], resources['mem']
    values = {
        "Container Name": name, **csv_columns.run_fields(args.deploy),
        **workload.values(args, runtime),
        "Execution Time (s)": float(runtime),
        "Container CPU Limit": cpu_limit, "Container Energy (J)": float(energy["energy_j"]),
        "Container Avg Power (W)": float(energy["avg_power_w"]),
        "Container Avg CPU (%)": float(cpu.get('avg', 0.0)), "Container Peak CPU (%)": float(cpu.get('peak', 0.0)),
        "Container Total CPU (%*s)": float(cpu.get('total', 0.0)), "Container Avg Mem (MB)": float(mem.get('avg', 0.0)),
        "Container Peak Mem (MB)": float(mem.get('peak', 0.0)), "Container Total Mem (MB*s)": float(mem.get('total', 0.0)),
        "Host CPUs": int(num_cores) if num_cores is not None else 1,
        "Host Energy (J)": round(energy["host_energy_j"], 6), "Host Avg Power (W)": round(energy["host_avg_power_w"], 6),
        **thermal_fields(thermal_before, thermal_after, args, pre_load), **watch.fields(),
        **phases,
        "Energy Samples": int(energy["samples"]), "Energy Sampling Step (ms)": energy["step_ms"],
        "Energy Window Coverage": round(energy["coverage"], 4),
        "Raw Log": csv_columns.raw_log_field(output_json),
        "Server Processes": server_box.describe(box_processes),
    }
    index = workload.index(args)
    load_conditions.judge(watch, values, output_csv, workload.measurement(args), args.server_image, index)
    csv_columns.append(output_csv, workload.columns, values)
    results_index.record(output_csv, values, args.server_image, index)

    success, total = workload.counts()
    rate = total / runtime if runtime > 0 else 0
    if quiet:
        cnt = f"{_M_GREEN}{success}/{total} ok{_M_NC}" if success == total else f"{success}/{total}"
        measure_quiet_msg(f"{name} | {cnt} | {runtime:.1f}s | {rate:.0f} {workload.rate_unit} | {output_csv}")
    else:
        logger.info("=== Measurement Summary ===")
        logger.info(f"Container: {name}")
        for line in workload.summary(args, runtime):
            logger.info(line)
        logger.info(f"Total: {total}, Successful: {success}, Failed: {total - success}")
        logger.info(f"Execution Time: {runtime:.2f} s, {workload.rate_unit}: {rate:.2f}")
        logger.info(f"Energy: Total {energy['energy_j']:.2f} J, Avg Power {energy['avg_power_w']:.2f} W")
        logger.info(f"CPU: Avg {cpu.get('avg', 0.0):.2f}%, Peak {cpu.get('peak', 0.0):.2f}%, Total {cpu.get('total', 0.0):.2f} %*s")
        logger.info(f"Memory: Avg {mem.get('avg', 0.0):.2f} MB, Peak {mem.get('peak', 0.0):.2f} MB, Total {mem.get('total', 0.0):.2f} MB*s")
        logger.info(f"JSON: {output_json}, CSV: {output_csv}")
        logger.info("==========================")
