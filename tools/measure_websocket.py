import os
import sys
import time
import subprocess
import csv
import argparse
import json
import threading
from datetime import datetime
import logging
import psutil
import asyncio
import websockets

import measure_failure
import readiness
import run_metadata
from scaphandre_energy import compute_window_energy, finish_raw, raw_json_path, scaphandre_json_args, window_record
import load_phases
import csv_columns

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()

def is_measure_quiet():
    """Match run_benchmarks.sh semantics: verbose only when BENCH_MEASURE_QUIET is exactly 0."""
    v = (os.environ.get("BENCH_MEASURE_QUIET") or "1").strip()
    return v != "0"

# [MEASURE] uses magenta so it is distinct from bash [PROGRESS] (cyan).
_M_MAGENTA = "\033[0;35m"
_M_GREEN = "\033[0;32m"
_M_NC = "\033[0m"


def measure_quiet_msg(body: str) -> None:
    print(f"{_M_MAGENTA}[MEASURE]{_M_NC} {body}", flush=True)


def measure_quiet_heartbeat_interval_sec():
    try:
        return max(10, int(os.environ.get("MEASURE_HEARTBEAT_SEC", "60")))
    except ValueError:
        return 60

# =====================
# Argument Parsing
# =====================
def parse_args():
    parser = argparse.ArgumentParser(description="Measure WebSocket server energy with Scaphandre in Docker (echo burst/stream)")
    parser.add_argument('--server_image', type=str, required=True, help="Docker image of the server (e.g., ws-nginx-python-websockets)")
    parser.add_argument('--container_name', type=str, default=None, help="Name of the Docker container (defaults to server_image)")
    parser.add_argument('--port_mapping', type=str, default='8001:80', help="Port mapping (default: 8001:80)")
    parser.add_argument('--network', type=str, default='bridge', choices=['bridge', 'host'], help="Network mode (default: bridge)")
    parser.add_argument('--output_csv', type=str, default=None, help="Output CSV file path (default: results_docker/<container_name>.csv)")
    parser.add_argument('--output_json', type=str, default=None, help="Output JSON file path (default: output/<timestamp>.json)")
    parser.add_argument('--verbose', action='store_true', help="Enable verbose logging")
    parser.add_argument('--measurement_type', type=str, default='websocket', help="Type of measurement (websocket)")
    # WebSocket-specific
    parser.add_argument('--mode', choices=['echo'], default='echo', help='Benchmark mode: echo (C→S→C)')
    parser.add_argument('--pattern', choices=['burst', 'stream'], required=True, help='Traffic pattern: burst (as fast as possible), stream (controlled rate)')
    parser.add_argument('--clients', type=int, default=1, help='Number of concurrent clients')
    parser.add_argument('--size_kb', type=int, default=64, help='Message size in KB (per message)')
    parser.add_argument('--rate', type=int, default=10, help='Messages per second per client (stream mode only)')
    parser.add_argument('--bursts', type=int, default=10, help='Number of bursts (burst mode only)')
    parser.add_argument('--interval', type=float, default=0.0, help='Seconds to wait between bursts (default: 0 = back-to-back saturation burst, which matches "as fast as possible"; set higher for a paced burst)')
    parser.add_argument('--duration', type=int, default=30, help='Test duration in seconds (stream mode)')
    parser.add_argument('--url', type=str, default='ws://localhost:8001/ws', help='WebSocket server URL')
    parser.add_argument('--warmup_s', type=float, default=load_phases.default_warmup_s(),
                        help="Seconds of unmeasured echo messages after boot, before the measurement (default: 0, or WARMUP_SECONDS of the config)")
    parser.add_argument('--idle_s', type=float, default=load_phases.default_idle_s(),
                        help="Seconds the idle server is measured right before the load (default: 0, or IDLE_SECONDS of the config)")
    parser.add_argument('--waited_s', type=float, default=None, help="Seconds the readiness gate waited before this run (set by run_benchmarks.sh --config)")
    parser.add_argument('--ready_check', type=str, default="not checked", help="Result of the readiness gate before this run (set by run_benchmarks.sh --config)")
    parser.add_argument('--repeat', type=int, default=1, help="Quick manual check only: run this measurement N times with a fixed --cooldown, without machine settings or readiness checks. For real measurements use: make run CONFIG=bench.config (default: 1)")
    parser.add_argument('--cooldown', type=int, default=30, help="Seconds to rest between repeated runs (default: 30; only applies when --repeat > 1)")
    return parser.parse_args()

