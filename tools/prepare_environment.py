#!/usr/bin/env python3
"""Apply steady-measurement settings, then restore them afterwards (needs sudo).

Two actions:
  apply    - save the current state, then set the CPU governor to performance,
             turn turbo off, and stop other running Docker containers. Optionally
             also set the screen brightness, turn the keyboard light off, and turn
             Wi-Fi and Bluetooth off (each 'unchanged' by default).
  restore  - read the saved state and put everything back: the governor, turbo,
             the containers that were stopped, the screen and keyboard light, and
             the Wi-Fi and Bluetooth radios.

It records what it changed in a small state file, so restore undoes exactly what
apply did and nothing more. Read-only checking is in tools/check_environment.py;
this tool changes the system, so it needs root. Run it with sudo.

It does not touch swap or time sync, which are riskier to flip automatically; the
check tool flags those so you can decide. Standard library only.

  sudo python3 tools/prepare_environment.py apply
  ...run the measurement campaign...
  sudo python3 tools/prepare_environment.py restore
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile

DEFAULT_STATE = os.path.join(tempfile.gettempdir(), "wseb_env_state.json")
GOV_GLOB = "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"
INTEL_NO_TURBO = "/sys/devices/system/cpu/intel_pstate/no_turbo"
BOOST = "/sys/devices/system/cpu/cpufreq/boost"
BACKLIGHT_GLOB = "/sys/class/backlight/*"
KBD_LIGHT_GLOB = "/sys/class/leds/*kbd_backlight*"
RFKILL_GLOB = "/sys/class/rfkill/rfkill*"


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def write(path, value):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(value)
        return True
    except OSError as e:
        print(f"  could not write {path}: {e}")
        return False


def require_root():
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        sys.exit("This changes system settings, so it needs root. Run it with sudo.")


def docker_running():
    try:
        out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                             capture_output=True, text=True, timeout=10)
        return [n for n in out.stdout.splitlines() if n.strip()]
    except Exception:
        return []


def set_brightness(pattern, percent, label, saved):
    """Set every light matching `pattern` to `percent` of its maximum; previous values go to `saved`."""
    dirs = [d for d in sorted(glob.glob(pattern)) if (read(os.path.join(d, "max_brightness")) or "").isdigit()]
    if not dirs:
        print(f"{label}: not available, skipped")
        return
    for d in dirs:
        path = os.path.join(d, "brightness")
        saved[path] = read(path)
        write(path, str(round(int(read(os.path.join(d, "max_brightness"))) * percent / 100)))
    print(f"{label}: {percent}%")


def radio_off(kind, label, saved):
    """Block every radio of `kind` ('wlan' or 'bluetooth') in software, like airplane mode."""
    radios = [r for r in sorted(glob.glob(RFKILL_GLOB)) if read(os.path.join(r, "type")) == kind]
    if not radios:
        print(f"{label}: not available, skipped")
        return
    for r in radios:
        path = os.path.join(r, "soft")
        saved[path] = read(path)
        write(path, "1")
    print(f"{label}: off")


def _route_dev(ip):
    """Network interface the machine uses to reach `ip` (e.g. wlp0s20f3), or ""."""
    try:
        out = subprocess.run(["ip", "-o", "route", "get", ip], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = out.split()
    return parts[parts.index("dev") + 1] if "dev" in parts else ""


def _is_wireless(dev):
    return bool(dev) and os.path.isdir(f"/sys/class/net/{dev}/wireless")


def remote_over_wifi(env=None):
    """True when this session came in over SSH through a Wi-Fi interface (switching Wi-Fi off would cut it)."""
    conn = (env if env is not None else os.environ).get("SSH_CONNECTION", "").split()
    return bool(conn) and _is_wireless(_route_dev(conn[0]))


def do_apply(args):
    require_root()
    state = {"governors": {}, "turbo": None, "stopped_containers": [], "files": {}}

    gov_files = sorted(glob.glob(GOV_GLOB))
    if args.governor == "unchanged":
        print("CPU governor: unchanged")
    elif gov_files:
        for p in gov_files:
            state["governors"][p] = read(p)
        changed = sum(1 for p in gov_files if write(p, args.governor))
        print(f"CPU governor: set {changed}/{len(gov_files)} cores to '{args.governor}'")
    else:
        print("CPU governor: not available, skipped")

    if args.turbo == "unchanged":
        print("Turbo: unchanged")
    elif read(INTEL_NO_TURBO) is not None:
        # intel_pstate: no_turbo=1 means turbo off
        state["turbo"] = {"path": INTEL_NO_TURBO, "prev": read(INTEL_NO_TURBO)}
        write(INTEL_NO_TURBO, "1" if args.turbo == "off" else "0")
        print(f"Turbo (Intel): {args.turbo}")
    elif read(BOOST) is not None:
        # cpufreq boost: boost=1 means turbo on
        state["turbo"] = {"path": BOOST, "prev": read(BOOST)}
        write(BOOST, "0" if args.turbo == "off" else "1")
        print(f"Turbo (boost): {args.turbo}")
    else:
        print("Turbo: not available, skipped")

    if args.screen_brightness != "unchanged":
        set_brightness(BACKLIGHT_GLOB, int(args.screen_brightness), "Screen brightness", state["files"])
    if args.keyboard_light == "off":
        set_brightness(KBD_LIGHT_GLOB, 0, "Keyboard light", state["files"])
    if args.wifi == "off":
        radio_off("wlan", "Wi-Fi", state["files"])
    if args.bluetooth == "off":
        radio_off("bluetooth", "Bluetooth", state["files"])

    keep = {n.strip() for n in (args.keep or "").split(",") if n.strip()}
    to_stop = [n for n in docker_running() if n not in keep] if args.stop_containers else []
    if not args.stop_containers:
        print("Containers: left running (--no-stop-containers)")
    elif to_stop:
        subprocess.run(["docker", "stop"] + to_stop, capture_output=True, text=True)
        state["stopped_containers"] = to_stop
        print(f"Stopped containers: {', '.join(to_stop)}")
    else:
        print("Containers: none to stop")

    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(state, f)
    print(f"\nSaved previous state to {args.state}")
    print("Run 'restore' after your measurements to put everything back.")


def do_restore(args):
    require_root()
    if not os.path.isfile(args.state):
        sys.exit(f"No saved state at {args.state}; nothing to restore.")
    with open(args.state, encoding="utf-8") as f:
        state = json.load(f)

    restored = sum(1 for p, prev in (state.get("governors") or {}).items() if prev and write(p, prev))
    print(f"CPU governor: restored {restored} core(s)")

    t = state.get("turbo")
    if t and t.get("prev") is not None:
        write(t["path"], t["prev"])
        print("Turbo: restored")

    files = state.get("files") or {}
    restored = sum(1 for path, prev in files.items() if prev is not None and write(path, prev))
    if files:
        print(f"Screen, keyboard light and radios: restored {restored} setting(s)")

    stopped = state.get("stopped_containers") or []
    if stopped:
        subprocess.run(["docker", "start"] + stopped, capture_output=True, text=True)
        print(f"Restarted containers: {', '.join(stopped)}")

    os.remove(args.state)
    print(f"\nRestored. Removed {args.state}.")


def brightness_percent(pattern):
    for d in sorted(glob.glob(pattern)):
        b, m = read(os.path.join(d, "brightness")), read(os.path.join(d, "max_brightness"))
        if (b or "").isdigit() and (m or "").isdigit() and int(m) > 0:
            return round(100 * int(b) / int(m))
    return ""


def brightness_arg(v):
    if v == "unchanged":
        return v
    if v.isdigit() and 0 <= int(v) <= 100:
        return v
    raise argparse.ArgumentTypeError("must be 'unchanged' or a percentage 0-100")


def do_verify(args):
    """Check the machine is in the requested state; exit 1 if not. Needs no root."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import run_metadata
    problems = []
    gov = run_metadata.cpu_governor()
    if args.governor != "unchanged" and gov and gov != args.governor:
        problems.append(f"CPU governor is '{gov}', expected '{args.governor}'")
    turbo = run_metadata.turbo_state()
    if args.turbo != "unchanged" and turbo and turbo != args.turbo:
        problems.append(f"turbo is '{turbo}', expected '{args.turbo}'")
    if args.screen_brightness != "unchanged":
        now = brightness_percent(BACKLIGHT_GLOB)
        # The steps of a backlight are coarse, so a difference of 2% is still the requested level
        if now != "" and abs(now - int(args.screen_brightness)) > 2:
            problems.append(f"screen brightness is {now}%, expected {args.screen_brightness}%")
    if args.keyboard_light == "off" and brightness_percent(KBD_LIGHT_GLOB) not in ("", 0):
        problems.append("keyboard light is on, expected off")
    for kind, label, wanted in (("wlan", "Wi-Fi", args.wifi), ("bluetooth", "Bluetooth", args.bluetooth)):
        if wanted == "off" and any(read(os.path.join(r, "type")) == kind and read(os.path.join(r, "soft")) == "0"
                                   for r in glob.glob(RFKILL_GLOB)):
            problems.append(f"{label} is on, expected off")
    if args.stop_containers:
        keep = {n.strip() for n in (args.keep or "").split(",") if n.strip()}
        others = [n for n in docker_running() if n not in keep]
        if others:
            problems.append(f"other containers are running: {', '.join(others)}")
    print(f"Environment: governor={gov or '?'} turbo={turbo or '?'}")
    for p in problems:
        print(f"  NOT AS REQUESTED: {p}")
    sys.exit(1 if problems else 0)


