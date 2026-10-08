"""The server's box: the cgroup that holds all its processes, a Docker container or a systemd scope.

Which processes ran in it is recorded with every run (CSV column "Server Processes"), so a run
that measured more than the server (README server contract: only the server runs) shows it.
Shared by every measurement (tools/measure_core.py).
"""
import collections
import os
import subprocess

# Processes that are not the server itself: started by a node name, a build tool or a start script
HELPERS = {
    "epmd": "Erlang port mapper (the node has a name)",
    "mix": "Elixir build tool",
    "gleam": "Gleam build tool",
    "rebar3": "Erlang build tool",
    "mvn": "Maven build tool",
    "gradle": "Gradle build tool",
    "sleep": "keep-alive loop of the start script",
    "tail": "keep-alive loop of the start script",
}


def container_cgroup(docker_path, name):
    """The cgroup folder of a running container ("" when it cannot be found)."""
    try:
        pid = subprocess.run([docker_path, "inspect", "--format", "{{.State.Pid}}", name],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("0::"):
                    return "/sys/fs/cgroup" + line[3:].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def processes(folder):
    """Names of the processes in the cgroup `folder` (threads are not listed), sorted."""
    names = []
    try:
        with open(os.path.join(folder, "cgroup.procs"), encoding="utf-8") as fh:
            pids = fh.read().split()
    except OSError:
        return names
    for pid in pids:
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8") as fh:
                names.append(fh.read().strip())
        except OSError:
            continue
    return sorted(names)


def describe(names):
    """'beam.smp, erl_child_setup' or 'beam.smp, 2x sh' for the CSV."""
    return ", ".join(n if k == 1 else f"{k}x {n}" for n, k in sorted(collections.Counter(names).items()))


def helpers(names):
    """'epmd (Erlang port mapper ...)' for every known helper among the names."""
    return [f"{n} ({HELPERS[n]})" for n in sorted(set(names)) if n in HELPERS]
