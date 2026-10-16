"""Workload: TCP echo. Every client sends messages over plain TCP and waits for each one to come back.

Three patterns:
  connect  the earlier MIPRO 2024 echo paper: every client connects, sends ONE message, waits for its echo
           and disconnects; the clients start --spacing seconds apart (0.1 = "sleep" in that paper,
           0 = "nosleep": all at once)
  burst    long-lived connections: every client connects once and sends --messages messages back to back
  stream   long-lived connections: every client connects once and sends --rate messages per second for
           --duration seconds
A message is --size_bytes bytes; the default 15 bytes is the paper's "Hello, Servers!".
"""
import asyncio
import logging
import os
import resource
import time

import csv_columns
import load_phases
import results_index
from plugins.workload import Workload

logger = logging.getLogger()

PAPER_MESSAGE = b"Hello, Servers!"


def message(size):
    """The paper's message for 15 bytes, otherwise random bytes of that size."""
    return PAPER_MESSAGE if size == len(PAPER_MESSAGE) else os.urandom(size)


def allow_many_connections():
    """Every client is one open socket: raise this process's open-file limit to the hard limit."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < hard:
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))


async def echo_once(host, port, payload, result):
    """Send one message and wait for exactly that many bytes back; record the latency."""
    start = time.perf_counter()
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(payload)
        await writer.drain()
        got = await reader.readexactly(len(payload))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
    result["total"] += 1
    if got == payload:
        result["success"] += 1
        result["latencies"].append((time.perf_counter() - start) * 1000)
    else:
        result["fail"] += 1


async def connect_client(host, port, size, delay, result):
    """The paper's client: connect, one message, its echo, disconnect, after waiting its turn."""
    await asyncio.sleep(delay)
    try:
        await echo_once(host, port, message(size), result)
    except (OSError, asyncio.IncompleteReadError) as e:
        logger.warning("Echo client error: %s", e)
        result["total"] += 1
        result["fail"] += 1


async def session_client(host, port, size, count, rate, duration, result):
    """A long-lived connection: count messages back to back (burst), or rate per second for duration (stream)."""
    payload = message(size)
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError as e:
        logger.warning("Echo client could not connect: %s", e)
        result["total"] += count or max(1, int(rate * duration))
        result["fail"] += count or max(1, int(rate * duration))
        return
    end = time.time() + duration if duration else None
    sent = 0
    try:
        while (sent < count) if count else (time.time() < end):
            start = time.perf_counter()
            writer.write(payload)
            await writer.drain()
            got = await reader.readexactly(len(payload))
            sent += 1
            result["total"] += 1
            if got == payload:
                result["success"] += 1
                result["latencies"].append((time.perf_counter() - start) * 1000)
            else:
                result["fail"] += 1
            if rate:
                await asyncio.sleep(1.0 / rate)
    except (OSError, asyncio.IncompleteReadError) as e:
        logger.warning("Echo session error: %s", e)
        if count:                                  # count the unsent messages of a burst as failed
            result["total"] += count - sent
            result["fail"] += count - sent
        else:
            result["total"] += 1
            result["fail"] += 1
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def warm_up(host, port, seconds):
    """Unmeasured echo messages over one connection for `seconds`."""
    deadline = time.time() + seconds
    result = {"success": 0, "fail": 0, "total": 0, "latencies": []}
    try:
        await session_client(host, port, len(PAPER_MESSAGE), 0, 1000, max(0.0, deadline - time.time()), result)
    except Exception as e:  # noqa: BLE001 - a warm-up problem shows up in the measurement itself
        logger.warning("Warm-up stopped early: %s", e)


