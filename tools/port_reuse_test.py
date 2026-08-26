#!/usr/bin/env python3
"""
Standalone A/B test for the connection reuse hypothesis.

This script does not modify measure_docker.py or any other file. It reproduces
the exact HTTP load pattern used by the benchmark client in two modes and
compares the results.

  bare    One requests.get() per request, with no connection pooling.
          This is what tools/measure_docker.py currently does at line 79.

  pooled  A single requests.Session() with a connection pool, so TCP
          connections are reused across requests.

If the connection reuse hypothesis is correct, the bare run will stop at
roughly the size of the local ephemeral port range while the pooled run
completes every request.

Usage
    ./srv/bin/python tools/port_reuse_test.py --url http://localhost:8001/

Start the container first, for example
    docker run -d --rm --name portcheck -p 8001:80 st-erlang-cowboy-28-4-3
"""

import argparse
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

counter_lock = threading.Lock()


def read_port_range():
    try:
        with open("/proc/sys/net/ipv4/ip_local_port_range") as handle:
            low, high = handle.read().split()
        low, high = int(low), int(high)
        return low, high, high - low + 1
    except Exception:
        return None, None, None


def count_time_wait():
    try:
        out = subprocess.run(
            ["ss", "-tan", "state", "time-wait"],
            capture_output=True, text=True, check=False,
        )
        return max(0, len(out.stdout.strip().splitlines()) - 1)
    except Exception:
        return -1


class Sampler(threading.Thread):
    """Samples TIME_WAIT socket count in the background during a run."""

    def __init__(self, interval=1.0):
        super().__init__(daemon=True)
        self.interval = interval
        self.stop_event = threading.Event()
        self.peak = 0

    def run(self):
        while not self.stop_event.is_set():
            value = count_time_wait()
            if value > self.peak:
                self.peak = value
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()
        self.join(timeout=3)
        return self.peak


def run_load(url, num_requests, workers, pooled, verbose=False):
    results = {"success": 0, "failure": 0, "total": 0}
    first_failure_at = [None]

    if pooled:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=workers,
            pool_maxsize=workers,
            max_retries=0,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        getter = session.get
    else:
        getter = requests.get

    def send(index):
        try:
            response = getter(url, timeout=5)
            ok = 200 <= response.status_code < 300
        except requests.exceptions.RequestException:
            ok = False

        with counter_lock:
            results["total"] += 1
            if ok:
                results["success"] += 1
            else:
                results["failure"] += 1
                if first_failure_at[0] is None:
                    first_failure_at[0] = results["total"]

    sampler = Sampler()
    sampler.start()
    start = time.time()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        executor.map(send, range(num_requests))

    elapsed = time.time() - start
    peak_time_wait = sampler.stop()

    if pooled:
        session.close()

    return {
        "success": results["success"],
        "failure": results["failure"],
        "elapsed": elapsed,
        "peak_time_wait": peak_time_wait,
        "first_failure_at": first_failure_at[0],
    }


def describe(label, data, num_requests):
    rate = data["success"] / data["elapsed"] if data["elapsed"] > 0 else 0
    print(f"  {label}")
    print(f"    successful       {data['success']} of {num_requests}")
    print(f"    failed           {data['failure']}")
    print(f"    elapsed          {data['elapsed']:.1f} s")
    print(f"    rate             {rate:.0f} successful per second")
    print(f"    peak TIME_WAIT   {data['peak_time_wait']}")
    if data["first_failure_at"] is not None:
        print(f"    first failure    after {data['first_failure_at']} completed")
    else:
        print(f"    first failure    none")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Compare pooled and non pooled HTTP clients against one server."
    )
    parser.add_argument("--url", default="http://localhost:8001/",
                        help="Target URL (default http://localhost:8001/)")
    parser.add_argument("--requests", type=int, default=80000,
                        help="Requests per run (default 80000)")
    parser.add_argument("--workers", type=int, default=100,
                        help="Client worker threads (default 100)")
    parser.add_argument("--cooldown", type=int, default=90,
                        help="Seconds between runs so TIME_WAIT drains (default 90)")
    parser.add_argument("--mode", choices=["bare", "pooled", "both"], default="both",
                        help="Which run to perform (default both)")
    args = parser.parse_args()

    low, high, span = read_port_range()

    print()
    print("Connection reuse A/B test")
    print("=" * 60)
    print(f"  target           {args.url}")
    print(f"  requests per run {args.requests}")
    print(f"  worker threads   {args.workers}")
    if span:
        print(f"  port range       {low} to {high}")
        print(f"  ports available  {span}")
        print(f"  twice that       {span * 2}")
    print(f"  TIME_WAIT now    {count_time_wait()}")
    print("=" * 60)
    print()

    try:
        probe = requests.get(args.url, timeout=5)
        print(f"Reachability check returned {probe.status_code}.")
        print()
    except requests.exceptions.RequestException as error:
        print(f"Cannot reach {args.url}. Start the container first.")
        print(f"Error was {error}")
        return 1

    outcome = {}

    if args.mode in ("bare", "both"):
        print("Run 1 of 2. Bare client, a new connection for every request.")
        print("This mirrors measure_docker.py as it stands today.")
        print()
        outcome["bare"] = run_load(args.url, args.requests, args.workers, pooled=False)
        describe("bare", outcome["bare"], args.requests)

        if args.mode == "both":
            print(f"Cooling down for {args.cooldown} s so TIME_WAIT sockets expire.")
            for remaining in range(args.cooldown, 0, -10):
                print(f"  {remaining} s left, TIME_WAIT at {count_time_wait()}")
                time.sleep(min(10, remaining))
            print()

    if args.mode in ("pooled", "both"):
        print("Run 2 of 2. Pooled client, connections reused across requests.")
        print()
        outcome["pooled"] = run_load(args.url, args.requests, args.workers, pooled=True)
        describe("pooled", outcome["pooled"], args.requests)

    if "bare" in outcome and "pooled" in outcome:
        print("=" * 60)
        print("Result")
        print("=" * 60)
        bare = outcome["bare"]
        pooled = outcome["pooled"]
        print(f"  bare     {bare['success']} of {args.requests} successful")
        print(f"  pooled   {pooled['success']} of {args.requests} successful")
        print()

        if span and abs(bare["success"] - span) < span * 0.05:
            print(f"  The bare run stopped within five percent of {span},")
            print(f"  which is the number of ephemeral ports on this machine.")
        elif span and abs(bare["success"] - span * 2) < span * 0.05:
            print(f"  The bare run stopped within five percent of {span * 2},")
            print(f"  which is twice the ephemeral port range.")

        if pooled["success"] > bare["success"] * 1.2:
            gained = pooled["success"] - bare["success"]
            print(f"  Connection reuse recovered {gained} requests.")
            print(f"  The server was not the limiting factor.")
        elif pooled["success"] <= bare["success"]:
            print(f"  Pooling did not help here. The limit is somewhere else,")
            print(f"  and the connection reuse explanation does not hold for this server.")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
