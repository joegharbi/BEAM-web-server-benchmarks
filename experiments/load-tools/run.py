#!/usr/bin/env python3
"""Load tools experiment (README.md): which load tool holds a fixed request rate, at what CPU and energy cost, and
where does each tool and each server break?

For every server and repeat the rates rise step by step (a ramp); at each rate every tool still in the ramp runs
once, in a shuffled order, against GET / of the server (in Docker, or natively without Docker: DEPLOY). Per run
it records:
  - the rate reached (successful responses per second) against the target, and how steady it was second by second
    (bytes arriving at the server, counted every second: the same for every tool, no load on the tool; in Docker
    the container's network, natively the loopback interface)
  - failures, latency p50/p99 as the tool reports it
  - the tool's CPU time (kernel counters of the tool process and all its threads, read when it exits) and the
    server's CPU time (its container cgroup, or natively its systemd scope)
  - energy from Scaphandre over the tool's run: the tool's (by its process ID), the server's (its container) and
    the whole laptop's; in Docker also docker-proxy's CPU time and energy (Docker's relay between localhost and the
    container, which copies every request and answer; kept on the system CPUs). Scaphandre's raw log lists every process on the laptop, so it is deleted once the numbers
    are taken (--keep-raw keeps it, locally only).
Every repeat runs all servers in a new random order. Once per server and repeat, among the first rate's runs, an
"idle" run measures the server and the laptop with no load for the same length, and the Python client runs.
A tool leaves the ramp (for this server and repeat) once it reaches less than 95 % of the target, more than 1 % of
its requests fail, or its per-second rate swings more than 5 % (CV); the ramp ends when no tool is left. With
RATE_GROWTH the ramp has no ceiling: after the listed rates every step multiplies the last one, until the tool breaks.
With LIMIT_PRECISION_PCT the tool then searches between the last rate it held and the first it missed (always the
middle) until the two are that close: its limit. Every missed step gets a verdict from the CPU time of both sides:
the server broke (its CPUs were full), the client broke (the tool's CPUs were full), both, or neither (something in
between, e.g. docker-proxy). With MAX_P99_MS a step is also missed when 1 % of the answers took longer than that
(each tool's own latency; wrk2 and oha correct it for requests sent late, k6 does not).
SHAPE chooses what a "level" is: rate (requests per second on a timetable, RATES), clients (that many clients, each
sends its next request as soon as the last is answered, CLIENTS; oha and k6) or burst (that many requests at once
every BURST_EVERY_SECONDS, BURSTS; oha sends them as fast as its connections allow, k6 within one second).
With LOAD_PERCENTS (shape rate) every tool, after its ramp, also runs at those percents of the highest rate it held
on this server in this repeat: the energy against load curve. The Python
client (the framework's own) has no rate setting: it runs once per server and repeat and sends PYTHON_REQUESTS
requests as fast as it can.

All settings are in load-tools.config (servers, tools, rates, run length, connections, deploy, the CPU split, the
machine); a command-line option overrides the file for one run. Everything printed is also saved to console.log.

    ./srv/bin/python3 experiments/load-tools/run.py --short       (asks for sudo once; 2 rates, 10 s, 1 repeat)
    ./srv/bin/python3 experiments/load-tools/run.py               (as load-tools.config says)
    ./srv/bin/python3 experiments/load-tools/run.py --no-machine --no-energy --short   (no sudo: a code check only)

Machine settings as the paper's HTTP campaigns (load-tools.config: minimal profile, CPU at 1800 MHz), applied and
restored with tools/prepare_environment.py; the readiness gate (tools/readiness.py) before every run.
Results: results/<time>_load-tools/ with runs.jsonl (one line per run), raw/ (the tools' own outputs and the
per-second rates), console.log, tools.txt, bench.config, metadata.json, EXPERIMENT.txt.
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
import native_server  # noqa: E402
import scaphandre_energy  # noqa: E402

PY = sys.executable
BIN = os.path.join(HERE, "bin")
SERVERS = {"erlang-nobw": "st-erlang-pure-29-1-1-nobw", "erlang-bw": "st-erlang-pure-29-1-1",
           "java": "st-java-netty-4-2-19"}
RATES = [1000, 2000, 5000, 10000, 20000, 40000, 80000]
PORT = 8001
URL = f"http://localhost:{PORT}/"
CONTAINER = "lt-server"
WARMUP_MAX_RATE = 5000     # k6 warms the server up; it holds 5,000 req/s on the tool CPUs (about 9,200 at most)
MIN_RATE_SHARE = 0.95      # below this share of the target, the tool leaves the ramp
MAX_FAILED_SHARE = 0.01    # above this share of failed requests, too
BURST_EVERY = 5            # set in main(): seconds from one burst to the next
TOOL_CPUS = None           # set in main(): the CPUs the tool may use (None = all)
SYSTEM_CPUS = None         # set in main(): the CPUs of this script, Scaphandre and docker-proxy (None = all)
# The experiment's own settings in load-tools.config, with their defaults (the other keys there are the framework's
# machine settings, read by tools/bench_config.py). The CPU split fits this laptop (i7-10610U: 4 cores, 2 hardware
# threads each; core n = CPUs n and n+4): core 0 for the system, core 1 for the tool, cores 2 and 3 for the server.
SETTINGS = {"SERVERS": "erlang-nobw erlang-bw java", "TOOLS": "idle python wrk2 oha k6 k6-paced vegeta",
            "RATES": "1000 2000 5000 10000 20000 40000 80000", "SECONDS": "60", "CONNECTIONS": "100",
            "RATE_GROWTH": "0", "MAX_RATE": "1000000", "LIMIT_PRECISION_PCT": "0", "MAX_P99_MS": "0",
            "SHAPE": "rate", "CLIENTS": "1 10 50 100 200 500 1000", "BURSTS": "1000 2000 5000 10000",
            "BURST_EVERY_SECONDS": "5", "LOAD_PERCENTS": "",
            "WARMUP_SECONDS": "20", "PYTHON_REQUESTS": "60000", "DEPLOY": "docker",
            "SYSTEM_CPUS": "0,4", "TOOL_CPUS": "1,5", "SERVER_CPUS": "2,3,6,7"}
MAX_RATE_CV_PCT = 5.0      # and when the per-second rate swings more than this (an overloaded tool can still hold
                           # the average: vegeta at 40k/s on Java, ±19 % per second and 2 s median latency)


def say(text):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {text}", flush=True)


# ---- tools: command for (rate, seconds, connections, raw file prefix) and a parser of what they wrote --------------

def wrk2_cmd(rate, seconds, conns, raw):
    # one thread per CPU the tool may use, as oha, k6 and vegeta do by default (they size themselves to the CPUs)
    threads = min(len(TOOL_CPUS or os.sched_getaffinity(0)), conns)
    return [os.path.join(BIN, "wrk2"), f"-t{threads}", f"-c{conns}", f"-d{seconds}s", f"-R{rate}", "--latency", URL], None, raw + ".txt"


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


def oha_clients_cmd(clients, seconds, conns, raw):
    return [os.path.join(BIN, "oha"), "-z", f"{seconds}s", "-c", str(clients), "--no-tui", "--output-format", "json",
            "-o", raw + ".json", URL], None, None


def oha_burst_cmd(burst, seconds, conns, raw):
    # oha waits BURST_EVERY before its first burst; one more second lets the last burst (at `seconds`) finish
    return [os.path.join(BIN, "oha"), "-z", f"{seconds + 1}s", "-c", str(conns), "--burst-delay", f"{BURST_EVERY}s",
            "--burst-rate", str(burst), "--no-tui", "--output-format", "json", "-o", raw + ".json", URL], None, None


def k6_clients_cmd(clients, seconds, conns, raw):
    cmd, env, out = k6_cmd(0, seconds, conns, raw, "k6_clients.js")
    return cmd, dict(env, CLIENTS=str(clients)), out


def k6_burst_cmd(burst, seconds, conns, raw):
    cmd, env, out = k6_cmd(0, seconds, conns, raw, "k6_burst.js")
    return cmd, dict(env, BURST=str(burst), EVERY=str(BURST_EVERY), SECONDS=str(seconds)), out


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
    return [PY, os.path.join(HERE, "python_client.py"), URL, str(n_requests), str(conns)], None, raw + ".json"


def python_parse(raw):
    d = json.loads(open(raw + ".json").read().strip().splitlines()[-1])
    return {k: d[k] for k in ("ok", "failed", "seconds", "p50_ms", "p99_ms")}


def idle_cmd(_, seconds, conns, raw):
    return ["sleep", str(seconds)], None, None


VERSIONS = {"wrk2": [os.path.join(BIN, "wrk2"), "-v"], "oha": [os.path.join(BIN, "oha"), "--version"],
            "k6": [os.path.join(BIN, "k6"), "version"], "vegeta": [os.path.join(BIN, "vegeta"), "-version"],
            "python": [PY, "-c", "import sys, requests; print('Python', sys.version.split()[0], 'requests', requests.__version__)"]}

# "idle": the server running and no tool for the run's length, to know what the laptop and the server use at rest
TOOLS = {"idle": (idle_cmd, None), "python": (python_cmd, python_parse), "wrk2": (wrk2_cmd, wrk2_parse), "oha": (oha_cmd, oha_parse),
         "k6": (k6_cmd, k6_parse), "k6-paced": (k6_paced_cmd, k6_parse), "vegeta": (vegeta_cmd, vegeta_parse)}
# The other shapes (SHAPE): the level is a number of clients, or the requests of one burst
SHAPES = {"clients": {"oha": (oha_clients_cmd, oha_parse), "k6": (k6_clients_cmd, k6_parse)},
          "burst": {"oha": (oha_burst_cmd, oha_parse), "k6": (k6_burst_cmd, k6_parse)}}
UNIT = {"rate": "/s", "clients": " clients", "burst": " per burst"}


# ---- server ---------------------------------------------------------------------------------------------------------

class Server:
    """The server, in Docker or natively (the image's own program in a systemd user scope, tools/native_server.py),
    on its CPUs; it answers on localhost:PORT either way."""

    def __init__(self, image, deploy, cpus=None):
        self.image, self.deploy, self.cpus = image, deploy, cpus
        if deploy == "docker":
            subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
            pin = ["--cpuset-cpus", ",".join(map(str, sorted(cpus)))] if cpus else []
            subprocess.run(["docker", "run", "-d", "--rm", *pin, "--ulimit", "nofile=100000:100000", "--name",
                            CONTAINER, "-e", f"PORT={PORT}", "-p", f"{PORT}:{PORT}", image], check=True,
                           capture_output=True)
        else:
            mine = os.sched_getaffinity(0)
            if cpus:                       # the server's processes inherit this from the start command
                os.sched_setaffinity(0, cpus)
            try:
                self.unit = native_server.start(image, CONTAINER, str(PORT))
            finally:
                os.sched_setaffinity(0, mine)
        for _ in range(60):
            try:
                if urllib.request.urlopen(URL, timeout=2).status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        else:
            self.stop()
            raise SystemExit(f"{image} ({deploy}): no HTTP 200 within 30 s")
        if deploy == "docker":
            info = json.loads(subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, text=True,
                                             check=True).stdout)[0]
            self.id = info["Id"]
            self.cpu_stat = f"/sys/fs/cgroup/system.slice/docker-{self.id}.scope/cpu.stat"
            self.net_dev, self.iface = f"/proc/{info['State']['Pid']}/net/dev", "eth0"
            self.proxies = [int(p) for p in subprocess.run(["pgrep", "-f", f"docker-proxy .*-host-port {PORT} "],
                                                           capture_output=True, text=True).stdout.split()]
            if SYSTEM_CPUS:                # docker-proxy runs as root: moving it needs sudo
                cpus_txt = ",".join(map(str, sorted(SYSTEM_CPUS)))
                for pid in self.proxies:
                    subprocess.run(["sudo", "-n", "taskset", "-apc", cpus_txt, str(pid)], capture_output=True)
        else:
            self.id = self.unit            # Scaphandre's processes are matched by this name in their cgroup
            self.cpu_stat = native_server.cgroup_dir(self.unit) + "/cpu.stat"
            self.net_dev, self.iface = "/proc/net/dev", "lo"
            self.proxies = []

    def cpu_s(self):
        for line in open(self.cpu_stat):
            if line.startswith("usage_usec"):
                return int(line.split()[1]) / 1e6

    def rx_bytes(self):
        for line in open(self.net_dev):
            name, _, rest = line.partition(":")
            if name.strip() == self.iface:
                return int(rest.split()[0])

    def proxy_cpu_s(self):
        total = 0.0
        for pid in self.proxies:
            try:
                fields = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
                total += (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
            except OSError:
                pass
        return total

    def stop(self):
        if self.deploy == "docker":
            subprocess.run(["docker", "stop", CONTAINER], capture_output=True)
        else:
            native_server.stop(self.unit)


class BytesPerSecond(threading.Thread):
    """Bytes arriving at the server, read every second while a tool runs."""

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
    if len(rates) < 2 or statistics.mean(rates) <= 0:
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
    e = {"server_energy_j": s["energy_j"], "tool_energy_j": t["energy_j"], "host_energy_j": s["host_energy_j"],
         "host_power_w": s["host_avg_power_w"], "meter_samples": s["samples"], "meter_coverage": round(s["coverage"], 3)}
    if server.proxies:
        p = scaphandre_energy.compute_window_energy(log, "docker-proxy", t0, t1, pids=set(server.proxies),
                                                    kinds={pid: "process" for pid in server.proxies},
                                                    zero_is_normal=True)
        e["proxy_energy_j"] = p["energy_j"]
    return e


# ---- one run ----------------------------------------------------------------------------------------------------------

def one_run(tool, target, seconds, conns, raw, server, energy, keep_raw, shape="rate"):
    fixed_rate = tool not in ("python", "idle")
    make_cmd, parse = SHAPES[shape][tool] if fixed_rate and shape != "rate" else TOOLS[tool]
    cmd, env, stdout_file = make_cmd(target, seconds, conns, raw)
    meter = Meter(raw + ".scaphandre.json") if energy else None
    out = open(stdout_file, "w") if stdout_file else subprocess.DEVNULL
    err = open(raw + ".err", "w")
    counter = BytesPerSecond(server)
    counter.start()
    server0, proxy0, t0, m0 = server.cpu_s(), server.proxy_cpu_s(), time.time(), time.monotonic()
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE if tool == "vegeta" else subprocess.DEVNULL,
                            stdout=out, stderr=err, text=True, preexec_fn=pin_tool)
    if tool == "vegeta":
        proc.stdin.write(f"GET {URL}\n")
        proc.stdin.close()
    _, status, usage = os.wait4(proc.pid, 0)          # rusage of the tool process and all its threads
    wall, t1, server1, proxy1 = time.monotonic() - m0, time.time(), server.cpu_s(), server.proxy_cpu_s()
    counter.done.set()
    counter.join()
    proc.returncode = os.waitstatus_to_exitcode(status)
    if meter:
        time.sleep(1.0)        # two more Scaphandre readings, so that its samples cover the run up to its end
        meter.stop()
    err.close()
    if out is not subprocess.DEVNULL:
        out.close()
    row = {"tool": tool, "shape": shape if fixed_rate else None, "level": target if fixed_rate else None,
           "target_rate": target if fixed_rate and shape == "rate" else None, "target_seconds": seconds,
           "connections": (target if shape == "clients" else conns) if fixed_rate else None, "command": " ".join(cmd), "exit": proc.returncode,
           "deploy": server.deploy, "tool_cpus": sorted(TOOL_CPUS) if TOOL_CPUS else "all",
           "server_cpus": sorted(server.cpus) if server.cpus else "all", "wall_s": round(wall, 3),
           "start_epoch": t0, "end_epoch": t1, "tool_cpu_s": round(usage.ru_utime + usage.ru_stime, 3),
           "server_cpu_s": round(server1 - server0, 3)}
    if server.proxies:
        row["proxy_cpu_s"] = round(proxy1 - proxy0, 3)
    if meter:
        row.update(energies(meter.log, server, proc.pid, t0, t1))
        if not keep_raw:
            os.remove(meter.log)
    if proc.returncode != 0:
        row["error"] = open(raw + ".err").read().strip()[-500:]
        return row
    if parse is None:                                  # idle: no requests
        os.remove(raw + ".err")
        return row
    row.update(parse(raw))
    row["rate"] = row["ok"] / row["seconds"] if row["seconds"] else 0.0
    if fixed_rate and shape == "burst":
        row["bursts"] = seconds // BURST_EVERY
        row["expected_requests"] = row["bursts"] * target
    if row["target_rate"]:
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


def pin_tool():
    """In the tool's process before it starts: only the tool's CPUs (its threads and children inherit it)."""
    if TOOL_CPUS:
        os.sched_setaffinity(0, TOOL_CPUS)


def cpu_set(text):
    return None if text in ("none", "all", "") else {int(c) for c in text.split(",")}


def load_config(path):
    """load-tools.config: the experiment's own keys (SETTINGS) and the framework's machine settings (the rest)."""
    exp, rest = dict(SETTINGS), []
    for line in open(path, encoding="utf-8"):
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() in SETTINGS:
            exp[key.strip()] = value.split("#")[0].strip().strip('"')
        else:
            rest.append(line)
    tmp = os.path.join(os.path.dirname(path), ".machine-settings.config")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(rest)
    try:
        cfg, _ = bench_config.load(tmp)
    finally:
        os.remove(tmp)
    return exp, cfg


def save_console(path):
    """Everything this script and the programs it starts print also goes to `path` (as `tee`)."""
    tee = subprocess.Popen(["tee", "-a", path], stdin=subprocess.PIPE)
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(tee.stdin.fileno(), 1)
    os.dup2(tee.stdin.fileno(), 2)
    return tee


def warm_up(rate, seconds, conns, out):
    """Unmeasured load right after the server starts, so that no tool gets a cold server (Java's JIT compiler made
    the first run of a ramp cost the server about 5 times more CPU than the next ones)."""
    if seconds <= 0:
        return
    say(f"   warm-up: k6 at {rate}/s for {seconds} s (not measured)")
    cmd, env, _ = k6_cmd(rate, seconds, conns, os.path.join(out, ".warmup"))
    subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=pin_tool)
    if os.path.exists(os.path.join(out, ".warmup.json")):
        os.remove(os.path.join(out, ".warmup.json"))


def held(row, max_p99_ms=0):
    """Did the tool hold this level? Few failures and, if asked, p99 within max_p99_ms; at a fixed rate also the
    rate reached and its steadiness (clients and bursts have no rate to hold, and bursts are unsteady on purpose)."""
    if row["exit"] != 0 or not row.get("ok"):
        return False
    if row["failed"] > MAX_FAILED_SHARE * (row["ok"] + row["failed"]) or (max_p99_ms and row["p99_ms"] > max_p99_ms):
        return False
    if row.get("expected_requests") and row["ok"] < MIN_RATE_SHARE * row["expected_requests"]:
        return False                                   # bursts not answered in full
    if not row.get("target_rate"):
        return True
    steady = row.get("rate_cv_pct") is None or row["rate_cv_pct"] <= MAX_RATE_CV_PCT
    return row["rate"] >= MIN_RATE_SHARE * row["target_rate"] and steady


FULL = 0.80    # CPUs at least this busy count as full (two hardware threads of one core rarely reach 100 % together)


def busy(row, side):
    """Share of the CPUs a side may use that it kept busy over the run (from its CPU time and the CPU split)."""
    def count(key, before):            # runs before the field existed had the server on 4 CPUs
        value = row.get(key, before)
        return len(value) if isinstance(value, list) else os.cpu_count()
    cpus = count(f"{side}_cpus", [2, 3, 6, 7] if side == "server" else "all")
    return row[f"{side}_cpu_s"] / row["wall_s"] / cpus


def who_broke(server_busy, tool_busy):
    return ("the server" if server_busy >= FULL > tool_busy else "the client" if tool_busy >= FULL > server_busy
            else "both" if server_busy >= FULL and tool_busy >= FULL else "neither: something in between")


class Ramp:
    """One tool's rates on one server: the listed rates, then (growth) each step = the last one x growth up to
    max_rate, until a step is missed; then (precision_pct) the middle between the highest rate held and the lowest
    missed, until they are within precision_pct of each other."""

    def __init__(self, rates, growth, max_rate, precision_pct):
        self.rates, self.growth, self.max_rate, self.precision = rates, growth, max_rate, precision_pct
        self.held, self.missed, self.step = None, None, 0
        self.server_busy, self.tool_busy = 0.0, 0.0     # the fullest each side's CPUs were at any missed step

    def next(self):
        if self.missed is None:
            if self.step < len(self.rates):
                return self.rates[self.step]
            rate = int(self.held * self.growth) if self.growth > 1 and self.held else None
            return rate if rate and rate <= self.max_rate else None
        if not self.precision or self.held is None or self.missed - self.held <= self.precision / 100 * self.held:
            return None
        middle = int(round((self.held + self.missed) / 2, -2))
        return middle if self.held < middle < self.missed else None

    def record(self, rate, ok, server_busy=0.0, tool_busy=0.0):
        if ok:
            self.held = max(self.held or 0, rate)
            self.step += 1
        else:
            self.missed = min(self.missed or rate, rate)
            self.server_busy, self.tool_busy = max(self.server_busy, server_busy), max(self.tool_busy, tool_busy)

    @property
    def who(self):
        """Over all missed steps: right next to the limit a side is not yet full, further above it is."""
        return who_broke(self.server_busy, self.tool_busy)

    def summary(self):
        if self.missed is None:
            return f"held every step up to {self.held}/s (no break found)"
        if self.held is None:
            return f"missed its first step, {self.missed}/s ({self.who} broke)"
        return f"limit between {self.held}/s (held) and {self.missed}/s (missed): {self.who} broke"


def show(n, row):
    where = "" if row["deploy"] == "docker" else f" ({row['deploy']})"
    head = f"{n} {row['server']}{where} #{row['repeat']} {row['tool']:8} " + \
           (f"{row['level']:>6}{UNIT[row['shape']]}" if row.get("level") else "own pace" if row["tool"] == "python" else "") + \
           (f" ({row['load_pct']:g} % of {row['limit_rate']}/s)" if "load_pct" in row else "")
    if row["exit"] != 0:
        return say(f"{head}: FAILED (exit {row['exit']}) {row.get('error', '')}")
    if row["tool"] == "idle":
        en = (f", energy server {row['server_energy_j']:.1f} J laptop {row['host_energy_j']:.0f} J "
              f"({row['host_power_w']:.1f} W)") if "server_energy_j" in row else ""
        return say(f"{head}: no load, CPU server {row['server_cpu_s']:.2f} s{en}")
    vs = f" ({row['rate_vs_target_pct']:+.1f} %)" if "rate_vs_target_pct" in row else ""
    cv = f", steady ±{row['rate_cv_pct']:.1f} %" if row.get("rate_cv_pct") is not None else ""
    en = (f", energy tool {row['tool_energy_j']:.1f} J server {row['server_energy_j']:.1f} J "
          f"laptop {row['host_energy_j']:.0f} J") if "tool_energy_j" in row else ""
    if "proxy_cpu_s" in row:
        en += f", docker-proxy CPU {row['proxy_cpu_s']:.2f} s" + \
              (f" {row['proxy_energy_j']:.1f} J" if "proxy_energy_j" in row else "")
    if "expected_requests" in row:
        vs, cv = f" ({row['ok']} of {row['expected_requests']} requests in {row['bursts']} bursts)", ""
    say(f"{head}: {row['rate']:8.1f} req/s{vs}{cv}, {row['failed']} failed, p50 {row['p50_ms']:.2f} ms, "
        f"p99 {row['p99_ms']:.2f} ms, CPU tool {row['tool_cpu_s']:.2f} s server {row['server_cpu_s']:.2f} s{en}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "load-tools.config"))
    ap.add_argument("--servers", nargs="+", choices=list(SERVERS))
    ap.add_argument("--tools", nargs="+", choices=list(TOOLS))
    ap.add_argument("--rates", nargs="+", type=int, help="the ramp (sorted rising)")
    ap.add_argument("--shape", choices=["rate", "clients", "burst"])
    ap.add_argument("--clients", nargs="+", type=int, help="shape clients: the numbers of clients")
    ap.add_argument("--bursts", nargs="+", type=int, help="shape burst: the requests per burst")
    ap.add_argument("--burst-every-seconds", type=int)
    ap.add_argument("--max-p99-ms", type=float, help="a step is missed when p99 is above this (0 = no latency rule)")
    ap.add_argument("--load-percents", nargs="*", type=float,
                    help="shape rate: after the ramp, also run at these percents of the highest rate held")
    ap.add_argument("--rate-growth", type=float, help="after the listed rates: next = last x this (0 = stop there)")
    ap.add_argument("--max-rate", type=int, help="ceiling of the growing ramp")
    ap.add_argument("--limit-precision-pct", type=float,
                    help="search between the last rate held and the first missed until this close (0 = no search)")
    ap.add_argument("--seconds", type=int)
    ap.add_argument("--connections", type=int)
    ap.add_argument("--repeats", type=int)
    ap.add_argument("--python-requests", type=int)
    ap.add_argument("--warmup-seconds", type=int, help="unmeasured k6 load after each server start")
    ap.add_argument("--deploy", nargs="+", choices=["docker", "native"])
    for side in ("system", "tool", "server"):
        ap.add_argument(f"--{side}-cpus", help=f"CPUs for the {side}, e.g. 1,5, or all")
    ap.add_argument("--short", action="store_true", help="2 rates (1000 5000), 10 s, 1 repeat, 10000 Python requests")
    ap.add_argument("--no-machine", action="store_true", help="no machine settings and no readiness gate (no sudo)")
    ap.add_argument("--no-energy", action="store_true", help="no Scaphandre (no sudo)")
    ap.add_argument("--keep-raw", action="store_true", help="keep Scaphandre's raw logs (they list every process)")
    a = ap.parse_args()
    os.chdir(ROOT)                       # native_server keeps its copies in .cache/native of the repository
    exp, cfg = load_config(a.config)
    pick = {"servers": exp["SERVERS"].split(), "tools": exp["TOOLS"].split(),
            "rates": [int(x) for x in exp["RATES"].split()], "seconds": int(exp["SECONDS"]),
            "rate_growth": float(exp["RATE_GROWTH"]), "max_rate": int(exp["MAX_RATE"]),
            "limit_precision_pct": float(exp["LIMIT_PRECISION_PCT"]), "max_p99_ms": float(exp["MAX_P99_MS"]),
            "shape": exp["SHAPE"], "clients": [int(x) for x in exp["CLIENTS"].split()],
            "bursts": [int(x) for x in exp["BURSTS"].split()], "burst_every_seconds": int(exp["BURST_EVERY_SECONDS"]),
            "load_percents": [float(x) for x in exp["LOAD_PERCENTS"].split()],
            "connections": int(exp["CONNECTIONS"]), "repeats": int(cfg["REPEATS"]),
            "python_requests": int(exp["PYTHON_REQUESTS"]), "warmup_seconds": int(exp["WARMUP_SECONDS"]),
            "deploy": exp["DEPLOY"].split(), "system_cpus": exp["SYSTEM_CPUS"], "tool_cpus": exp["TOOL_CPUS"],
            "server_cpus": exp["SERVER_CPUS"]}
    for key, value in pick.items():      # the file's value unless the command line gives one
        if getattr(a, key) is None:
            setattr(a, key, value)
    if a.short:
        a.rates, a.seconds, a.repeats, a.python_requests = [1000, 5000], 10, 1, 10000
    if a.shape not in ("rate", "clients", "burst"):
        raise SystemExit(f"unknown SHAPE in {a.config}: {a.shape}")
    levels = sorted({"rate": a.rates, "clients": a.clients, "burst": a.bursts}[a.shape])
    a.rates = sorted(a.rates)
    if a.shape != "rate":                # these shapes exist for oha and k6; Python has its own pace
        skipped = [t for t in a.tools if t not in SHAPES[a.shape] and t != "idle"]
        a.tools = [t for t in a.tools if t not in skipped]
        a.load_percents = []
    unknown = [x for x in a.servers if x not in SERVERS] + [x for x in a.tools if x not in TOOLS] + \
              [x for x in a.deploy if x not in ("docker", "native")]
    if unknown:
        raise SystemExit(f"unknown in {a.config}: {', '.join(unknown)}")
    global TOOL_CPUS, SYSTEM_CPUS, BURST_EVERY
    BURST_EVERY = a.burst_every_seconds
    TOOL_CPUS, SYSTEM_CPUS, server_cpus = cpu_set(a.tool_cpus), cpu_set(a.system_cpus), cpu_set(a.server_cpus)
    if SYSTEM_CPUS:                      # this script, and so Scaphandre, the readiness gate and the byte counter
        os.sched_setaffinity(0, SYSTEM_CPUS)

    out = os.path.join(ROOT, "results", datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_load-tools")
    os.makedirs(os.path.join(out, "raw"))
    tee = save_console(os.path.join(out, "console.log"))
    shutil.copy(a.config, os.path.join(out, "bench.config"))
    seed = random.randrange(10 ** 9)
    with open(os.path.join(out, "EXPERIMENT.txt"), "w") as fh:
        fh.write("load tools experiment (experiments/load-tools/README.md), not a campaign\n"
                 f"{json.dumps(vars(a))}\nshuffle seed {seed}\n")
    rng = random.Random(seed)
    once = [t for t in ("idle", "python") if t in a.tools]       # run once per server and repeat, not per rate
    rate_tools = [t for t in a.tools if t not in once]
    most = len(a.servers) * len(a.deploy) * a.repeats * ((len(levels) + len(a.load_percents)) * len(rate_tools) + len(once))
    with open(os.path.join(out, "tools.txt"), "w") as fh:          # exact versions; each run's command is in runs.jsonl
        for tool, cmd in VERSIONS.items():
            if tool in a.tools or (tool == "k6" and "k6-paced" in a.tools):
                v = subprocess.run(cmd, capture_output=True, text=True)
                fh.write(f"{tool}: {(v.stdout or v.stderr).strip().splitlines()[0]}\n")
        pins = re.findall(r"^(OHA|K6|VEGETA|WRK2|LUAJIT)=(\S+)", open(os.path.join(HERE, "install.sh")).read(), re.M)
        fh.write("install.sh pins: " + ", ".join(f"{k}={v}" for k, v in pins) + "\n")
    if a.shape != "rate" and skipped:
        say(f"shape {a.shape}: no {', '.join(skipped)} (only {', '.join(SHAPES[a.shape])} have this shape)")
    count = (f"shape {a.shape}, open ramp (x{a.rate_growth:g} after {levels[-1]}{UNIT[a.shape]}, up to {a.max_rate}): the number of runs depends "
             "on where the tools break" if a.rate_growth > 1 else f"shape {a.shape}, at most {most} runs (fewer once tools leave the ramp)")
    say(f"{count}, {a.seconds} s each -> {os.path.relpath(out, ROOT)}")

    machine = not a.no_machine
    stop_sudo, state = threading.Event(), os.path.join(out, ".environment_state.json")
    if machine or not a.no_energy:
        subprocess.run(["sudo", "-v"], check=True)
        threading.Thread(target=keep_sudo, args=(stop_sudo,), daemon=True).start()
    n, server = 0, None
    # Block sleep and lid-close suspend until the end, as run_benchmarks.sh (this laptop suspends after 2 h idle)
    inhibit = subprocess.Popen(["systemd-inhibit", "--what=sleep:handle-lid-switch", "--mode=block",
                                "--who=load tools experiment", "--why=measuring", "sleep", "infinity"],
                               start_new_session=True) if shutil.which("systemd-inhibit") else None
    if inhibit is None:
        say("WARNING: systemd-inhibit not found; make sure the machine does not go to sleep")
    try:
        if machine:
            subprocess.run(["sudo", PY, "tools/prepare_environment.py", "apply", *env_args(cfg), "--state", state],
                           cwd=ROOT, check=True)
            subprocess.run([PY, "tools/prepare_environment.py", "verify", *env_args(cfg)], cwd=ROOT, check=True)
        # the machine's state is recorded as measured: after the settings are applied, before they are restored
        subprocess.run([PY, "tools/run_metadata.py", "start", out, "--set", "experiment=load tools",
                        "--set", f"shuffle_seed={seed}", "--set", f"machine={'minimal' if machine else 'untouched'}"],
                       cwd=ROOT, check=False, capture_output=True)
        subprocess.run([PY, "tools/run_metadata.py", "images", out, *[SERVERS[s] for s in a.servers]], cwd=ROOT,
                       check=False, capture_output=True)
        if machine:
            say(f"letting the machine settle for {cfg['SETTLE_SECONDS']} s ...")
            time.sleep(int(cfg["SETTLE_SECONDS"]))
            temp_ref, cpu_ref = subprocess.run([PY, "tools/readiness.py", "baseline", "--seconds",
                                                cfg["RESTING_MEASURE_SECONDS"]], cwd=ROOT, capture_output=True,
                                               text=True, check=True).stdout.split()[:2]
            say(f"resting state: {temp_ref} C, CPU {cpu_ref}%")
        boxes = [(name, deploy) for name in a.servers for deploy in a.deploy]
        for rep in range(1, a.repeats + 1):                   # every repeat has all servers, in a new random order,
            for server_name, deploy in rng.sample(boxes, len(boxes)):   # so a slow drift of the laptop is not a server effect
                say(f"{server_name} ({deploy}) repeat {rep}: starting {SERVERS[server_name]}")
                server = Server(SERVERS[server_name], deploy, server_cpus)
                warm_up(min(a.rates[0], WARMUP_MAX_RATE), a.warmup_seconds, a.connections, out)
                def measure(tool, target, label="", **extra):
                    nonlocal n
                    n += 1
                    gate = ready(cfg, out, temp_ref, cpu_ref) if machine else {}
                    raw = os.path.join(out, "raw", f"{server_name}_{deploy}_{rep}_{target or 'own'}_{tool}{label}")
                    row = dict(server=server_name, image=SERVERS[server_name], repeat=rep, **gate, **extra,
                               **one_run(tool, target or a.python_requests, a.seconds, a.connections, raw,
                                         server, not a.no_energy, a.keep_raw, a.shape))
                    if tool not in once:
                        row["held"] = held(row, a.max_p99_ms)
                        if not row["held"]:
                            row["server_cpus_busy"] = round(busy(row, "server"), 3)
                            row["tool_cpus_busy"] = round(busy(row, "tool"), 3)
                            row["who_broke"] = who_broke(row["server_cpus_busy"], row["tool_cpus_busy"])
                    with open(os.path.join(out, "runs.jsonl"), "a") as fh:
                        fh.write(json.dumps(row) + "\n")
                    show(n, row)
                    if tool not in once and not row["held"]:
                        proxy = (f", docker-proxy {row['proxy_cpu_s'] / row['wall_s']:.2f} CPUs"
                                 if "proxy_cpu_s" in row else "")
                        slow = (f" (p99 {row['p99_ms']:.0f} ms, above {a.max_p99_ms:g} ms)"
                                if a.max_p99_ms and row.get("p99_ms", 0) > a.max_p99_ms else "")
                        say(f"   {tool} missed {target}{UNIT[a.shape]}{slow}: server CPUs "
                            f"{row['server_cpus_busy'] * 100:.0f} % busy, tool CPUs {row['tool_cpus_busy'] * 100:.0f} % "
                            f"busy{proxy} -> at this step: {row['who_broke']}")
                    return row

                first = True
                ramps = {t: Ramp(levels, a.rate_growth, a.max_rate, a.limit_precision_pct) for t in rate_tools}
                while True:                                 # one round = every tool's next level, in a shuffled order
                    plan = [(t, ramps[t].next()) for t in rng.sample(rate_tools, len(rate_tools))]
                    plan = [(t, level) for t, level in plan if level]
                    if first:                               # idle and Python run once, among the first round's tools
                        for t in once:
                            plan.insert(rng.randrange(len(plan) + 1), (t, None))
                        first = False
                    if not plan:
                        break
                    for tool, target in plan:
                        row = measure(tool, target, phase="ramp")
                        if tool not in once:
                            ramps[tool].record(target, row["held"], row.get("server_cpus_busy", 0.0),
                                               row.get("tool_cpus_busy", 0.0))
                for tool in rate_tools:
                    say(f"   {server_name} ({deploy}) #{rep} {tool}: {ramps[tool].summary()}".replace("/s", UNIT[a.shape]))
                # The energy against load curve: each tool at percents of the highest rate it held here, shuffled
                curve = [(t, pct) for t in rate_tools if ramps[t].held for pct in a.load_percents]
                for tool, pct in rng.sample(curve, len(curve)):
                    limit = ramps[tool].held
                    measure(tool, max(1, int(round(limit * pct / 100))), f"_curve{pct:g}", phase="curve",
                            load_pct=pct, limit_rate=limit)
                server.stop()
                server = None
        say("all runs done")
    finally:
        if server is not None:
            server.stop()
        subprocess.run([PY, "tools/run_metadata.py", "end", out], cwd=ROOT, check=False, capture_output=True)
        if os.path.exists(state):
            say("restoring machine settings ...")
            subprocess.run(["sudo", PY, "tools/prepare_environment.py", "restore", "--state", state], cwd=ROOT)
        stop_sudo.set()
        if inhibit is not None:
            os.killpg(inhibit.pid, 15)
    say(f"results: {os.path.relpath(out, ROOT)}/runs.jsonl (terminal output: console.log)")
    sys.stdout.flush()
    sys.stderr.flush()
    os.close(1)                          # end of input for tee, so the log is complete before the prompt returns
    os.close(2)
    tee.stdin.close()
    tee.wait()


if __name__ == "__main__":
    main()