def main():
    ap = argparse.ArgumentParser(description="Apply/restore/verify steady-measurement settings (apply/restore need sudo).")
    ap.add_argument("action", choices=["apply", "restore", "verify"])
    ap.add_argument("--governor", default="performance",
                    help="CPU governor to set on apply, or 'unchanged' (default: performance)")
    ap.add_argument("--turbo", choices=["off", "on", "unchanged"], default="off",
                    help="Turbo/boost on apply (default: off)")
    ap.add_argument("--no-stop-containers", dest="stop_containers", action="store_false",
                    help="Leave other running containers alone on apply")
    ap.add_argument("--keep", default="",
                    help="Comma-separated container names to keep running on apply")
    ap.add_argument("--screen-brightness", type=brightness_arg, default="unchanged",
                    help="Screen brightness in percent on apply, or 'unchanged' (default)")
    ap.add_argument("--keyboard-light", choices=["off", "unchanged"], default="unchanged",
                    help="Keyboard backlight on apply (default: unchanged)")
    ap.add_argument("--wifi", choices=["off", "unchanged"], default="unchanged",
                    help="Wi-Fi radio on apply (default: unchanged)")
    ap.add_argument("--bluetooth", choices=["off", "unchanged"], default="unchanged",
                    help="Bluetooth radio on apply (default: unchanged)")
    ap.add_argument("--state", default=DEFAULT_STATE,
                    help=f"State file path (default: {DEFAULT_STATE})")
    args = ap.parse_args()
    {"apply": do_apply, "restore": do_restore, "verify": do_verify}[args.action](args)


if __name__ == "__main__":
    main()
