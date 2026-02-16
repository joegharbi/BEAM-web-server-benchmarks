# Extending the Framework

The benchmark framework is **general-purpose** and designed to be extended with new benchmark types (gRPC, RPC, custom protocols, etc.) without modifying the core structure. This document describes how.

## Architecture

- **benchmarks/** — Each subdirectory is a benchmark type (e.g. `static`, `dynamic`, `websocket`). The build and health check scripts discover these dynamically. Container directory name = Docker image name = CSV/graph label; use unified naming `<type>-<language>-<framework>-<version>` (see [MINIMAL_BASES_AND_UNIFICATION.md](MINIMAL_BASES_AND_UNIFICATION.md)).
- **Path-based routing** — The health check infers the test to run from the container’s path (e.g. `benchmarks/websocket/...` → WebSocket handshake + echo).
- **Measurement tools** — **One script per type**: each supported type has its own Python measurement script (`measure_docker.py` for HTTP, `measure_websocket.py` for WebSocket). New types (gRPC, etc.) need a new script. This keeps each script focused and makes extending straightforward.

## Adding a New Benchmark Type

Example: adding **gRPC** benchmarks.

### 1. Create the benchmark layout

```text
benchmarks/grpc/
└── go/
    └── grpc-go/
        └── grpc-echo/
            ├── Dockerfile
            └── ...
```

Any `benchmarks/<type>/` subdirectory is discovered automatically. `make build` will build all images under `benchmarks/grpc/`. The Makefile uses a pattern rule, so **`make run-grpc` works as soon as you add the benchmark layout and extend the run script** — no Makefile edits needed.

### 2. Extend the health check

In `scripts/check_health.sh`, `check_container_health` uses `find_container_dir` to get the container path. Add a branch for your type:

```bash
# Infer benchmark type from path
container_dir=$(find_container_dir "$image_name")
if [[ "$container_dir" == *"/websocket/"* ]]; then
    # WebSocket test
elif [[ "$container_dir" == *"/grpc/"* ]]; then
    # gRPC health test (e.g. grpcurl or similar)
else
    # HTTP test (default)
fi
```

### 3. Create a measurement tool (one script per type)

Add `tools/measure_grpc.py` (or equivalent) that:

- Accepts `--server_image`, `--port_mapping`, `--output_csv`, etc.
- Starts the container, runs Scaphandre for energy, sends load, writes CSV results.
- Follows the same pattern as `measure_docker.py` and `measure_websocket.py` for port handling and cleanup.

### 4. Extend the run script

In `scripts/run_benchmarks.sh`:

1. Add `mkdir -p "$RESULTS_DIR/grpc"` (or discover result dirs from benchmark types).
2. Add a `discover_containers` case for `grpc` (it already takes a type argument).
3. Add a `run_grpc_tests` function that calls `measure_grpc.py`.
4. In the main flow, add a branch for `grpc` similar to the websocket flow.

### 5. Extend the graph GUI (optional)

The GUI uses **extensible registries** at the top of `tools/gui_graph_generator.py`. Add your type in one place:

1. **CSV_TYPE_DETECTORS** — Add a tuple so gRPC CSVs are detected:
   ```python
   ((["Total RPCs", "RPC/s"], []), "grpc"),
   ```

2. **CATEGORY_PREFIXES** — Map filename prefixes to display names:
   ```python
   "grpc-": "gRPC",
   ```

3. **CATEGORY_PATH_PARTS** — Map path segments (e.g. `benchmarks/grpc/`) to display names:
   ```python
   "grpc": "gRPC",
   ```

4. **X_AXIS_COLUMNS** — Define which columns to use as x-axis for your CSV type:
   ```python
   "grpc": ["Total RPCs", "Concurrent Streams"],
   ```

5. **NUMERIC_KEYWORDS** — Add keywords for new plottable metrics (e.g. `"rpc"`).

The filter dropdown, save paths (`graphs/grpc/`), and plot logic update automatically. No changes to the GUI class structure are needed.

## This Repository’s Focus

This repository uses the framework to compare **BEAM languages** (Erlang, Elixir, Gleam) on HTTP and WebSocket workloads. The framework itself is language-agnostic; you can add Java, Go, Node.js, or any stack by adding containers under `benchmarks/` and, if needed, new measurement tools and run-script cases.
