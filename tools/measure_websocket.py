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

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()

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
    parser.add_argument('--interval', type=float, default=1.0, help='Interval between bursts (seconds)')
    parser.add_argument('--duration', type=int, default=30, help='Test duration in seconds (stream mode)')
    parser.add_argument('--url', type=str, default='ws://localhost:8001/ws', help='WebSocket server URL')
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
        sys.exit(1)

def cleanup_existing_scaphandre():
    subprocess.run(["sudo", "pkill", "-9", "scaphandre"], capture_output=True, text=True, check=False)
    time.sleep(2)

def start_scaphandre(output_json, scaphandre_path):
    os.makedirs("output", exist_ok=True)
    cmd = ["sudo", scaphandre_path, "json", "--containers", "-f", output_json]
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
    non_zero_cpu = [x for x in cpu_usage if x > 0.0]
    non_zero_mem = [x for x in mem_usage if x > 0.0]
    cpu_avg = sum(non_zero_cpu) / len(non_zero_cpu) if non_zero_cpu else 0.0
    cpu_peak = max(cpu_usage) if cpu_usage else 0.0
    cpu_total = cpu_avg * len(cpu_usage) if cpu_usage else 0.0  # Average CPU × number of samples
    mem_avg = sum(non_zero_mem) / len(non_zero_mem) if non_zero_mem else 0.0
    mem_peak = max(mem_usage) if mem_usage else 0.0
    mem_total = mem_avg * len(mem_usage) if mem_usage else 0.0  # Average memory × number of samples
    return {'avg': cpu_avg, 'peak': cpu_peak, 'total': cpu_total}, \
           {'avg': mem_avg, 'peak': mem_peak, 'total': mem_total}

def _pid_in_container(pid, container_id):
    """Check if pid belongs to container via /proc/pid/cgroup (fallback when Scaphandre reports container=null)."""
    if not container_id or pid <= 0:
        return False
    try:
        with open(f"/proc/{pid}/cgroup", "r") as f:
            cgroup = f.read()
        return container_id in cgroup
    except (OSError, IOError):
        return False


def parse_json_and_compute_energy(file_name, container_name, runtime, container_id=None):
    """Extract energy from Scaphandre JSON. Prefers Scaphandre's container field; falls back to cgroup when all container=null."""
    with open(file_name, "r") as file:
        data = json.load(file)
    total_power_microwatts = 0.0
    number_samples = 0
    found_containers = set()
    for entry in data:
        for consumer in entry.get("consumers", []):
            container = consumer.get("container")
            if container:
                found_containers.add(container.get("name"))
            if container and container.get("name") == container_name:
                power = consumer.get("consumption", 0.0)
                if power > 0:
                    total_power_microwatts += power
                    number_samples += 1
    # Fallback: when Scaphandre reports container=null for all (e.g. cgroups v2), attribute by cgroup path
    if number_samples == 0 and container_id and not found_containers:
        for entry in data:
            for consumer in entry.get("consumers", []):
                if consumer.get("container"):
                    continue
                pid = consumer.get("pid", 0)
                power = consumer.get("consumption", 0.0)
                if power > 0 and _pid_in_container(pid, container_id):
                    total_power_microwatts += power
                    number_samples += 1
        if number_samples > 0:
            logger.info(f"Using cgroup fallback for '{container_name}' (Scaphandre container=null on this system)")
    if not found_containers and number_samples == 0:
        logger.warning(f"No containers found in Scaphandre output {file_name}")
    elif found_containers:
        logger.info(f"Containers found in Scaphandre output: {found_containers}")
    if container_name not in found_containers and number_samples == 0:
        logger.warning(f"Container '{container_name}' not found in Scaphandre output!")
    if number_samples == 0:
        logger.warning(f"No energy samples found for container '{container_name}' in {file_name}")
        return 0.0, 0.0, 0
    avg_power_watts = (total_power_microwatts / number_samples) * 1e-6
    total_energy_joules = avg_power_watts * runtime
    return total_energy_joules, avg_power_watts, number_samples

# =====================
# Container Lifecycle
# =====================
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
    try:
        async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
            payload = os.urandom(size_kb * 1024)
            for b in range(bursts):
                start = time.perf_counter()
                await ws.send(payload)
                resp = await ws.recv()
                end = time.perf_counter()
                if resp == payload:
                    latency = (end - start) * 1000
                    latencies.append(latency)
                    results['success'] += 1
                else:
                    results['fail'] += 1
                results['total'] += 1
                if verbose:
                    logger.info(f"[Client {client_id}] Burst {b+1}/{bursts} latency: {latency:.2f} ms")
                await asyncio.sleep(interval)
    except Exception as e:
        logger.warning(f"[Client {client_id}] WebSocket connection error: {e}")
        results['fail'] += bursts
        results['total'] += bursts
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
                if resp == payload:
                    latency = (end - start) * 1000
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
    results['latencies'].extend(latencies)

