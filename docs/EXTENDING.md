# Extending the Framework

The benchmark framework is **general-purpose** and designed to be extended with new benchmark types (gRPC, RPC, custom protocols, etc.) without modifying the core structure. This document describes how.

## Architecture

- **benchmarks/** — Each subdirectory is a benchmark type (e.g. `static`, `dynamic`, `websocket`). The build and health check scripts discover these dynamically. Container directory name = Docker image name = CSV/graph label; use unified naming `<type>-<language>-<framework>-<version>` (see [MINIMAL_BASES_AND_UNIFICATION.md](MINIMAL_BASES_AND_UNIFICATION.md)).
- **Path-based routing** — The health check infers the test to run from the container’s path (e.g. `benchmarks/websocket/...` → WebSocket handshake + echo).
- **One measuring core, three kinds of plugins** — every measurement takes the same steps in the same order
  (`tools/measure_core.py`): check the tools and the port, start the server, wait until it answers, warm up,
  readiness check, meter on, idle window, the load (with statistics and the conditions watch), meter off,
  energy of the load window, processes in the server's box, stop the server, judge the conditions, write
  the CSV row and the results-index line. Only three parts differ, each a plugin in `tools/plugins/`:

  | Kind | Answers | Now | Base class (the interface) |
  |---|---|---|---|
  | `deploy/` | where the server runs | `container` (Docker), `native` (systemd scope) | `plugins/deploy/__init__.py` |
  | `workload/` | what load it gets | `http`, `websocket` | `plugins/workload/__init__.py` |
  | `meter/` | how its energy is measured | `scaphandre` | `plugins/meter/__init__.py` |

  A plugin is one file with a class `Plugin`; it is found by its file name (`--deploy` and `--meter` list
  them by themselves). `measure_docker.py` (HTTP) and `measure_websocket.py` (WebSocket) are thin entry
  points with their command lines as before. So a new deploy (e.g. `deploy/kubernetes.py`), meter
  (`meter/rapl.py`) or workload (`workload/kafka.py`) is measured exactly like everything else.

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

### 3. Add a workload plugin and its entry point

1. `tools/plugins/workload/grpc.py` with a class `Plugin(Workload)`: its options (`add_arguments`), its URL
   and health probe (`url`, `probe`), the load (`run`, `progress`, `counts`), its CSV columns (`columns`,
   `values`) and its results-index entry (`index`). Everything else (deploy, meter, readiness, conditions,
   CSV, index) comes from the core and is the same as for HTTP and WebSocket.
2. Its CSV layout in `tools/csv_columns.py` (the shared blocks plus its own load columns).
3. `tools/measure_grpc.py`, three lines like `measure_websocket.py`:
   `measure_core.main(__file__, "grpc", "Measure gRPC server energy")`.
4. `tests/test_changes.py` (MeasuringCore) checks that the plugin has the whole interface.

### 4. Extend the run script

In `scripts/run_benchmarks.sh`:

1. Add `mkdir -p "$RESULTS_DIR/grpc"` (or discover result dirs from benchmark types).
2. Add a `discover_containers` case for `grpc` (it already takes a type argument).
3. Add a `run_grpc_tests` function that calls `measure_grpc.py` (through `bench_measure`, like the others).
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

This repository uses the framework to compare **BEAM languages** (Erlang, Elixir, Gleam) on HTTP and WebSocket workloads. The framework itself is language-agnostic; you can add Java, Go, Node.js, or any stack by adding containers under `benchmarks/` (server contract: README) and, if needed, new plugins and run-script cases.
