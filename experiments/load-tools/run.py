#!/usr/bin/env python3
"""Load tools experiment (README.md): which load tool holds a fixed request rate, at what CPU and energy cost, and
where does each tool and each server break?

For every server and repeat the rates rise step by step (a ramp); at each rate every tool still in the ramp runs
once, in a shuffled order, against GET / of the server in Docker. Per run it records:
  - the rate reached (successful responses per second) against the target, and how steady it was second by second
    (bytes arriving at the server container, counted every second: the same for every tool, no load on the tool)
  - failures, latency p50/p99 as the tool reports it
  - the tool's CPU time (kernel counters of the tool process and all its threads, read when it exits) and the
    server's CPU time (its container cgroup)
  - energy from Scaphandre over the tool's run: the tool's (by its process ID), the server's (its container) and
    the whole laptop's. Scaphandre's raw log lists every process on the laptop, so it is deleted once the numbers
    are taken (--keep-raw keeps it, locally only).
A tool leaves the ramp (for this server and repeat) once it reaches less than 95 % of the target or more than 1 % of
its requests fail; the ramp ends when no tool is left. The Python client (the framework's own) has no rate setting:
it runs once per server and repeat and sends --python-requests requests as fast as it can.

    ./srv/bin/python3 experiments/load-tools/run.py --short       (asks for sudo once; 2 rates, 10 s, 1 repeat)
    ./srv/bin/python3 experiments/load-tools/run.py               (full: 60 s per run, REPEATS of load-tools.config)
    ./srv/bin/python3 experiments/load-tools/run.py --no-machine --no-energy --short   (no sudo: a code check only)

Machine settings as the paper's HTTP campaigns (load-tools.config: minimal profile, CPU at 1800 MHz), applied and
restored with tools/prepare_environment.py; the readiness gate (tools/readiness.py) before every run.
Results: results/<time>_load-tools/ with runs.jsonl (one line per run), raw/ (the tools' own outputs and the
per-second rates), metadata.json, EXPERIMENT.txt.
"""
import argparse
import datetime
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bench_config  # noqa: E402
import scaphandre_energy  # noqa: E402

PY = sys.executable
BIN = os.path.join(HERE, "bin")
SERVERS = {"erlang": "st-erlang-pure-29-1-1-nobw", "java": "st-java-netty-4-2-19"}
RATES = [1000, 2000, 5000, 10000, 20000, 40000, 80000]
PORT = 8001
URL = f"http://localhost:{PORT}/"
CONTAINER = "lt-server"
MIN_RATE_SHARE = 0.95      # below this share of the target, the tool leaves the ramp
MAX_FAILED_SHARE = 0.01    # above this share of failed requests, too


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


def python_cmd(n_requests, seconds, conns, raw):
    return [PY, os.path.join(HERE, "python_client.py"), URL, str(n_requests)], None, raw + ".json"


def python_parse(raw):
    d = json.loads(open(raw + ".json").read().strip().splitlines()[-1])
    return {k: d[k] for k in ("ok", "failed", "seconds", "p50_ms", "p99_ms")}


TOOLS = {"python": (python_cmd, python_parse), "wrk2": (wrk2_cmd, wrk2_parse), "oha": (oha_cmd, oha_parse),
         "k6": (k6_cmd, k6_parse), "k6-paced": (k6_paced_cmd, k6_parse), "vegeta": (vegeta_cmd, vegeta_parse)}


# ---- server ---------------------------------------------------------------------------------------------------------

class Server:
    def __init__(self, image):
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
        info = json.loads(subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, text=True,
                                         check=True).stdout)[0]
        self.id = info["Id"]
        self.cpu_stat = f"/sys/fs/cgroup/system.slice/docker-{self.id}.scope/cpu.stat"
        self.net_dev = f"/proc/{info['State']['Pid']}/net/dev"

    def cpu_s(self):
        for line in open(self.cpu_stat):
            if line.startswith("usage_usec"):
                return int(line.split()[1]) / 1e6

    def rx_bytes(self):
        for line in open(self.net_dev):
            name, _, rest = line.partition(":")
            if name.strip() == "eth0":
                return int(rest.split()[0])

    @staticmethod
    def stop():
        subprocess.run(["docker", "stop", CONTAINER], capture_output=True)