# =====================
# Main Benchmark Runner
# =====================
def main():
    args = parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    check_prerequisites()  # Exit with error before any measurement if anything is missing
    scaphandre_path = get_binary_path("scaphandre")
    docker_path = get_binary_path("docker")
    num_cores = os.cpu_count()
    output_json = args.output_json or os.path.join("output", datetime.now().strftime("%Y-%m-%d-%H%M%S") + ".json")
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
        exit(1)

    cleanup_existing_scaphandre()
    logger.info(f"Starting container '{container_name}'...")
    start_server_container(args.server_image, args.port_mapping, container_name, docker_path, args.network)
    url = args.url
    if not url:
        url = f"ws://localhost:{args.port_mapping.split(':')[0]}/ws"
    logger.info(f"Checking container health at {url}...")
    
    # Actual WebSocket health check: try to connect and echo a small binary message
    async def check_websocket_health(ws_url, max_attempts=10, delay=2):
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
        stop_server_container(container_name, docker_path)
        exit(1)

    logger.info("Starting Scaphandre...")
    scaphandre_process = start_scaphandre(output_json, scaphandre_path)
    time.sleep(2)

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
    start_time = time.time()
    asyncio.run(run_all())
    runtime = time.time() - start_time

    time.sleep(3)
    stop_event.set()
    resource_thread.join()
    
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
    total_energy, avg_power, total_samples = parse_json_and_compute_energy(
        output_json, container_name, runtime, container_id=container_id
    )
    stop_server_container(container_name, docker_path)

    headers = ["Container Name", "Test Type", "Num CPUs", "Total Messages", "Successful Messages", "Failed Messages", "Execution Time (s)", "Messages/s", "Throughput (MB/s)",
               "Avg Latency (ms)", "Min Latency (ms)", "Max Latency (ms)",
               "Total Energy (J)", "Avg Power (W)", "Samples", "Avg CPU (%)", "Peak CPU (%)", "Total CPU (%)",
               "Avg Mem (MB)", "Peak Mem (MB)", "Total Mem (MB)",
               "Pattern", "Num Clients", "Message Size (KB)", "Rate (msg/s)", "Bursts", "Interval (s)", "Duration (s)"]
    # Calculate latency statistics
    min_latency = min(all_latencies) if all_latencies else 0.0
    max_latency = max(all_latencies) if all_latencies else 0.0
    
    row = [
        container_name,
        args.measurement_type,
        int(num_cores) if num_cores is not None else 1,
        total_msgs,
        total_success,
        total_fail,
        runtime,
        requests_per_second,
        throughput_mb_s,
        avg_latency,
        min_latency,
        max_latency,
        total_energy,
        avg_power,
        total_samples,
        resource_results['cpu'].get('avg', 0.0),
        resource_results['cpu'].get('peak', 0.0),
        resource_results['cpu'].get('total', 0.0),
        resource_results['mem'].get('avg', 0.0),
        resource_results['mem'].get('peak', 0.0),
        resource_results['mem'].get('total', 0.0),
        args.pattern,  # Pattern (burst/stream)
        args.clients,
        args.size_kb,  # Message Size (KB)
        args.rate if args.pattern == 'stream' else '',  # Rate (msg/s) for stream mode
        args.bursts if args.pattern == 'burst' else '',  # Bursts count for burst mode
        args.interval if args.pattern == 'burst' else '',  # Interval (s) for burst mode
        args.duration if args.pattern == 'stream' else ''  # Duration (s) for stream mode
    ]
    write_header = not os.path.exists(output_csv)
    with open(output_csv, 'a', newline='') as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(headers)
        writer.writerow(row)

    logger.info("=== Measurement Summary ===")
    logger.info(f"Container: {container_name}")
    logger.info(f"Total Requests: {total_msgs}, Successful: {total_success}, Failed: {total_fail}")
    logger.info(f"Execution Time: {runtime:.2f} s, Requests/s: {throughput_mb_s:.2f}")
    logger.info(f"Energy: Total {total_energy:.2f} J, Avg Power {avg_power:.2f} W")
    logger.info(f"CPU: Avg {resource_results['cpu'].get('avg', 0.0):.2f}%, Peak {resource_results['cpu'].get('peak', 0.0):.2f}%, Total {resource_results['cpu'].get('total', 0.0):.2f}%")
    logger.info(f"Memory: Avg {resource_results['mem'].get('avg', 0.0):.2f} MB, Peak {resource_results['mem'].get('peak', 0.0):.2f} MB, Total {resource_results['mem'].get('total', 0.0):.2f} MB")
    logger.info(f"JSON: {output_json}, CSV: {output_csv}")
    logger.info("==========================")

if __name__ == "__main__":
    main() 