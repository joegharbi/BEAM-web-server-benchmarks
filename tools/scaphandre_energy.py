"""Energy of one container over the load window, from a Scaphandre JSON log.

Shared by measure_docker.py and measure_websocket.py.

How Scaphandre reports power: every step it writes one entry with a host
timestamp and, for each top process, the average power (microwatts) since the
previous entry. So the value at t_i is the mean power over (t_{i-1}, t_i].
We treat the power as a step function over those intervals and integrate it
over exactly [load_start, load_end]. Samples outside the load window (boot,
settle and tail time) are ignored, and samples where the container drew zero
power count as zero rather than being skipped.

Per entry, the container's power is the sum over all of its processes, so a
container with several processes is counted in full.
"""
import datetime
import gzip
import json
import logging
import os

logger = logging.getLogger()

# Sampling step for Scaphandre. Its default is 2 s, too coarse for short loads. 500 ms
# chosen from tests/step_sweep.sh: container energy showed no step effect beyond run
# noise, while Scaphandre's own draw under load fell from 3.7 W (100 ms) to 1.2 W.
DEFAULT_STEP_MS = 500
# Scaphandre lists only the top N processes per entry (default 10). Raise it so
# a container's processes do not drop out of an entry on a busy host.
DEFAULT_MAX_TOP_CONSUMERS = 50


