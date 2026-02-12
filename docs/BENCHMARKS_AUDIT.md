# Benchmarks Audit

This document summarizes the **BEAM benchmark set** in this repository. This repo focuses on comparing Erlang, Elixir, and Gleam on the BEAM VM. The framework is general; see [EXTENDING.md](EXTENDING.md) for adding other languages or benchmark types.

## Naming convention

- **Path**: `benchmarks/<type>/<language>/<framework-or-variant>/<container-dir>/`
- **Container dir** = directory containing `Dockerfile` = **Docker image name**
- **Prefix**: `st-` (static), `dy-` (dynamic), `ws-` (websocket); no `-self` suffix (use base images, not runtime-built-in-container)
- **EXPOSE**: All containers use port 80 (script reads from Dockerfile)

## Current layout (30 containers)

### Static (13)

| Language | Framework/Variant | Container(s) | Notes |
|----------|-------------------|--------------|--------|
| Erlang  | cowboy  | `st-cowboy-27` | ✓ |
| Erlang  | index   | `st-erlindex23`, `st-erlindex26`, `st-erlindex27` | Serves index HTML file |
| Erlang  | pure    | `st-erlang23`, `st-erlang26`, `st-erlang27` | HTML in code |
| Erlang  | yaws    | `st-yaws-26`, `st-yaws-27` | ✓ |
| Elixir  | cowboy  | `st-elixir-cowboy-1-16` | ✓ |
| Elixir  | phoenix | `st-phoenix-1-8` | ✓ |
| Elixir  | pure    | `st-elixir-1-16` | ✓ |
| Gleam   | mist    | `st-gleam-1-0` | ✓ |

### Dynamic (13)

| Language | Framework/Variant | Container(s) | Notes |
|----------|-------------------|--------------|--------|
| Erlang  | cowboy  | `dy-cowboy-27` | ✓ |
| Erlang  | index   | `dy-erlindex23`, `dy-erlindex26`, `dy-erlindex27` | ✓ |
| Erlang  | pure    | `dy-erlang23`, `dy-erlang26`, `dy-erlang27` | ✓ |
| Erlang  | yaws    | `dy-yaws-26`, `dy-yaws-27` | ✓ |
| Elixir  | cowboy  | `dy-elixir-cowboy-1-16` | ✓ |
| Elixir  | phoenix | `dy-phoenix-1-8` | ✓ |
| Elixir  | pure    | `dy-elixir-1-16` | ✓ |
| Gleam   | mist    | `dy-gleam-1-0` | ✓ |

### WebSocket (4)

| Language | Framework/Variant | Container(s) | Notes |
|----------|-------------------|--------------|--------|
| Erlang  | cowboy | `ws-cowboy-27` | ✓ |
| Erlang  | yaws   | `ws-yaws-27` | ✓ |
| Elixir  | cowboy | `ws-elixir-cowboy-1-16` | ✓ |
| Elixir  | phoenix | `ws-phoenix-1-8` | ✓ |
| **Gleam** | — | **None** | **Missing** (could be added later if Mist/gramps WebSocket support is used) |

## Consistency check

- All 30 directories with a `Dockerfile` have **EXPOSE 80**.
- Folder names match the image names used by scripts (auto-discovery).
- Static and dynamic cover **Erlang, Elixir, Gleam** with the intended frameworks/variants.
- WebSocket covers **Erlang** and **Elixir** only; **Gleam WebSocket is missing** (not required for scripts to run).

## Results and graph naming convention

Results use **clean names** only (no benchmark paths in filenames or graphs).

- **Folder**: `results/<timestamp>/` with subdirs `static/`, `dynamic/`, `websocket/`.
- **File names**: One CSV per container, named by **image name** (e.g. `st-cowboy-27.csv`, `dy-erlang27.csv`, `ws-phoenix-1-8.csv`).
- **CSV column**: First column is **"Container Name"** with that same value (e.g. `st-cowboy-27`).
- **Graphs**: The GUI graph generator uses **"Container Name"** from the CSV (or the filename without `.csv`) as the series label in the legend, so graphs show clean names like `st-cowboy-27`, not paths.

## Ready for measurement

- **Scripts**: `scripts/install_benchmarks.sh`, `scripts/check_health.sh`, `scripts/run_benchmarks.sh` (run from repo root).
- **Tools**: `tools/measure_docker.py`, `tools/measure_websocket.py` (invoked by scripts).
- **Port**: Single host port (default 8001), container port from Dockerfile.
- **WebSocket path**: Health and measurement use `/ws`; existing WebSocket containers expose this path.
- **Configuration**: For a full audit of max_connections, ulimit, ports, request counts, and WebSocket parameters, see [CONFIGURATION_AUDIT.md](CONFIGURATION_AUDIT.md).

## Small test (recommended before full run)

From repo root:

1. **Environment** (once): Ensure Python 3.8+ and the **python3-venv** package (on Debian/Ubuntu: `sudo apt install python3-venv`). Then from repo root:
   ```bash
   make setup    # create srv/ venv and install dependencies (uses srv/bin/python3 -m pip)
   ```
   If `make setup` fails with "Failed to create venv", install python3-venv and run again. If you have an existing broken `srv/` (e.g. no pip), run `make clean-env && make setup`.
2. **Build**: Build all 30 images (already done if you see `[BUILD SUMMARY] All images built successfully`):
   ```bash
   make build
   ```
3. **Health**: Verify all built containers pass health (startup, HTTP/WebSocket, ulimit, large payload):
   ```bash
   make check-health
   ```
   (Requires `python3` with the `websockets` package—use the venv: `srv/bin/pip install websockets` if needed.)
4. **Small run**: One request count per HTTP container and minimal WebSocket test:
   ```bash
   make run-super-quick
   ```
   Results go to `results/<timestamp>/`.

If the small test passes, run the full suite with `make run-all` or `make run-quick`.
