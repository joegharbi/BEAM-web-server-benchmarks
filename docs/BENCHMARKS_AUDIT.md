# Benchmarks Audit

This document summarizes the **BEAM benchmark set** in this repository. This repo focuses on comparing Erlang, Elixir, and Gleam on the BEAM VM. The framework is general; see [EXTENDING.md](EXTENDING.md) for adding other languages or benchmark types.

## Naming convention (unified)

- **Path**: `benchmarks/<type>/<language>/<framework-or-variant>/<container-dir>/`
- **Container dir** = directory containing `Dockerfile` = **Docker image name**
- **Pattern**: `<type>-<language>-<framework>-<version>` (e.g. `st-erlang-cowboy-27`, `dy-elixir-pure-1-16`)
- **Type**: `st-` (static), `dy-` (dynamic), `ws-` (websocket)
- **EXPOSE**: All containers use port 80 (script reads from Dockerfile)

See [MINIMAL_BASES_AND_UNIFICATION.md](MINIMAL_BASES_AND_UNIFICATION.md) for base-image and build-pattern details.

## Current layout (34 containers)

### Static (15)

| Language | Framework/Variant | Container(s) | Notes |
|----------|-------------------|--------------|--------|
| Erlang  | cowboy  | `st-erlang-cowboy-27` | ✓ |
| Erlang  | index   | `st-erlang-index-23`, `st-erlang-index-26`, `st-erlang-index-27` | Serves index HTML file |
| Erlang  | pure    | `st-erlang-pure-23`, `st-erlang-pure-26`, `st-erlang-pure-27` | HTML in code |
| Erlang  | yaws    | `st-erlang-yaws-26`, `st-erlang-yaws-27` | ✓ |
| Elixir  | cowboy  | `st-elixir-cowboy-1-16` | ✓ |
| Elixir  | index   | `st-elixir-index-1-16` | Serves index HTML file |
| Elixir  | phoenix | `st-elixir-phoenix-1-8` | ✓ |
| Elixir  | pure    | `st-elixir-pure-1-16` | ✓ |
| Gleam   | index   | `st-gleam-index-1-0` | Serves index HTML file |
| Gleam   | mist    | `st-gleam-mist-1-0` | ✓ |

### Dynamic (15)

| Language | Framework/Variant | Container(s) | Notes |
|----------|-------------------|--------------|--------|
| Erlang  | cowboy  | `dy-erlang-cowboy-27` | ✓ |
| Erlang  | index   | `dy-erlang-index-23`, `dy-erlang-index-26`, `dy-erlang-index-27` | ✓ |
| Erlang  | pure    | `dy-erlang-pure-23`, `dy-erlang-pure-26`, `dy-erlang-pure-27` | ✓ |
| Erlang  | yaws    | `dy-erlang-yaws-26`, `dy-erlang-yaws-27` | ✓ |
| Elixir  | cowboy  | `dy-elixir-cowboy-1-16` | ✓ |
| Elixir  | index   | `dy-elixir-index-1-16` | ✓ |
| Elixir  | phoenix | `dy-elixir-phoenix-1-8` | ✓ |
| Elixir  | pure    | `dy-elixir-pure-1-16` | ✓ |
| Gleam   | index   | `dy-gleam-index-1-0` | ✓ |
| Gleam   | mist    | `dy-gleam-mist-1-0` | ✓ |

### WebSocket (4)

| Language | Framework/Variant | Container(s) | Notes |
|----------|-------------------|--------------|--------|
| Erlang  | cowboy | `ws-erlang-cowboy-27` | ✓ |
| Erlang  | yaws   | `ws-erlang-yaws-27` | ✓ |
| Elixir  | cowboy | `ws-elixir-cowboy-1-16` | ✓ |
| Elixir  | phoenix | `ws-elixir-phoenix-1-8` | ✓ |
| **Gleam** | — | **None** | **Missing** (could be added later if Mist/gramps WebSocket support is used) |

## Consistency check

- All 34 directories with a `Dockerfile` have **EXPOSE 80**.
- Folder names match the image names used by scripts (auto-discovery).
- Static and dynamic cover **Erlang, Elixir, Gleam** with the intended frameworks/variants; index and pure variants exist where documented.
- WebSocket covers **Erlang** and **Elixir** only; **Gleam WebSocket is missing** (not required for scripts to run).

## Results and graph naming convention

Results use **container names only** (no benchmark paths in filenames or graphs).

- **Folder**: `results/<timestamp>/` with subdirs `static/`, `dynamic/`, `websocket/`.
- **File names**: One CSV per container, named by **image name** (e.g. `st-erlang-cowboy-27.csv`, `dy-elixir-pure-1-16.csv`, `ws-elixir-phoenix-1-8.csv`).
- **CSV column**: First column is **"Container Name"** with that same value.
- **Graphs**: The GUI graph generator uses **"Container Name"** from the CSV (or the filename without `.csv`) as the series label in the legend.

## Ready for measurement

- **Scripts**: `scripts/install_benchmarks.sh`, `scripts/check_health.sh`, `scripts/run_benchmarks.sh` (run from repo root).
- **Tools**: `tools/measure_docker.py`, `tools/measure_websocket.py` (invoked by scripts).
- **Port**: Single host port (default 8001), container port from Dockerfile.
- **WebSocket path**: Health and measurement use `/ws`; existing WebSocket containers expose this path.
- **Configuration**: See [CONFIGURATION_AUDIT.md](CONFIGURATION_AUDIT.md) and [CONFIGURATION_PARITY.md](CONFIGURATION_PARITY.md).

## Small test (recommended before full run)

From repo root:

1. **Environment** (once): Ensure Python 3.8+ and the **python3-venv** package (on Debian/Ubuntu: `sudo apt install python3-venv`). Then from repo root:
   ```bash
   make setup    # create srv/ venv and install dependencies (uses srv/bin/python3 -m pip)
   ```
   If `make setup` fails with "Failed to create venv", install python3-venv and run again. If you have an existing broken `srv/` (e.g. no pip), run `make clean-env && make setup`.
2. **Build**: Build all 34 images (already done if you see `[BUILD SUMMARY] All images built successfully`):
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