class BytesPerSecond(threading.Thread):
    """Bytes arriving at the server container, read every second while a tool runs."""

    def __init__(self, server):
        super().__init__(daemon=True)
        self.server, self.samples, self.done = server, [], threading.Event()

    def run(self):
        nxt = time.monotonic()
        while not self.done.is_set():
            self.samples.append((time.time(), self.server.rx_bytes()))
            nxt += 1.0
            self.done.wait(max(0.0, nxt - time.monotonic()))
        self.samples.append((time.time(), self.server.rx_bytes()))


def steadiness(samples, ok, t0, t1):
    """Per-second request rate from the byte counter: bytes in each whole second inside the run (the first and last
    second left out), divided by the bytes per successful request of the whole run. Returns the series and its
    coefficient of variation (population stdev / mean, %)."""
    total = samples[-1][1] - samples[0][1]
    if not ok or total <= 0:
        return [], None
    per_req = total / ok
    rates = [(b1 - b0) / (s1 - s0) / per_req for (s0, b0), (s1, b1) in zip(samples, samples[1:])
             if s0 >= t0 + 1 and s1 <= t1 - 1]
    if len(rates) < 2:
        return rates, None
    return rates, 100 * statistics.pstdev(rates) / statistics.mean(rates)


# ---- machine and meter (as experiments/meter-overhead/run.py) ---------------------------------------------------------

def keep_sudo(stop):
    while not stop.wait(60):
        subprocess.run(["sudo", "-n", "-v"], capture_output=True)


def env_args(cfg):
    args = ["--governor", cfg["ENV_GOVERNOR"], "--turbo", cfg["ENV_TURBO"], "--cpu-speed", cfg["ENV_CPU_SPEED"] or "unchanged",
            "--power-profile", cfg["ENV_POWER_PROFILE"] or "unchanged", "--pause-timers", cfg["ENV_PAUSE_TIMERS"] or "none",
            "--pause-services", cfg["ENV_PAUSE_SERVICES"], "--keep", cfg["ENV_KEEP_CONTAINERS"],
            "--screen-brightness", cfg["ENV_SCREEN_BRIGHTNESS"], "--keyboard-light", cfg["ENV_KEYBOARD_LIGHT"],
            "--wifi", cfg["ENV_WIFI"], "--bluetooth", cfg["ENV_BLUETOOTH"], "--tcp-tw-reuse", cfg["ENV_TCP_TW_REUSE"]]
    return args + ([] if cfg["ENV_STOP_CONTAINERS"] == "1" else ["--no-stop-containers"])


def ready(cfg, out, temp_ref, cpu_ref):
    """Readiness gate before a run, as in run_benchmarks.sh; returns how long it waited and the check's verdict."""
    result = os.path.join(out, ".ready.json")
    rc = subprocess.run([PY, "tools/readiness.py", "wait", "--result", result, "--temp-reference", temp_ref,
                         "--temp-margin", cfg["READY_TEMP_MARGIN_C"], "--cpu-reference", cpu_ref,
                         "--cpu-margin", cfg["READY_CPU_BUSY_MARGIN_PERCENT"], "--no-throttling", cfg["READY_NO_THROTTLING"],
                         "--cpu-speed", cfg["ENV_CPU_SPEED"] or cfg["READY_CPU_SPEED"],
                         "--check-every", cfg["READY_CHECK_EVERY_SECONDS"], "--consecutive", cfg["READY_CONSECUTIVE_CHECKS"],
                         "--min-wait", cfg["READY_MIN_WAIT_SECONDS"], "--max-wait", cfg["READY_MAX_WAIT_SECONDS"],
                         "--on-timeout", cfg["READY_ON_TIMEOUT"], "--on-battery", cfg["ON_BATTERY"]], cwd=ROOT).returncode
    if rc != 0:
        raise SystemExit("machine not ready; stopping")
    d = json.load(open(result))
    os.remove(result)
    return {"waited_s": d["waited_s"], "ready_check": d["ready_check"]}


