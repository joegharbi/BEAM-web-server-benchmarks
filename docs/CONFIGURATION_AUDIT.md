# Benchmark Configuration Audit

This document explains every major configuration used in the BEAM web server benchmarks, what each one is for, where it is set, and how it fits together. It is written so that a new (green) benchmark engineer can understand and change settings safely.

---

## 1. max_connections (not set — Ranch default)

### What it is

- **Ranch/Cowboy option**: Maximum number of **concurrent TCP connections** the server will accept per listener.
- **Default in Ranch**: **1024** when not set.

### Current configuration

- **We do not set** `max_connections` in any benchmark (static, dynamic, or WebSocket).
- All Cowboy/Ranch-based servers use **Ranch default 1024**.
- If you hit "connection refused" under very high concurrency, you can add `max_connections` to the relevant transport options (e.g. `{max_connections, 100000}` for Erlang; `%{max_connections: 100_000, socket_opts: [port: 80]}` for Elixir Ranch 2.0 map).


## 2. ulimit / nofile (100,000)

### What it is

- **nofile**: Per-process limit on **number of open file descriptors** (includes sockets, files, pipes).
- **ulimit -n 100000**: In the shell, the current process (and its children) can open up to 100,000 FDs.
- **--ulimit nofile=100000:100000** (Docker): Soft and hard limit inside the container, both 100,000.

### Why it matters for benchmarking

- Each TCP connection is a file descriptor. Many concurrent connections ⇒ many FDs.
- If the limit is low (e.g. default 1024), the process hits “too many open files” and cannot accept more connections.
- **100,000** is chosen for high-concurrency load (same order of magnitude as your request counts in full runs).

### Where it is configured

| Location | How | Purpose |
|----------|-----|--------|
| **run_benchmarks.sh** | `ulimit -n 100000` at top | Host process (script + Python tools) can open many FDs (e.g. many client connections). |
| **check_health.sh** | `ulimit -n 100000` at top | Same for health-check session. |
| **measure_docker.py** | `--ulimit nofile=100000:100000` in `docker run` | Containers used for HTTP benchmarks get 100k FDs. |
| **measure_websocket.py** | `--ulimit nofile=100000:100000` in `docker run` | Containers used for WebSocket benchmarks get 100k FDs. |
| **check_health.sh** | `docker run ... --ulimit nofile=100000:100000 ...` | Health-check containers get 100k FDs. |
| **Container start.sh** (some) | `ulimit -n 100000` | Extra safety inside container (e.g. Elixir/Gleam); Docker already sets the limit. |
| **Health check** | `ulimit -n` inside container must be ≥ 100000 | Script fails the container if the effective limit is lower. |

So: **host scripts and all benchmark/health containers are consistently set to 100,000 open files.**  
This supports high concurrency; Cowboy/Ranch listeners use their default connection limit (1024) unless you set max_connections.

### Summary

- **What**: Per-process limit on open file descriptors (sockets, etc.).
- **Why**: Avoid “too many open files” when running many concurrent connections.
- **Recommendation**: Keep 100000 everywhere; only lower if you explicitly want to test under a lower FD limit.

---

## 3. Port configuration

### Host port (8001)

- **Default**: `HOST_PORT=8001` (in `run_benchmarks.sh` and `check_health.sh`).
- **Meaning**: On the host, all benchmark and health-check containers are mapped to **port 8001** (one container at a time).
- **Override**: e.g. `HOST_PORT=8002 make run-super-quick` to use another port.

### Container port (80)

- **EXPOSE 80** in every benchmark Dockerfile.
- Scripts read EXPOSE from the Dockerfile and build the mapping `HOST_PORT:80` (e.g. `8001:80`).
- So: **host 8001 → container 80** for HTTP and WebSocket.

### Summary

- **What**: One host port (8001) for all runs; container port 80.
- **Why**: Simple, consistent; avoids port conflicts as long as only one container runs at a time.
- **Recommendation**: Document in README that “port 8001 must be free during runs” and that `make clean-port PORT=8001` only affects Docker containers, not other apps.

---

## 4. HTTP benchmark: request counts

### What they control

- **Number of HTTP GET requests** sent to the server in each measurement.
- More requests ⇒ longer run, more stable throughput/energy averages, but longer total time.

### Where they are set

**scripts/run_benchmarks.sh:**

| Mode | Variable | Values | Use case |
|------|----------|--------|----------|
| Full | `full_http_requests` | 100, 1000, 5000, 8000, 10000, 15000, 20000, 30000, 40000, 50000, 60000, 70000, 80000 | Full sweep; many points. |
| Quick | `quick_http_requests` | 1000, 5000, 10000 | Shorter run; 3 points. |
| Super-quick | `super_quick_http_requests` | 1000 | Single point; smoke test. |

- **measure_docker.py** receives `--num_requests` from the script; it does not hard-code counts.

### Summary

