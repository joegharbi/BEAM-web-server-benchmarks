import os
import sys
import time
import subprocess
import requests
import csv
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import argparse
import json
import threading
from datetime import datetime
import logging
import psutil

import measure_failure
import native_server
import readiness
import run_metadata
from scaphandre_energy import compute_window_energy, finish_raw, raw_json_path, scaphandre_json_args, window_record
import load_phases
import csv_columns

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()

def is_measure_quiet():
    """Match scripts/run_benchmarks.sh: full measure_docker logs only when BENCH_MEASURE_QUIET is exactly 0.

    Default when unset is quiet (same as bash ``${BENCH_MEASURE_QUIET:-1}``). Any value other than ``0``
    is treated as quiet so bash and Python stay aligned (e.g. typos do not flip to verbose).
    """
    v = (os.environ.get("BENCH_MEASURE_QUIET") or "1").strip()
    return v != "0"

# [MEASURE] uses magenta so it is distinct from bash [PROGRESS] (cyan).
_M_MAGENTA = "\033[0;35m"
_M_GREEN = "\033[0;32m"
_M_NC = "\033[0m"


def http_max_workers_label(args):
    return "System default" if args.max_workers is None else str(int(args.max_workers))


def measure_quiet_msg(body: str) -> None:
    print(f"{_M_MAGENTA}[MEASURE]{_M_NC} {body}", flush=True)


def measure_quiet_heartbeat_interval_sec():
    try:
        return max(10, int(os.environ.get("MEASURE_HEARTBEAT_SEC", "60")))
    except ValueError:
        return 60


results_counter = Counter()
runtime_data = {}
results_lock = threading.Lock()

def get_binary_path(binary_name):
    result = subprocess.run(["which", binary_name], capture_output=True, text=True, check=True)
    return result.stdout.strip() or f"'{binary_name}' not found"

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
        path = (result.stdout or "").strip()
        if result.returncode != 0 or not path:
            missing.append((name, install_hint))
    if missing:
        logger.error("The following required tools are missing. Please install them before running measurements.")
        for name, install_hint in missing:
            logger.error("  - %s: %s", name, install_hint)
        sys.exit(measure_failure.SETUP_BROKEN)

# One HTTP session per worker thread, so "reuse" mode keeps one keep-alive
# connection per worker instead of opening a new TCP connection per request.
_thread_local = threading.local()

def _get(url, connection_mode):
    if connection_mode == "reuse":
        session = getattr(_thread_local, "session", None)
        if session is None:
            session = requests.Session()
            _thread_local.session = session
        return session.get(url, timeout=5)
    # "per-request": a fresh connection for every request, closed by the client.
    # This is how the published results were produced; with servers that keep the
    # connection open, the client's closed sockets pile up in TIME_WAIT and can
    # exhaust the ephemeral port range at high request counts.
    return requests.get(url, timeout=5)

def send_request(url, request_num, verbose=False, connection_mode="reuse"):
    try:
        response = _get(url, connection_mode)
        if verbose:
            logger.debug(f'{url} "GET / HTTP/1.1" {response.status_code} {len(response.content)}')
        with results_lock:
            if 200 <= response.status_code < 300:
                results_counter['success'] += 1
            else:
                results_counter['failure'] += 1
    except requests.exceptions.RequestException:
        with results_lock:
            results_counter['failure'] += 1
    finally:
        with results_lock:
            results_counter['total'] += 1

def warm_up(url, seconds, max_workers, connection_mode):
    """Unmeasured requests for `seconds`, with the same client settings as the load."""
    if seconds <= 0:
        return
    deadline = time.time() + seconds

    def worker(_):
        while time.time() < deadline:
            try:
                _get(url, connection_mode)
            except requests.exceptions.RequestException:
                pass

    workers = max_workers or min(32, (os.cpu_count() or 1) + 4)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(worker, range(workers)))


def cleanup_existing_container(container_name, docker_path):
    logger.info(f"Cleaning up any existing container named '{container_name}'...")
    subprocess.run([docker_path, "stop", container_name], capture_output=True, text=True, check=False)
    subprocess.run([docker_path, "rm", "-f", container_name], capture_output=True, text=True, check=False)
    # Wait and check if container is really gone
    for _ in range(5):
        result = subprocess.run([docker_path, "ps", "-a", "--filter", f"name={container_name}", "--format", "{{{{.Names}}}}"], capture_output=True, text=True)
        if container_name not in result.stdout:
            break
        time.sleep(1)
    else:
        logger.warning(f"Container '{container_name}' could not be removed after multiple attempts.")
    time.sleep(3)  # Ensure port and resources released (important after long runs)