class Meter:
    """Scaphandre over one run, started as the framework starts it (tools/plugins/meter/scaphandre.py, step 500 ms)."""

    def __init__(self, log):
        self.log = log
        subprocess.run(["sudo", "pkill", "-9", "scaphandre"], capture_output=True)
        time.sleep(1)
        self.proc = subprocess.Popen(["sudo", shutil.which("scaphandre")] + scaphandre_energy.scaphandre_json_args(log),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        time.sleep(2)
        if self.proc.poll() is not None:
            raise RuntimeError(f"Scaphandre failed to start: {self.proc.stderr.read().strip()}")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            subprocess.run(["sudo", "pkill", "-9", "scaphandre"], capture_output=True)
        time.sleep(1)


def energies(log, server, tool_pid, t0, t1):
    s = scaphandre_energy.compute_window_energy(log, CONTAINER, t0, t1, container_id=server.id, zero_is_normal=True)
    t = scaphandre_energy.compute_window_energy(log, "load-tool", t0, t1, pids={tool_pid},
                                                kinds={tool_pid: "process"}, zero_is_normal=True)
    return {"server_energy_j": s["energy_j"], "tool_energy_j": t["energy_j"], "host_energy_j": s["host_energy_j"],
            "host_power_w": s["host_avg_power_w"], "meter_samples": s["samples"], "meter_coverage": round(s["coverage"], 3)}


# ---- one run ----------------------------------------------------------------------------------------------------------

def one_run(tool, target, seconds, conns, raw, server, energy, keep_raw):
    make_cmd, parse = TOOLS[tool]
    cmd, env, stdout_file = make_cmd(target, seconds, conns, raw)
    meter = Meter(raw + ".scaphandre.json") if energy else None
    out = open(stdout_file, "w") if stdout_file else subprocess.DEVNULL
    err = open(raw + ".err", "w")
    counter = BytesPerSecond(server)
    counter.start()
    server0, t0, m0 = server.cpu_s(), time.time(), time.monotonic()
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE if tool == "vegeta" else subprocess.DEVNULL,
                            stdout=out, stderr=err, text=True)
    if tool == "vegeta":
        proc.stdin.write(f"GET {URL}\n")
        proc.stdin.close()
    _, status, usage = os.wait4(proc.pid, 0)          # rusage of the tool process and all its threads
    wall, t1, server1 = time.monotonic() - m0, time.time(), server.cpu_s()
    counter.done.set()
    counter.join()
    proc.returncode = os.waitstatus_to_exitcode(status)
    if meter:
        meter.stop()
    err.close()
    if out is not subprocess.DEVNULL:
        out.close()
    row = {"tool": tool, "target_rate": target if tool != "python" else None, "target_seconds": seconds,
           "connections": conns if tool != "python" else None, "exit": proc.returncode, "wall_s": round(wall, 3),
           "start_epoch": t0, "end_epoch": t1, "tool_cpu_s": round(usage.ru_utime + usage.ru_stime, 3),
           "server_cpu_s": round(server1 - server0, 3)}
    if meter:
        row.update(energies(meter.log, server, proc.pid, t0, t1))
        if not keep_raw:
            os.remove(meter.log)
    if proc.returncode != 0:
        row["error"] = open(raw + ".err").read().strip()[-500:]
        return row
    row.update(parse(raw))
    row["rate"] = row["ok"] / row["seconds"] if row["seconds"] else 0.0
    if tool != "python":
        row["rate_vs_target_pct"] = 100.0 * (row["rate"] - target) / target
    per_s, cv = steadiness(counter.samples, row["ok"], t0, t1)
    with open(raw + ".persecond.json", "w") as fh:
        json.dump([round(r, 1) for r in per_s], fh)
    row["rate_cv_pct"] = cv
    row["rate_min_s"] = round(min(per_s), 1) if per_s else None
    row["rate_max_s"] = round(max(per_s), 1) if per_s else None
    row["tool_cpu_per_1000_req_ms"] = 1e6 * row["tool_cpu_s"] / row["ok"] if row["ok"] else None
    if "tool_energy_j" in row and row["ok"]:
        row["tool_energy_per_1000_req_j"] = 1000 * row["tool_energy_j"] / row["ok"]
    return row


