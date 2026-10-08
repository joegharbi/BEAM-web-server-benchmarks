#!/usr/bin/env python3
"""Provenance of one measurement, written once to <results dir>/metadata.json.

A measurement is one run of the benchmark that fills one results folder. Its CSV
rows share the same software, machine and settings, so those are recorded here
once instead of being repeated in every row.

  start  - versions, machine, settings, and the machine state before the first run
  end    - the machine state after the last run, the finish time, the ID of every
           image that appears in the folder's CSVs, and whether the conditions
           stayed the same from start to end

Usage:
  python3 tools/run_metadata.py start results/2026-09-30_120000 --set quick=0 --set http_max_workers=100
  python3 tools/run_metadata.py end   results/2026-09-30_120000
  python3 tools/run_metadata.py temp      # CPU package temperature (used by the cooldown)

Every value is read without root. A value that cannot be read is left empty,
never guessed. If `end` is missing from a metadata.json, the measurement did not
finish.
"""
import argparse
import csv
import datetime
import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
FILENAME = "metadata.json"


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _run(cmd, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=cwd)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# --- software and machine (fixed for a measurement) ---

def framework_version():
    """Git commit of the framework, with -dirty when there are uncommitted changes."""
    commit = _run(["git", "rev-parse", "--short", "HEAD"], cwd=_TOOLS_DIR)
    if not commit:
        return ""
    dirty = _run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=_TOOLS_DIR)
    return commit + ("-dirty" if dirty else "")


def scaphandre_version():
    out = _run(["scaphandre", "--version"])
    return out.split()[-1] if out else ""


def scaphandre_package_version():
    """Version of the installed Debian package. Needed because Scaphandre 1.0.3 still
    reports "1.0.2" with --version (its source was released with the old version number)."""
    return _run(["dpkg-query", "-W", "-f=${Version}", "scaphandre"])


def cpu_model():
    for line in (_read("/proc/cpuinfo") or "").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.processor()