def raw_json_path(container_name, measurement_type):
    """Where Scaphandre writes its log for one run.

    With MEASURE_RAW_DIR (set by run_benchmarks.sh --config): <raw dir>/<server>_<type>_<UTC time>.json,
    inside the measurement's own results folder. Without it: output/<local time>.json, as before.
    """
    raw_dir = os.environ.get("MEASURE_RAW_DIR")
    if not raw_dir:
        return os.path.join("output", datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S") + ".json")
    os.makedirs(raw_dir, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(raw_dir, f"{container_name}_{measurement_type or 'unknown'}_{stamp}.json")


def window_path(raw_path):
    """Companion file of a kept raw log: <name>.window.json next to <name>.json."""
    base = raw_path[:-3] if raw_path.endswith(".gz") else raw_path
    return base[:-5] + ".window.json" if base.endswith(".json") else base + ".window.json"


def finish_raw(path, window=None):
    """After the energy is calculated: keep the raw log as it is (RAW_DATA=keep) or delete it (RAW_DATA=delete).

    With keep, `window` (load start/end, container, the process IDs counted, the energy) is
    saved next to it, so the energy can be recalculated later with `recompute`. Only applies
    with MEASURE_RAW_DIR set; otherwise the log is left as it is. Returns the final path,
    or "" when deleted.
    """
    mode = os.environ.get("MEASURE_RAW_DATA")
    if not os.environ.get("MEASURE_RAW_DIR") or mode not in ("keep", "delete") or not os.path.isfile(path):
        return path
    if mode == "delete":
        os.remove(path)
        return ""
    if window is not None:
        with open(window_path(path), "w", encoding="utf-8") as fh:
            json.dump(window, fh, indent=2)
    return path


def window_record(container_name, container_id, t0, t1, energy):
    """What finish_raw saves next to a kept raw log."""
    return {"container_name": container_name, "container_id": container_id or "",
            "load_start_epoch": t0, "load_end_epoch": t1, "pids": energy["pids"],
            "energy_j": energy["energy_j"], "host_energy_j": energy["host_energy_j"]}


def recompute(raw_path):
    """Recalculate the energy of one run from its kept raw log and window file."""
    with open(window_path(raw_path), encoding="utf-8") as fh:
        w = json.load(fh)
    return compute_window_energy(raw_path, w["container_name"], w["load_start_epoch"], w["load_end_epoch"],
                                 pids=set(w["pids"]))


def scaphandre_step_ms():
    try:
        return max(10, int(os.environ.get("MEASURE_SCAPH_STEP_MS", DEFAULT_STEP_MS)))
    except ValueError:
        return DEFAULT_STEP_MS


def scaphandre_json_args(output_json):
    """Arguments for `scaphandre json` with the configured step and top-consumer count."""
    step_ms = scaphandre_step_ms()
    try:
        top = max(10, int(os.environ.get("MEASURE_SCAPH_MAX_TOP", DEFAULT_MAX_TOP_CONSUMERS)))
    except ValueError:
        top = DEFAULT_MAX_TOP_CONSUMERS
    return ["json", "--containers",
            "--step", str(step_ms // 1000),
            "--step-nano", str((step_ms % 1000) * 1_000_000),
            "--max-top-consumers", str(top),
            "-f", output_json]


def _pid_in_container(pid, container_id, cache):
    """True if pid belongs to the container, via /proc/pid/cgroup (for when Scaphandre reports container=null)."""
    if not container_id or pid <= 0:
        return False
    if pid not in cache:
        try:
            with open(f"/proc/{pid}/cgroup", "r") as f:
                cache[pid] = container_id in f.read()
        except OSError:
            cache[pid] = False
    return cache[pid]


def _load_json(file_name):
    opener = gzip.open if file_name.endswith(".gz") else open
    with opener(file_name, "rt") as fh:
        return json.load(fh)


def load_power_series(file_name, container_name, container_id=None, pids=None, matched=None):
    """Return [(timestamp, container_W, host_W), ...] from a Scaphandre JSON log.

    The container is matched by Scaphandre's container name when Scaphandre reports
    containers at all, otherwise by cgroup (the container must still be running), or by
    an explicit set of `pids` (used when recalculating from a kept raw log). The PIDs that
    were counted are added to the `matched` set when one is given.
    """
    data = _load_json(file_name)

    found_containers = {
        (c.get("container") or {}).get("name")
        for e in data for c in e.get("consumers", []) if c.get("container")
    }
    use_cgroup = not found_containers and bool(container_id) and pids is None
    if pids is not None:
        logger.info("Matching %d saved process IDs for '%s'", len(pids), container_name)
    elif found_containers:
        logger.info("Containers found in Scaphandre output: %s", found_containers)
        if container_name not in found_containers:
            logger.warning("Container '%s' not found in Scaphandre output!", container_name)
    elif use_cgroup:
        logger.info("Using cgroup fallback for '%s' (Scaphandre container=null on this system)", container_name)
    else:
        logger.warning("No containers found in Scaphandre output %s", file_name)

    cache = {}
    series = []
    started = False
    for entry in data:
        host = entry.get("host") or {}
        t = host.get("timestamp")
        host_uw = host.get("consumption", 0.0) or 0.0
        consumers = entry.get("consumers", [])
        # The first entry has no previous reading, so its power is 0 and meaningless.
        if not started and host_uw == 0 and not consumers:
            continue
        started = True
        if t is None:
            continue
        cont_uw = 0.0
        for c in consumers:
            cont = c.get("container")
            if pids is not None:
                hit = c.get("pid") in pids
            elif use_cgroup:
                hit = not cont and _pid_in_container(c.get("pid", 0), container_id, cache)
            else:
                hit = bool(cont) and cont.get("name") == container_name
            if hit:
                cont_uw += c.get("consumption", 0.0) or 0.0
                if matched is not None:
                    matched.add(c.get("pid"))
        series.append((float(t), cont_uw * 1e-6, host_uw * 1e-6))
    series.sort(key=lambda s: s[0])
    return series


def integrate_window(series, t0, t1):
    """Integrate a Scaphandre power series over [t0, t1].

    Returns (container_J, host_J, samples_in_window, coverage), where coverage is the
    fraction of [t0, t1] covered by sample intervals (1.0 when fully covered).
    """
    if t1 <= t0 or len(series) < 2:
        return 0.0, 0.0, 0, 0.0
    cont_j = host_j = covered = 0.0
    n = 0
    for (ta, _, _), (tb, cw, hw) in zip(series, series[1:]):
        # (ta, tb] carries the power reported at tb.
        lo, hi = max(ta, t0), min(tb, t1)
        if hi > lo:
            dt = hi - lo
            cont_j += cw * dt
            host_j += hw * dt
            covered += dt
        if t0 < tb <= t1:
            n += 1
    return cont_j, host_j, n, covered / (t1 - t0)


def compute_window_energy(file_name, container_name, t0, t1, container_id=None, pids=None, zero_is_normal=False):
    """Energy of the container and of the host over [t0, t1] (wall-clock epoch seconds).

    Returns a dict: energy_j, avg_power_w, host_energy_j, host_avg_power_w, samples,
    coverage, step_ms, and pids (the process IDs that were counted as the container).
    `zero_is_normal`: no warning for 0 J (an idle server can use no CPU at all).
    """
    matched = set()
    series = load_power_series(file_name, container_name, container_id, pids=pids, matched=matched)
    cont_j, host_j, n, coverage = integrate_window(series, t0, t1)
    dur = t1 - t0
    if coverage < 0.99:
        logger.warning("Scaphandre samples cover only %.0f%% of the load window; energy is underestimated.",
                       coverage * 100)
    if n == 0 or (cont_j == 0 and not zero_is_normal):
        logger.warning("No energy samples found for container '%s' in %s", container_name, file_name)
    return {
        "energy_j": cont_j,
        "avg_power_w": cont_j / dur if dur > 0 else 0.0,
        "host_energy_j": host_j,
        "host_avg_power_w": host_j / dur if dur > 0 else 0.0,
        "samples": n,
        "coverage": coverage,
        "step_ms": scaphandre_step_ms(),
        "pids": sorted(p for p in matched if p is not None),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != "recompute":
        sys.exit("Usage: python3 tools/scaphandre_energy.py recompute <raw log>.json")
    r = recompute(sys.argv[2])
    print(f"container energy {r['energy_j']:.6f} J, host energy {r['host_energy_j']:.6f} J, "
          f"{r['samples']} samples, coverage {r['coverage']:.3f}")
