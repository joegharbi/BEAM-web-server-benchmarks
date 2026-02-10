# BEAM Web Server Energy and Performance Benchmarking Framework

## Overview
A **BEAM-only** benchmarking framework for evaluating the performance and energy efficiency of web servers on the BEAM VM (Erlang, Elixir, Gleam). It compares **languages** (pure Erlang, pure Elixir, pure Gleam) and **frameworks** (Cowboy, Yaws, Phoenix, Mist/Wisp) using Docker containers. Features automatic container discovery, intelligent health checks, simplified port management, and extensive automation.

## Key Features
- **🔄 Auto-Discovery**: Automatically finds and benchmarks all containers from directory structure
- **🏥 Intelligent Health Checks**: Comprehensive health validation before benchmarking
- **🔧 Simplified Port Management**: Fixed host port with automatic container port detection
- **📊 Multi-Modal Testing**: Static, dynamic, and WebSocket server benchmarks (containers only)
- **⚡ Energy Measurement**: Integrated Scaphandre for power consumption analysis
- **📈 Visualization**: Interactive GUI for result analysis and graph generation
- **🧹 Repository Management**: Powerful cleaning and maintenance tools

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Directory Structure](#directory-structure)
4. [Naming Convention and Auto-Discovery](#naming-convention-and-auto-discovery)
5. [Benchmarks Audit](#benchmarks-audit)
6. [Health Check System](#health-check-system)
7. [Port Management](#port-management)
8. [Adding New Servers](#adding-new-servers)
9. [Running Benchmarks](#running-benchmarks)
10. [WebSocket Testing](#websocket-testing)
11. [Results and Visualization](#results-and-visualization)
12. [Repository Management](#repository-management)
13. [Makefile Commands](#makefile-commands)
14. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- **OS**: Linux (Debian-based recommended)
- **Python 3**: 3.8+ with pip (required for benchmark scripts). To create the project venv (`make setup`), you need the **python3-venv** package (e.g. `sudo apt install python3-venv` on Debian/Ubuntu).
- **Docker**: installed and running (e.g. `sudo apt install docker.io`)
- **Scaphandre**: `cargo install scaphandre` (for energy measurement)
- **Make**: Usually pre-installed

Verify installations:
```bash
python3 --version
docker --version
scaphandre --version
make --version
```

**System requirements:**
- All containers are started with `--ulimit nofile=100000:100000` for high concurrency. The health check enforces this.
- Scripts `run_benchmarks.sh` and `check_health.sh` set `ulimit -n 100000` at the start.
- If your system or Docker daemon restricts file descriptor limits, increase them (see [Troubleshooting](#ulimit-and-file-descriptor-limits)).
- For a detailed explanation of all benchmark settings (max_connections, ulimit, ports, request counts, WebSocket params), see [docs/CONFIGURATION_AUDIT.md](docs/CONFIGURATION_AUDIT.md).

---

## Quick Start

### 1. Environment Setup

```bash
# Create Python virtual environment and install dependencies
make setup

# Build all BEAM Docker images (Erlang, Elixir, Phoenix, Gleam, Cowboy, Yaws, etc.)
make build
```

### 2. Build and Validate
```bash
# Build all Docker images (if not already done)
make build

# Run comprehensive health checks (includes ulimit check)
make check-health
```

### 3. Run Benchmarks
```bash
# Quick test (3 request counts: 1000, 5000, 10000 per container)
make run-quick

# Super-quick test (1 request count: 1000 per container)
make run-super-quick

# Full benchmark suite (static, dynamic, websocket)
make run-all

# Specific benchmark types
make run-static
make run-dynamic
make run-websocket
```

### 4. Analyze Results
```bash
# Generate interactive graphs (recommended)
make graph

# Or run the GUI directly from repo root
python3 tools/gui_graph_generator.py
```

---

## Directory Structure
```
BEAM-web-server-benchmarks/
├── benchmarks/         # Docker containers only (Type → Language → Framework → container)
│   ├── static/        # Static HTTP
│   │   ├── erlang/    # cowboy/, yaws/, index/, pure/
│   │   ├── elixir/    # cowboy/, phoenix/, pure/
│   │   └── gleam/     # mist/
│   ├── dynamic/       # Dynamic HTTP (same layout)
│   │   ├── erlang/
│   │   ├── elixir/
│   │   └── gleam/
│   └── websocket/     # WebSocket echo servers
│       ├── erlang/    # cowboy/, yaws/
│       └── elixir/    # cowboy/, phoenix/
├── scripts/           # All shell scripts
│   ├── check_health.sh
│   ├── run_benchmarks.sh
│   └── install_benchmarks.sh
├── tools/              # All Python tools (measurement, visualization)
│   ├── gui_graph_generator.py
│   ├── measure_docker.py      # HTTP benchmark measurement
│   └── measure_websocket.py   # WebSocket benchmark measurement
├── results/
├── logs/
├── Makefile
└── README.md
```

---

## Naming Convention and Auto-Discovery

### Folder layout (where things live)

- **benchmarks/** — Only Docker benchmark definitions. No scripts or tools.
- **scripts/** — All shell scripts: `check_health.sh`, `run_benchmarks.sh`, `install_benchmarks.sh`.
- **tools/** — All Python tools: `measure_docker.py`, `measure_websocket.py`, `gui_graph_generator.py`.

### Benchmark path: Type → Language → Framework (or variant) → container

Each benchmark container lives under a path that encodes:

1. **Type** — `static`, `dynamic`, or `websocket`
2. **Language** — `erlang`, `elixir`, or `gleam`
3. **Framework or variant** — `cowboy`, `yaws`, `phoenix`, `mist`, `index`, or `pure`
4. **Container folder** — the directory that contains the `Dockerfile` (and is the image name)

**Erlang static/dynamic variants (not frameworks):**

- **`erlang/pure`** — Pure Erlang serving the same HTML **from within the application** (content in code). Image names: `st-erlang23-self`, `st-erlang26-self`, `st-erlang27-self`, etc.
- **`erlang/index`** — Erlang serving an **index HTML file** (same content, but served from an index file rather than inline). Image names: `st-erlindex23-self`, `st-erlindex26-self`, `st-erlindex27-self`, etc. This is not a framework; it distinguishes “serve from index file” from “serve from code” (pure).

Examples:

- `benchmarks/static/erlang/cowboy/st-cowboy-27-self/` → static, Erlang, Cowboy
- `benchmarks/static/erlang/pure/st-erlang27-self/` → static, pure Erlang (HTML in code)
- `benchmarks/static/erlang/index/st-erlindex27-self/` → static, Erlang serving index HTML file
- `benchmarks/dynamic/elixir/phoenix/dy-phoenix-1-8-self/` → dynamic, Elixir, Phoenix
- `benchmarks/websocket/elixir/cowboy/ws-elixir-cowboy-1-16-self/` → WebSocket, Elixir, Cowboy

### Container (image) naming

The **directory name** of the leaf folder (the one with the `Dockerfile`) is the **Docker image name**. Convention:

- **Static**: `st-<name>-self` (e.g. `st-erlang27-self`, `st-phoenix-1-8-self`)
- **Dynamic**: `dy-<name>-self` (e.g. `dy-erlang27-self`, `dy-gleam-1-0-self`)
- **WebSocket**: `ws-<name>-self` (e.g. `ws-cowboy-27-self`, `ws-phoenix-1-8-self`)

The `-self` suffix means the runtime is built inside the container image. The prefix (`st-`, `dy-`, `ws-`) is used by the scripts to know the benchmark type when resolving paths (e.g. for port mapping).

### How auto-discovery works

1. **Scan** — Scripts recursively scan `benchmarks/static/`, `benchmarks/dynamic/`, and `benchmarks/websocket/`.
2. **Detect** — Any directory that contains a `Dockerfile` is treated as one benchmark container.
3. **Image name** — The **name of that directory** (e.g. `st-erlang27-self`) is the Docker image name. The rest of the path (type/language/framework) is only for organization.
4. **Port** — The container port is read from the `EXPOSE` line in that directory’s `Dockerfile` (default 80).
5. **Lookup** — When a script needs the path for an image (e.g. to read `EXPOSE`), it searches under `benchmarks/` for a directory whose name is the image name and that contains a `Dockerfile`, and uses that path.

So: you can add a new benchmark by creating a new folder (with a `Dockerfile`) anywhere under `benchmarks/<type>/<language>/<framework>/`. The framework will find it; the folder name must match the image name you use to build/run.

### Autodiscovery implementation

- **Build** (`scripts/install_benchmarks.sh`): Finds all such directories and runs `docker build -t <dirname> <dir>`.
- **Health** (`scripts/check_health.sh`): Finds all container dirs, filters to built images, and tests each.
- **Run** (`scripts/run_benchmarks.sh`): Finds containers by type (static/dynamic/websocket) and runs the right measurement tool (`tools/measure_docker.py` or `tools/measure_websocket.py`).
- **Cleanup**: Removes containers and images for all discovered image names.

---

## Benchmarks Audit

A full list of current containers, naming checks, and a **small-test procedure** (build → health → run-super-quick) is in [docs/BENCHMARKS_AUDIT.md](docs/BENCHMARKS_AUDIT.md). Summary:

- **Static (13)**: Erlang (cowboy, index, pure, yaws), Elixir (cowboy, phoenix, pure), Gleam (mist). All use `st-*` and EXPOSE 80.
- **Dynamic (13)**: Same layout with `dy-*`. All consistent.
- **WebSocket (4)**: Erlang (cowboy, yaws), Elixir (cowboy, phoenix). Gleam WebSocket is not present (can be added later). All use `ws-*` and path `/ws`.

Run a small test before a full run: `make build` → `make check-health` → `make run-super-quick`.

---

## Payload Size Support

All BEAM web servers in this framework are configured and tested to support large payloads:

| Server Type / Container    | Payload / Limit        | Health Check Test            |
|----------------------------|------------------------|-------------------------------|
| **Erlang / Cowboy / Yaws** | Unlimited (default)    | 10MB HTTP POST / 1MB WebSocket |
| **Elixir / Phoenix**       | Unlimited (default)    | 10MB HTTP POST / 1MB WebSocket |
| **Gleam (Mist)**           | Unlimited (default)    | 10MB HTTP POST                 |
| **WebSocket (Cowboy, Phoenix, Yaws)** | 64MB frame size | 1MB WebSocket message echo     |

- All HTTP servers accept at least 100MB payloads where applicable.
- All WebSocket servers are tested with at least 1MB messages (echo).
- The health check will fail if a server cannot handle these payloads.

---

## Health Check System

The framework includes a comprehensive health check system that validates containers before benchmarking:

### Health Check Features
- **Container Startup**: Verifies containers start successfully
- **HTTP Response**: Tests for proper HTTP 200 responses (HTTP containers only)
- **WebSocket Handshake**: Only a successful WebSocket handshake (`101 Switching Protocols`) is accepted as healthy for WebSocket containers. HTTP 200 OK is **not** accepted for WebSocket containers.
- **Large Payload Test**: For HTTP containers, a 10MB POST is sent and must be accepted (200/201/204/413). For WebSocket containers, a 1MB binary message is sent and must be echoed back correctly.
- **Stability Testing**: Ensures containers remain running
- **ulimit Enforcement**: Checks that `ulimit -n` is set to 100000 inside each container (required for high concurrency)
- **Automatic Cleanup**: Stops and removes test containers
- **ulimit Reporting**: The health check prints the actual `ulimit -n` value inside each container. A container is only reported as healthy if this value is correct.

### Running Health Checks
```bash
# Using Makefile (recommended)
make check-health
make health
make check

# Using script directly (from repo root)
./scripts/check_health.sh

# Custom port
HOST_PORT=9001 make check-health
HOST_PORT=9001 ./scripts/check_health.sh

# Custom timeouts
./scripts/check_health.sh --timeout 60 --startup 15
```

### Health Check Output
```
[INFO] Starting health check for all built containers...
[INFO] Using fixed host port: 8001
[INFO] Found 28 containers to test

[INFO] Testing st-cowboy-27-self...
[SUCCESS] st-cowboy-27-self: Healthy (ready for benchmarking)

[INFO] Testing dy-erlang27-self...
[SUCCESS] dy-erlang27-self: Healthy (ready for benchmarking)

[INFO] === HEALTH CHECK SUMMARY ===
[INFO] Total containers tested: 28
[SUCCESS] Healthy containers: 28
[SUCCESS] All containers are healthy! 🎉
```

## Why the Health Check Verifies ulimit and Large Payload Support

In this benchmarking framework, the health check does more than just verify that a container is up and running. It also:

- **Checks the open file descriptor limit (`ulimit -n`)** inside each container to ensure it is set high enough (e.g., 100,000) for high-concurrency benchmarks.
- **Tests large payload support** by sending a 10MB HTTP POST (for HTTP servers) or a 1MB message (for WebSocket servers) to verify the server is configured to handle large requests.

**Why is this important?**

- A container that is merely "up" may still be misconfigured for benchmarking (e.g., low ulimit, small payload limits).
- Running benchmarks on such containers can lead to failed tests, misleading results, or wasted time.
- By verifying these settings in the health check, you ensure that all servers are truly ready for high-load, high-concurrency, and large-payload benchmarks.

**Best Practice:**
- This approach is recommended for any benchmarking or performance testing framework, as it catches subtle misconfigurations early and guarantees the validity of your results.

---

## Port Management

The framework uses a simplified port management system:

### Fixed Host Port
- **Default**: All containers use host port `8001`
- **Configurable**: Set `HOST_PORT` environment variable
- **Container Port**: Automatically detected from Dockerfile `EXPOSE` directive

### Port Configuration
```bash
# Default behavior (from repo root)
./scripts/check_health.sh     # Uses port 8001
./scripts/run_benchmarks.sh   # Uses port 8001

# Custom port
HOST_PORT=9001 ./scripts/check_health.sh
HOST_PORT=9001 ./scripts/run_benchmarks.sh

# Session-wide setting
export HOST_PORT=9001
./scripts/check_health.sh
./scripts/run_benchmarks.sh
```

### Port Mapping Examples
```dockerfile
# Dockerfile with EXPOSE directive
EXPOSE 80
# Results in: 8001:80 mapping

EXPOSE 8080
# Results in: 8001:8080 mapping
```

---

## Adding New Servers

### Docker Containers
1. **Create Directory**: Any name works with autodiscovery
   ```bash
   mkdir -p benchmarks/static/erlang/cowboy/my-server
   mkdir -p benchmarks/dynamic/elixir/phoenix/my-server
   mkdir -p benchmarks/websocket/elixir/cowboy/my-ws
   ```

2. **Add Dockerfile**: Include `EXPOSE` directive and ensure your server supports high file descriptor limits (ulimit 100000 is enforced by the health check).
   ```dockerfile
   FROM erlang:27-alpine
   # ... your app ...
   EXPOSE 80
   CMD ["your-entrypoint"]
   # Entrypoint/CMD must not lower the ulimit
   ```

3. **Auto-Discovery**: Container will be automatically found, built, tested, and benchmarked
   ```bash
   make build        # Automatically builds your new container
   make check-health # Automatically tests your new container (including ulimit)
   make run-all      # Automatically benchmarks your new container
   ```

### Local Servers
This repository is **BEAM-only** and uses only Docker containers. Local server benchmarks (e.g. `local/`, `run-local`) are not used; the corresponding Makefile targets are no-ops.

---

## Running Benchmarks

### Execution flow
- **Makefile** invokes **scripts** (`scripts/run_benchmarks.sh`, `scripts/check_health.sh`, `scripts/install_benchmarks.sh`). Commands like `make run-super-quick` or `make check-health` run from the repository root.
- **Scripts** invoke **tools** (Python): `tools/measure_docker.py` for HTTP (static/dynamic) and `tools/measure_websocket.py` for WebSocket. Scripts use the project venv (`srv/bin/python3`) when present, else `python3`.
- Each measurement run starts one container, runs Scaphandre and the load test, then stops Scaphandre and removes the container before the next run. Health check does the same (start → check → stop/remove) per container.

### Using Makefile (Recommended)
```bash
# Complete benchmark suite
make run-all

# Specific benchmark types
make run-static      # Static containers only
make run-dynamic     # Dynamic containers only
make run-websocket   # WebSocket containers only
make run-local       # Local servers only

# Quick testing
make run-quick       # Quick test (3 request counts: 1000, 5000, 10000 per container)
make run-super-quick # Super-quick test (1 request count: 1000 per container)
```

### Using Scripts Directly
```bash
# All benchmarks (from repo root)
./scripts/run_benchmarks.sh

# Specific types
./scripts/run_benchmarks.sh static
./scripts/run_benchmarks.sh dynamic
./scripts/run_benchmarks.sh websocket

# Quick mode
./scripts/run_benchmarks.sh --quick static
```

### Benchmark Parameters

#### HTTP Benchmarks (Static/Dynamic/Local)
- **Full test**: 13 request counts (100, 1000, 5000, 8000, 10000, 15000, 20000, 30000, 40000, 50000, 60000, 70000, 80000)
- **Quick test**: 3 request counts (1000, 5000, 10000)
- **Super quick test**: 1 request count (1000) - fastest validation

#### WebSocket Benchmarks
- **Full test**: Comprehensive parameter combinations
  - Burst mode: 4 client counts × 7 message sizes × 2 burst counts × 3 intervals
  - Stream mode: 4 client counts × 7 message sizes × 3 rates × 3 durations
- **Quick test**: Moderate parameter combinations
  - Burst mode: 2 client counts × 2 message sizes × 2 burst counts × 1 interval
  - Stream mode: 2 client counts × 2 message sizes × 2 rates × 1 duration
- **Super quick test**: Single parameter combination
  - Burst mode: 1 client × 8KB × 1 burst × 0.5s interval
  - Stream mode: 1 client × 8KB × 10 msg/s × 3s duration

---

## WebSocket Testing

The framework includes comprehensive WebSocket benchmarking:

### Test Modes
1. **Burst Mode**: Rapid message bursts with intervals
2. **Streaming Mode**: Continuous message streams at fixed rates

### Parameters
```bash
# Burst Mode
--clients     # Concurrent WebSocket connections
--size_kb     # Message size in kilobytes
--bursts      # Number of messages per burst
--interval    # Time between bursts (seconds)

# Streaming Mode
--clients     # Concurrent WebSocket connections
--size_kb     # Message size in kilobytes
--rate        # Messages per second
--duration    # Test duration (seconds)
```

### Example Test Scenarios
```bash
# Burst: 10 clients, 8KB messages, 50 bursts, 0.5s intervals
# Stream: 50 clients, 64KB messages, 100 msg/s, 30s duration
```

---

## Results and Visualization

### Results structure and naming convention

Results use **clean names** (container/image names only). No benchmark paths like `benchmarks/dynamic/erlang/...` appear in the results or in graphs.

**Folder layout:**
```
results/
└── <timestamp>/              # e.g. 2024-01-15_143022
    ├── static/               # Static HTTP results
    ├── dynamic/              # Dynamic HTTP results
    └── websocket/            # WebSocket results
```

**File names:** One CSV per container, named by the **Docker image (container) name**:
- `static/st-cowboy-27-self.csv`, `static/st-erlang27-self.csv`, …
- `dynamic/dy-phoenix-1-8-self.csv`, …
- `websocket/ws-cowboy-27-self.csv`, …

**Inside each CSV:** The first column is **"Container Name"** and holds that same clean name (e.g. `st-cowboy-27-self`). Multiple rows = different request counts or test parameters.

**In the graph generator:** When you load these CSVs, series labels in the legend use the **container name** (e.g. `st-cowboy-27-self`), not file paths—so graphs stay readable and comparable.

### CSV Output Format

#### HTTP Containers (Static/Dynamic/Local)
Results are grouped by container name with multiple rows per file:
```csv
Container Name,Type,Num CPUs,Total Requests,Successful Requests,Failed Requests,Execution Time (s),Requests/s,Total Energy (J),Avg Power (W),Samples,Avg CPU (%),Peak CPU (%),Total CPU (%),Avg Mem (MB),Peak Mem (MB),Total Mem (MB)
```

**Example:** `st-cowboy-27-self.csv` contains multiple rows (e.g. 1000, 5000, 10000 requests).

#### WebSocket Containers
WebSocket-specific metrics with latency and throughput data:
```csv
Container Name,Test Type,Num CPUs,Total Messages,Successful Messages,Failed Messages,Execution Time (s),Messages/s,Throughput (MB/s),Avg Latency (ms),Min Latency (ms),Max Latency (ms),Total Energy (J),Avg Power (W),Samples,Avg CPU (%),Peak CPU (%),Total CPU (%),Avg Mem (MB),Peak Mem (MB),Total Mem (MB),Pattern,Num Clients,Message Size (KB),Rate (msg/s),Bursts,Interval (s),Duration (s)
```

**Key WebSocket Metrics:**
- **Latency**: Average, minimum, and maximum round-trip times
- **Throughput**: Messages per second and data throughput in MB/s
- **Pattern Configuration**: Burst or stream mode with specific parameters

### Visualization
```bash
# Interactive graph generator (run from repo root; recommended)
make graph

# Or directly
python3 tools/gui_graph_generator.py
```

Features:
- **File Selection**: Browse and select CSV files/folders
- **Column Selection**: Choose metrics to visualize
- **Interactive Graphs**: Zoom, pan, and export capabilities
- **Multi-Server Comparison**: Compare multiple servers simultaneously

---

## Repository Management

### Clean levels (what gets removed)

| Target | Removes | Keeps | Use when |
|--------|--------|--------|----------|
| **`clean-results`** | `results/`, `logs/`, `graphs/`, `graphs_compressed/`, `output/`, `results_docker/`, etc. | `benchmarks/`, `srv/`, all source | You want a **fresh measurement** (no old results or logs). |
| **`clean-build`** | Docker containers and images for discovered benchmarks | Source, results, logs, venv | Rebuild images from scratch. |
| **`clean-all`** | Same as `clean-build` (+ local servers, no-op here) | Source, results, logs, venv | Same as clean-build for BEAM-only. |
| **`clean-benchmarks`** | Entire **`benchmarks/`** folder (then recreates empty `benchmarks/`) | Scripts, tools, Makefile, docs, `srv/` | You want an **empty framework** ready to add new benchmark definitions. **Requires:** `make clean-benchmarks CONFIRM=1` |
| **`clean-nuclear`** | Results + Docker images + **`benchmarks/`** folder | Scripts, tools, Makefile, docs, `srv/` | **Full reset**: empty BEAM framework, no benchmarks, no results, no images. **Requires:** `make clean-nuclear CONFIRM=1` |
| **`clean-repo`** | Everything untracked/ignored + git reset to HEAD | Only committed files | Back to **fresh clone state** (use with care). |

### Cleaning commands
```bash
# Remove only generated outputs (results, logs, graphs) — safe, no confirmation
make clean-results

# Remove Docker containers and images
make clean-build
make clean-all

# Empty the benchmarks folder (framework ready for new benchmarks). Requires confirmation:
make clean-benchmarks CONFIRM=1

# Full reset: results + Docker + benchmarks (empty framework). Requires confirmation:
make clean-nuclear CONFIRM=1

# Git-based reset (fresh clone state; use with care)
make clean-repo
```

### Post-clean setup
- After **`clean-results`**: Just run benchmarks again; no need to rebuild.
- After **`clean-build`** or **`clean-all`**: Run `make build` (and optionally `make check-health`) before running benchmarks.
- After **`clean-benchmarks`** or **`clean-nuclear`**: Add new benchmark definitions under `benchmarks/` (see [Adding New Servers](#adding-new-servers)), then `make build` and run.
- After **`clean-repo`**: Run `make setup`, then `make build`.

---

## Makefile Commands

### Environment Management
```bash
make setup          # Create virtual environment
make ensure-env     # Ensure environment is active
make install        # Install dependencies
make validate       # Validate all prerequisites
```

### Docker Operations
```bash
make build          # Build all Docker images
make clean-build    # Remove Docker containers/images
```

### Benchmarking
```bash
make quick-test     # Quick benchmark test
make run-all        # Complete benchmark suite
make run-static     # Static container benchmarks
make run-dynamic    # Dynamic container benchmarks
make run-websocket  # WebSocket benchmarks
make run-local      # Local server benchmarks
```

### Visualization
```bash
make graph          # Launch graph generator
```

### Repository Management
```bash
make clean-repo     # Complete repository reset
make help           # Show all available commands
```

---

## Troubleshooting

### Common Issues

#### Health Check Failures
```bash
# Check container logs
docker logs health-check-<container-name>

# Verify port availability
netstat -tlnp | grep 8001

# Test manual container startup
docker run -d --rm --ulimit nofile=100000:100000 -p 8001:80 <image-name>

# Check ulimit inside container (should be 100000)
docker exec health-check-<container-name> sh -c 'ulimit -n'
```

- The health check now verifies that `ulimit -n` (open file descriptors) is set to 100000 inside each container. The script prints the actual value for each container. If this is not met, the container will fail the health check and will not be benchmarked.
- If you see ulimit errors, ensure your Docker daemon and host OS allow high file descriptor limits. See below for tips.

#### Docker Issues
```bash
# Check Docker status
docker info

# Clean up containers
docker stop $(docker ps -q)
docker rm $(docker ps -aq)

# Rebuild images
make clean-build
make build
```

#### Energy Measurement
```bash
# Verify Scaphandre installation
scaphandre --version

# Check hardware support
cat /sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj

# Test energy measurement
scaphandre -t 1
```

#### Performance Issues
```bash
# Increase timeouts
./scripts/check_health.sh --timeout 60 --startup 20

# Check system resources
htop
free -h
df -h
```

### Debug Mode
```bash
# Enable verbose output
export DEBUG=1
make quick-test

# Check logs
tail -f logs/run_*.log
```

### Getting Help
```bash
# Show all Makefile commands
make help

# Script help
./scripts/check_health.sh --help
./scripts/run_benchmarks.sh help
```

### ulimit and File Descriptor Limits
- All containers are started with `--ulimit nofile=100000:100000` for high concurrency.
- If you see errors about ulimit or file descriptors, you may need to:
  - Increase your system's open file limit (e.g., edit `/etc/security/limits.conf` on Linux)
  - Configure Docker daemon to allow higher ulimits (see Docker docs: https://docs.docker.com/engine/reference/commandline/dockerd/#default-ulimit)
  - Restart Docker after changing system or daemon limits
- You can check the current limit inside any running container:
  ```bash
  docker exec <container-name> sh -c 'ulimit -n'
  ```
  The health check script will also print this value for you.
- The health check will fail if the limit is not 100000.

---

## Recent Improvements

### v2.1 Enhancements
- **CSV Result Grouping**: Results grouped by container name for easier analysis
- **WebSocket-Specific Metrics**: Enhanced WebSocket CSV format with latency and throughput data
- **Super Quick Testing**: New `run-super-quick` option for fastest validation
- **Improved Local Scripts**: Fixed path resolution for local server setup scripts
- **Enhanced Port Management**: All containers now use port 80 internally for consistency

### v2.0 Enhancements
- **Simplified Port Management**: Fixed host port with automatic container port detection
- **Enhanced Health Checks**: Comprehensive validation with HTTP and WebSocket testing
- **Auto-Discovery**: Intelligent container detection from directory structure
- **Improved Automation**: Streamlined Makefile commands and script integration
- **Better Error Handling**: Robust error detection and reporting
- **Configurable Ports**: Environment variable support for custom ports

### Key Changes
- Removed complex port arrays and manual configuration
- Added real HTTP response validation in health checks
- Implemented WebSocket handshake testing
- Enhanced container discovery and management
- Improved repository cleaning and maintenance tools
- Added comprehensive logging and error reporting

---

## License
This project is licensed under the MIT License. See the `LICENSE` file for details.