def cleanup_existing_scaphandre():
    subprocess.run(["sudo", "pkill", "-9", "scaphandre"], capture_output=True, text=True, check=False)
    time.sleep(2)  # Ensure OS releases resources

def start_scaphandre(output_json, scaphandre_path):
    os.makedirs("output", exist_ok=True)
    cmd = ["sudo", scaphandre_path] + scaphandre_json_args(output_json)
    scaphandre_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    time.sleep(2)
    if scaphandre_process.poll() is not None:
        out, err = scaphandre_process.communicate(timeout=1)
        err = (err or "").strip()
        out = (out or "").strip()
        logger.error("Scaphandre failed to start (exit code %s).", scaphandre_process.returncode)
        if err:
            logger.error("Scaphandre stderr: %s", err)
        if out:
            logger.error("Scaphandre stdout: %s", out)
        raise RuntimeError("Scaphandre failed to start")
    return scaphandre_process

def stop_scaphandre(scaphandre_process):
    scaphandre_process.terminate()
    scaphandre_process.wait(timeout=5)
    time.sleep(2)  # Ensure OS releases resources

def check_container_health(url, retries=None, delay=None, startup_wait=None):
    """Wait for container to respond with HTTP 200. BEAM/Elixir apps often need 15–60s to boot, especially after long runs."""
    startup_wait = int(os.environ.get("MEASURE_STARTUP_WAIT", startup_wait or 15))
    retries = int(os.environ.get("MEASURE_HEALTH_RETRIES", retries or 25))
    delay = int(os.environ.get("MEASURE_HEALTH_DELAY", delay or 2))
    total_max = startup_wait + retries * delay
    logger.info("Waiting up to %ds for container (initial %ds, then %d retries every %ds)...", total_max, startup_wait, retries, delay)
    time.sleep(startup_wait)
    for attempt in range(1, retries + 1):
        try:
            if requests.get(url, timeout=10).status_code == 200:
                logger.info("Container ready after %d attempt(s).", attempt)
                return True
        except requests.exceptions.RequestException:
            if attempt < retries:
                time.sleep(delay)
    return False

def start_server_container(server_image, port_mapping, container_name, docker_path, network="bridge"):
    cleanup_existing_container(container_name, docker_path)
    # --cgroupns=host: needed for Scaphandre to detect container names on cgroups v2
    cmd = [docker_path, "run", "-d", "--cgroupns=host", "--ulimit", "nofile=100000:100000", "--name", container_name]
    # The server listens on PORT (server contract, README): the container side of the mapping.
    cmd.extend(["-e", f"PORT={port_mapping.split(':')[-1]}"])
    if network == "host":
        cmd.extend(["--network", "host"])
    else:
        cmd.extend(["-p", port_mapping])
    cmd.append(server_image)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("Failed to start container. Docker stderr: %s", result.stderr or "(none)")
        logger.error("If --cgroupns=host is unsupported, try: docker run --rm --cgroupns=host hello-world")
        raise RuntimeError("Container failed to start")
    time.sleep(5)

def stop_server_container(container_name, docker_path):
    subprocess.run([docker_path, "stop", container_name], capture_output=True, text=True, check=True)
    subprocess.run([docker_path, "rm", container_name], capture_output=True, text=True, check=True)
    time.sleep(2)  # Ensure Docker/OS releases resources