def warm_up(rate, seconds, conns, out):
    """Unmeasured load right after the server starts, so that no tool gets a cold server (Java's JIT compiler made
    the first run of a ramp cost the server about 5 times more CPU than the next ones)."""
    if seconds <= 0:
        return
    say(f"   warm-up: k6 at {rate}/s for {seconds} s (not measured)")
    cmd, env, _ = k6_cmd(rate, seconds, conns, os.path.join(out, ".warmup"))
    subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(os.path.join(out, ".warmup.json")):
        os.remove(os.path.join(out, ".warmup.json"))


def held(row):
    if row["exit"] != 0 or not row.get("ok"):
        return False
    total = row["ok"] + row["failed"]
    return row["rate"] >= MIN_RATE_SHARE * row["target_rate"] and row["failed"] <= MAX_FAILED_SHARE * total


def show(n, row):
    head = f"{n} {row['server']} #{row['repeat']} {row['tool']:8} " + \
           (f"{row['target_rate']:>6}/s" if row["target_rate"] else "own pace")
    if row["exit"] != 0:
        return say(f"{head}: FAILED (exit {row['exit']}) {row.get('error', '')}")
    vs = f" ({row['rate_vs_target_pct']:+.1f} %)" if "rate_vs_target_pct" in row else ""
    cv = f", steady ±{row['rate_cv_pct']:.1f} %" if row.get("rate_cv_pct") is not None else ""
    en = (f", energy tool {row['tool_energy_j']:.1f} J server {row['server_energy_j']:.1f} J "
          f"laptop {row['host_energy_j']:.0f} J") if "tool_energy_j" in row else ""
    say(f"{head}: {row['rate']:8.1f} req/s{vs}{cv}, {row['failed']} failed, p50 {row['p50_ms']:.2f} ms, "
        f"p99 {row['p99_ms']:.2f} ms, CPU tool {row['tool_cpu_s']:.2f} s server {row['server_cpu_s']:.2f} s{en}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--servers", nargs="+", choices=list(SERVERS), default=list(SERVERS))
    ap.add_argument("--tools", nargs="+", choices=list(TOOLS), default=list(TOOLS))
    ap.add_argument("--rates", nargs="+", type=int, default=RATES, help="the ramp (sorted rising)")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--connections", type=int, default=100)
    ap.add_argument("--repeats", type=int, default=None, help="default: REPEATS of load-tools.config")
    ap.add_argument("--python-requests", type=int, default=60000)
    ap.add_argument("--warmup-seconds", type=int, default=20, help="unmeasured k6 load after each server start")
    ap.add_argument("--short", action="store_true", help="2 rates (1000 5000), 10 s, 1 repeat, 10000 Python requests")
    ap.add_argument("--no-machine", action="store_true", help="no machine settings and no readiness gate (no sudo)")
    ap.add_argument("--no-energy", action="store_true", help="no Scaphandre (no sudo)")
    ap.add_argument("--keep-raw", action="store_true", help="keep Scaphandre's raw logs (they list every process)")
    a = ap.parse_args()
    cfg, _ = bench_config.load(os.path.join(HERE, "load-tools.config"))
    if a.short:
        a.rates, a.seconds, a.repeats, a.python_requests = [1000, 5000], 10, 1, 10000
    a.repeats = a.repeats or int(cfg["REPEATS"])
    a.rates = sorted(a.rates)

    out = os.path.join(ROOT, "results", datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_load-tools")
    os.makedirs(os.path.join(out, "raw"))
    shutil.copy(os.path.join(HERE, "load-tools.config"), os.path.join(out, "bench.config"))
    seed = random.randrange(10 ** 9)
    with open(os.path.join(out, "EXPERIMENT.txt"), "w") as fh:
        fh.write("load tools experiment (experiments/load-tools/README.md), not a campaign\n"
                 f"{json.dumps(vars(a))}\nshuffle seed {seed}\n")
    rng = random.Random(seed)
    rate_tools = [t for t in a.tools if t != "python"]
    most = len(a.servers) * a.repeats * (len(a.rates) * len(rate_tools) + ("python" in a.tools))
    say(f"at most {most} runs (fewer once tools leave the ramp), {a.seconds} s each -> {os.path.relpath(out, ROOT)}")

    machine = not a.no_machine
    stop_sudo, state = threading.Event(), os.path.join(out, ".environment_state.json")
    if machine or not a.no_energy:
        subprocess.run(["sudo", "-v"], check=True)
        threading.Thread(target=keep_sudo, args=(stop_sudo,), daemon=True).start()
    n = 0
    try:
        subprocess.run([PY, "tools/run_metadata.py", "start", out, "--set", "experiment=load tools",
                        "--set", f"shuffle_seed={seed}", "--set", f"machine={'minimal' if machine else 'untouched'}"],
                       cwd=ROOT, check=False, capture_output=True)
        subprocess.run([PY, "tools/run_metadata.py", "images", out, *[SERVERS[s] for s in a.servers]], cwd=ROOT,
                       check=False, capture_output=True)
        if machine:
            subprocess.run(["sudo", PY, "tools/prepare_environment.py", "apply", *env_args(cfg), "--state", state],
                           cwd=ROOT, check=True)
            subprocess.run([PY, "tools/prepare_environment.py", "verify", *env_args(cfg)], cwd=ROOT, check=True)
            say(f"letting the machine settle for {cfg['SETTLE_SECONDS']} s ...")
            time.sleep(int(cfg["SETTLE_SECONDS"]))
            temp_ref, cpu_ref = subprocess.run([PY, "tools/readiness.py", "baseline", "--seconds",
                                                cfg["RESTING_MEASURE_SECONDS"]], cwd=ROOT, capture_output=True,
                                               text=True, check=True).stdout.split()[:2]
            say(f"resting state: {temp_ref} C, CPU {cpu_ref}%")
        for server_name in rng.sample(a.servers, len(a.servers)):
            for rep in range(1, a.repeats + 1):
                say(f"{server_name} repeat {rep}: starting {SERVERS[server_name]}")
                server = Server(SERVERS[server_name])
                warm_up(a.rates[0], a.warmup_seconds, a.connections, out)
                python_left = "python" in a.tools
                left = list(rate_tools)
                for rate in a.rates:
                    plan = [(t, rate) for t in rng.sample(left, len(left))]
                    if python_left:                         # the Python client runs once, among the first rate's tools
                        plan.insert(rng.randrange(len(plan) + 1), ("python", None))
                        python_left = False
                    if not plan:
                        break
                    for tool, target in plan:
                        n += 1
                        gate = ready(cfg, out, temp_ref, cpu_ref) if machine else {}
                        raw = os.path.join(out, "raw", f"{server_name}_{rep}_{target or 'own'}_{tool}")
                        row = dict(server=server_name, image=SERVERS[server_name], repeat=rep, **gate,
                                   **one_run(tool, target or a.python_requests, a.seconds, a.connections, raw,
                                             server, not a.no_energy, a.keep_raw))
                        with open(os.path.join(out, "runs.jsonl"), "a") as fh:
                            fh.write(json.dumps(row) + "\n")
                        show(n, row)
                        if tool != "python" and not held(row):
                            left.remove(tool)
                            say(f"   {tool} leaves the ramp at {rate}/s")
                Server.stop()
        say("all runs done")
    finally:
        Server.stop()
        if os.path.exists(state):
            say("restoring machine settings ...")
            subprocess.run(["sudo", PY, "tools/prepare_environment.py", "restore", "--state", state], cwd=ROOT)
        subprocess.run([PY, "tools/run_metadata.py", "end", out], cwd=ROOT, check=False, capture_output=True)
        stop_sudo.set()
    say(f"results: {os.path.relpath(out, ROOT)}/runs.jsonl")


if __name__ == "__main__":
    main()
