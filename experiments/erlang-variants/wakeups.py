#!/usr/bin/env python3
"""How often does a server wake up, read and write per request? A quick check without a meter and without sudo.

The server runs natively on the server CPUs (as experiments/load-tools), oha sends a fixed rate for a few seconds,
and the kernel's own counters of the server's processes are read before and after:
  - wake-ups: voluntary context switches of all its threads (a thread that slept and was woken up)
  - read and write calls: syscr and syscw of /proc/<pid>/io
  - CPU time: its cgroup
Per request these show whether a server handles each request on its own (about one wake-up and one read each) or
several per wake-up (fewer than one).

    ./srv/bin/python3 experiments/erlang-variants/wakeups.py [image ...]
"""
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "experiments", "load-tools"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import native_server  # noqa: E402
import run  # noqa: E402

RATES, SECONDS, CONNECTIONS = [1000, 5000, 20000, 35000], 10, 100
DEFAULT = ["st-erlang-pure-29-1-1-nobw", "st-erlang-pure-29-1-1", "st-java-netty-4-2-19"]


def counters(server):
    pids = [int(p) for p in open(native_server.cgroup_dir(server.unit) + "/cgroup.procs").read().split()]
    wake = reads = writes = threads = 0
    for pid in pids:
        for status in glob.glob(f"/proc/{pid}/task/*/status"):
            threads += 1
            for line in open(status):
                if line.startswith("voluntary_ctxt_switches"):
                    wake += int(line.split()[1])
        io = dict(line.split(": ") for line in open(f"/proc/{pid}/io").read().splitlines())
        reads, writes = reads + int(io["syscr"]), writes + int(io["syscw"])
    return {"wake": wake, "reads": reads, "writes": writes, "cpu": server.cpu_s(), "threads": threads}


def main(images):
    os.chdir(ROOT)
    run.TOOL_CPUS, server_cpus = {1, 5}, {2, 3, 6, 7}
    os.sched_setaffinity(0, {0, 4})
    out = []
    for image in images:
        server = run.Server(image, "native", server_cpus)
        try:
            raw = os.path.join("/tmp", f"wakeups-{os.getpid()}")
            subprocess.run(run.oha_cmd(5000, 5, CONNECTIONS, raw)[0], preexec_fn=run.pin_tool, capture_output=True)
            for rate in RATES:
                before = counters(server)
                subprocess.run(run.oha_cmd(rate, SECONDS, CONNECTIONS, raw)[0], preexec_fn=run.pin_tool,
                               capture_output=True)
                after = counters(server)
                ok = run.oha_parse(raw)["ok"]
                row = {"image": image, "rate": rate, "delivered": round(ok / SECONDS), "threads": after["threads"]}
                for key in ("wake", "reads", "writes"):
                    row[key + "_per_request"] = round((after[key] - before[key]) / ok, 3)
                row["cpu_us_per_request"] = round(1e6 * (after["cpu"] - before["cpu"]) / ok, 1)
                out.append(row)
                print(f"{image:28} {rate:>6}/s delivered {row['delivered']:>6}: wake-ups {row['wake_per_request']:.2f}, "
                      f"reads {row['reads_per_request']:.2f}, writes {row['writes_per_request']:.2f} per request, "
                      f"CPU {row['cpu_us_per_request']:.0f} us per request, {row['threads']} threads", flush=True)
            os.remove(raw + ".json")
        finally:
            server.stop()
        time.sleep(2)
    return out


if __name__ == "__main__":
    json.dump(main(sys.argv[1:] or DEFAULT), open(os.path.join(HERE, "wakeups.json"), "w"), indent=1)
