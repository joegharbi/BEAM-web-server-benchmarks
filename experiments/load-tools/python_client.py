#!/usr/bin/env python3
"""Baseline: the framework's own HTTP client (tools/plugins/workload/http.py, 'reuse' mode: requests + a thread
pool of WORKERS threads, 100 as the framework's HTTP_MAX_WORKERS default, one keep-alive connection per worker). It has no rate setting, so it sends
a fixed number of requests as fast as it can and prints one JSON line: requests, failures, seconds, p50/p99 (ms).

    python_client.py URL N_REQUESTS WORKERS
"""
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

local = threading.local()


def one(url):
    session = getattr(local, "session", None)
    if session is None:
        session = local.session = requests.Session()
    start = time.perf_counter()
    try:
        ok = session.get(url, timeout=5).status_code == 200
    except requests.exceptions.RequestException:
        ok = False
    return ok, time.perf_counter() - start


def main():
    url, n, workers = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _: one(url), range(n)))
    seconds = time.perf_counter() - start
    lat = sorted(t for ok, t in results if ok)
    q = statistics.quantiles(lat, n=100) if len(lat) > 1 else [float("nan")] * 99
    print(json.dumps({"requests": n, "ok": len(lat), "failed": n - len(lat), "seconds": seconds,
                      "p50_ms": q[49] * 1000, "p99_ms": q[98] * 1000}))


if __name__ == "__main__":
    main()
