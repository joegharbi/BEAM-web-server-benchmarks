"""Native mode: run a server's own program without Docker, in a systemd user scope.

The program is the one inside the server's image: /app and /start.sh are copied out of the image
(native/<image>/), so container and native runs use the same build, the same runtime version and the
same settings (the image's ENV, except PATH). Only the box differs.

A scope is not a container: it is only a cgroup, the group every Linux process belongs to anyway.
It gives the server's processes one name, so they can be found for the energy (by that name in
/proc/<pid>/cgroup, as for a container) and stopped together. No namespaces, no own file system,
no own network: the server listens on the machine's port directly.

Shared by measure_docker.py and measure_websocket.py.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time

logger = logging.getLogger()

NATIVE_DIR = os.environ.get("MEASURE_NATIVE_DIR", "native")      # the copies out of the images
HOST_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def unit_name(name):
    """The scope of a server: wseb-<name>.scope. With the .scope ending the name is never part of
    another server's (st-x.scope is not inside st-x-nobw.scope), so the cgroup match is exact."""
    return "wseb-" + re.sub(r"[^A-Za-z0-9_.-]", "_", name) + ".scope"


def _docker(docker_path, *args):
    return subprocess.run([docker_path, *args], capture_output=True, text=True, check=True).stdout.strip()


def image_env(image, docker_path="docker"):
    """The image's ENV as a dict, without PATH (the image's PATH points into the image)."""
    pairs = json.loads(_docker(docker_path, "image", "inspect", "--format", "{{json .Config.Env}}", image) or "null")
    env = dict(p.split("=", 1) for p in pairs or [] if "=" in p)
    env.pop("PATH", None)
    return env


def unpack(image, docker_path="docker", root=None):
    """Copy /app and /start.sh out of the image into <root>/<image>/; reused while the image is the same."""
    root = root or NATIVE_DIR
    target = os.path.join(root, image)
    image_id = _docker(docker_path, "image", "inspect", "--format", "{{.Id}}", image)
    stamp = os.path.join(target, ".image_id")
    try:
        with open(stamp, encoding="utf-8") as fh:
            if fh.read().strip() == image_id:
                return os.path.abspath(target)
    except OSError:
        pass
    os.makedirs(root, exist_ok=True)
    work = tempfile.mkdtemp(prefix=f".{image}-", dir=root)
    box = _docker(docker_path, "create", image)
    try:
        _docker(docker_path, "cp", f"{box}:/app", os.path.join(work, "app"))
        _docker(docker_path, "cp", f"{box}:/start.sh", os.path.join(work, "start.sh"))
    finally:
        subprocess.run([docker_path, "rm", "-f", box], capture_output=True, text=True, check=False)
    with open(os.path.join(work, ".image_id"), "w", encoding="utf-8") as fh:
        fh.write(image_id)
    shutil.rmtree(target, ignore_errors=True)
    os.rename(work, target)
    logger.info("Unpacked %s into %s", image, target)
    return os.path.abspath(target)


def command(unit, folder, env, port):
    """systemd-run command: the image's start.sh in its own scope, with only the image's settings
    (env -i: nothing of the measuring tool's environment leaks in), APP_DIR and PORT."""
    settings = {**env, "PATH": HOST_PATH, "HOME": os.environ.get("HOME", "/"),
                "APP_DIR": os.path.join(folder, "app"), "PORT": str(port)}
    return (["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={unit}", "env", "-i"]
            + [f"{k}={v}" for k, v in sorted(settings.items())] + [os.path.join(folder, "start.sh")])


def stop(unit):
    """Stop every process of the scope (no error when it is not running)."""
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, text=True, check=False)
    subprocess.run(["systemctl", "--user", "reset-failed", unit], capture_output=True, text=True, check=False)


def log_path(unit):
    return os.path.join(NATIVE_DIR, unit + ".log")


def start(image, name, port, docker_path="docker"):
    """Start the server natively; returns its scope name (used like a container ID)."""
    unit = unit_name(name)
    stop(unit)
    folder = unpack(image, docker_path)
    with open(log_path(unit), "w", encoding="utf-8") as log:
        proc = subprocess.Popen(command(unit, folder, image_env(image, docker_path), port),
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(5)
    if proc.poll() is not None:
        raise RuntimeError(f"native server ended at once (exit code {proc.returncode}); see {log_path(unit)}")
    return unit


def log_tail(unit, lines=30):
    try:
        with open(log_path(unit), encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])
    except OSError:
        return ""


def cgroup_dir(unit):
    path = subprocess.run(["systemctl", "--user", "show", "-p", "ControlGroup", "--value", unit],
                          capture_output=True, text=True).stdout.strip()
    return "/sys/fs/cgroup" + path if path else ""


def _cpu_usec(folder):
    with open(os.path.join(folder, "cpu.stat"), encoding="utf-8") as fh:
        for line in fh:
            key, value = line.split()
            if key == "usage_usec":
                return int(value)
    return 0


def _mem_mb(folder):
    with open(os.path.join(folder, "memory.current"), encoding="utf-8") as fh:
        return int(fh.read()) / (1024 * 1024)


def collect_stats(unit, stop_event, interval=0.5):
    """CPU and memory of the scope, every `interval` s, from its cgroup: the same figures and units
    as docker stats (CPU % of one core, memory in MB), so the CSV columns mean the same."""
    folder = cgroup_dir(unit)
    cpu_usage, mem_usage = [], []
    last = None
    while not stop_event.is_set():
        try:
            now, usec = time.monotonic(), _cpu_usec(folder)
            if last is not None and now > last[0]:
                cpu_usage.append((usec - last[1]) / ((now - last[0]) * 1e6) * 100)
            last = (now, usec)
            mem_usage.append(_mem_mb(folder))
        except (OSError, ValueError):
            cpu_usage.append(0.0)
            mem_usage.append(0.0)
        time.sleep(interval)

    def stats(values):
        return {"avg": sum(values) / len(values) if values else 0.0, "peak": max(values, default=0.0),
                "total": sum(values) * interval}
    return stats(cpu_usage), stats(mem_usage)


def problem(image, docker_path="docker"):
    """Why the image cannot run natively ("" when it can): it must follow the server contract
    (README): the server under /app, started by /start.sh, which finds it under $APP_DIR."""
    try:
        folder = unpack(image, docker_path)
    except (subprocess.CalledProcessError, OSError) as e:
        detail = (getattr(e, "stderr", "") or str(e)).strip().splitlines()
        return f"could not copy /app and /start.sh out of the image ({detail[-1] if detail else e})"
    try:
        with open(os.path.join(folder, "start.sh"), encoding="utf-8", errors="replace") as fh:
            script = fh.read()
    except OSError:
        return "no /start.sh in the image"
    if "APP_DIR" not in script:
        return "its start.sh does not use APP_DIR (it would run the server from /app, which is not there natively)"
    return ""


if __name__ == "__main__":
    # check IMAGE...: unpack every image and print one line per image that cannot run natively
    import sys
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if sys.argv[1:2] != ["check"]:
        sys.exit("usage: native_server.py check IMAGE...")
    for name in sys.argv[2:]:
        why = problem(name)
        if why:
            print(f"  {name}: {why}")
