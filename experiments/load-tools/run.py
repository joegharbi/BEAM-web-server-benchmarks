#!/usr/bin/env python3
"""Load tools experiment: which tool holds a fixed request rate, at what CPU cost? (plan: memory/README to come)

For every server, rate, repeat and tool (tools in a shuffled order) it starts the tool against GET / of the server in
Docker and records: the rate the tool reached against the target, failures, latency p50/p99 (as the tool reports
them), the tool's own CPU time (kernel counters of the tool process and its threads, read when it exits) and the
server's CPU time (its container cgroup). The Python client is the framework's own and has no rate setting: it sends
rate x seconds requests as fast as it can.

This version has no machine preparation and no energy yet (step 2: does every tool run and report).

    ./srv/bin/python3 experiments/load-tools/run.py                      (short check: Erlang nobw, 1000 req/s, 10 s)
    ./srv/bin/python3 experiments/load-tools/run.py --servers erlang java --rates 1000 2000 --seconds 60 --repeats 5
"""
import argparse
import datetime
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BIN = os.path.join(HERE, "bin")
SERVERS = {"erlang": "st-erlang-pure-29-1-1-nobw", "java": "st-java-netty-4-2-19"}
PORT = 8001
URL = f"http://localhost:{PORT}/"
CONTAINER = "lt-server"


def say(text):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {text}", flush=True)


# ---- tools: command for (rate, seconds, connections, raw file prefix) and a parser of what they wrote --------------

def wrk2_cmd(rate, seconds, conns, raw):
    return [os.path.join(BIN, "wrk2"), "-t2", f"-c{conns}", f"-d{seconds}s", f"-R{rate}", "--latency", URL], None, raw + ".txt"


def _ms(value, unit):
    return float(value) * {"us": 1e-3, "ms": 1.0, "s": 1e3, "m": 6e4}[unit]


def wrk2_parse(raw):
    text = open(raw + ".txt").read()
    done = re.search(r"(\d+) requests in ([\d.]+)(\w+),", text)
    secs = float(done.group(2)) * {"ms": 1e-3, "s": 1.0, "m": 60.0}[done.group(3)]
    lat = {p: _ms(v, u) for p, v, u in re.findall(r"^\s+(50|99)\.000%\s+([\d.]+)(us|ms|s|m)\s*$", text, re.M)}
    sock = re.search(r"Socket errors: connect (\d+), read (\d+), write (\d+), timeout (\d+)", text)
    non2xx = re.search(r"Non-2xx or 3xx responses: (\d+)", text)
    failed = (sum(map(int, sock.groups())) if sock else 0) + (int(non2xx.group(1)) if non2xx else 0)
    total = int(done.group(1))
    return {"ok": total - (int(non2xx.group(1)) if non2xx else 0), "failed": failed, "seconds": secs,
            "p50_ms": lat.get("50"), "p99_ms": lat.get("99")}


def oha_cmd(rate, seconds, conns, raw):
    return [os.path.join(BIN, "oha"), "-z", f"{seconds}s", "-c", str(conns), "-q", str(rate), "--latency-correction",
            "--no-tui", "--output-format", "json", "-o", raw + ".json", URL], None, None


def oha_parse(raw):
    d = json.load(open(raw + ".json"))
    ok = int(d["statusCodeDistribution"].get("200", 0))
    other = sum(int(v) for k, v in d["statusCodeDistribution"].items() if k != "200")
    # oha stops at the deadline and counts the requests still open then as "aborted due to deadline": not failures
    errors = sum(int(v) for k, v in d["errorDistribution"].items() if "deadline" not in k)
    return {"ok": ok, "failed": other + errors, "seconds": d["summary"]["total"],
            "p50_ms": d["latencyPercentiles"]["p50"] * 1e3, "p99_ms": d["latencyPercentiles"]["p99"] * 1e3,
            "aborted_at_end": sum(int(v) for k, v in d["errorDistribution"].items() if "deadline" in k)}


