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


DOCKER_TIMEOUT_S = 300      # a Docker call that does not answer within this ends with an error, not a hang


def _docker(docker_path, *args):
    return subprocess.run([docker_path, *args], capture_output=True, text=True, check=True,
                          timeout=DOCKER_TIMEOUT_S).stdout.strip()


def image_env(image, docker_path="docker"):
    """The image's ENV as a dict, without PATH (the image's PATH points into the image)."""
    pairs = json.loads(_docker(docker_path, "image", "inspect", "--format", "{{json .Config.Env}}", image) or "null")
    env = dict(p.split("=", 1) for p in pairs or [] if "=" in p)
    env.pop("PATH", None)
    return env


def unpack(image, docker_path="docker", root=None):
    """Copy /app, /start.sh and the image's OS name (os-release) out of the image into <root>/<image>/;
    reused while the image is the same."""
    root = root or NATIVE_DIR
    target = os.path.join(root, image)
    image_id = _docker(docker_path, "image", "inspect", "--format", "{{.Id}}", image)
    stamp = os.path.join(target, ".image_id")
    try:
        with open(stamp, encoding="utf-8") as fh:
            if fh.read().strip() == image_id and os.path.exists(os.path.join(target, "os-release")):
                return os.path.abspath(target)
    except OSError:
        pass
    os.makedirs(root, exist_ok=True)
    work = tempfile.mkdtemp(prefix=f".{image}-", dir=root)
    box = _docker(docker_path, "create", image)
    try:
        _docker(docker_path, "cp", f"{box}:/app", os.path.join(work, "app"))
        _docker(docker_path, "cp", f"{box}:/start.sh", os.path.join(work, "start.sh"))
        # -L: /etc/os-release is usually a link; empty when the image has none (e.g. FROM scratch)
        if subprocess.run([docker_path, "cp", "-L", f"{box}:/etc/os-release", os.path.join(work, "os-release")],
                          capture_output=True, text=True).returncode != 0:
            open(os.path.join(work, "os-release"), "w").close()
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


def os_name(path):
    """'debian 13' from an os-release file ("" when unknown)."""
    try:
        with open(path, encoding="utf-8") as fh:
            fields = dict(line.rstrip("\n").split("=", 1) for line in fh if "=" in line)
    except OSError:
        return ""
    return " ".join(fields.get(k, "").strip('"') for k in ("ID", "VERSION_ID")).strip()


def problem(image, docker_path="docker", host_os_release="/etc/os-release"):
    """Why the image cannot run natively ("" when it can): it must follow the server contract
    (README): the server under /app, started by /start.sh, which finds it under $APP_DIR, built on
    the host's OS."""
    try:
        folder = unpack(image, docker_path)
    except subprocess.TimeoutExpired:
        return f"Docker did not answer within {DOCKER_TIMEOUT_S // 60} min while copying the image (try again; docker info)"
    except (subprocess.CalledProcessError, OSError) as e:
        detail = (getattr(e, "stderr", "") or str(e)).strip().splitlines()
        return f"could not copy /app and /start.sh out of the image ({detail[-1] if detail else e})"
    try:
        with open(os.path.join(folder, "start.sh"), encoding="utf-8", errors="replace") as fh:
            script = fh.read()
    except OSError:
        return "no /start.sh in the image"
    problems = []
    if "APP_DIR" not in script:
        problems.append("its start.sh does not use APP_DIR (it would run the server from /app, which is not there natively)")
    # Natively the bundle uses the host's system libraries (glibc, OpenSSL): the image must be built on
    # the host's OS. An image without an OS (FROM scratch, e.g. a static Go binary) needs none.
    image_os, host_os = os_name(os.path.join(folder, "os-release")), os_name(host_os_release)
    if image_os and host_os and image_os != host_os:
        problems.append(f"built on {image_os}, but this machine runs {host_os} (build it on the host's OS)")
    return "; ".join(problems)


def _size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def stale_copies(docker_path="docker", root=None):
    """[(folder, bytes, reason)] of copies that can never be used again: their image is gone or was
    rebuilt (another image ID), or an unpacking was cut off (a hidden .<image>-... work folder)."""
    root = root or NATIVE_DIR
    stale = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return stale
    for name in names:
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        if name.startswith("."):
            stale.append((folder, _size(folder), "unpacking was cut off"))
            continue
        try:
            with open(os.path.join(folder, ".image_id"), encoding="utf-8") as fh:
                copied = fh.read().strip()
        except OSError:
            copied = ""
        now = subprocess.run([docker_path, "image", "inspect", "--format", "{{.Id}}", name],
                             capture_output=True, text=True).stdout.strip()
        if not now:
            stale.append((folder, _size(folder), "its image no longer exists"))
        elif now != copied:
            stale.append((folder, _size(folder), "its image was rebuilt"))
    return stale


def all_copies(root=None):
    """[(folder, bytes)] of every copy (folders only; the small scope logs stay)."""
    root = root or NATIVE_DIR
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [(os.path.join(root, n), _size(os.path.join(root, n))) for n in names
            if os.path.isdir(os.path.join(root, n))]


def remove_copy(folder, root=None):
    """Delete one copy; only ever a folder directly inside the native folder."""
    root = os.path.realpath(root or NATIVE_DIR)
    real = os.path.realpath(folder)
    if os.path.dirname(real) != root or real == root:
        raise ValueError(f"not a native copy: {folder}")
    shutil.rmtree(real)


def tidy(mode, docker_path="docker", root=None):
    """NATIVE_COPIES at the end of a run: prune = delete the copies that can never be used again,
    delete = delete every copy, keep = nothing. Returns (number deleted, bytes freed)."""
    if mode == "keep":
        return 0, 0
    targets = [(f, b) for f, b, _ in stale_copies(docker_path, root)] if mode == "prune" else all_copies(root)
    for folder, _ in targets:
        remove_copy(folder, root)
    return len(targets), sum(b for _, b in targets)


if __name__ == "__main__":
    # check IMAGE...: unpack every image and print one line per image that cannot run natively
    # tidy MODE: NATIVE_COPIES at the end of a run (prune, delete, keep)
    import sys
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if sys.argv[1:2] == ["tidy"] and len(sys.argv) == 3:
        n, freed = tidy(sys.argv[2])
        if n:
            print(f"Native copies: deleted {n} ({freed / 1e9:.1f} GB; NATIVE_COPIES={sys.argv[2]})")
        sys.exit(0)
    if sys.argv[1:2] != ["check"]:
        sys.exit("usage: native_server.py check IMAGE... | tidy prune|delete|keep")
    names = sys.argv[2:]
    for i, name in enumerate(names, 1):
        # Progress on stderr (the problems on stdout are what the run script collects)
        print(f"  Preparing native copies: {i} of {len(names)} ({name}) ...", file=sys.stderr, flush=True)
        why = problem(name)
        if why:
            print(f"  {name}: {why}")