def collect_resources_docker_stats(container_name, stop_event, docker_path, interval=0.5):
    import re
    cpu_usage = []
    mem_usage = []
    sample_count = 0
    while not stop_event.is_set():
        try:
            stats_format = "{{.CPUPerc}},{{.MemUsage}}"
            cmd = [docker_path, "stats", container_name, "--no-stream", "--format", stats_format]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            output = result.stdout.strip()
            if not output:
                cpu_usage.append(0.0)
                mem_usage.append(0.0)
                time.sleep(interval)
                sample_count += 1
                continue
            cpu_str, mem_str = output.split(',')
            cpu_val = float(cpu_str.strip().replace('%',''))
            mem_usage_part = mem_str.strip().split('/')[0].strip()
            mem_match = re.match(r"([\d.]+)([KMG]iB)", mem_usage_part)
            mem_val = 0.0
            if mem_match:
                mem_num = float(mem_match.group(1))
                mem_unit = mem_match.group(2)
                if mem_unit == 'KiB':
                    mem_val = mem_num / 1024
                elif mem_unit == 'MiB':
                    mem_val = mem_num
                elif mem_unit == 'GiB':
                    mem_val = mem_num * 1024
            cpu_usage.append(cpu_val)
            mem_usage.append(mem_val)
        except Exception:
            cpu_usage.append(0.0)
            mem_usage.append(0.0)
        time.sleep(interval)
        sample_count += 1
    cpu_avg = sum(cpu_usage) / len(cpu_usage) if cpu_usage else 0.0
    cpu_peak = max(cpu_usage) if cpu_usage else 0.0
    cpu_total = sum(cpu_usage) * interval if cpu_usage else 0.0  # cumulative CPU (%*s)
    mem_avg = sum(mem_usage) / len(mem_usage) if mem_usage else 0.0
    mem_peak = max(mem_usage) if mem_usage else 0.0
    mem_total = sum(mem_usage) * interval if mem_usage else 0.0  # cumulative memory (MB*s)
    return {'avg': cpu_avg, 'peak': cpu_peak, 'total': cpu_total}, \
           {'avg': mem_avg, 'peak': mem_peak, 'total': mem_total}


def save_results_to_csv(filename, values):
    """Append one run to the CSV, in the column order of tools/csv_columns.py (HTTP)."""
    if filename is None:
        filename = os.path.join("results_docker", f"{values['Container Name']}.csv")
    csv_columns.append(filename, csv_columns.HTTP_COLUMNS, values)


def print_summary(results, total_energy, average_power, runtime, requests_per_second, cpu_metrics, mem_metrics, num_cores, output_json, output_csv, container_name, http_max_workers_label=None):
    logger.info("=== Measurement Summary ===")
    logger.info(f"Container: {container_name}")
    if http_max_workers_label is not None:
        logger.info("HTTP max workers: %s", http_max_workers_label)
    logger.info(f"Total Requests: {results['total']}, Successful: {results['success']}, Failed: {results['failure']}")
    logger.info(f"Execution Time: {runtime:.2f} s, Requests/s: {requests_per_second:.2f}")
    logger.info(f"Energy: Total {total_energy:.2f} J, Avg Power {average_power:.2f} W")
    logger.info(f"CPU: Avg {cpu_metrics['avg']:.2f}%, Peak {cpu_metrics['peak']:.2f}%, Total {cpu_metrics['total']:.2f} %*s")
    logger.info(f"Memory: Avg {mem_metrics['avg']:.2f} MB, Peak {mem_metrics['peak']:.2f} MB, Total {mem_metrics['total']:.2f} MB*s")
    logger.info(f"JSON: {output_json}, CSV: {output_csv or f'results_docker/{container_name}.csv'}")
    logger.info("==========================")



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