- **What**: How many requests per run (per container × per request-count).
- **Why**: Full = detailed curve; quick/super-quick = fast validation.
- **Recommendation**: Keep as is; document in README that “super-quick = 1000 requests per container” and “quick = 3 request counts (1000, 5k, 10k).”

---

## 5. WebSocket benchmark: clients, size, bursts, stream

### What they control

- **Burst**: N clients, each sending M bursts of K KB; interval between bursts.
- **Stream**: N clients, each sending at R messages/sec for D seconds; message size K KB.
- **Concurrency sweep**: Vary number of clients (e.g. 100, 1000, 5000).
- **Payload sweep**: Vary message size (e.g. 8, 1024, 65536 KB).

### Where they are set

**scripts/run_benchmarks.sh:**

- **Full**: `full_ws_burst_*`, `full_ws_stream_*` (e.g. clients 5,50,100; sizes 8,1024,65536 KB; etc.).
- **Quick / super-quick**: `quick_ws_burst_*`, `quick_ws_stream_*` (e.g. 5 clients, 8 KB, 1 burst; stream 1 msg/s, 1 s).
- **Concurrency**: `quick_concurrency_sweep_clients`, `concurrency_sweep_clients`.
- **Payload**: `quick_payload_sweep_*`, `payload_sweep_*`.

**tools/measure_websocket.py**: Reads `--clients`, `--size_kb`, `--bursts`, `--interval`, `--rate`, `--duration` from the script; no hard-coded defaults that override the script.

### Summary

- **What**: WebSocket load shape (clients, message size, rate, duration).
- **Why**: Test different concurrency and payload sizes; quick/super-quick reduce combinations.
- **Recommendation**: Document in README or BENCHMARKS_AUDIT that “super-quick WebSocket = 5 clients, 8 KB, 1 burst” and “full = multiple client counts and sizes.”

---

## 6. Health check: timeouts and wait

### Where they are set

**scripts/check_health.sh:**

- `TIMEOUT=30` (s) — overall per-container timeout.
- `STARTUP_WAIT=10` (s) — wait after start before first HTTP/WS check.
- Extra wait for some stacks: e.g. Cowboy/Erlang/Elixir/Phoenix +5 s; Gleam +20 s (compile on first run).
- `HTTP_TIMEOUT=10` (s) — curl timeout for HTTP.

### Summary

- **What**: How long we wait for the container to be “ready” and respond.
- **Why**: BEAM apps (especially Elixir/Gleam) can need a few seconds to boot; Gleam may compile.
- **Recommendation**: Keep; document that “health check waits 10–30 s per container depending on stack.”

---

## 7. Scaphandre and measurement

- **Scaphandre**: Started with `sudo scaphandre json --containers -f <file>`; runs for the duration of the measurement.
- **No TTY**: Script runs without a terminal; sudo must be pre-authenticated or passwordless for scaphandre.
- **Energy 0.00 J**: With very short or light load (e.g. super-quick, 1000 requests), container power can be below resolution or not attributed; 0.00 J is expected in those cases.

---

## 8. Consistency summary and recommendations

| Setting | Intended value | Inconsistency / note |
|---------|----------------|----------------------|
| **max_connections** | Not set (Ranch default 1024) | No benchmark sets it; static, dynamic, and WebSocket Cowboy/Ranch use default. |
| **ulimit / nofile** | 100,000 | Consistent in scripts and Docker. |
| **Port** | Host 8001, container 80 | Consistent; EXPOSE 80 everywhere. |
| **Request counts** | Script-defined (full/quick/super-quick) | Consistent; only the script defines them. |
| **WebSocket params** | Script-defined | Consistent. |

### For a green engineer

1. **max_connections**: Not set; Ranch default is 1024. If you see "connection refused" under high concurrency, add max_connections to the listener transport options.
2. **ulimit 100000**: Do not lower it if you run high-concurrency or full benchmarks.
3. **Changing load (requests, WebSocket params)**: Prefer changing only in **scripts/run_benchmarks.sh** (one place); tools should keep taking args from the script.
4. **Changing port**: Use **HOST_PORT**; ensure nothing else uses that port during the run (clean-port only clears Docker containers).
5. **Documentation**: Keep README and this CONFIGURATION_AUDIT in sync when you add or change a global limit or port.

---

## 9. Where each configuration lives (quick reference)

- **scripts/run_benchmarks.sh**: HOST_PORT, ulimit, HTTP request arrays, WebSocket arrays (burst/stream/concurrency/payload).
- **scripts/check_health.sh**: HOST_PORT, ulimit, TIMEOUT, STARTUP_WAIT, HTTP_TIMEOUT, docker run --ulimit.
- **tools/measure_docker.py**: docker run --ulimit nofile=100000:100000, port from args.
- **tools/measure_websocket.py**: docker run --ulimit nofile=100000:100000, port from args.
- **Benchmark app code**: Port 80 in Cowboy/Ranch listeners; max_connections not set (Ranch default). Other stacks use their own listener config (but still EXPOSE 80).

This file is the single place that ties all of these together for a green computer benchmark engineer.