# =====================
# Resource Measurement (Scaphandre, CPU, Mem)
# =====================
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

def cleanup_existing_scaphandre():
    subprocess.run(["sudo", "pkill", "-9", "scaphandre"], capture_output=True, text=True, check=False)
    time.sleep(2)

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
    time.sleep(2)

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


def cleanup_existing_container(container_name, docker_path):
    logger.info(f"Cleaning up any existing container named '{container_name}'...")
    subprocess.run([docker_path, "stop", container_name], capture_output=True, text=True, check=False)
    subprocess.run([docker_path, "rm", "-f", container_name], capture_output=True, text=True, check=False)
    for _ in range(5):
        result = subprocess.run([docker_path, "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.Names}}"], capture_output=True, text=True)
        if container_name not in result.stdout:
            break
        time.sleep(1)
    else:
        logger.warning(f"Container '{container_name}' could not be removed after multiple attempts.")
    time.sleep(2)

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
    time.sleep(2)

# =====================
# WebSocket Benchmark Logic
# =====================
async def echo_burst_client(url, size_kb, bursts, interval, results, client_id, verbose=False):
    latencies = []
    completed_bursts = 0
    try:
        async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
            payload = os.urandom(size_kb * 1024)
            for b in range(bursts):
                start = time.perf_counter()
                await ws.send(payload)
                resp = await ws.recv()
                end = time.perf_counter()
                latency = (end - start) * 1000
                if resp == payload:
                    latencies.append(latency)
                    results['success'] += 1
                else:
                    results['fail'] += 1
                results['total'] += 1
                completed_bursts += 1
                if verbose:
                    logger.info(f"[Client {client_id}] Burst {b+1}/{bursts} latency: {latency:.2f} ms")
                await asyncio.sleep(interval)
    except Exception as e:
        logger.warning(f"[Client {client_id}] WebSocket connection error: {e}")
        # Count only the unfinished bursts as failures to avoid over-counting.
        remaining_bursts = max(0, bursts - completed_bursts)
        results['fail'] += remaining_bursts
        results['total'] += remaining_bursts
    results['latencies'].extend(latencies)

async def echo_stream_client(url, size_kb, rate, duration, results, client_id, verbose=False):
    latencies = []
    try:
        async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
            payload = os.urandom(size_kb * 1024)
            end_time = time.time() + duration
            while time.time() < end_time:
                start = time.perf_counter()
                await ws.send(payload)
                resp = await ws.recv()
                end = time.perf_counter()
                latency = (end - start) * 1000
                if resp == payload:
                    latencies.append(latency)
                    results['success'] += 1
                else:
                    results['fail'] += 1
                results['total'] += 1
                if verbose:
                    logger.info(f"[Client {client_id}] Stream latency: {latency:.2f} ms")
                await asyncio.sleep(1.0 / rate)
    except Exception as e:
        logger.warning(f"[Client {client_id}] WebSocket stream error: {e}")
        # Surface stream session failures in totals instead of silently dropping them.
        results['fail'] += 1
        results['total'] += 1
    results['latencies'].extend(latencies)

async def warm_up(url, seconds, size_kb=8):
    """Unmeasured echo messages over one connection for `seconds` (8 KB, back to back)."""
    if seconds <= 0:
        return
    deadline = time.time() + seconds
    payload = os.urandom(size_kb * 1024)
    try:
        async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
            while time.time() < deadline:
                await ws.send(payload)
                await ws.recv()
    except Exception as e:  # noqa: BLE001 - a warm-up problem shows up in the measurement itself
        logger.warning("Warm-up stopped early: %s", e)

# =====================
# Main Benchmark Runner
# =====================

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


def pre_load_check(container_name, docker_path):
    """Readiness check 2, with the server booted; removes the container if the campaign must stop."""
    try:
        return readiness.pre_load_gate()
    except SystemExit:
        stop_server_container(container_name, docker_path)
        raise


