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

**CSV contents:** First column is "Container Name". Multiple rows = different request counts or test parameters.

---

## CSV Format

### HTTP (Static/Dynamic)

```csv
Container Name,Type,Num CPUs,Total Requests,Successful Requests,Failed Requests,Execution Time (s),Requests/s,Total Energy (J),Avg Power (W),Samples,Avg CPU (%),Peak CPU (%),Total CPU (%),Avg Mem (MB),Peak Mem (MB),Total Mem (MB)
```

### WebSocket

```csv
Container Name,Test Type,Num CPUs,Total Messages,Successful Messages,Failed Messages,Execution Time (s),Messages/s,Throughput (MB/s),Avg Latency (ms),Min Latency (ms),Max Latency (ms),Total Energy (J),Avg Power (W),Samples,Avg CPU (%),Peak CPU (%),Total CPU (%),Avg Mem (MB),Peak Mem (MB),Total Mem (MB),Pattern,Num Clients,Message Size (KB),Rate (msg/s),Bursts,Interval (s),Duration (s)
```

**WebSocket metrics:** Latency (avg/min/max), throughput (msg/s, MB/s), pattern configuration (burst/stream parameters).

---

## Benchmark Parameters

### HTTP

- **Full:** 13 request counts (100–80000)
- **Quick:** 3 request counts (1000, 5000, 10000)
- **Super quick:** 1 request count (1000)

### WebSocket

| Mode   | Full                                           | Quick / Super quick                     |
|--------|-------------------------------------------------|-----------------------------------------|
| Burst  | 4 clients × 7 sizes × 2 bursts × 3 intervals    | Reduced combinations                    |
| Stream | 4 clients × 7 sizes × 3 rates × 3 durations    | Reduced combinations                    |

---

## Graph Generator

```bash
make graph
# or
python3 tools/gui_graph_generator.py
```

- **File selection:** Browse and select CSV files/folders
- **Column selection:** Choose metrics to plot
- **Interactive:** Zoom, pan, export
- **Compare:** Multiple servers in one graph
