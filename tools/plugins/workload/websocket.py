"""Workload: WebSocket echo, as a burst (as fast as possible) or a stream (a fixed rate)."""
import asyncio
import logging
import os
import time

import websockets

import csv_columns
import load_phases
import results_index
from plugins.workload import Workload

logger = logging.getLogger()


async def echo_burst_client(url, size_kb, bursts, interval, results, client_id, verbose=False):
    latencies = []
    completed_bursts = 0
    try:
        async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
            payload = os.urandom(size_kb * 1024)
            for b in range(bursts):
                start = time.perf_counter()
                await ws.send(payload)
                resp = await ws.recv()
                latency = (time.perf_counter() - start) * 1000
                if resp == payload:
                    latencies.append(latency)
                    results['success'] += 1
                else:
                    results['fail'] += 1
                results['total'] += 1
                completed_bursts += 1
                if verbose:
                    logger.info(f"[Client {client_id}] Burst {b+1}/{bursts} latency: {latency:.2f} ms")
                await asyncio.sleep(interval)
    except Exception as e:
        logger.warning(f"[Client {client_id}] WebSocket connection error: {e}")
        # Count only the unfinished bursts as failures to avoid over-counting.
        remaining_bursts = max(0, bursts - completed_bursts)
        results['fail'] += remaining_bursts
        results['total'] += remaining_bursts
    results['latencies'].extend(latencies)


async def echo_stream_client(url, size_kb, rate, duration, results, client_id, verbose=False):
    latencies = []
    try:
        async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
            payload = os.urandom(size_kb * 1024)
            end_time = time.time() + duration
            while time.time() < end_time:
                start = time.perf_counter()
                await ws.send(payload)
                resp = await ws.recv()
                latency = (time.perf_counter() - start) * 1000
                if resp == payload:
                    latencies.append(latency)
                    results['success'] += 1
                else:
                    results['fail'] += 1
                results['total'] += 1
                if verbose:
                    logger.info(f"[Client {client_id}] Stream latency: {latency:.2f} ms")
                await asyncio.sleep(1.0 / rate)
    except Exception as e:
        logger.warning(f"[Client {client_id}] WebSocket stream error: {e}")
        # Surface stream session failures in totals instead of silently dropping them.
        results['fail'] += 1
        results['total'] += 1
    results['latencies'].extend(latencies)


async def warm_up(url, seconds, size_kb=8):
    """Unmeasured echo messages over one connection for `seconds` (8 KB, back to back)."""
    deadline = time.time() + seconds
    payload = os.urandom(size_kb * 1024)
    try:
        async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
            while time.time() < deadline:
                await ws.send(payload)
                await ws.recv()
    except Exception as e:  # noqa: BLE001 - a warm-up problem shows up in the measurement itself
        logger.warning("Warm-up stopped early: %s", e)


async def echo_once(url):
    async with websockets.connect(url, max_size=None, ping_interval=None) as ws:
        payload = os.urandom(64)                               # a small binary message
        await ws.send(payload)
        return await ws.recv() == payload


