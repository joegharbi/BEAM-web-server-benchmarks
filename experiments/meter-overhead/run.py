#!/usr/bin/env python3
"""The meter-overhead experiment (README.md): does running Scaphandre change the laptop's energy, and for which
server? Every echo server (Erlang on and off, Java, C) runs the nosleep test (10,000 clients at once, 5 rounds) with
the meter on and off, in Docker and natively, 5 repeats in a shuffled order: 4 x 2 x 2 x 5 = 80 measurements.

    ./srv/bin/python3 experiments/meter-overhead/run.py            (asks for sudo once)

Same machine settings as the echo campaigns (meter-overhead.config: minimal profile, CPU at 1800 MHz, early port
reuse for Docker), applied and restored with tools/prepare_environment.py; the readiness gate (tools/readiness.py)
before every run; each run is the framework's own measuring core (measure.py). psys_sampler.py records the laptop's
energy counter and Scaphandre's CPU time every 0.1 s during the whole experiment, in both conditions.
Results: results/<time>/ with echo/<server>_meter-<on|off>.csv, meter_overhead.jsonl, samples.txt, metadata.json.
"""
import datetime
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bench_config  # noqa: E402

PY = sys.executable
SERVERS = [("ec-erlang-pure-29-1-1", ""), ("ec-erlang-pure-29-1-1-nobw", "nobw"), ("ec-java-pure-27", ""),
           ("ec-c-pure-14", "")]
TEST = ["--pattern", "connect", "--clients", "10000", "--size_bytes", "15", "--spacing", "0", "--rounds", "5",
        "--measurement_type", "nosleep_10000_15_x5", "--port_mapping", "8001:8001", "--warmup_s", "0", "--idle_s", "0"]
INVALID_RETRIES = 3


def say(text):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {text}", flush=True)


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
    """Readiness gate before a run, as in run_benchmarks.sh; returns the CSV's --waited_s/--ready_check args."""
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
    return ["--waited_s", str(d["waited_s"]), "--ready_check", d["ready_check"]]


def plan(seed, repeats):
    items = [(img, variant, deploy, meter) for img, variant in SERVERS for deploy in ("container", "native")
             for meter in ("scaphandre", "off")]
    return [(p, item) for p in range(1, repeats + 1) for item in random.Random(f"{seed}-{p}").sample(items, len(items))]


def main():
    cfg, _ = bench_config.load(os.path.join(HERE, "meter-overhead.config"))
    out = os.path.join(ROOT, "results", datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    os.makedirs(os.path.join(out, "echo"))
    os.makedirs(os.path.join(out, "raw"))
    shutil.copy(os.path.join(HERE, "meter-overhead.config"), os.path.join(out, "bench.config"))
    with open(os.path.join(out, "EXPERIMENT.txt"), "w") as fh:
        fh.write("meter-overhead experiment (experiments/meter-overhead/README.md), not a campaign\n")
    samples, sidecar = os.path.join(out, "samples.txt"), os.path.join(out, "meter_overhead.jsonl")
    seed = random.randrange(10 ** 9)
    runs = plan(seed, int(cfg["REPEATS"]))
    say(f"{len(runs)} measurements (seed {seed}) -> {out}")

    subprocess.run(["sudo", "-v"], check=True)
    stop_sudo = threading.Event()
    threading.Thread(target=keep_sudo, args=(stop_sudo,), daemon=True).start()
    state = os.path.join(out, ".environment_state.json")
    sampler = None
    env = dict(os.environ, MEASURE_RAW_DIR=os.path.join(out, "raw"), MEASURE_SESSION="1", BENCH_MEASURE_QUIET="1",
               METER_OVERHEAD_SAMPLES=samples, METER_OVERHEAD_SIDECAR=sidecar)
    try:
        subprocess.run([PY, "tools/run_metadata.py", "start", out, "--set", "measure=echo",
                        "--set", "deploy=container native", "--set", "variants=nobw:ERL_FLAGS=+sbwt none +sbwtdcpu none +sbwtdio none",
                        "--set", f"shuffle_seed={seed}", "--set", "machine=minimal",
                        "--set", "experiment=meter-overhead (Scaphandre on vs off)"], cwd=ROOT, check=False)
        subprocess.run([PY, "tools/run_metadata.py", "images", out, *[img for img, _ in SERVERS]], cwd=ROOT, check=False)
        subprocess.run(["sudo", PY, "tools/prepare_environment.py", "apply", *env_args(cfg), "--state", state],
                       cwd=ROOT, check=True)
        subprocess.run([PY, "tools/prepare_environment.py", "verify", *env_args(cfg)], cwd=ROOT, check=True)
        sampler = subprocess.Popen(["sudo", PY, os.path.join(HERE, "psys_sampler.py"), samples])
        say(f"letting the machine settle for {cfg['SETTLE_SECONDS']} s ...")
        time.sleep(int(cfg["SETTLE_SECONDS"]))
        temp_ref, cpu_ref = subprocess.run([PY, "tools/readiness.py", "baseline", "--seconds",
                                            cfg["RESTING_MEASURE_SECONDS"]], cwd=ROOT, capture_output=True,
                                           text=True, check=True).stdout.split()[:2]
        say(f"resting state: {temp_ref} C, CPU {cpu_ref}%")
        failed_in_a_row = 0
        for n, (p, (img, variant, deploy, meter)) in enumerate(runs, 1):
            name = img + ("-native" if deploy == "native" else "")
            mode = "on" if meter == "scaphandre" else "off"
            say(f"{n}/{len(runs)} · repeat {p} · {name} · meter {mode}")
            cmd = [PY, os.path.join(HERE, "measure.py"), "--server_image", img, *TEST, "--meter", meter,
                   "--output_csv", os.path.join(out, "echo", f"{name}_meter-{mode}.csv")]
            if deploy == "native":
                cmd += ["--container_name", name, "--deploy", "native"]
            run_env = dict(env, MEASURE_VARIANT=variant, MEASURE_REPEAT=str(p))
            for attempt in range(INVALID_RETRIES + 1):
                rc = subprocess.run(cmd + ready(cfg, out, temp_ref, cpu_ref), cwd=ROOT, env=run_env).returncode
                if rc != 4:                     # 4 = the conditions during the load broke a rule: measure again
                    break
                say(f"invalid run (conditions during the load); again ({attempt + 1}/{INVALID_RETRIES})")
            if rc != 0:
                failed_in_a_row += 1
                say(f"measurement failed (exit {rc})")
                if failed_in_a_row >= 3:
                    raise SystemExit("3 failures in a row; stopping")
            else:
                failed_in_a_row = 0
        say("all measurements done")
    finally:
        if sampler is not None:
            sampler.terminate()
            sampler.wait(timeout=10)
        if os.path.exists(state):
            say("restoring machine settings ...")
            subprocess.run(["sudo", PY, "tools/prepare_environment.py", "restore", "--state", state], cwd=ROOT)
        subprocess.run([PY, "tools/run_metadata.py", "end", out], cwd=ROOT, check=False)
        stop_sudo.set()
    say(f"results: {out}\nnext: ./srv/bin/python3 experiments/meter-overhead/analyze.py {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