def k6_cmd(rate, seconds, conns, raw, script="k6_http.js"):
    env = dict(os.environ, RATE=str(rate), DURATION=f"{seconds}s", CONNECTIONS=str(conns), URL=URL,
               SUMMARY=raw + ".json", K6_NO_USAGE_REPORT="true")
    return [os.path.join(BIN, "k6"), "run", "-q", "--no-color", os.path.join(HERE, script)], env, raw + ".txt"


def k6_paced_cmd(rate, seconds, conns, raw):
    return k6_cmd(rate, seconds, conns, raw, "k6_paced.js")


def k6_parse(raw):
    d = json.load(open(raw + ".json"))
    m = d["metrics"]
    total = int(m["http_reqs"]["values"]["count"])
    failed = int(m["http_req_failed"]["values"]["passes"])
    return {"ok": total - failed, "failed": failed, "seconds": d["state"]["testRunDurationMs"] / 1e3,
            "p50_ms": m["http_req_duration"]["values"]["p(50)"], "p99_ms": m["http_req_duration"]["values"]["p(99)"],
            "dropped": int(m.get("dropped_iterations", {}).get("values", {}).get("count", 0))}


def vegeta_cmd(rate, seconds, conns, raw):
    return [os.path.join(BIN, "vegeta"), "attack", f"-rate={rate}/s", f"-duration={seconds}s",
            f"-max-connections={conns}", "-output", raw + ".bin"], None, None


def vegeta_parse(raw):
    out = subprocess.run([os.path.join(BIN, "vegeta"), "report", "-type=json", raw + ".bin"],
                         capture_output=True, text=True, check=True).stdout
    open(raw + ".json", "w").write(out)
    os.remove(raw + ".bin")
    d = json.loads(out)
    ok = int(d["status_codes"].get("200", 0))
    return {"ok": ok, "failed": int(d["requests"]) - ok, "seconds": d["duration"] / 1e9,
            "p50_ms": d["latencies"]["50th"] / 1e6, "p99_ms": d["latencies"]["99th"] / 1e6}


def python_cmd(rate, seconds, conns, raw):
    return [sys.executable, os.path.join(HERE, "python_client.py"), URL, str(rate * seconds)], None, raw + ".json"


def python_parse(raw):
    d = json.loads(open(raw + ".json").read().strip().splitlines()[-1])
    return {k: d[k] for k in ("ok", "failed", "seconds", "p50_ms", "p99_ms")}


TOOLS = {"python": (python_cmd, python_parse), "wrk2": (wrk2_cmd, wrk2_parse), "oha": (oha_cmd, oha_parse),
         "k6": (k6_cmd, k6_parse), "k6-paced": (k6_paced_cmd, k6_parse), "vegeta": (vegeta_cmd, vegeta_parse)}


# ---- server ---------------------------------------------------------------------------------------------------------

def start_server(image):
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--ulimit", "nofile=100000:100000", "--name", CONTAINER,
                    "-e", f"PORT={PORT}", "-p", f"{PORT}:{PORT}", image], check=True, capture_output=True)
    for _ in range(60):
        try:
            if urllib.request.urlopen(URL, timeout=2).status == 200:
                break
        except OSError:
            pass
        time.sleep(0.5)
    else:
        raise SystemExit(f"{image}: no HTTP 200 within 30 s")
    cid = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", CONTAINER], capture_output=True, text=True,
                         check=True).stdout.strip()
    return f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/cpu.stat"


def cgroup_cpu_s(cpu_stat):
    for line in open(cpu_stat):
        if line.startswith("usage_usec"):
            return int(line.split()[1]) / 1e6


def stop_server():
    subprocess.run(["docker", "stop", CONTAINER], capture_output=True)


# ---- one run ----------------------------------------------------------------------------------------------------------

