# BEAM Web Server Benchmarks

A benchmarking framework for HTTP and WebSocket servers. This repository compares **BEAM languages** (Erlang, Elixir, Gleam) and their frameworks (Cowboy, Phoenix, Yaws, Mist). The framework is general-purpose: you can add other languages or benchmark types.

## Overview

- **Auto-discovery**: Finds all containers under `benchmarks/static/`, `benchmarks/dynamic/`, `benchmarks/websocket/`. No hardcoded lists.
- **Health checks**: Validates startup, HTTP/WebSocket response, large payloads, and ulimit before benchmarking.
- **Measurement**: Runs containers under load with Scaphandre for energy and performance metrics.
- **Visualization**: GUI for plotting results from CSV output.

```
make init → make build → make check-health → make run → make graph
```

## Prerequisites

- **Linux** (Debian-based recommended)
- **Python 3.8+** and **python3-venv** (`sudo apt install python3 python3-venv`)
- **Docker** (`sudo apt install docker.io`)
- **Make**
- **Scaphandre** (optional, for energy): `cargo install scaphandre`

Verify: `make check-tools`

## Quick Start

```bash
make init              # venv + deps + build + health check
make run-super-quick   # Fast validation (1 request count per container)
make run-quick         # 3 request counts per container
make run-all           # Full suite (static, dynamic, websocket)
make graph             # Interactive result visualization
```

Step-by-step: `make setup` → `make build` → `make check-health` → `make run-quick`

## Directory Structure

```
benchmarks/           # Type → Language → Framework → container (with Dockerfile)
  static/             # Static HTTP
  dynamic/            # Dynamic HTTP
  websocket/          # WebSocket (must expose /ws)
scripts/              # check_health.sh, run_benchmarks.sh, install_benchmarks.sh
tools/                # measure_docker.py, measure_websocket.py, gui_graph_generator.py
results/              # Output CSVs (results/<timestamp>/{static,dynamic,websocket}/)
```

Each directory containing a `Dockerfile` under `benchmarks/` is one benchmark. The **directory name** is the Docker image name. Type is inferred from the path (`benchmarks/websocket/...` → WebSocket test).

## Adding a Server

1. Create `benchmarks/<type>/<lang>/<framework>/<container>/` with a `Dockerfile`.
2. Add `EXPOSE 80` (or your port). Ensure ulimit 100000 (health check enforces this).
3. Run `make build` → `make check-health` → `make run-super-quick`.

## Commands

| Task           | Command                         |
|----------------|---------------------------------|
| Build          | `make build`                    |
| Health check   | `make check-health`             |
| Benchmarks     | `make run-quick`, `make run-all`|
| Graphs         | `make graph`                    |
| Clean results  | `make clean-results`            |
| Clean env      | `make clean-env` (venv + __pycache__) |
| Clean Docker   | `make clean-build`              |
| Clean all      | `make clean-all` (results + env + Docker; run `make setup` after) |
| Empty benchmarks| `make clean-benchmarks CONFIRM=1` |
| Full reset     | `make clean-nuclear CONFIRM=1`  |

Port: `HOST_PORT=9001 make check-health` (default 8001). Full clean options: [docs/CONFIGURATION_AUDIT.md](docs/CONFIGURATION_AUDIT.md) or `make help`.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/CONFIGURATION_AUDIT.md](docs/CONFIGURATION_AUDIT.md) | All settings (ulimit, ports, request counts, Scaphandre) |
| [docs/BENCHMARKS_AUDIT.md](docs/BENCHMARKS_AUDIT.md) | Current BEAM benchmark set |
| [docs/RESULTS.md](docs/RESULTS.md) | CSV format, parameters, visualization |
| [docs/EXTENDING.md](docs/EXTENDING.md) | Adding new benchmark types (gRPC, etc.) |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Setup, health, Docker, energy, ulimit |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Version history |

## Energy Measurement

Scaphandre attributes energy by container. On systems where it reports `container: null` (e.g. cgroups v2, Debian 13), the scripts use a **cgroup fallback** (matching `/proc/<pid>/cgroup` to the container ID). See [Scaphandre issue #420](https://github.com/hubblo-org/scaphandre/issues/420). Ensure Scaphandre runs on the host with `sudo`. Debug: `python tools/debug_scaphandre_docker.py --server_image <image> --duration 10`.

## Troubleshooting

- **Setup fails**: `sudo apt install python3-venv`; if `srv/` is broken, `make clean-env && make setup`
- **Health check fails**: Check `docker logs health-check-<name>`, ensure port 8001 is free, verify ulimit inside container
- **Energy 0.00 J**: See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md#energy-measurement-000-j-container-not-found)
- **ulimit errors**: Increase system/Docker limits; health check requires 100000

Full troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## License

MIT. See `LICENSE`.
