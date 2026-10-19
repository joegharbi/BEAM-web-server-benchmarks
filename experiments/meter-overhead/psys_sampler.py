#!/usr/bin/env python3
"""Root helper of the meter-overhead experiment: every 0.1 s, one line with the time, the laptop's energy
counter (RAPL psys, the counter Scaphandre reads too) and the CPU time Scaphandre has used so far (from
/proc/<pid>/stat, the kernel's own counter for the process and all its threads; 0 when it does not run).

    sudo python3 experiments/meter-overhead/psys_sampler.py OUTPUT_FILE

It runs the same way whether Scaphandre is on or off, so its own small cost is in both conditions.
Lines: "<unix time> <psys energy, microjoules> <psys counter range, microjoules> <scaphandre CPU, clock ticks>"
"""
import glob
import os
import signal
import sys
import time

STEP_S = 0.1


def psys_domain():
    for d in glob.glob("/sys/class/powercap/intel-rapl:*"):
        try:
            if open(os.path.join(d, "name")).read().strip() == "psys":
                return d
        except OSError:
            pass
    sys.exit("no RAPL psys domain on this machine")


def find_scaphandre():
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                if open(f"/proc/{pid}/comm").read().strip() == "scaphandre":
                    return pid
            except OSError:
                pass
    return None


def cpu_ticks(pid):
    """utime + stime of the process (all its threads), in clock ticks; None when it is gone."""
    try:
        fields = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, IndexError, ValueError):
        return None


def main():
    domain = psys_domain()
    energy_path = os.path.join(domain, "energy_uj")
    max_range = int(open(os.path.join(domain, "max_energy_range_uj")).read())
    running = [True]
    signal.signal(signal.SIGTERM, lambda *_: running.__setitem__(0, False))
    signal.signal(signal.SIGINT, lambda *_: running.__setitem__(0, False))
    pid, last_search = None, 0.0
    with open(sys.argv[1], "a", buffering=1) as out:
        out.write(f"# clk_tck {os.sysconf('SC_CLK_TCK')}\n")
        nxt = time.time()
        while running[0]:
            now = time.time()
            ticks = cpu_ticks(pid) if pid else None
            if ticks is None and now - last_search >= 1.0:          # look for Scaphandre at most once a second
                pid, last_search = find_scaphandre(), now
                ticks = cpu_ticks(pid) if pid else None
            energy = int(open(energy_path).read())
            out.write(f"{now:.4f} {energy} {max_range} {ticks or 0}\n")
            nxt += STEP_S
            time.sleep(max(0.0, nxt - time.time()))


if __name__ == "__main__":
    main()
