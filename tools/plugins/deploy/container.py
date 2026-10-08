"""Deploy: the server's image in a Docker container."""
import logging
import re
import subprocess
import time

import csv_columns
import measure_failure
import server_box
from plugins.deploy import Deploy

logger = logging.getLogger()


def cleanup_existing_container(container_name, docker_path):
    logger.info(f"Cleaning up any existing container named '{container_name}'...")
    subprocess.run([docker_path, "stop", container_name], capture_output=True, text=True, check=False)
    subprocess.run([docker_path, "rm", "-f", container_name], capture_output=True, text=True, check=False)
    # Wait and check if container is really gone
    for _ in range(5):
        result = subprocess.run([docker_path, "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
                                capture_output=True, text=True)
        if container_name not in result.stdout:
            break
        time.sleep(1)
    else:
        logger.warning(f"Container '{container_name}' could not be removed after multiple attempts.")
    time.sleep(3)  # Ensure port and resources released (important after long runs)


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
    cpu_usage = []
    mem_usage = []
    while not stop_event.is_set():
        try:
            cmd = [docker_path, "stats", container_name, "--no-stream", "--format", "{{.CPUPerc}},{{.MemUsage}}"]
            output = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
            if not output:
                cpu_usage.append(0.0)
                mem_usage.append(0.0)
                time.sleep(interval)
                continue
            cpu_str, mem_str = output.split(',')
            cpu_val = float(cpu_str.strip().replace('%', ''))
            mem_match = re.match(r"([\d.]+)([KMG]iB)", mem_str.strip().split('/')[0].strip())
            mem_val = 0.0
            if mem_match:
                mem_val = float(mem_match.group(1)) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[mem_match.group(2)]
            cpu_usage.append(cpu_val)
            mem_usage.append(mem_val)
        except Exception:
            cpu_usage.append(0.0)
            mem_usage.append(0.0)
        time.sleep(interval)

    def stats(values):
        # avg, peak, and the cumulative value (%*s for CPU, MB*s for memory)
        return {"avg": sum(values) / len(values) if values else 0.0, "peak": max(values, default=0.0),
                "total": sum(values) * interval if values else 0.0}
    return stats(cpu_usage), stats(mem_usage)


class Plugin(Deploy):
    label = "Docker start"

    def start(self):
        measure_failure.started_container(self.name, self.docker)
        start_server_container(self.image, self.port_mapping, self.name, self.docker, self.network)

    def stop(self):
        stop_server_container(self.name, self.docker)

    def log_tail(self, lines):
        try:
            out = subprocess.run([self.docker, "logs", "--tail", str(lines), self.name],
                                 capture_output=True, text=True, timeout=5)
            return (out.stdout or "") + (out.stderr or "")
        except Exception as e:  # noqa: BLE001 - only for the error message
            return f"(could not get the container's logs: {e})"

    def energy_id(self):
        result = subprocess.run([self.docker, "ps", "-q", "-f", f"name={self.name}"], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None

    def collect_stats(self, stop_event, interval=0.5):
        return collect_resources_docker_stats(self.name, stop_event, self.docker, interval)

    def cpu_limit(self):
        return csv_columns.container_cpu_limit(self.docker, self.name)

    def box(self):
        return server_box.container_cgroup(self.docker, self.name)