def one_run(tool, rate, seconds, conns, raw, server_cpu_stat):
    make_cmd, parse = TOOLS[tool]
    cmd, env, stdout_file = make_cmd(rate, seconds, conns, raw)
    stdin = subprocess.PIPE if tool == "vegeta" else subprocess.DEVNULL
    out = open(stdout_file, "w") if stdout_file else subprocess.DEVNULL
    err = open(raw + ".err", "w")
    server0, t0 = cgroup_cpu_s(server_cpu_stat), time.monotonic()
    proc = subprocess.Popen(cmd, env=env, stdin=stdin, stdout=out, stderr=err, text=True)
    if tool == "vegeta":
        proc.stdin.write(f"GET {URL}\n")
        proc.stdin.close()
    _, status, usage = os.wait4(proc.pid, 0)          # rusage of the tool process and all its threads
    wall, server1 = time.monotonic() - t0, cgroup_cpu_s(server_cpu_stat)
    proc.returncode = os.waitstatus_to_exitcode(status)
    err.close()
    stderr = open(raw + ".err").read()
    if out is not subprocess.DEVNULL:
        out.close()
    row = {"tool": tool, "target_rate": rate, "target_seconds": seconds, "connections": conns,
           "exit": proc.returncode, "wall_s": round(wall, 3),
           "tool_cpu_s": round(usage.ru_utime + usage.ru_stime, 3), "server_cpu_s": round(server1 - server0, 3)}
    if proc.returncode != 0:
        row["error"] = stderr.strip()[-500:]
        return row
    row.update(parse(raw))
    row["rate"] = row["ok"] / row["seconds"] if row["seconds"] else 0.0
    row["rate_vs_target_pct"] = 100.0 * (row["rate"] - rate) / rate
    row["tool_cpu_per_1000_req_ms"] = 1e6 * row["tool_cpu_s"] / row["ok"] if row["ok"] else None
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--servers", nargs="+", choices=list(SERVERS), default=["erlang"])
    ap.add_argument("--tools", nargs="+", choices=list(TOOLS), default=list(TOOLS))
    ap.add_argument("--rates", nargs="+", type=int, default=[1000])
    ap.add_argument("--seconds", type=int, default=10)
    ap.add_argument("--connections", type=int, default=100)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--pause", type=float, default=3.0, help="seconds between runs")
    a = ap.parse_args()

    out = os.path.join(ROOT, "results", datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_load-tools")
    os.makedirs(os.path.join(out, "raw"))
    seed = random.randrange(10 ** 9)
    with open(os.path.join(out, "EXPERIMENT.txt"), "w") as fh:
        fh.write("load tools experiment (experiments/load-tools), not a campaign\n"
                 f"{json.dumps(vars(a))}\nshuffle seed {seed}\n")
    rng = random.Random(seed)
    total = len(a.servers) * len(a.rates) * a.repeats * len(a.tools)
    n = 0
    try:
        for server in a.servers:
            say(f"starting {SERVERS[server]}")
            cpu_stat = start_server(SERVERS[server])
            for rate in a.rates:
                for rep in range(1, a.repeats + 1):
                    for tool in rng.sample(a.tools, len(a.tools)):
                        n += 1
                        time.sleep(a.pause)
                        raw = os.path.join(out, "raw", f"{server}_{rate}_{rep}_{tool}")
                        row = dict(server=server, repeat=rep, **one_run(tool, rate, a.seconds, a.connections, raw, cpu_stat))
                        with open(os.path.join(out, "runs.jsonl"), "a") as fh:
                            fh.write(json.dumps(row) + "\n")
                        if row["exit"] != 0:
                            say(f"{n}/{total} {server} {rate}/s #{rep} {tool}: FAILED (exit {row['exit']}) {row.get('error', '')}")
                        else:
                            say(f"{n}/{total} {server} {rate}/s #{rep} {tool:8}: {row['rate']:8.1f} req/s "
                                f"({row['rate_vs_target_pct']:+.1f} %), {row['failed']} failed, "
                                f"p50 {row['p50_ms']:.2f} ms, p99 {row['p99_ms']:.2f} ms, "
                                f"tool CPU {row['tool_cpu_s']:.2f} s, server CPU {row['server_cpu_s']:.2f} s")
            stop_server()
    finally:
        stop_server()
    say(f"results: {os.path.relpath(out, ROOT)}/runs.jsonl")


if __name__ == "__main__":
    main()