class Plugin(Workload):
    name = "WebSocket"
    columns = csv_columns.WS_COLUMNS
    rate_unit = "msg/s"
    idle_note = "no messages"

    def __init__(self):
        self.clients = []

    def add_arguments(self, parser):
        parser.add_argument('--measurement_type', type=str, default='websocket', help="Type of measurement (websocket)")
        parser.add_argument('--mode', choices=['echo'], default='echo', help='Benchmark mode: echo (C→S→C)')
        parser.add_argument('--pattern', choices=['burst', 'stream'], required=True,
                            help='Traffic pattern: burst (as fast as possible), stream (controlled rate)')
        parser.add_argument('--clients', type=int, default=1, help='Number of concurrent clients')
        parser.add_argument('--size_kb', type=int, default=64, help='Message size in KB (per message)')
        parser.add_argument('--rate', type=int, default=10, help='Messages per second per client (stream mode only)')
        parser.add_argument('--bursts', type=int, default=10, help='Number of bursts (burst mode only)')
        parser.add_argument('--interval', type=float, default=0.0,
                            help='Seconds to wait between bursts (default: 0 = back-to-back saturation burst, which matches '
                                 '"as fast as possible"; set higher for a paced burst)')
        parser.add_argument('--duration', type=int, default=30, help='Test duration in seconds (stream mode)')
        parser.add_argument('--url', type=str, default='ws://localhost:8001/ws', help='WebSocket server URL')
        parser.add_argument('--warmup_s', type=float, default=load_phases.default_warmup_s(),
                            help="Seconds of unmeasured echo messages after boot, before the measurement (default: 0, or WARMUP_SECONDS of the config)")
        parser.add_argument('--idle_s', type=float, default=load_phases.default_idle_s(),
                            help="Seconds the idle server is measured right before the load (default: 0, or IDLE_SECONDS of the config)")

    def url(self, args):
        return args.url or f"ws://localhost:{args.port_mapping.split(':')[0]}/ws"

    def probe(self, url):
        try:
            return asyncio.run(echo_once(url))
        except Exception:  # noqa: BLE001 - not answering yet
            return False

    def health_failure(self):
        return "health check failed: no WebSocket echo from the container within the wait time"

    def warm_up(self, args, url):
        asyncio.run(warm_up(url, args.warmup_s))

    def describe(self, args, url):
        desc = f"{args.pattern} | clients={args.clients} size_kb={args.size_kb}"
        if args.pattern == "burst":
            return desc + f" bursts={args.bursts} interval={args.interval}s"
        return desc + f" rate={args.rate}/s duration={args.duration}s"

    def run(self, args, url):
        self.clients = [{'success': 0, 'fail': 0, 'total': 0, 'latencies': []} for _ in range(args.clients)]
        tasks = []
        for i in range(args.clients):
            if args.mode == 'echo' and args.pattern == 'burst':
                tasks.append(echo_burst_client(url, args.size_kb, args.bursts, args.interval, self.clients[i], i, args.verbose))
            elif args.mode == 'echo' and args.pattern == 'stream':
                tasks.append(echo_stream_client(url, args.size_kb, args.rate, args.duration, self.clients[i], i, args.verbose))
            else:
                raise ValueError(f"Unsupported mode/pattern: {args.mode}/{args.pattern}")

        async def run_all():
            await asyncio.gather(*tasks)
        asyncio.run(run_all())

    def progress(self, args):
        return f"WebSocket messages {sum(int(r.get('total', 0)) for r in self.clients)}"

    def counts(self):
        return sum(int(r['success']) for r in self.clients), sum(int(r['total']) for r in self.clients)

    def values(self, args, runtime):
        total = sum(int(r['total']) for r in self.clients)
        lat = [x for r in self.clients for x in r['latencies']]
        return {
            "Test Type": args.measurement_type, "Pattern": args.pattern, "Num Clients": args.clients,
            "Message Size (KB)": args.size_kb,
            "Rate (msg/s)": args.rate if args.pattern == 'stream' else '',
            "Bursts": args.bursts if args.pattern == 'burst' else '',
            "Interval (s)": args.interval if args.pattern == 'burst' else '',
            "Duration (s)": args.duration if args.pattern == 'stream' else '',
            "Total Messages": total, "Successful Messages": sum(int(r['success']) for r in self.clients),
            "Failed Messages": sum(int(r['fail']) for r in self.clients),
            "Messages/s": total / runtime if runtime > 0 else 0.0,
            "Throughput (MB/s)": (total * args.size_kb / 1024) / runtime if runtime > 0 else 0.0,
            "Avg Latency (ms)": sum(lat) / len(lat) if lat else 0.0,
            "Min Latency (ms)": min(lat) if lat else 0.0, "Max Latency (ms)": max(lat) if lat else 0.0,
        }

    def index(self, args):
        return results_index.websocket_workload(args.measurement_type, args.pattern, args.clients, args.size_kb,
                                                args.rate, args.bursts, args.interval, args.duration)

    def measurement(self, args):
        return f"{args.measurement_type} {args.pattern} clients={args.clients} size_kb={args.size_kb}"

    def repeat_args(self, args):
        return ["--pattern", args.pattern, "--mode", args.mode, "--clients", str(args.clients),
                "--size_kb", str(args.size_kb), "--rate", str(args.rate), "--bursts", str(args.bursts),
                "--interval", str(args.interval), "--duration", str(args.duration), "--url", args.url,
                "--measurement_type", args.measurement_type]