def run_repeats(args):
    """Run the measurement args.repeat times, each as a fresh process, then summarise.

    Each repeat is a separate run of this same script with --repeat 1, so it boots
    its own container and does its own load. All runs append to one CSV, and then
    tools/aggregate_repeats.py turns those rows into an average with a give-or-take.
    """
    import subprocess
    import sys
    import time as _time

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
        "--pattern", args.pattern,
        "--port_mapping", args.port_mapping,
        "--network", args.network,
        "--mode", args.mode,
        "--clients", str(args.clients),
        "--size_kb", str(args.size_kb),
        "--rate", str(args.rate),
        "--bursts", str(args.bursts),
        "--interval", str(args.interval),
        "--duration", str(args.duration),
        "--url", args.url,
        "--measurement_type", args.measurement_type,
        "--output_csv", target_csv,
        "--repeat", "1",
    ]
    if args.container_name:
        base_cmd += ["--container_name", args.container_name]
    if args.verbose:
        base_cmd += ["--verbose"]

    logger.warning("Quick repeat mode (no machine settings or readiness checks; "
                   "for real measurements use: make run CONFIG=bench.config)")
    logger.warning("Repeat mode: %d runs of '%s' (%s, %d clients), %ds cooldown -> %s",
                   args.repeat, container_name, args.pattern, args.clients, args.cooldown, target_csv)
    meta_path = os.path.splitext(target_csv)[0] + "_metadata.json"
    run_metadata.write_start(meta_path, {"tool": "measure_websocket", "repeat": args.repeat, "cooldown_s": args.cooldown,
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
            _time.sleep(args.cooldown)

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
    args = parse_args()
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
    container_name = args.container_name or args.server_image
    output_csv = args.output_csv or os.path.join("results_docker", f"{container_name}.csv")
    output_csv_dir = os.path.dirname(output_csv)
    if output_csv_dir:
        os.makedirs(output_csv_dir, exist_ok=True)

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
        measure_quiet_msg(f"{container_name} | Docker start + WebSocket readiness wait …")
    logger.info(f"Starting container '{container_name}'...")
    measure_failure.started_container(container_name, docker_path)
    start_server_container(args.server_image, args.port_mapping, container_name, docker_path, args.network)
    url = args.url
    if not url:
        url = f"ws://localhost:{args.port_mapping.split(':')[0]}/ws"
    logger.info(f"Checking container health at {url}...")
    startup_wait = int(os.environ.get("MEASURE_STARTUP_WAIT", "15"))
    if startup_wait > 0:
        logger.info("Waiting %ds for container to boot before WebSocket health check...", startup_wait)
        time.sleep(startup_wait)

    # Actual WebSocket health check: try to connect and echo a small binary message
    max_attempts = int(os.environ.get("MEASURE_HEALTH_RETRIES", "20"))
    delay = int(os.environ.get("MEASURE_HEALTH_DELAY", "2"))
    async def check_websocket_health(ws_url, max_attempts=max_attempts, delay=delay):
        for attempt in range(1, max_attempts + 1):
            try:
                async with websockets.connect(ws_url, max_size=None, ping_interval=None) as ws:
                    test_payload = os.urandom(64)  # Small binary payload (64 bytes)
                    await ws.send(test_payload)
                    response = await ws.recv()
                    if response == test_payload:
                        logger.info(f"WebSocket health check passed (attempt {attempt}/{max_attempts})")
                        return True
                    else:
                        logger.warning(f"WebSocket health check failed: echo mismatch (attempt {attempt}/{max_attempts})")
            except Exception as e:
                if attempt < max_attempts:
                    logger.debug(f"WebSocket health check attempt {attempt}/{max_attempts} failed: {e}, retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"WebSocket health check failed after {max_attempts} attempts. Last error: {e}")
                    return False
        return False
    
    health_ok = asyncio.run(check_websocket_health(url))
    if not health_ok:
        logger.error(f"Container '{container_name}' failed WebSocket health check. Stopping container and exiting.")
        # Show container logs to help diagnose (e.g. crash or port not bound)
        try:
            logs_result = subprocess.run(
                [docker_path, "logs", "--tail", "50", container_name],
                capture_output=True, text=True, timeout=5
            )
            if logs_result.stdout or logs_result.stderr:
                logger.error("Container logs (last 50 lines):")
                if logs_result.stdout:
                    for line in logs_result.stdout.splitlines():
                        logger.error("  %s", line)
                if logs_result.stderr:
                    for line in logs_result.stderr.splitlines():
                        logger.error("  %s", line)
        except Exception as e:
            logger.debug("Could not get container logs: %s", e)
        measure_failure.fail("health check failed: no WebSocket echo from the container within the wait time")

    if args.warmup_s > 0:
        if is_measure_quiet() and not args.verbose:
            measure_quiet_msg(f"{container_name} | warm-up {args.warmup_s:g}s (not measured) …")
        asyncio.run(warm_up(url, args.warmup_s))

    # Readiness check 2: booting the server warms the CPU, so wait again right before the load.
    pre_load = pre_load_check(container_name, docker_path)

    if is_measure_quiet() and not args.verbose:
        traffic_desc = f"{args.pattern} | clients={args.clients} size_kb={args.size_kb}"
        if args.pattern == "burst":
            traffic_desc += f" bursts={args.bursts} interval={args.interval}s"
        else:
            traffic_desc += f" rate={args.rate}/s duration={args.duration}s"
        measure_quiet_msg(
            f"{container_name} | Scaphandre power sampling + WebSocket load | {traffic_desc}"
        )
    logger.info("Starting Scaphandre...")
    scaphandre_process = start_scaphandre(output_json, scaphandre_path)
    time.sleep(2)
    if args.idle_s > 0 and is_measure_quiet() and not args.verbose:
        measure_quiet_msg(f"{container_name} | idle {args.idle_s:g}s (measured, no messages) …")
    idle = load_phases.idle_window(args.idle_s)

    stop_event = threading.Event()
    resource_results = {'cpu': {}, 'mem': {}}
    def collect():
        cpu_metrics, mem_metrics = collect_resources_docker_stats(container_name, stop_event, docker_path)
        resource_results['cpu'] = cpu_metrics
        resource_results['mem'] = mem_metrics
    resource_thread = threading.Thread(target=collect)
    resource_thread.start()
    logger.info("Sleeping 1s to let docker stats stabilize...")
    time.sleep(1)

    # Prepare per-client result dicts
    client_results = []
    for _ in range(args.clients):
        r = {'success': 0, 'fail': 0, 'total': 0, 'latencies': []}
        client_results.append(r)
    tasks = []
    for i in range(args.clients):
        if args.mode == 'echo' and args.pattern == 'burst':
            tasks.append(echo_burst_client(url, args.size_kb, args.bursts, args.interval, client_results[i], i, args.verbose))
        elif args.mode == 'echo' and args.pattern == 'stream':
            tasks.append(echo_stream_client(url, args.size_kb, args.rate, args.duration, client_results[i], i, args.verbose))
        else:
            raise ValueError(f"Unsupported mode/pattern: {args.mode}/{args.pattern}")
    async def run_all():
        await asyncio.gather(*tasks)
    hb_stop = threading.Event()
    hb_thread = None
    load_t0 = time.time()
    if is_measure_quiet() and not args.verbose:
        iv = measure_quiet_heartbeat_interval_sec()

        def _heartbeat_worker():
            while not hb_stop.wait(iv):
                done = sum(int(r.get("total", 0)) for r in client_results)
                measure_quiet_msg(
                    f"{container_name} | WebSocket messages {done} ({int(time.time() - load_t0)}s elapsed)"
                )

        hb_thread = threading.Thread(target=_heartbeat_worker, daemon=True)
        hb_thread.start()

    thermal_before = thermal_reading()
    charger_before = run_metadata.ac_power()
    start_time = time.time()
    try:
        asyncio.run(run_all())
    finally:
        if hb_thread is not None:
            hb_stop.set()
            hb_thread.join(timeout=3)
    end_time = time.time()
    thermal_after = thermal_reading()
    if load_phases.charger_unplugged(charger_before, run_metadata.ac_power()):
        measure_failure.fail("the laptop ran on battery during the load (charger unplugged)")
    runtime = end_time - start_time

    time.sleep(3)
    stop_event.set()
    resource_thread.join()
    
    if is_measure_quiet() and not args.verbose:
        measure_quiet_msg(f"{container_name} | stopping Scaphandre + appending CSV …")
    logger.info("Waiting for Scaphandre...")
    time.sleep(5)
    stop_scaphandre(scaphandre_process)
    container_id = None
    result = subprocess.run(
        [docker_path, "ps", "-q", "-f", f"name={container_name}"],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        container_id = result.stdout.strip()
    total_msgs = sum(int(r['total']) for r in client_results)
    total_success = sum(int(r['success']) for r in client_results)
    total_fail = sum(int(r['fail']) for r in client_results)
    all_latencies = [lat for r in client_results for lat in r['latencies']]
    avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0.0
    requests_per_second = total_msgs / runtime if runtime > 0 else 0.0
    throughput_mb_s = (total_msgs * args.size_kb / 1024) / runtime if runtime > 0 else 0.0
    energy = compute_window_energy(output_json, container_name, start_time, end_time, container_id=container_id)
    phases = load_phases.idle_fields(output_json, container_name, container_id, idle, args.warmup_s)
    record = window_record(container_name, container_id, start_time, end_time, energy)
    if idle:
        record["idle_start_epoch"], record["idle_end_epoch"] = idle
    output_json = finish_raw(output_json, record)
    total_energy, avg_power, total_samples = energy["energy_j"], energy["avg_power_w"], energy["samples"]
    cpu_limit = csv_columns.container_cpu_limit(docker_path, container_name)
    stop_server_container(container_name, docker_path)

    min_latency = min(all_latencies) if all_latencies else 0.0
    max_latency = max(all_latencies) if all_latencies else 0.0
    cpu, mem = resource_results['cpu'], resource_results['mem']
    csv_columns.append(output_csv, csv_columns.WS_COLUMNS, {
        "Container Name": container_name, **csv_columns.run_fields(),
        "Test Type": args.measurement_type, "Pattern": args.pattern, "Num Clients": args.clients,
        "Message Size (KB)": args.size_kb,
        "Rate (msg/s)": args.rate if args.pattern == 'stream' else '',
        "Bursts": args.bursts if args.pattern == 'burst' else '',
        "Interval (s)": args.interval if args.pattern == 'burst' else '',
        "Duration (s)": args.duration if args.pattern == 'stream' else '',
        "Total Messages": total_msgs, "Successful Messages": total_success, "Failed Messages": total_fail,
        "Execution Time (s)": runtime, "Messages/s": requests_per_second, "Throughput (MB/s)": throughput_mb_s,
        "Avg Latency (ms)": avg_latency, "Min Latency (ms)": min_latency, "Max Latency (ms)": max_latency,
        "Container CPU Limit": cpu_limit, "Container Energy (J)": total_energy, "Container Avg Power (W)": avg_power,
        "Container Avg CPU (%)": cpu.get('avg', 0.0), "Container Peak CPU (%)": cpu.get('peak', 0.0),
        "Container Total CPU (%*s)": cpu.get('total', 0.0), "Container Avg Mem (MB)": mem.get('avg', 0.0),
        "Container Peak Mem (MB)": mem.get('peak', 0.0), "Container Total Mem (MB*s)": mem.get('total', 0.0),
        "Host CPUs": int(num_cores) if num_cores is not None else 1,
        "Host Energy (J)": round(energy["host_energy_j"], 6), "Host Avg Power (W)": round(energy["host_avg_power_w"], 6),
        **thermal_fields(thermal_before, thermal_after, args, pre_load),
        **phases,
        "Energy Samples": total_samples, "Energy Sampling Step (ms)": energy["step_ms"],
        "Energy Window Coverage": round(energy["coverage"], 4),
        "Raw Log": csv_columns.raw_log_field(output_json),
    })

    if is_measure_quiet() and not args.verbose:
        ok = total_success == total_msgs
        cnt = (
            f"{_M_GREEN}{total_success}/{total_msgs} ok{_M_NC}"
            if ok
            else f"{total_success}/{total_msgs}"
        )
        measure_quiet_msg(
            f"{container_name} | {cnt} | {runtime:.1f}s | {requests_per_second:.0f} msg/s | {output_csv}"
        )
    else:
        logger.info("=== Measurement Summary ===")
        logger.info(f"Container: {container_name}")
        logger.info(f"Total Requests: {total_msgs}, Successful: {total_success}, Failed: {total_fail}")
        logger.info(f"Execution Time: {runtime:.2f} s, Messages/s: {requests_per_second:.2f}")
        logger.info(f"Energy: Total {total_energy:.2f} J, Avg Power {avg_power:.2f} W")
        logger.info(f"CPU: Avg {resource_results['cpu'].get('avg', 0.0):.2f}%, Peak {resource_results['cpu'].get('peak', 0.0):.2f}%, Total {resource_results['cpu'].get('total', 0.0):.2f} %*s")
        logger.info(f"Memory: Avg {resource_results['mem'].get('avg', 0.0):.2f} MB, Peak {resource_results['mem'].get('peak', 0.0):.2f} MB, Total {resource_results['mem'].get('total', 0.0):.2f} MB*s")
        logger.info(f"JSON: {output_json}, CSV: {output_csv}")
        logger.info("==========================")

if __name__ == "__main__":
    measure_failure.run(main)