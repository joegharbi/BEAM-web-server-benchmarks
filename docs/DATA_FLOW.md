# How the data flows

From `make run` to a number in the paper: which program runs when, what it writes, and what reads it next. Every
file named here is in this repository; nothing in this guide is a plan, it describes the code as it is.

```
  make run CONFIG=<file>
        │
  A  PREPARE        scripts/run_benchmarks.sh ── tools/bench_config.py        (read and check the config)
        │                                     ── tools/prepare_environment.py (machine settings, restored at the end)
        │                                     ── tools/readiness.py baseline  (how calm is the machine at rest)
        │                                     ── tools/run_metadata.py start  (metadata.json: versions, machine, settings)
        │
  B  MEASURE        for every repeat, for every server and load (shuffled):
     (one run)        tools/readiness.py wait            wait until the machine is calm again
                      tools/measure_<kind>.py ─► tools/measure_core.py
                          deploy plugin    start the server (Docker, or natively from the image's files)
                          workload plugin  wait until it answers, then the load (HTTP, WebSocket, echo)
                          meter plugin     Scaphandre records power every 0.5 s ─► raw/<run>.json
                          tools/scaphandre_energy.py   energy of the load window  ─► one CSV row
                          tools/results_index.py       one line in manifest.jsonl
        │
  C  FINISH         tools/aggregate_repeats.py   per-configuration statistics (*_summary.csv)
                    tools/run_metadata.py end     tools/prepare_environment.py restore
        │
  D  ANALYSE        analysis/*.py  ─►  checks, statistics, Table I, figures (graphs/paper/)
```

## A. Prepare (once per campaign)

1. **`make run CONFIG=campaigns/paper/static.config`** activates the Python environment and starts
   `scripts/run_benchmarks.sh`.
2. **`tools/bench_config.py`** reads the config and its machine profile (`configs/machine/*.config`). Every key has
   a default, a check and a description (all of them: `docs/CONFIG.md`); a typo stops the run instead of falling
   back to a default. The result is saved as `bench.config.resolved` in the results folder.
3. **`tools/prepare_environment.py apply`** (with sudo) puts the machine in the measuring state: CPU governor,
   fixed CPU speed, turbo off, power profile, screen and radios, maintenance timers and services paused, other
   containers stopped. Everything it changes is saved in `.environment_state.json`, and **`restore`** puts it back
   at the end, also after Ctrl-C.
4. The machine **settles** (60 s), then **`tools/readiness.py baseline`** measures its resting temperature and CPU
   use: the references for the readiness gate.
5. **`tools/run_metadata.py start`** writes `metadata.json`: software versions, the machine, the settings, the image
   IDs. The plan (`plan.json`) and the shuffled order (`schedule.txt`) are written too.

## B. One measurement (repeated for every server, load and repeat)

1. **`tools/readiness.py wait`**: before every run, wait until the CPU is back near its resting temperature and
   use, not throttled, at the expected speed, on the charger.
