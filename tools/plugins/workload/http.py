"""Workload: HTTP GET requests (static and dynamic pages)."""
import logging
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

import csv_columns
import load_phases
import results_index
from plugins.workload import Workload

logger = logging.getLogger()

# One HTTP session per worker thread, so "reuse" mode keeps one keep-alive
# connection per worker instead of opening a new TCP connection per request.
_thread_local = threading.local()


def _get(url, connection_mode):
    if connection_mode == "reuse":
        session = getattr(_thread_local, "session", None)
        if session is None:
            session = requests.Session()
            _thread_local.session = session
        return session.get(url, timeout=5)
    # "per-request": a fresh connection for every request, closed by the client.
    # This is how the published results were produced; with servers that keep the
    # connection open, the client's closed sockets pile up in TIME_WAIT and can
    # exhaust the ephemeral port range at high request counts.
    return requests.get(url, timeout=5)


def http_max_workers_label(args):
    return "System default" if args.max_workers is None else str(int(args.max_workers))


class Plugin(Workload):
    name = "HTTP"
    columns = csv_columns.HTTP_COLUMNS
    rate_unit = "req/s"
    idle_note = "no requests"

    def __init__(self):
        self.results = Counter()
        self.lock = threading.Lock()

    def add_arguments(self, parser):
        parser.add_argument('--num_requests', type=int, default=500, help="Number of requests to send (default: 500)")
        parser.add_argument('--max_workers', type=int, default=None,
                            help="Max workers for ThreadPoolExecutor (default: None; CSV records System default when unset)")
        parser.add_argument('--measurement_type', type=str, default=None, help="Type of measurement (static, dynamic, etc.)")
        parser.add_argument('--connection', choices=['reuse', 'per-request'], default='reuse',
                            help="HTTP connection handling: 'reuse' keeps one keep-alive connection per worker (default); "
                                 "'per-request' opens a new connection for every request, as in the published results")
        parser.add_argument('--warmup_s', type=float, default=load_phases.default_warmup_s(),
                            help="Seconds of unmeasured requests after boot, before the measurement (default: 0, or WARMUP_SECONDS of the config)")
        parser.add_argument('--idle_s', type=float, default=load_phases.default_idle_s(),
                            help="Seconds the idle server is measured right before the load (default: 0, or IDLE_SECONDS of the config)")

    def url(self, args):
        return "http://localhost:80/" if args.network == "host" else f"http://localhost:{args.port_mapping.split(':')[0]}/"

    def probe(self, url):
        try:
            return requests.get(url, timeout=10).status_code == 200
        except requests.exceptions.RequestException:
            return False

    def health_failure(self):
        return "health check failed: no HTTP 200 from the container within the wait time"

    def warm_up(self, args, url):
        """Unmeasured requests for args.warmup_s seconds, with the same client settings as the load."""
        deadline = time.time() + args.warmup_s

        def worker(_):
            while time.time() < deadline:
                try:
                    _get(url, args.connection)
                except requests.exceptions.RequestException:
                    pass
        workers = args.max_workers or min(32, (os.cpu_count() or 1) + 4)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(worker, range(workers)))

    def describe(self, args, url):
        return f"{args.num_requests} GET → {url}"

    def send_request(self, url, verbose=False, connection_mode="reuse"):
        try:
            response = _get(url, connection_mode)
            if verbose:
                logger.debug(f'{url} "GET / HTTP/1.1" {response.status_code} {len(response.content)}')
            with self.lock:
                self.results['success' if 200 <= response.status_code < 300 else 'failure'] += 1
        except requests.exceptions.RequestException:
            with self.lock:
                self.results['failure'] += 1
        finally:
            with self.lock:
                self.results['total'] += 1

    def run(self, args, url):
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            executor.map(lambda i: self.send_request(url, args.verbose, args.connection), range(args.num_requests))

    def progress(self, args):
        with self.lock:
            return f"HTTP requests {self.results['total']}/{args.num_requests}"

    def counts(self):
        return self.results['success'], self.results['total']

    def values(self, args, runtime):
        total = self.results['total']
        return {
            "Type": args.measurement_type or "unknown", "Total Requests": int(total),
            "HTTP Max Workers": http_max_workers_label(args), "HTTP Connection Mode": args.connection,
            "Successful Requests": int(self.results['success']), "Failed Requests": int(self.results['failure']),
            "Requests/s": float(total / runtime if runtime > 0 else 0),
        }

    def index(self, args):
        return results_index.http_workload(args.measurement_type or "unknown", int(args.num_requests),
                                           http_max_workers_label(args), args.connection)

    def measurement(self, args):
        return f"{args.measurement_type or 'unknown'} {args.num_requests} requests"

    def repeat_args(self, args):
        out = ["--num_requests", str(args.num_requests), "--connection", args.connection]
        if args.max_workers is not None:
            out += ["--max_workers", str(args.max_workers)]
        if args.measurement_type:
            out += ["--measurement_type", args.measurement_type]
        return out

    def summary(self, args, runtime):
        return [f"HTTP max workers: {http_max_workers_label(args)}"]
