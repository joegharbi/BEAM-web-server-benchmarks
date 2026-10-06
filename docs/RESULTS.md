# Results and Visualization

## Structure

Results use **container names only**—no benchmark paths in output or graphs.

```
results/
└── <timestamp>/              # e.g. 2024-01-15_143022
    ├── static/
    ├── dynamic/
    └── websocket/
```

**File names:** One CSV per container: `static/st-erlang-cowboy-27.csv`, `dynamic/dy-elixir-phoenix-1-8.csv`, `websocket/ws-erlang-cowboy-27.csv`, etc.

**CSV contents:** one row per run (server, load level, repeat); the columns are described below.

---

## CSV Format

One row per run. The columns come in six blocks, defined once in `tools/csv_columns.py`; every measured
value starts with `Container`, `Host` or `Energy` to say whose it is. HTTP files have blocks 1 to 6 with the
HTTP columns, WebSocket files the same with the WebSocket columns.

| Block | Columns |
|---|---|
| 1. What was measured (HTTP) | Container Name, Variant, Deploy, Repeat, Session, Measured At (UTC), Type, Total Requests, HTTP Max Workers, HTTP Connection Mode |
| 1. What was measured (WebSocket) | Container Name, Variant, Deploy, Repeat, Session, Measured At (UTC), Test Type, Pattern, Num Clients, Message Size (KB), Rate (msg/s), Bursts, Interval (s), Duration (s) |
| 2. Performance (HTTP) | Successful Requests, Failed Requests, Execution Time (s), Requests/s |
| 2. Performance (WebSocket) | Total Messages, Successful Messages, Failed Messages, Execution Time (s), Messages/s, Throughput (MB/s), Avg Latency (ms), Min Latency (ms), Max Latency (ms) |
| 3. Container | Container CPU Limit, Container Energy (J), Container Avg Power (W), Container Avg CPU (%), Container Peak CPU (%), Container Total CPU (%*s), Container Avg Mem (MB), Container Peak Mem (MB), Container Total Mem (MB*s) |
| 4. Host | Host CPUs, Host Energy (J), Host Avg Power (W), Host CPU Temp Start (C), Host CPU Temp End (C), Host Throttled (ms), Host CPU Speed Limit Min (MHz), Host CPU Avg Speed (MHz) |
| 5. Idle | Idle Time (s), Container Idle Energy (J), Container Idle Avg Power (W), Host Idle Avg Power (W) |
| 6. How the run went | Warm-up (s), Waited Before Start (s), Waited Before Load (s), Ready Check, Energy Samples, Energy Sampling Step (ms), Energy Window Coverage, Raw Log, Server Processes |

Files written by earlier releases use older names (for example `Total Energy (J)` for `Container Energy (J)`,
`Num CPUs` for `Host CPUs`); the statistics and the graph window read them under the current names. The
full table of renamed columns is in the README.

**Deploy:** how the server ran: `container` (its image in Docker) or `native` (the same program copied out of
the image, run without Docker in a systemd user scope; `DEPLOY` in the config). Native rows are named
`<server>-native`; for them the Container columns describe the scope (its cgroup), and Container CPU Limit is
`none`. Files written before this column read as empty (container).

**Server Processes:** the processes found in the server's box (container or scope) at the end of the load,
e.g. `beam.smp, erl_child_setup`. Anything besides the server (`epmd`, a build tool, a keep-alive loop) means
the run measured more than the server (README: server contract); the tool also prints a warning.

**Conditions during the load:** the machine is watched every 0.5 s while each load runs (`tools/load_conditions.py`).
**Host CPU Speed Limit Min (MHz)** is the lowest speed limit seen (the firmware can cap the CPU, and the cap can
come and go); **Host CPU Avg Speed (MHz)** the average speed of all cores. A run that broke a rule of the config
(CPU capped below the expected speed, throttling, charger unplugged) is **invalid**: it is not in these CSVs but
kept in `invalid_runs.csv` of the results folder (time, server, variant, deploy, measurement, reason, its values and
raw log) and measured again (`INVALID_RUN_RETRIES`). The end of a run lists the invalid runs per server: if they
pile up on one configuration, the cause may be that server rather than the machine.

**HTTP Max Workers:** the client's parallel requests (`HTTP_MAX_WORKERS`, default 100; `system` = Python's
default pool size, recorded as **System default**).

---

## Benchmark Parameters

Full runs use the config file's values (defaults below; see docs/CONFIG.md):

- **HTTP load levels:** 100 1000 5000 8000 10000 15000 20000 30000 40000 50000 60000 70000 80000 requests
- **WebSocket burst:** 5 50 100 clients × 8 1024 65536 KB × 3 bursts, 0.5 s apart
- **WebSocket stream:** 5 50 100 clients × 8 1024 65536 KB, 10 messages/s for 5 s
- **WebSocket concurrency:** 100 1000 5000 clients, 8 KB
- **WebSocket payload:** 5 clients, 8 1024 65536 KB

`--quick` (HTTP: 1000, 5000, 10000) and `--super-quick` (HTTP: 1000; one test of each WebSocket kind) keep
their short built-in lists.

---

## Graph Generator

```bash
make graph
# or
python3 tools/gui_graph_generator.py
```

- **File selection:** select CSV files or a whole results folder (summary files are skipped; the window computes
  the same statistics from the run files)
- **Scope:** Container, Host or All, to show only that scope's values in the metric list
- **Metric and Type:** the measured value, as lines, bars or (WebSocket) a heatmap
- **Repeats:** median + IQR, mean + 95% CI, every run, or one repeat only
- **Statistics:** table per server and load: runs, median, Q1, Q3, mean, 95% CI, CV% (the numbers of summary.csv)
- **Run detail:** how one run went: power over time from its raw Scaphandre log, with its conditions
- **Variants:** a `<server>-<variant>` series has its server's colour, dashed (bars hatched)
- **Export Studio:** PNG/PDF/SVG, sizes, DPI, a paper style, batch export