2. **`tools/measure_<kind>.py`** (`measure_docker.py` for HTTP, `measure_websocket.py`, `measure_echo.py`) are thin
   entry points into **`tools/measure_core.py`**, which always takes the same steps:
   1. start the server with the **deploy plugin** (`tools/plugins/deploy/`): `container` = `docker run`;
      `native` = the image's own files copied out (`tools/native_server.py`) and started in a systemd scope;
   2. wait until it answers (**workload plugin**, `tools/plugins/workload/`), optional warm-up, readiness check 2;
   3. start the **meter plugin** (`tools/plugins/meter/scaphandre.py`): Scaphandre writes the laptop's power and
      each program's share every 0.5 s into `raw/<server>_<test>_<time>.json`;
   4. the **load** (the workload plugin's client). Around it: the server's exact CPU time from its cgroup
      (`tools/server_box.py`), and the conditions during the load (`tools/load_conditions.py`: CPU speed,
      throttling, charger);
   5. stop the meter; **`tools/scaphandre_energy.py`** adds up power × time over exactly the load window, for the
      server (its processes, identified by cgroup) and for the whole laptop (`*.window.json` keeps the window and
      the process IDs, so it can be checked and recomputed later);
   6. stop the server; judge the conditions: a run that broke a rule goes to `invalid_runs.csv` and is measured
      again;
   7. write **one CSV row** (`tools/csv_columns.py` defines the 44+ columns, in blocks: what was measured,
      performance, server, laptop, idle, how the run went) to `<family>/<server>.csv`, and one line to
      `manifest.jsonl` (`tools/results_index.py`).
3. **`tools/measure_failure.py`**: how a run ends when it cannot be measured (exit codes the run script reads);
   failures go to `failures.csv`, the campaign continues.
4. **`tools/progress.py`** prints the progress panel; `timing.tsv` and `progress.txt` record what is done, so
   `make resume` continues an interrupted campaign where it stopped.

## C. Finish (once per campaign)

- **`tools/aggregate_repeats.py`**: per server and load, the statistics of the repeats (mean, median, spread, 95%
  range, outlier rules) into `<server>_summary.csv` and `<family>/summary.csv`.
- A summary of failed and partly failed runs and of runs where the meter fell behind.
- **`tools/run_metadata.py end`** completes `metadata.json`; **`tools/prepare_environment.py restore`** restores
  the machine; `results/index.json` lists every results folder.

## What is in a results folder

| File | Written by | What it is |
|---|---|---|
| `<family>/<server>.csv` | measure_core | **the data**: one row per run (static, dynamic, websocket, echo) |
| `<family>/*summary.csv` | aggregate_repeats | statistics per configuration (derived; can be rebuilt) |
| `raw/*.json`, `raw/*.window.json` | Scaphandre, measure_core | the meter's log and the load window of every run |
| `manifest.jsonl` | results_index | one line per run: which server, which CSV row, valid or not |
| `metadata.json` | run_metadata | versions, machine, settings, image IDs, start and end state |
| `bench.config`, `bench.config.resolved`, `machine.config` | run script | the config as written and with every value filled in |
| `plan.json`, `schedule.txt` | run script | what will be measured, in which order (shuffle seed) |
| `timing.tsv`, `progress.txt` | run script | what is done, how long each run took (progress, resume) |
| `invalid_runs.csv`, `failures.csv` | load_conditions, run script | runs measured again, runs that could not be measured |

The numbers are only in the CSV rows and the raw logs; every other file is an index, a record of the settings, or
derived from them, so nothing can disagree.

## D. From results to the paper (`analysis/`, run by hand)

All analysis scripts only read results folders and change nothing; run with `./srv/bin/python3 analysis/<script>`.

| Step | Script | What it does |
|---|---|---|
| (shared) | `data.py` | reads the rows of a results folder; server name, busy-waiting on/off, load level, median, spread |
| 1 | `check.py` | is the folder complete? same conditions in every run? odd runs? enough meter readings? |
| 1b | `check_attribution.py` | recomputes the server energy from the raw logs with its own code: equal to the CSV, nothing counted twice or missed |
| 2 | `busy_wait_http.py` | each server with busy-waiting on vs off: server energy, laptop energy, CPU, speed |
| 3 | `stats.py` | exact tests (Mann-Whitney p, sign test), effect size, bootstrap 95% ranges |
| 4 | `busy_wait_websocket.py` | the same for WebSocket (only tests long enough for the meter) |
| 5 | `table.py` | Table I of the paper, in LaTeX |
| 5 | `figures.py` | Figures 1–5 (HTTP, WebSocket) as images + `figures.json` for the web page |
| 6 | `echo_figures.py` | the echo figures (on vs off with Java and C, spread of the repeats) |

How each statistic is computed, step by step and with examples: the "Statistics, explained" tab of the meeting
page, and the docstrings of `stats.py` and `data.py`.

## Fixing things afterwards

- **`tools/recompute_results.py`**: when the energy calculation is corrected, recompute the CSV energy of finished
  campaigns from their raw logs (nothing is measured again).
- **`tools/tidy.py`** (`make tidy`): free disk space (old images, caches); results are never touched.

## Other parts

- `benchmarks/`: the servers (one folder each, with a Dockerfile and labels; found automatically).
- `campaigns/`, `configs/machine/`: the config files of every campaign and the machine profiles.
- `experiments/`: separate studies with their own scripts (busy-wait evidence, meter overhead).
- `tools/gui_graph_generator.py` (`make graph`): the older graph window; the paper's figures come from `analysis/`.
- `tests/test_changes.py`: the tests (`./srv/bin/python3 -m pytest tests/`).

## Known weak spots (planned for V2, not changed for the paper)

- `scripts/run_benchmarks.sh` is about 2,300 lines of shell: every workload adds its own loops there. V2: a small
  Python orchestrator driven by an experiment file, each workload bringing its own settings and columns.
- A results folder uses five formats and one CSV file per server. V2: one results table per campaign with a written
  schema (Parquet + DuckDB), CSV export for papers, live metrics for Prometheus/Grafana.
- The old graph window, the single large test file, and the two repositories of the same framework.