def os_name():
    for line in (_read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return platform.system()


def memory_gb():
    for line in (_read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            return round(int(line.split()[1]) / 1024 / 1024, 1)
    return ""


def image_id(image):
    """Short content ID of a Docker image, so a rebuilt image is never mistaken for the old one."""
    full = _run(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    if not full and image.endswith("-native"):
        return image_id(image[:-len("-native")])        # DEPLOY=native: <image>-native runs <image>
    return full.split(":", 1)[-1][:12] if full else ""


def virtualization():
    """'none' on real hardware, else the kind of virtual machine or container (kvm, xen, docker, ...)."""
    out = subprocess.run(["systemd-detect-virt"], capture_output=True, text=True).stdout.strip() \
        if shutil.which("systemd-detect-virt") else ""
    return out or "unknown"


def rapl_available():
    """True when the CPU's energy counters (RAPL), which Scaphandre reads, are visible."""
    return bool(glob.glob("/sys/class/powercap/*rapl*:0"))


def software_and_machine():
    return {
        "framework_version": framework_version(),
        "scaphandre_version": scaphandre_version(),
        "scaphandre_package_version": scaphandre_package_version(),
        "docker_version": _run(["docker", "version", "--format", "{{.Server.Version}}"]),
        "python_version": platform.python_version(),
        "os": os_name(),
        "kernel": platform.release(),
        "cpu_model": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "memory_gb": memory_gb(),
        "virtualization": virtualization(),
        "rapl_available": rapl_available(),
    }


# --- machine state (can change during a measurement) ---

def cpu_governor():
    govs = sorted({_read(p) for p in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")} - {None})
    return "/".join(govs)


def turbo_state():
    no_turbo = _read("/sys/devices/system/cpu/intel_pstate/no_turbo")
    if no_turbo is not None:
        return "off" if no_turbo == "1" else "on"
    boost = _read("/sys/devices/system/cpu/cpufreq/boost")
    if boost is not None:
        return "on" if boost == "1" else "off"
    return ""


# The power profile (low-power / balanced / performance): how much power and heat the firmware allows.
# Standard on Linux: the kernel's ACPI platform profile, managed by power-profiles-daemon where it runs
# (then it is only changed through the daemon, never behind its back). Lenovo, Dell, HP, ASUS and AMD
# laptops have it; desktops, servers and VMs usually not.
PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
_FROM_DAEMON = {"power-saver": "low-power"}               # the daemon's names -> the kernel's names


def _power_profiles_daemon(*args):
    """powerprofilesctl's output, or None when power-profiles-daemon does not run here."""
    if not shutil.which("powerprofilesctl"):
        return None
    try:
        r = subprocess.run(["powerprofilesctl", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def power_profile():
    """(profile, manager): the power profile in the kernel's names, and who manages it: "daemon"
    (power-profiles-daemon) or "firmware" (the kernel file only); ("", "") without power profiles."""
    out = _power_profiles_daemon("get")
    if out and out.strip():
        return _FROM_DAEMON.get(out.strip(), out.strip()), "daemon"
    profile = _read(PLATFORM_PROFILE)
    return (profile, "firmware") if profile else ("", "")


def power_profile_choices():
    """The profiles this machine offers (kernel names); [] without power profiles."""
    _, manager = power_profile()
    if manager == "daemon":
        names = re.findall(r"^[* ]\s*([a-z-]+):\s*$", _power_profiles_daemon("list") or "", re.M)
        return [_FROM_DAEMON.get(n, n) for n in names]
    if manager == "firmware":
        return (_read(PLATFORM_PROFILE + "_choices") or "").split()
    return []


def performance_degraded():
    """Why power-profiles-daemon holds the performance profile back (e.g. "lap-detected",
    "high-operating-temperature"); "" when it does not, or cannot tell."""
    m = re.search(r"Degraded:\s*yes\s*\(([^)]*)\)", _power_profiles_daemon("list") or "")
    return m.group(1).strip() if m else ""


def cpu_max_freq_mhz():
    vals = {_read(p) for p in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_max_freq")} - {None}
    return "/".join(str(int(v) // 1000) for v in sorted(vals, key=int))


def _min_mhz(pattern):
    vals = [int(v) for v in (_read(p) for p in glob.glob(pattern)) if v and v.isdigit()]
    return min(vals) // 1000 if vals else None


def cpu_speed_limit_mhz():
    """The lowest per-core maximum speed the CPU may run at now (MHz), or None.

    Includes limits set by the firmware (e.g. a weak charger), which software cannot lift.
    """
    return _min_mhz("/sys/devices/system/cpu/cpu*/cpufreq/scaling_max_freq")


def rated_cpu_speed_mhz(cpuinfo="/proc/cpuinfo"):
    """The CPU's rated base speed from its model name (Intel: '... CPU @ 1.80GHz'), or None. Unlike
    base_frequency, which the firmware can lower together with a cap, it never changes."""
    try:
        with open(cpuinfo, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("model name"):
                    m = re.search(r"@\s*([0-9.]+)\s*GHz", line)
                    return int(round(float(m.group(1)) * 1000)) if m else None
    except OSError:
        pass
    return None


def cpu_cap_clues():
    """What the machine reports that can make its firmware cap the CPU, for the waiting message:
    laptop lap mode, what the USB-C charger offers, the power profile, the battery, the temperature.
    Each clue only when the machine has it."""
    clues = []
    lap = _read("/sys/devices/platform/thinkpad_acpi/dytc_lapmode")
    if lap is not None:
        clues.append(f"lap mode {'on' if lap == '1' else 'off'}")
    for psy in sorted(glob.glob("/sys/class/power_supply/*")):
        if _read(os.path.join(psy, "online")) == "1" and _read(os.path.join(psy, "type")) == "USB":
            # What the charger offers (voltage_max x current_max): voltage_now is unreliable on some
            # machines (a 65 W charger read as 5 V); whether it is enough shows in the battery clue
            volts, amps = _read(os.path.join(psy, "voltage_max")), _read(os.path.join(psy, "current_max"))
            if volts and amps and volts.isdigit() and amps.isdigit():
                v, a = int(volts) / 1e6, int(amps) / 1e6
                clues.append(f"USB-C charger up to {v:g} V {a:g} A ({v * a:.0f} W)")
    profile, _ = power_profile()
    if profile:
        clues.append(f"power profile {profile}")
    degraded = performance_degraded()
    if degraded:
        clues.append(f"performance held back ({degraded})")
    for bat in sorted(glob.glob("/sys/class/power_supply/BAT*")):
        status = _read(os.path.join(bat, "status"))
        if status:
            # Draining while plugged in: the charger does not cover the load
            watts = _read(os.path.join(bat, "power_now"))
            drain = f" {int(watts) / 1e6:.1f} W" if status == "Discharging" and watts and watts.isdigit() else ""
            clues.append(f"battery {status.lower()}{drain}")
    temp = cpu_package_temp_c()
    if temp != "":
        clues.append(f"CPU {temp:g} C")
    return ", ".join(clues)


def cpu_cap_advice():
    """What a person can do about a firmware CPU cap, from what the machine reports ("" when unclear)."""
    degraded = performance_degraded()
    advice = []
    if _read("/sys/devices/platform/thinkpad_acpi/dytc_lapmode") == "1" or "lap" in degraded:
        # A motion sensor: it switches off by itself about 5 minutes after the last movement
        advice.append("the laptop's lap sensor is on (it reacts to movement): leave the laptop still on a hard, "
                      "flat desk; it switches off about 5 minutes after the last movement "
                      "(if it keeps coming back: measure at a fixed lower speed, ENV_CPU_SPEED=800)")
    if "temperature" in degraded:
        advice.append("the machine is too hot: let it cool down, free its vents")
    for bat in sorted(glob.glob("/sys/class/power_supply/BAT*")):
        if _read(os.path.join(bat, "status")) == "Discharging":
            advice.append("the battery drains: plug in the original charger, straight into the laptop")
            break
    return "; ".join(advice)


def expected_cpu_speed(set_speed=None):
    """(MHz, source): the speed limit the CPU should have, or (None, "") when unknown.

    The rule: expect the speed we set; if we set none, the rated speed; else ask (unknown). So:
    a speed in MHz set by ENV_CPU_SPEED ("set"); with turbo off the rated base speed from the model
    name ("rated"); with turbo on the hardware maximum ("maximum"). Values the firmware can change
    together with a cap (base_frequency, the current limits) are never used. run_benchmarks.sh works
    it out once per run.
    """
    if set_speed and str(set_speed).isdigit():
        return int(set_speed), "set"
    if turbo_state() == "off":
        rated = rated_cpu_speed_mhz()
        return (rated, "rated") if rated else (None, "")
    top = _min_mhz("/sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq")
    return (top, "maximum") if top else (None, "")


def expected_cpu_speed_mhz():
    return expected_cpu_speed()[0]


def ac_power():
    for p in glob.glob("/sys/class/power_supply/*/type"):
        if _read(p) == "Mains":
            online = _read(os.path.join(os.path.dirname(p), "online"))
            return {"1": "yes", "0": "no"}.get(online, "")
    return ""


def battery():
    """Battery charge and state (e.g. Charging, Full, Not charging), or "" without a battery."""
    for p in glob.glob("/sys/class/power_supply/*/type"):
        if _read(p) == "Battery":
            d = os.path.dirname(p)
            cap = _read(os.path.join(d, "capacity")) or ""
            return {"percent": int(cap) if cap.isdigit() else "", "status": _read(os.path.join(d, "status")) or ""}
    return ""


def _brightness_percent(pattern):
    for d in sorted(glob.glob(pattern)):
        b, m = _read(os.path.join(d, "brightness")), _read(os.path.join(d, "max_brightness"))
        if b and m and b.isdigit() and m.isdigit() and int(m) > 0:
            return round(100 * int(b) / int(m))
    return ""


def screen_brightness_percent():
    return _brightness_percent("/sys/class/backlight/*")


def keyboard_backlight_percent():
    return _brightness_percent("/sys/class/leds/*kbd_backlight*")


def radios():
    """Wi-Fi and Bluetooth radios: "on", or "off" when blocked (airplane mode, switch)."""
    state = {}
    for r in sorted(glob.glob("/sys/class/rfkill/rfkill*")):
        kind = {"wlan": "wifi", "bluetooth": "bluetooth"}.get(_read(os.path.join(r, "type")))
        if kind:
            on = _read(os.path.join(r, "soft")) == "0" and _read(os.path.join(r, "hard")) == "0"
            state[kind] = "on" if on or state.get(kind) == "on" else "off"
    return state


def cpu_package_temp_c():
    """CPU package temperature: coretemp "Package id 0", else the x86_pkg_temp thermal zone."""
    for hw in glob.glob("/sys/class/hwmon/hwmon*"):
        if _read(os.path.join(hw, "name")) != "coretemp":
            continue
        for label in sorted(glob.glob(os.path.join(hw, "temp*_label"))):
            if (_read(label) or "").startswith("Package id"):
                v = _read(label.replace("_label", "_input"))
                if v:
                    return round(int(v) / 1000, 1)
    for tz in glob.glob("/sys/class/thermal/thermal_zone*"):
        if _read(os.path.join(tz, "type")) == "x86_pkg_temp":
            v = _read(os.path.join(tz, "temp"))
            if v:
                return round(int(v) / 1000, 1)
    return ""


def throttle_counters():
    """(events, milliseconds) of CPU thermal throttling since boot, summed over packages and cores.

    Intel exposes these per CPU in /sys/devices/system/cpu/cpu*/thermal_throttle/. Package
    counters repeat on every CPU of a package, so each package is counted once. Returns
    ("", "") when the machine does not expose them.
    """
    events = ms = 0
    found = False
    seen_packages = set()
    for d in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/thermal_throttle"):
        cpu = os.path.dirname(d)
        pkg = _read(os.path.join(cpu, "topology", "physical_package_id"))
        if pkg not in seen_packages:
            seen_packages.add(pkg)
            e = _read(os.path.join(d, "package_throttle_count"))
            t = _read(os.path.join(d, "package_throttle_total_time_ms"))
            if e is not None:
                found = True
                events += int(e)
                ms += int(t or 0)
        e = _read(os.path.join(d, "core_throttle_count"))
        t = _read(os.path.join(d, "core_throttle_total_time_ms"))
        if e is not None:
            found = True
            events += int(e)
            ms += int(t or 0)
    return (events, ms) if found else ("", "")


def cpu_times():
    """(busy, total) jiffies of all CPUs from /proc/stat; busy excludes idle and iowait."""
    fields = (_read("/proc/stat") or "").splitlines()[0].split()[1:]
    vals = [int(x) for x in fields]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    total = sum(vals[:8])   # guest time is already counted in user/nice
    return total - idle, total


def load1():
    la = _read("/proc/loadavg")
    return float(la.split()[0]) if la else ""


def machine_state():
    return {
        "time_utc": _now(),
        "cpu_governor": cpu_governor(),
        "turbo": turbo_state(),
        "cpu_max_freq_mhz": cpu_max_freq_mhz(),
        "power_profile": power_profile()[0],
        "ac_power": ac_power(),
        "battery": battery(),
        "screen_brightness_percent": screen_brightness_percent(),
        "keyboard_backlight_percent": keyboard_backlight_percent(),
        "wifi": radios().get("wifi", ""),
        "bluetooth": radios().get("bluetooth", ""),
        "cpu_package_temp_c": cpu_package_temp_c(),
        "load1": load1(),
    }


# Settings that change the measurement itself; a difference between start and end
# means the measurement did not run under one set of conditions.
STABLE_KEYS = ("cpu_governor", "turbo", "cpu_max_freq_mhz", "power_profile", "ac_power")


def tool_settings():
    """Measurement settings read from the environment, plus the Scaphandre defaults in use."""
    sys.path.insert(0, _TOOLS_DIR)
    import scaphandre_energy
    env = {k: v for k, v in sorted(os.environ.items()) if k.startswith(("MEASURE_", "BENCH_"))}
    return {
        "scaphandre_step_ms": scaphandre_energy.scaphandre_step_ms(),
        "scaphandre_max_top_consumers": int(os.environ.get("MEASURE_SCAPH_MAX_TOP",
                                                           scaphandre_energy.DEFAULT_MAX_TOP_CONSUMERS)),
        "environment": env,
    }


def csvs_in(folder):
    return [p for p in glob.glob(os.path.join(folder, "**", "*.csv"), recursive=True)
            if not p.endswith("_summary.csv") and os.path.basename(p) not in ("failures.csv", "invalid_runs.csv", "summary.csv")]


def images_in(csv_paths):
    """Image name -> image ID for every "Container Name" in the given CSVs."""
    names = set()
    for path in csv_paths:
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                names.update(r["Container Name"] for r in csv.DictReader(fh) if r.get("Container Name"))
        except (OSError, KeyError, csv.Error):
            continue
    return {n: image_id(n) for n in sorted(names)}


def write_start(path, settings):
    """Write the start of a measurement's metadata to `path`."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # Named after the results folder (metadata.json) or after the CSV (<csv>_metadata.json).
    if os.path.basename(path) == FILENAME:
        name = os.path.basename(os.path.dirname(os.path.abspath(path)))
    else:
        name = os.path.basename(path).removesuffix("_metadata.json")
    meta = {
        "measurement": name,
        "started_at_utc": _now(),
        "software_and_machine": software_and_machine(),
        "settings": {**settings, **tool_settings()},
        "machine_state_start": machine_state(),
    }
    # Fingerprint of the config copy kept in the folder, so a resume can tell if it was edited since
    folder = os.path.dirname(os.path.abspath(path))
    for key, name in (("config_sha256", "bench.config"), ("machine_config_sha256", "machine.config")):
        if os.path.isfile(os.path.join(folder, name)):
            meta[key] = file_sha256(os.path.join(folder, name))
    original = settings.get("reproduces")
    if original:
        # A reproduction: say which measurement it repeats and what is different this time
        meta["reproduces"] = original
        try:
            with open(os.path.join(original, FILENAME), encoding="utf-8") as fh:
                diffs = differences(json.load(fh))
            meta["differences_from_original"] = [{"what": w, "then": t, "now": n} for w, t, n in diffs]
        except (OSError, ValueError):
            meta["differences_from_original"] = "original metadata.json not readable"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    return path


# What must be the same to continue a measurement in the same folder (resume), and what is
# reported when a measurement is made again (reproduce).
COMPARED = ("framework_version", "scaphandre_version", "scaphandre_package_version", "docker_version",
            "python_version", "os", "kernel", "cpu_model", "logical_cpus", "memory_gb", "virtualization")
LABELS = {"framework_version": "Framework (git commit)", "scaphandre_version": "Scaphandre",
          "scaphandre_package_version": "Scaphandre package", "docker_version": "Docker",
          "python_version": "Python", "os": "Operating system", "kernel": "Kernel", "cpu_model": "CPU",
          "logical_cpus": "Logical CPUs", "memory_gb": "Memory (GB)", "virtualization": "Virtualization"}


def file_sha256(path):
    """Fingerprint of a file's exact content, or "" when it does not exist."""
    import hashlib
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


# The fingerprint of a server folder's content, stored in its image at build time (make build)
RECIPE_LABEL = "wseb.recipe"
_NOT_RECIPE = {"_build", "build", "deps", "node_modules", ".git"}      # build output, not the recipe


def recipe_hash(folder):
    """SHA-256 over the names and content of every file of a server folder (not their times), so it
    changes exactly when the recipe changes."""
    h = hashlib.sha256()
    paths = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in _NOT_RECIPE]
        paths += [os.path.join(root, f) for f in files]
    for path in sorted(paths, key=lambda p: os.path.relpath(p, folder)):
        h.update(os.path.relpath(path, folder).encode() + b"\0")
        with open(path, "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def stale_images(benchmarks_dir, names):
    """[(image, reason)] for images whose recipe folder changed since they were built.

    A server whose Dockerfile, code or configuration changed after its image was built would be
    measured in its old form; `make build` brings it up to date. An image built by make build carries
    the fingerprint of its folder's content (RECIPE_LABEL), compared exactly; an image without it falls
    back to comparing times (a rebuild that Docker answers from its cache keeps the old time).
    Images without a folder (variants, images built elsewhere) are not checked.
    """
    folders = {}
    for root, dirs, files in os.walk(benchmarks_dir):
        if "Dockerfile" in files and os.path.basename(root) in names:
            folders[os.path.basename(root)] = root
    stale = []
    for name, folder in sorted(folders.items()):
        label = _run(["docker", "image", "inspect", "--format", "{{index .Config.Labels \"%s\"}}" % RECIPE_LABEL, name])
        if label and label != "<no value>":
            if label != recipe_hash(folder):
                stale.append((name, "its folder changed after the image was built"))
            continue
        created = _run(["docker", "image", "inspect", "--format", "{{.Created}}", name])
        if not created:
            continue
        try:
            # e.g. 2026-10-01T14:41:14.175731853+02:00: drop the fraction (nanoseconds), keep the zone
            built = datetime.datetime.fromisoformat(re.sub(r"\.\d+", "", created).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        newest, newest_file = 0.0, ""
        for root, _, files in os.walk(folder):
            for f in files:
                t = os.path.getmtime(os.path.join(root, f))
                if t > newest:
                    newest, newest_file = t, os.path.relpath(os.path.join(root, f), benchmarks_dir)
        if newest > built + 1:
            stale.append((name, f"{newest_file} changed after the image was built"))
    return stale


def write_images(path, names):
    """Record the ID of every image the measurement will use, before the first run."""
    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)
    meta["images_at_start"] = {n: image_id(n) for n in sorted(set(names))}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    return path


def differences(meta, folder=None):
    """[(what, then, now)] for software, machine and images that differ from `meta` (a metadata.json).

    With `folder` (resume), the folder's own copy of the config must also be unchanged since the start.
    """
    then = meta.get("software_and_machine", {})
    now = software_and_machine()
    diffs = [(LABELS[k], then.get(k, ""), now.get(k, "")) for k in COMPARED
             if k in then and str(then.get(k, "")) != str(now.get(k, ""))]
    for name, old in sorted((meta.get("images_at_start") or {}).items()):
        new = image_id(name)
        if new != old:
            diffs.append((f"Image {name}", old, new or "missing"))
    for key, name in (("config_sha256", "bench.config"), ("machine_config_sha256", "machine.config")):
        recorded = meta.get(key)
        if folder and recorded:
            now_sha = file_sha256(os.path.join(folder, name))
            if now_sha != recorded:
                diffs.append((f"Config ({name} in the folder, edited)", recorded[:12], now_sha[:12] or "missing"))
    return diffs


def differences_text(diffs):
    width = max(len(d[0]) for d in diffs)
    return "\n".join(f"  {what:<{width}}  {then or '?'}  ->  {now or '?'}" for what, then, now in diffs)


def write_resume(path, settings):
    """Record that an unfinished measurement was resumed (the original start record is kept)."""
    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)
    diffs = differences(meta, os.path.dirname(os.path.abspath(path)))
    meta.setdefault("resumes", []).append({
        "resumed_at_utc": _now(),
        "settings": settings,
        "machine_state": machine_state(),
        # Only non-empty when the resume was forced with RESUME_ANYWAY=1
        "differences_from_start": [{"what": w, "then": t, "now": n} for w, t, n in diffs],
    })
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    return path


def _original_args(settings, config):
    """The measurement's original arguments, with its --config replaced by `config` and any
    --config/--resume/--reproduce of the original dropped."""
    import shlex
    args, out, skip = shlex.split(settings.get("arguments", "")), [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ("--config", "--resume", "--reproduce"):
            skip = True
            continue
        out.append(a)
    return ["--config", config] + out


def resume_info(folder):
    """Shell assignments to resume `folder`: original arguments, seed, and whether it can be resumed.

    The original --config is replaced by the copy saved in the folder; any --resume is dropped.
    A measurement is only continued with the same software, machine and images it started with,
    unless RESUME_ANYWAY=1 (the differences are then recorded in metadata.json).
    """
    import shlex
    path = os.path.join(folder, FILENAME)
    problems = []
    meta = {}
    if not os.path.isfile(path):
        problems.append(f"{path} not found")
    else:
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)
        if "finished_at_utc" in meta:
            problems.append("this measurement already finished")
    if not os.path.isfile(os.path.join(folder, "bench.config")):
        problems.append("no bench.config in the folder (only measurements made with --config can be resumed)")
    if meta and not problems and os.environ.get("RESUME_ANYWAY") != "1":
        diffs = differences(meta, folder)
        if diffs:
            problems.append("these changed since the measurement started:\n" + differences_text(diffs) +
                            "\nResults made with different tools must not share one folder. Start a new "
                            "measurement, or continue anyway with RESUME_ANYWAY=1 (the differences are then "
                            "recorded in metadata.json).")
    settings = meta.get("settings", {})
    out = _original_args(settings, os.path.join(folder, "bench.config"))
    lines = [f"RESUME_PROBLEMS={shlex.quote('; '.join(problems))}",
             f"RESUME_SEED={shlex.quote(str(settings.get('shuffle_seed', '')))}",
             "set -- " + " ".join(shlex.quote(a) for a in out)]
    return "\n".join(lines)


def latest_unfinished(results_root):
    """The most recently started measurement under `results_root` that can be resumed, or ""."""
    found = []
    for path in glob.glob(os.path.join(results_root, "*", FILENAME)):
        folder = os.path.dirname(path)
        try:
            with open(path, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            continue
        if "finished_at_utc" not in meta and "abandoned_at_utc" not in meta \
                and os.path.isfile(os.path.join(folder, "bench.config")):
            found.append((meta.get("started_at_utc", ""), folder))
    return max(found)[1] if found else ""


ABANDONED = ".abandoned"


def unfinished(results_root, config_path):
    """For a fresh run of `config_path`: "SAME <folder> <done> <total> <yes|no>" for the newest unfinished
    measurement made with the same config (same file content; yes/no: whether it can still be continued,
    i.e. the framework, machine and images are unchanged), else "OTHER <folder>" for the newest
    unfinished one of another config, else ""."""
    try:
        with open(config_path, "rb") as fh:
            wanted = fh.read()
    except OSError:
        return ""
    same, other = [], []
    for path in glob.glob(os.path.join(results_root, "*", FILENAME)):
        folder = os.path.dirname(path)
        try:
            with open(path, encoding="utf-8") as fh:
                meta = json.load(fh)
            with open(os.path.join(folder, "bench.config"), "rb") as fh:
                config = fh.read()
        except (OSError, ValueError):
            continue
        if "finished_at_utc" in meta or "abandoned_at_utc" in meta:
            continue
        (same if config == wanted else other).append((meta.get("started_at_utc", ""), folder, meta))
    if same:
        _, folder, meta = max(same, key=lambda s: (s[0], s[1]))
        try:
            with open(os.path.join(folder, "progress.txt"), encoding="utf-8") as fh:
                done = sum(1 for line in fh if line.strip())
        except OSError:
            done = 0
        try:
            with open(os.path.join(folder, "plan.json"), encoding="utf-8") as fh:
                total = json.load(fh).get("total", "?")
        except (OSError, ValueError):
            total = "?"
        try:
            resumable = os.environ.get("RESUME_ANYWAY") == "1" or not differences(meta, folder)
        except Exception:                                   # unknown: let the resume itself say why
            resumable = True
        return f"SAME {folder} {done} {total} {'yes' if resumable else 'no'}"
    return f"OTHER {max(other, key=lambda s: (s[0], s[1]))[1]}" if other else ""


def abandon(folder, reason):
    """Mark an unfinished measurement as abandoned: never resumed, skipped by the graphs and reports.
    Its data stays as it is."""
    path = os.path.join(folder, FILENAME)
    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)
    meta["abandoned_at_utc"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta["abandoned_because"] = reason
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    with open(os.path.join(folder, ABANDONED), "w", encoding="utf-8") as fh:
        fh.write(f"{meta['abandoned_at_utc']} {reason}\n")


def reproduce_info(folder):
    """Shell assignments to measure `folder` again in a new folder: every setting as it was used
    (bench.config.resolved, shuffle seed included) and the original arguments. The differences
    from the original are printed (REPRODUCE_DIFFERENCES) and recorded in the new metadata.json."""
    import shlex
    path = os.path.join(folder, FILENAME)
    resolved = os.path.join(folder, "bench.config.resolved")
    problems, meta = [], {}
    if not os.path.isfile(path):
        problems.append(f"{path} not found")
    else:
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)
    if not os.path.isfile(resolved):
        problems.append("no bench.config.resolved in the folder (only measurements made with --config "
                        "can be reproduced)")
    diffs = differences(meta) if meta else []
    out = _original_args(meta.get("settings", {}), resolved)
    lines = [f"REPRODUCE_PROBLEMS={shlex.quote('; '.join(problems))}",
             f"REPRODUCE_CONFIG={shlex.quote(meta.get('settings', {}).get('config_file', ''))}",
             f"REPRODUCE_DIFFERENCES={shlex.quote(differences_text(diffs) if diffs else '')}",
             "set -- " + " ".join(shlex.quote(a) for a in out)]
    return "\n".join(lines)


def write_end(path, csv_paths):
    """Complete the metadata at `path`; image IDs come from the measurement's CSVs."""
    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)
    end = machine_state()
    start = meta.get("machine_state_start", {})
    meta["finished_at_utc"] = _now()
    meta["machine_state_end"] = end
    meta["conditions_stable"] = all(start.get(k) == end.get(k) for k in STABLE_KEYS)
    meta["images"] = images_in(csv_paths)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    return path, meta["conditions_stable"]


def main():
    ap = argparse.ArgumentParser(description="Write the provenance of one measurement to <folder>/metadata.json.")
    ap.add_argument("phase", choices=["start", "end", "resume", "resume-info", "reproduce-info", "images",
                                      "latest-unfinished", "temp", "recipe-hash", "expected-cpu-speed",
                                      "unfinished", "abandon"],
                    help="start/end/resume of a measurement, images: record the image IDs at the start, "
                         "resume-info/reproduce-info: shell assignments to resume or reproduce a folder, "
                         "latest-unfinished: print the newest folder that can be resumed, "
                         "temp: print the CPU package temperature, "
                         "recipe-hash: print the fingerprint of a server folder (make build stores it in the image)")
    ap.add_argument("folder", nargs="?", help="Results folder of the measurement (start/end)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="Extra setting to record at start (repeatable)")
    ap.add_argument("names", nargs="*", help="Image names (images)")
    args = ap.parse_args()
    if args.phase == "temp":
        print(cpu_package_temp_c())
        return
    if args.phase == "expected-cpu-speed":
        mhz, source = expected_cpu_speed(args.folder)          # optional: ENV_CPU_SPEED
        print(f"{mhz} {source}" if mhz else "")
        return
    if args.phase == "recipe-hash":
        print(recipe_hash(args.folder))
        return
    if args.phase == "unfinished":                         # unfinished <results root> <config file>
        print(unfinished(args.folder or "results", (args.names or [""])[0]))
        return
    if args.phase == "abandon":                            # abandon <folder> <reason words>
        abandon(args.folder, " ".join(args.names) or "started again from zero")
        return
    if args.phase == "latest-unfinished":
        print(latest_unfinished(args.folder or "results"))
        return
    if not args.folder:
        ap.error("start/end need the results folder")
    path = os.path.join(args.folder, FILENAME)
    if args.phase == "resume-info":
        print(resume_info(args.folder))
        return
    if args.phase == "reproduce-info":
        print(reproduce_info(args.folder))
        return
    if args.phase == "images":
        write_images(path, args.names)
        return
    if args.phase == "resume":
        settings = dict(s.split("=", 1) for s in args.set if "=" in s)
        print(f"Metadata: {write_resume(path, settings)} (resumed)")
        return
    if args.phase == "start":
        settings = dict(s.split("=", 1) for s in args.set if "=" in s)
        print(f"Metadata: {write_start(path, settings)}")
    else:
        path, stable = write_end(path, csvs_in(args.folder))
        print(f"Metadata: {path}" + ("" if stable else
              "  WARNING: governor, turbo, frequency cap or AC power changed during the measurement"))


if __name__ == "__main__":
    main()
