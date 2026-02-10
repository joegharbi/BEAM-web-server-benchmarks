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

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()

results_counter = Counter()
runtime_data = {}

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

def send_request(url, request_num, verbose=False):
    try:
        response = requests.get(url, timeout=5)
        if verbose:
            logger.debug(f'{url} "GET / HTTP/1.1" {response.status_code} {len(response.content)}')
        if 200 <= response.status_code < 300:
            results_counter['success'] += 1
        else:
            results_counter['failure'] += 1
    except requests.exceptions.RequestException:
        results_counter['failure'] += 1
    finally:
        results_counter['total'] += 1

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
    time.sleep(2)  # Ensure Docker/OS releases resources

def cleanup_existing_scaphandre():
    subprocess.run(["sudo", "pkill", "-9", "scaphandre"], capture_output=True, text=True, check=False)
    time.sleep(2)  # Ensure OS releases resources

def start_scaphandre(output_json, scaphandre_path):
    os.makedirs("output", exist_ok=True)
    scaphandre_process = subprocess.Popen(
        ["sudo", scaphandre_path, "json", "--containers", "-f", output_json],
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

def check_container_health(url, retries=5, delay=1):
    for _ in range(retries):
        try:
            if requests.get(url, timeout=5).status_code == 200:
                return True
        except requests.exceptions.RequestException:
            time.sleep(delay)
    return False

def start_server_container(server_image, port_mapping, container_name, docker_path, network="bridge"):
    cleanup_existing_container(container_name, docker_path)
    cmd = [docker_path, "run", "-d", "--ulimit", "nofile=100000:100000", "--name", container_name]
    if network == "host":
        cmd.extend(["--network", "host"])
    else:
        cmd.extend(["-p", port_mapping])
    cmd.append(server_image)
    subprocess.run(cmd, capture_output=True, text=True, check=True)
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
    # Only consider non-zero samples for averages
    non_zero_cpu = [x for x in cpu_usage if x > 0.0]
    non_zero_mem = [x for x in mem_usage if x > 0.0]
    cpu_avg = sum(non_zero_cpu) / len(non_zero_cpu) if non_zero_cpu else 0.0
    cpu_peak = max(cpu_usage) if cpu_usage else 0.0
    cpu_total = sum(cpu_usage) if cpu_usage else 0.0
    mem_avg = sum(non_zero_mem) / len(non_zero_mem) if non_zero_mem else 0.0
    mem_peak = max(mem_usage) if mem_usage else 0.0
    mem_total = sum(mem_usage) if mem_usage else 0.0
    return {'avg': cpu_avg, 'peak': cpu_peak, 'total': cpu_total}, \
           {'avg': mem_avg, 'peak': mem_peak, 'total': mem_total}

def parse_json_and_compute_energy(file_name, container_name, runtime):
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
    if not found_containers:
        logger.warning(f"No containers found in Scaphandre output {file_name}")
    else:
        logger.info(f"Containers found in Scaphandre output: {found_containers}")
    if container_name not in found_containers:
        logger.warning(f"Container '{container_name}' not found in Scaphandre output!")
    if number_samples == 0:
        logger.warning(f"No energy samples found for container '{container_name}' in {file_name}")
        return 0.0, 0.0, 0
    avg_power_watts = (total_power_microwatts / number_samples) * 1e-6
    total_energy_joules = avg_power_watts * runtime
    return total_energy_joules, avg_power_watts, number_samples

def save_results_to_csv(filename, results, total_energy, average_power, runtime, requests_per_second, total_samples, 
                       cpu_metrics, mem_metrics, num_cores, container_name, measurement_type, extra_fields=None):
    if filename is None:
        os.makedirs("results_docker", exist_ok=True)
        filename = os.path.join("results_docker", f"{container_name}.csv")
    
    headers = ["Container Name", "Type", "Num CPUs", "Total Requests", "Successful Requests", "Failed Requests", "Execution Time (s)", "Requests/s",
               "Total Energy (J)", "Avg Power (W)", "Samples", "Avg CPU (%)", "Peak CPU (%)", "Total CPU (%)",
               "Avg Mem (MB)", "Peak Mem (MB)", "Total Mem (MB)"]
    # Ensure num_cores is an int and not None
    num_cores_csv = int(num_cores) if num_cores is not None else 1
    data = [[
        str(container_name),
        str(measurement_type),
        int(num_cores_csv),
        int(results['total']),
        int(results['success']),
        int(results['failure']),
        float(runtime),
        float(requests_per_second),
        float(total_energy),
        float(average_power),
        int(total_samples),
        float(cpu_metrics['avg']),
        float(cpu_metrics['peak']),
        float(cpu_metrics['total']),
        float(mem_metrics['avg']),
        float(mem_metrics['peak']),
        float(mem_metrics['total'])
    ]]
    if extra_fields:
        headers += list(extra_fields.keys())
        for i, row in enumerate(data):
            data[i] += list(extra_fields.values())
    with open(filename, mode='a', newline='') as file:
        writer = csv.writer(file)
        if not os.path.isfile(filename) or os.stat(filename).st_size == 0:
            writer.writerow(headers)
        writer.writerows(data)

def print_summary(results, total_energy, average_power, runtime, requests_per_second, cpu_metrics, mem_metrics, num_cores, output_json, output_csv, container_name):
    logger.info("=== Measurement Summary ===")
    logger.info(f"Container: {container_name}")
    logger.info(f"Total Requests: {results['total']}, Successful: {results['success']}, Failed: {results['failure']}")
    logger.info(f"Execution Time: {runtime:.2f} s, Requests/s: {requests_per_second:.2f}")
    logger.info(f"Energy: Total {total_energy:.2f} J, Avg Power {average_power:.2f} W")
    logger.info(f"CPU: Avg {cpu_metrics['avg']:.2f}%, Peak {cpu_metrics['peak']:.2f}%, Total {cpu_metrics['total']:.2f}%")
    logger.info(f"Memory: Avg {mem_metrics['avg']:.2f} MB, Peak {mem_metrics['peak']:.2f} MB, Total {mem_metrics['total']:.2f} MB")
    logger.info(f"JSON: {output_json}, CSV: {output_csv or f'results_docker/{container_name}.csv'}")
    logger.info("==========================")

def main():
    parser = argparse.ArgumentParser(description="Measure web server energy with Scaphandre in Docker")
    parser.add_argument('--server_image', type=str, required=True, help="Docker image of the server (e.g., nginx-deb)")
    parser.add_argument('--container_name', type=str, default=None, help="Name of the Docker container (defaults to server_image)")
    parser.add_argument('--port_mapping', type=str, default='8001:80', help="Port mapping (default: 8001:80)")
    parser.add_argument('--network', type=str, default='bridge', choices=['bridge', 'host'], help="Network mode (default: bridge)")
    parser.add_argument('--num_requests', type=int, default=500, help="Number of requests to send (default: 500)")
    parser.add_argument('--max_workers', type=int, default=None, help="Max workers for ThreadPoolExecutor (default: None, uses system default)")
    parser.add_argument('--output_csv', type=str, default=None, help="Output CSV file path (default: results_docker/<container_name>.csv)")
    parser.add_argument('--output_json', type=str, default=None, help="Output JSON file path (default: output/<timestamp>.json)")
    parser.add_argument('--verbose', action='store_true', help="Enable verbose logging")
    parser.add_argument('--measurement_type', type=str, default=None, help="Type of measurement (static, dynamic, etc.)")
    
    args = parser.parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    check_prerequisites()  # Exit with error before any measurement if anything is missing
    scaphandre_path = get_binary_path("scaphandre")
    docker_path = get_binary_path("docker")
    num_cores = os.cpu_count()
    
    output_json = args.output_json or os.path.join("output", datetime.now().strftime("%Y-%m-%d-%H%M%S") + ".json")
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
        exit(1)

    cleanup_existing_scaphandre()
    logger.info(f"Starting container '{container_name}'...")
    start_server_container(args.server_image, args.port_mapping, container_name, docker_path, args.network)
    
    if not check_container_health(url):
        logger.error("Container health check failed")
        stop_server_container(container_name, docker_path)
        return
    
    logger.info("Starting Scaphandre...")
    scaphandre_process = start_scaphandre(output_json, scaphandre_path)
    
    logger.info(f"Sending {args.num_requests} requests to {url}...")
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

    start_time = time.time()
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        executor.map(lambda i: send_request(url, i, args.verbose), range(args.num_requests))
    runtime = time.time() - start_time
    runtime_data['runtime'] = runtime

    time.sleep(3)
    stop_event.set()
    resource_thread.join()

    requests_per_second = results_counter['total'] / runtime if runtime > 0 else 0
    
    logger.info("Waiting for Scaphandre...")
    time.sleep(5)
    stop_scaphandre(scaphandre_process)
    stop_server_container(container_name, docker_path)

    total_energy, average_power, total_samples = parse_json_and_compute_energy(output_json, container_name, runtime)
    measurement_type = getattr(args, 'measurement_type', None) or "unknown"
    save_results_to_csv(args.output_csv, results_counter, total_energy, average_power, runtime, requests_per_second, 
                       int(total_samples), resource_results['cpu'], resource_results['mem'], num_cores, args.server_image, measurement_type)
    print_summary(results_counter, total_energy, average_power, runtime, requests_per_second, 
                  resource_results['cpu'], resource_results['mem'], num_cores, output_json, args.output_csv, container_name)

if __name__ == "__main__":
    main()