def run_repeats(args):
    """Run the measurement args.repeat times, each as a fresh process, then summarise.

    Each repeat is a separate run of this same script with --repeat 1, so it boots
    its own container and does its own load. All runs append to one CSV, and then
    tools/aggregate_repeats.py turns those rows into an average with a give-or-take.
    """
    import subprocess
    import sys

    container_name = args.container_name or args.server_image
    if args.output_csv:
        target_csv = args.output_csv
    else:
        os.makedirs("results_docker", exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        target_csv = os.path.join("results_docker", f"{container_name}_{stamp}_repeats.csv")

    base_cmd = [
        sys.executable, os.path.abspath(__file__),
        "--server_image", args.server_image,
        "--port_mapping", args.port_mapping,
        "--network", args.network,
        "--num_requests", str(args.num_requests),
        "--output_csv", target_csv,
        "--repeat", "1",
        "--connection", args.connection,
    ]
    if args.container_name:
        base_cmd += ["--container_name", args.container_name]
    if args.max_workers is not None:
        base_cmd += ["--max_workers", str(args.max_workers)]
    if args.measurement_type:
        base_cmd += ["--measurement_type", args.measurement_type]
    if args.verbose:
        base_cmd += ["--verbose"]

    logger.warning("Quick repeat mode (no machine settings or readiness checks; "
                   "for real measurements use: make run CONFIG=bench.config)")
    logger.warning("Repeat mode: %d runs of '%s', %ds cooldown between runs -> %s",
                   args.repeat, container_name, args.cooldown, target_csv)
    meta_path = os.path.splitext(target_csv)[0] + "_metadata.json"
    run_metadata.write_start(meta_path, {"tool": "measure_docker", "repeat": args.repeat, "cooldown_s": args.cooldown,
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


def main():
    parser = argparse.ArgumentParser(description="Measure web server energy with Scaphandre in Docker")
    parser.add_argument('--server_image', type=str, required=True, help="Docker image of the server (e.g., nginx-deb)")
    parser.add_argument('--container_name', type=str, default=None, help="Name of the Docker container (defaults to server_image)")
    parser.add_argument('--port_mapping', type=str, default='8001:80', help="Port mapping (default: 8001:80)")
    parser.add_argument('--network', type=str, default='bridge', choices=['bridge', 'host'], help="Network mode (default: bridge)")
    parser.add_argument('--num_requests', type=int, default=500, help="Number of requests to send (default: 500)")
    parser.add_argument('--max_workers', type=int, default=None, help="Max workers for ThreadPoolExecutor (default: None; CSV records System default when unset)")
    parser.add_argument('--output_csv', type=str, default=None, help="Output CSV file path (default: results_docker/<container_name>.csv)")
    parser.add_argument('--output_json', type=str, default=None, help="Output JSON file path (default: output/<timestamp>.json)")
    parser.add_argument('--verbose', action='store_true', help="Enable verbose logging")
    parser.add_argument('--deploy', choices=['container', 'native'], default='container',
                        help="container: the image in Docker (default); native: the image's own program without Docker, "
                             "in a systemd user scope (tools/native_server.py)")
    parser.add_argument('--measurement_type', type=str, default=None, help="Type of measurement (static, dynamic, etc.)")
    parser.add_argument('--connection', choices=['reuse', 'per-request'], default='reuse',
                        help="HTTP connection handling: 'reuse' keeps one keep-alive connection per worker (default); "
                             "'per-request' opens a new connection for every request, as in the published results")
    parser.add_argument('--warmup_s', type=float, default=load_phases.default_warmup_s(),
                        help="Seconds of unmeasured requests after boot, before the measurement (default: 0, or WARMUP_SECONDS of the config)")
    parser.add_argument('--idle_s', type=float, default=load_phases.default_idle_s(),
                        help="Seconds the idle server is measured right before the load (default: 0, or IDLE_SECONDS of the config)")
    parser.add_argument('--waited_s', type=float, default=None, help="Seconds the readiness gate waited before this run (set by run_benchmarks.sh --config)")
    parser.add_argument('--ready_check', type=str, default="not checked", help="Result of the readiness gate before this run (set by run_benchmarks.sh --config)")
    parser.add_argument('--repeat', type=int, default=1, help="Quick manual check only: run this measurement N times with a fixed --cooldown, without machine settings or readiness checks. For real measurements use: make run CONFIG=bench.config (default: 1)")
    parser.add_argument('--cooldown', type=int, default=30, help="Seconds to rest between repeated runs (default: 30; only applies when --repeat > 1)")

    args = parser.parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    elif is_measure_quiet():
        logger.setLevel(logging.WARNING)

    # Repeat mode: run the whole measurement several times (each a fresh run), then
    # summarise with tools/aggregate_repeats.py. Handled before any measurement setup.
    if args.repeat and args.repeat > 1:
        sys.exit(run_repeats(args))

    check_prerequisites()  # Exit with error before any measurement if anything is missing
    scaphandre_path = get_binary_path("scaphandre")
    docker_path = get_binary_path("docker")
    num_cores = os.cpu_count()
    
    output_json = args.output_json or raw_json_path(args.container_name or args.server_image, args.measurement_type)
    url = "http://localhost:80/" if args.network == "host" else f"http://localhost:{args.port_mapping.split(':')[0]}/"
    container_name = args.container_name or args.server_image

    # Port-in-use check before starting container
    host_port = args.port_mapping.split(":")[0]
    import subprocess
    result = subprocess.run(["ss", "-ltn"], capture_output=True, text=True)
    if f":{host_port} " in result.stdout:
        logger.error(f"[ERROR] Port {host_port} is already in use. Please stop the process or container using it before running the benchmark.")
        result2 = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True)
        logger.error("[INFO] The following processes are using port %s:\n%s", host_port, '\n'.join([line for line in result2.stdout.splitlines() if f":{host_port} " in line]))
        result3 = subprocess.run(["docker", "ps", "--filter", f"publish={host_port}"], capture_output=True, text=True)
        logger.error("[INFO] Docker containers using this port:\n%s", result3.stdout)
        measure_failure.fail(f"port {host_port} already in use")

    cleanup_existing_scaphandre()
    if is_measure_quiet() and not args.verbose:
        measure_quiet_msg(f"{container_name} | Docker start + HTTP readiness wait …")
    logger.info(f"Starting container '{container_name}'...")
    native = args.deploy == "native"
    if native:
        # The scope name takes the place of the container ID (energy by cgroup, stop, statistics)
        unit = native_server.unit_name(container_name)
        measure_failure.started_native(unit)
        native_server.start(args.server_image, container_name, host_port, docker_path)
        stop_server = lambda: native_server.stop(unit)  # noqa: E731
    else:
        measure_failure.started_container(container_name, docker_path)
        start_server_container(args.server_image, args.port_mapping, container_name, docker_path, args.network)
        stop_server = lambda: stop_server_container(container_name, docker_path)  # noqa: E731

    if not check_container_health(url):
        logger.error("Container health check failed (no HTTP 200 within wait time).")
        if native:
            logger.error("Server output (last 30 lines):\n%s", native_server.log_tail(unit))
        else:
            try:
                out = subprocess.run(
                    [docker_path, "logs", "--tail", "30", container_name],
                    capture_output=True, text=True, timeout=5
                )
                if out.stdout or out.stderr:
                    logger.error("Container logs (last 30 lines):\n%s%s", out.stdout or "", out.stderr or "")
            except Exception as e:
                logger.debug("Could not get container logs: %s", e)
        logger.error("To allow more boot time: MEASURE_STARTUP_WAIT=25 MEASURE_HEALTH_RETRIES=30 make run")
        measure_failure.fail("health check failed: no HTTP 200 from the container within the wait time")

    if args.warmup_s > 0:
        if is_measure_quiet() and not args.verbose:
            measure_quiet_msg(f"{container_name} | warm-up {args.warmup_s:g}s (not measured) …")
        warm_up(url, args.warmup_s, args.max_workers, args.connection)

    # Readiness check 2: booting the server warms the CPU, so wait again right before the load.
    pre_load = pre_load_check(stop_server)

    if is_measure_quiet() and not args.verbose:
        measure_quiet_msg(
            f"{container_name} | Scaphandre power sampling + HTTP load | "
            f"{args.num_requests} GET → {url}"
        )
    logger.info("Starting Scaphandre...")
    scaphandre_process = start_scaphandre(output_json, scaphandre_path)

    logger.info(f"Sending {args.num_requests} requests to {url}...")
    time.sleep(2)
    if args.idle_s > 0 and is_measure_quiet() and not args.verbose:
        measure_quiet_msg(f"{container_name} | idle {args.idle_s:g}s (measured, no requests) …")
    idle = load_phases.idle_window(args.idle_s)

    stop_event = threading.Event()
    resource_results = {'cpu': {}, 'mem': {}}
    def collect():
        if native:
            cpu_metrics, mem_metrics = native_server.collect_stats(unit, stop_event)
        else:
            cpu_metrics, mem_metrics = collect_resources_docker_stats(container_name, stop_event, docker_path)
        resource_results['cpu'] = cpu_metrics
        resource_results['mem'] = mem_metrics

    resource_thread = threading.Thread(target=collect)
    resource_thread.start()

    logger.info("Sleeping 1s to let docker stats stabilize...")
    time.sleep(1)

    hb_stop = threading.Event()
    hb_thread = None
    load_t0 = time.time()
    if is_measure_quiet() and not args.verbose:
        iv = measure_quiet_heartbeat_interval_sec()

        def _heartbeat_worker():
            while not hb_stop.wait(iv):
                with results_lock:
                    done = results_counter["total"]
                measure_quiet_msg(
                    f"{container_name} | HTTP requests {done}/{args.num_requests} "
                    f"({int(time.time() - load_t0)}s elapsed)"
                )

        hb_thread = threading.Thread(target=_heartbeat_worker, daemon=True)
        hb_thread.start()

    thermal_before = thermal_reading()
    charger_before = run_metadata.ac_power()
    start_time = time.time()
    try:
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            executor.map(lambda i: send_request(url, i, args.verbose, args.connection), range(args.num_requests))
    finally:
        if hb_thread is not None:
            hb_stop.set()
            hb_thread.join(timeout=3)
    end_time = time.time()
    thermal_after = thermal_reading()
    if load_phases.charger_unplugged(charger_before, run_metadata.ac_power()):
        measure_failure.fail("the laptop ran on battery during the load (charger unplugged)")
    runtime = end_time - start_time
    runtime_data['runtime'] = runtime

    time.sleep(3)
    stop_event.set()
    resource_thread.join()

    requests_per_second = results_counter['total'] / runtime if runtime > 0 else 0

    if is_measure_quiet() and not args.verbose:
        measure_quiet_msg(f"{container_name} | stopping Scaphandre + appending CSV …")
    logger.info("Waiting for Scaphandre...")
    time.sleep(5)
    stop_scaphandre(scaphandre_process)
    container_id = unit if native else None
    if not native:
        result = subprocess.run(
            [docker_path, "ps", "-q", "-f", f"name={container_name}"],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            container_id = result.stdout.strip()
    energy = compute_window_energy(output_json, container_name, start_time, end_time, container_id=container_id)
    phases = load_phases.idle_fields(output_json, container_name, container_id, idle, args.warmup_s)
    record = window_record(container_name, container_id, start_time, end_time, energy)
    if idle:
        record["idle_start_epoch"], record["idle_end_epoch"] = idle
    output_json = finish_raw(output_json, record)
    total_energy, average_power, total_samples = energy["energy_j"], energy["avg_power_w"], energy["samples"]
    cpu_limit = "none" if native else csv_columns.container_cpu_limit(docker_path, container_name)
    stop_server()
    measurement_type = getattr(args, 'measurement_type', None) or "unknown"
    http_workers_label = http_max_workers_label(args)
    cpu, mem = resource_results['cpu'], resource_results['mem']
    save_results_to_csv(args.output_csv, {
        "Container Name": args.server_image, **csv_columns.run_fields(),
        "Type": measurement_type, "Total Requests": int(results_counter['total']),
        "HTTP Max Workers": http_workers_label, "HTTP Connection Mode": args.connection,
        "Successful Requests": int(results_counter['success']), "Failed Requests": int(results_counter['failure']),
        "Execution Time (s)": float(runtime), "Requests/s": float(requests_per_second),
        "Container CPU Limit": cpu_limit, "Container Energy (J)": float(total_energy),
        "Container Avg Power (W)": float(average_power),
        "Container Avg CPU (%)": float(cpu['avg']), "Container Peak CPU (%)": float(cpu['peak']),
        "Container Total CPU (%*s)": float(cpu['total']), "Container Avg Mem (MB)": float(mem['avg']),
        "Container Peak Mem (MB)": float(mem['peak']), "Container Total Mem (MB*s)": float(mem['total']),
        "Host CPUs": int(num_cores) if num_cores is not None else 1,
        "Host Energy (J)": round(energy["host_energy_j"], 6), "Host Avg Power (W)": round(energy["host_avg_power_w"], 6),
        **thermal_fields(thermal_before, thermal_after, args, pre_load),
        **phases,
        "Energy Samples": int(total_samples), "Energy Sampling Step (ms)": energy["step_ms"],
        "Energy Window Coverage": round(energy["coverage"], 4),
        "Raw Log": csv_columns.raw_log_field(output_json),
    })
    csv_disp = args.output_csv or os.path.join("results_docker", f"{container_name}.csv")
    if is_measure_quiet() and not args.verbose:
        ok = results_counter["success"] == results_counter["total"]
        cnt = (
            f"{_M_GREEN}{results_counter['success']}/{results_counter['total']} ok{_M_NC}"
            if ok
            else f"{results_counter['success']}/{results_counter['total']}"
        )
        measure_quiet_msg(
            f"{container_name} | {cnt} | {runtime:.1f}s | {requests_per_second:.0f} req/s | "
            f"{csv_disp}"
        )
    else:
        print_summary(results_counter, total_energy, average_power, runtime, requests_per_second, 
                      resource_results['cpu'], resource_results['mem'], num_cores, output_json, args.output_csv, container_name,
                      http_max_workers_label=http_workers_label)

if __name__ == "__main__":
    measure_failure.run(main)