class Plugin(Workload):
    name = "Echo"
    columns = csv_columns.ECHO_COLUMNS
    rate_unit = "msg/s"
    idle_note = "no messages"

    def __init__(self):
        self.clients = []

    def add_arguments(self, parser):
        parser.add_argument('--measurement_type', type=str, default='echo', help="Type of measurement (echo)")
        parser.add_argument('--pattern', choices=['connect', 'burst', 'stream'], required=True,
                            help="connect: one message per connection (the MIPRO 2024 echo paper); burst: messages "
                                 "back to back on one connection; stream: a fixed rate on one connection")
        parser.add_argument('--clients', type=int, default=10, help='Number of clients')
        parser.add_argument('--size_bytes', type=int, default=len(PAPER_MESSAGE),
                            help='Message size in bytes (15 = the paper\'s "Hello, Servers!")')
        parser.add_argument('--spacing', type=float, default=0.0,
                            help='connect: seconds between client starts (0.1 = the paper\'s "sleep", 0 = all at once)')
        parser.add_argument('--messages', type=int, default=100, help='burst: messages per client')
        parser.add_argument('--rate', type=int, default=10, help='stream: messages per second per client')
        parser.add_argument('--duration', type=int, default=5, help='stream: seconds each client sends')
        parser.add_argument('--url', type=str, default=None, help='tcp://host:port of the server (default: the mapped port)')
        parser.add_argument('--warmup_s', type=float, default=load_phases.default_warmup_s(),
                            help="Seconds of unmeasured echo messages after boot, before the measurement (default: 0, or WARMUP_SECONDS of the config)")
        parser.add_argument('--idle_s', type=float, default=load_phases.default_idle_s(),
                            help="Seconds the idle server is measured right before the load (default: 0, or IDLE_SECONDS of the config)")

    def url(self, args):
        return args.url or f"tcp://localhost:{args.port_mapping.split(':')[0]}"

    @staticmethod
    def _host_port(url):
        host, port = url.split("://", 1)[1].rsplit(":", 1)
        return host, int(port)

    def probe(self, url):
        host, port = self._host_port(url)
        result = {"success": 0, "fail": 0, "total": 0, "latencies": []}
        try:
            asyncio.run(asyncio.wait_for(echo_once(host, port, b"ping", result), 2))
        except Exception:  # noqa: BLE001 - not answering yet
            return False
        return result["success"] == 1

    def health_failure(self):
        return "health check failed: no TCP echo from the server within the wait time"

    def warm_up(self, args, url):
        asyncio.run(warm_up(*self._host_port(url), args.warmup_s))

    def describe(self, args, url):
        desc = f"{args.pattern} | clients={args.clients} size={args.size_bytes} B"
        if args.pattern == "connect":
            return desc + f" spacing={args.spacing}s"
        if args.pattern == "burst":
            return desc + f" messages={args.messages}"
        return desc + f" rate={args.rate}/s duration={args.duration}s"

    def run(self, args, url):
        allow_many_connections()
        host, port = self._host_port(url)
        self.clients = [{'success': 0, 'fail': 0, 'total': 0, 'latencies': []} for _ in range(args.clients)]

        async def run_all():
            if args.pattern == "connect":
                tasks = [connect_client(host, port, args.size_bytes, i * args.spacing, self.clients[i])
                         for i in range(args.clients)]
            elif args.pattern == "burst":
                tasks = [session_client(host, port, args.size_bytes, args.messages, 0, 0, self.clients[i])
                         for i in range(args.clients)]
            else:
                tasks = [session_client(host, port, args.size_bytes, 0, args.rate, args.duration, self.clients[i])
                         for i in range(args.clients)]
            await asyncio.gather(*tasks)
        asyncio.run(run_all())

    def progress(self, args):
        return f"echo messages {sum(int(r.get('total', 0)) for r in self.clients)}"

    def counts(self):
        return sum(int(r['success']) for r in self.clients), sum(int(r['total']) for r in self.clients)

    def values(self, args, runtime):
        total = sum(int(r['total']) for r in self.clients)
        lat = [x for r in self.clients for x in r['latencies']]
        return {
            "Test Type": args.measurement_type, "Pattern": args.pattern, "Num Clients": args.clients,
            "Message Size (B)": args.size_bytes,
            "Spacing (s)": args.spacing if args.pattern == 'connect' else '',
            "Messages per Client": args.messages if args.pattern == 'burst' else '',
            "Rate (msg/s)": args.rate if args.pattern == 'stream' else '',
            "Duration (s)": args.duration if args.pattern == 'stream' else '',
            "Total Messages": total, "Successful Messages": sum(int(r['success']) for r in self.clients),
            "Failed Messages": sum(int(r['fail']) for r in self.clients),
            "Messages/s": total / runtime if runtime > 0 else 0.0,
            "Avg Latency (ms)": sum(lat) / len(lat) if lat else 0.0,
            "Min Latency (ms)": min(lat) if lat else 0.0, "Max Latency (ms)": max(lat) if lat else 0.0,
        }

    def index(self, args):
        return results_index.echo_workload(args.measurement_type, args.pattern, args.clients, args.size_bytes,
                                           args.spacing, args.messages, args.rate, args.duration)

    def measurement(self, args):
        return f"{args.measurement_type} {args.pattern} clients={args.clients} size_bytes={args.size_bytes}"

    def repeat_args(self, args):
        return ["--pattern", args.pattern, "--clients", str(args.clients), "--size_bytes", str(args.size_bytes),
                "--spacing", str(args.spacing), "--messages", str(args.messages), "--rate", str(args.rate),
                "--duration", str(args.duration), "--measurement_type", args.measurement_type] + \
               (["--url", args.url] if args.url else [])
