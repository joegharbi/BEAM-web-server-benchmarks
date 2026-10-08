#!/usr/bin/env python3
"""Readiness gate: wait until the machine is back in a clean state before a run.

Checks, every CHECK_EVERY seconds:
  * temperature  - CPU package temperature <= temperature reference + TEMP_MARGIN
                   (reference = the resting temperature measured at the start, or a fixed value)
  * CPU busy     - whole-machine CPU use over the last interval <= CPU reference + CPU margin
                   (reference = the resting CPU use measured at the start, or a fixed value)
  * throttling   - the CPU throttle counters did not increase during the last interval
  * CPU speed    - the CPU may run at its expected speed: not capped below it by the firmware
                   (e.g. a charger too weak for the laptop). CPU_SPEED: auto = the base speed
                   with turbo off, the maximum with turbo on; a number = that many MHz; off.
  * charger      - a laptop must run on its charger (ON_BATTERY: wait, stop or ignore;
                   machines without a battery are never affected)
The machine is ready when every enabled check passes CONSECUTIVE times in a row and
at least MIN_WAIT seconds have passed. If it is still not ready after MAX_WAIT
seconds, ON_TIMEOUT decides: keep waiting, stop, or measure anyway (and say why).

Check 1 (`wait`, run by run_benchmarks.sh) happens before the server container starts.
Check 2 (`pre_load_gate()`, run by the measurement tools) happens after the server has
booted, right before the load, because booting a server warms the CPU again.

Commands:
  baseline                       print the resting CPU temperature and CPU use, e.g. "46.0 2.1"
  wait --result FILE [options]   wait for the gate; write {"waited_s", "ready_check"} to FILE
Exit codes of wait: 0 = go ahead (ready, or ON_TIMEOUT=measure), 2 = stop (ON_TIMEOUT=stop).
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_metadata  # noqa: E402

STATUS_EVERY_S = 60
ON_BATTERY_REASON = "on battery (connect the charger)"


QUIET_LIMIT_PERCENT = 2.0      # a program using more than this share of one CPU core at rest is reported


def process_cpu_ticks(proc="/proc"):
    """{pid: (CPU ticks used so far, program name)} of every user program (kernel threads, which have
    no command line, are left out)."""
    out = {}
    for d in os.listdir(proc):
        if not d.isdigit():
            continue
        try:
            with open(os.path.join(proc, d, "stat"), encoding="utf-8", errors="replace") as fh:
                stat = fh.read()
            with open(os.path.join(proc, d, "cmdline"), "rb") as fh:
                if not fh.read(1):
                    continue
        except OSError:
            continue
        name = stat[stat.find("(") + 1:stat.rfind(")")]
        fields = stat[stat.rfind(")") + 2:].split()
        out[int(d)] = (int(fields[11]) + int(fields[12]), name)          # utime + stime
    return out


def busy_programs(before, after, seconds, ticks_per_s=None, skip=()):
    """[(program, percent of one core)] of the programs that used CPU between two process_cpu_ticks
    readings, busiest first; processes in `skip` (this tool itself) are left out."""
    ticks_per_s = ticks_per_s or os.sysconf("SC_CLK_TCK")
    used = {}
    for pid, (ticks, name) in after.items():
        if pid in skip or pid not in before or seconds <= 0:
            continue
        delta = ticks - before[pid][0]
        if delta > 0:
            used[name] = used.get(name, 0) + delta
    return sorted(((n, round(100.0 * t / ticks_per_s / seconds, 1)) for n, t in used.items()),
                  key=lambda x: -x[1])


def resting_state(seconds=10, every=1.0, programs=None):
    """Resting temperature and CPU use over `seconds`.

    Temperature: one reading every `every` seconds, both ends included; the middle value
    (median) is used, so one odd reading cannot skew it. CPU use: the average over the
    same window.
    """
    readings = int(round(seconds / every)) + 1
    vals = []
    ticks0, t0 = process_cpu_ticks(), time.monotonic()
    busy0, total0 = run_metadata.cpu_times()
    for i in range(readings):
        t = run_metadata.cpu_package_temp_c()
        if t != "":
            vals.append(t)
        if i < readings - 1:
            time.sleep(every)
    busy1, total1 = run_metadata.cpu_times()
    if programs is not None:                                  # who kept the machine busy while it rested
        programs.extend(busy_programs(ticks0, process_cpu_ticks(), time.monotonic() - t0, skip={os.getpid()}))
    temp = round(statistics.median(vals), 1) if vals else ""
    cpu = round(100.0 * (busy1 - busy0) / (total1 - total0), 1) if total1 > total0 else ""
    return temp, cpu


def check_once(prev, args):
    """Sample the machine; return (new_sample, [failure reasons]) against the previous sample."""
    busy, total = run_metadata.cpu_times()
    events, _ = run_metadata.throttle_counters()
    temp = run_metadata.cpu_package_temp_c()
    now = {"busy": busy, "total": total, "throttle": events, "temp": temp}
    fails = []
    if args.temp_margin is not None and args.temp_reference is not None and temp != "":
        limit = args.temp_reference + args.temp_margin
        if temp > limit:
            fails.append(f"temperature {temp:.1f} C > {limit:.1f} C")
    if args.cpu_margin is not None and now["total"] > prev["total"]:
        pct = 100.0 * (now["busy"] - prev["busy"]) / (now["total"] - prev["total"])
        limit = min(100.0, (args.cpu_reference or 0.0) + args.cpu_margin)
        if pct > limit:
            fails.append(f"CPU busy {pct:.1f}% > {limit:g}%")
    if args.no_throttling and events != "" and prev["throttle"] != "" and events > prev["throttle"]:
        fails.append(f"throttling ({events - prev['throttle']} new events)")
    if getattr(args, "on_battery", "wait") != "ignore" and run_metadata.ac_power() == "no":
        fails.append(ON_BATTERY_REASON)
    speed = cpu_speed_problem(getattr(args, "cpu_speed", "off"))
    if speed:
        fails.append(speed)
    return now, fails


def cpu_speed_problem(setting):
    """A reason when the CPU is capped below its expected speed, else "" (also when unknown or off)."""
    if setting in ("off", "", None):
        return ""
    expected = run_metadata.expected_cpu_speed_mhz() if setting == "auto" else int(setting)
    limit = run_metadata.cpu_speed_limit_mhz()
    if expected and limit and limit < expected:
        clues = run_metadata.cpu_cap_clues()
        advice = run_metadata.cpu_cap_advice()
        return (f"CPU speed capped at {limit} MHz < {expected} MHz (charger or firmware" + (f"; {clues})" if clues else ")")
                + (f". What to do: {advice}" if advice else ""))
    return ""


def wait(args):
    t0 = time.monotonic()
    busy, total = run_metadata.cpu_times()
    events, _ = run_metadata.throttle_counters()
    prev = {"busy": busy, "total": total, "throttle": events, "temp": run_metadata.cpu_package_temp_c()}
    passes = 0
    fails = []
    last_status = t0
    timed_out = False
    while True:
        time.sleep(args.check_every)
        prev, fails = check_once(prev, args)
        passes = 0 if fails else passes + 1
        elapsed = time.monotonic() - t0
        if passes >= args.consecutive and elapsed >= args.min_wait:
            return round(elapsed, 1), "yes"
        on_battery = ON_BATTERY_REASON in fails
        if on_battery and getattr(args, "on_battery", "wait") == "stop":
            print("[READY] the laptop is on battery (ON_BATTERY=stop). Stopping.", flush=True)
            sys.exit(2)
        # On battery with ON_BATTERY=wait, never measure: READY_ON_TIMEOUT does not apply
        if elapsed >= args.max_wait and fails and not on_battery:
            if args.on_timeout == "measure":
                return round(elapsed, 1), "no: " + "; ".join(fails)
            if args.on_timeout == "stop":
                print(f"[READY] not ready after {elapsed:.0f}s: {'; '.join(fails)}. Stopping.", flush=True)
                sys.exit(2)
            if not timed_out:
                timed_out = True
                print(f"[READY] still not ready after {elapsed:.0f}s (READY_MAX_WAIT_SECONDS); "
                      "keeping on waiting (READY_ON_TIMEOUT=wait)", flush=True)
        if fails and time.monotonic() - last_status >= STATUS_EVERY_S:
            last_status = time.monotonic()
            print(f"[READY] waiting: {'; '.join(fails)} ({elapsed / 60:.0f}m elapsed)", flush=True)


def pre_load_gate():
    """Check 2: right before the load, with the server booted and idle.

    Waits until the temperature is back within the margin and nothing is throttling.
    CPU busy is not checked here: an idle server still uses some CPU, and how much
    differs between servers, so a limit here would treat servers differently.
    Settings come from MEASURE_READY_* variables exported by run_benchmarks.sh --config;
    without them this does nothing. Returns (waited_s or None, result); raises
    SystemExit(2) when READY_ON_TIMEOUT=stop and the machine does not become ready.
    """
    env = os.environ
    if "MEASURE_READY_ON_TIMEOUT" not in env:
        return None, "not checked"
    args = argparse.Namespace(
        temp_reference=optional_float(env.get("MEASURE_READY_TEMP_REFERENCE_C")),
        temp_margin=optional_float(env.get("MEASURE_READY_TEMP_MARGIN_C")),
        cpu_reference=None,
        cpu_margin=None,
        no_throttling=int(env.get("MEASURE_READY_NO_THROTTLING", "1")),
        check_every=float(env.get("MEASURE_READY_CHECK_EVERY_SECONDS", "5")),
        consecutive=int(env.get("MEASURE_READY_CONSECUTIVE_CHECKS", "2")),
        min_wait=0,
        max_wait=float(env.get("MEASURE_READY_MAX_WAIT_SECONDS", "300")),
        on_timeout=env["MEASURE_READY_ON_TIMEOUT"],
        on_battery=env.get("MEASURE_ON_BATTERY", "wait"),
        cpu_speed=env.get("MEASURE_READY_CPU_SPEED", "auto"),
    )
    return wait(args)


def combine(before_start, before_load):
    """One "Ready Check" value for both checks, saying which one failed."""
    results = [("before start", before_start), ("before load", before_load)]
    checked = [(name, r) for name, r in results if r != "not checked"]
    if not checked:
        return "not checked"
    failed = [f"{name}: {r[4:] if r.startswith('no: ') else r}" for name, r in checked if r != "yes"]
    return "yes" if not failed else "no: " + "; ".join(failed)


def optional_float(v):
    return None if v in ("", None) else float(v)


def main():
    ap = argparse.ArgumentParser(description="Readiness gate before each measurement run.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline", help="Print the resting CPU temperature and CPU use (two values)")
    b.add_argument("--seconds", type=float, default=10, help="How long to measure (default 10)")
    b.add_argument("--programs", action="store_true", help=f"then print the programs that used more than "
                   f"{QUIET_LIMIT_PERCENT:g}%% of a core meanwhile (name and percent, one per line)")
    w = sub.add_parser("wait", help="Wait until the machine is ready")
    w.add_argument("--result", required=True, help="JSON file to write waited_s and ready_check to")
    w.add_argument("--temp-reference", type=optional_float, default=None,
                   help="Temperature the CPU is compared to (resting or fixed), in C")
    w.add_argument("--temp-margin", type=optional_float, default=None, help="Empty = temperature check off")
    w.add_argument("--cpu-reference", type=optional_float, default=None,
                   help="CPU use the machine is compared to (resting or fixed), in percent")
    w.add_argument("--cpu-margin", type=optional_float, default=None, help="Empty = CPU busy check off")
    w.add_argument("--no-throttling", type=int, choices=[0, 1], default=1)
    w.add_argument("--check-every", type=float, default=5)
    w.add_argument("--consecutive", type=int, default=2)
    w.add_argument("--min-wait", type=float, default=10)
    w.add_argument("--max-wait", type=float, default=300)
    w.add_argument("--on-timeout", choices=["wait", "stop", "measure"], default="wait")
    w.add_argument("--on-battery", choices=["wait", "stop", "ignore"], default="wait")
    w.add_argument("--cpu-speed", default="auto", help="auto, off, or a speed in MHz (see CPU speed above)")
    args = ap.parse_args()

    if args.cmd == "baseline":
        programs = []
        temp, cpu = resting_state(args.seconds, programs=programs)
        print(temp, cpu)
        if args.programs:                                     # then "name 12.3" per line, over the limit only
            for n, p in programs:
                if p > QUIET_LIMIT_PERCENT:
                    print(n.replace(" ", "_"), p)
        return
    waited, ready = wait(args)
    with open(args.result, "w", encoding="utf-8") as fh:
        json.dump({"waited_s": waited, "ready_check": ready}, fh)


if __name__ == "__main__":
    main()
