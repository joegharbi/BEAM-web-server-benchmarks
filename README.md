# BEAM Web Server Benchmarks

A benchmarking framework for HTTP and WebSocket servers. This repository compares **BEAM languages** (Erlang, Elixir, Gleam) and their frameworks (Cowboy, Phoenix, Yaws, Mist). The framework is general-purpose: you can add other languages or benchmark types.

## Note on the high load HTTP results

Above roughly 30,000 requests the HTTP results split cleanly. Every target that keeps connections open loses a similar share of requests. Every target built directly on `gen_tcp`, which closes after each response, completes the work.

We are investigating whether this reflects the servers or the measurement setup. Connection handling is the leading candidate.

**Scope.** Treat HTTP results above roughly 20,000 requests as provisional pending that work. Results below that level, the WebSocket results, and the measurement framework itself are unaffected.

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
- **Scaphandre** (for energy): `sudo apt install scaphandre` on Debian 13. Version 1.0.3 prints `1.0.2` with `--version`
  (released with the old version number), so `metadata.json` also records the package version.

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

Benchmark root can be overridden (default remains `benchmarks/`):

```bash
make run BENCHMARKS_DIR=benchmarks
```

## Controlled measurements with a config file

For repeatable measurements, describe the measurement in one short file and run it with one command:

```
# my.config
MACHINE=minimal                            # how the machine is prepared (a profile, below)
MEASURE=static dynamic                     # kinds: static dynamic websocket concurrency payload (default: all)
SERVERS=st-erlang-cowboy-29-1-1 static:my-nginx   # empty (default) = every server found in BENCHMARKS_DIR
HTTP_REQUESTS=1000 20000 80000             # the load levels
REPEATS=5
```

```bash
make run CONFIG=my.config
```

Every line is optional; `bench.config.example` lists every setting of a measurement file with a one-line
comment, and [docs/CONFIG.md](docs/CONFIG.md) explains each one in full (both are generated from the code).

**Machine profiles** (`MACHINE=`) hold how the machine is prepared, so a measurement file stays short. They
are in `configs/machine/`:

| Profile | For | Machine settings |
|---|---|---|
| `minimal` (default) | A laptop or desktop you control, left alone while it measures (published results) | CPU at a fixed speed, turbo off, other containers stopped, screen at 1%, keyboard light, Wi-Fi and Bluetooth off |
| `tolerable` | The same, but reached over the network or needing to stay online | As minimal, but Wi-Fi stays on |
| `remote` | A machine you control over SSH: your desktop, a lab or university server reserved for you | CPU at a fixed speed, turbo off, other containers stopped, screen and Bluetooth off where present; Wi-Fi never touched; stops cleanly (resumable) after 15 minutes without calm |
| `cloud` | A rented cloud machine | CPU settings where the provider allows them; shared hardware, so it measures after 10 minutes without calm and records why. Needs visible RAPL energy counters (bare-metal instances) |
| `untouched` | A machine you must not change (shared with others, or no rights) | Nothing is changed; if the machine never becomes calm, it measures anyway and records why |

Settings a machine does not have (no screen, battery, Bluetooth, or no CPU control in a virtual machine)
are skipped and recorded. A machine without CPU energy counters (RAPL), as most cloud virtual machines,
stops at the start: Scaphandre could not measure anything there. Values come from the defaults, then the
profile, then the measurement file, which can override any
machine setting for one measurement (for example `ENV_WIFI=unchanged`). `MACHINE` can also be the path of
your own profile file. The results folder keeps a copy of both files (`bench.config`, `machine.config`)
and every value actually used (`bench.config.resolved`). A run started over SSH through Wi-Fi refuses to
switch Wi-Fi off, since that would cut its own connection; use the tolerable profile there.

**Variants.** `VARIANTS=nobw:ERL_FLAGS=+sbwt none +sbwtdcpu none +sbwtdio none` measures every selected
server also as `<server>-nobw`: at the start, a label is put on the server's own image with these environment
variables (no files change, no internet needed), and it is measured like any other server. The CSV says which
variant a row is (`Variant`). `VARIANT_ORDER=separate` (default) measures, within each repeat, all servers as
built, then all of the first variant, and so on; which group goes first rotates from repeat to repeat, so every
group runs early and late equally often. `VARIANT_ORDER=mixed` shuffles servers and variants together.
`tests/variant_flags_check.sh` checks that every server's BEAM receives `ERL_FLAGS`.

**Images must be up to date.** `make run` does not build images (`make build` does, online). Before anything
starts it stops if an image is missing, or older than a file of its recipe folder (rebuild with `make build`).

**What to measure.** A name in `SERVERS` is a server folder (its place, `static/`, `dynamic/` or
`websocket/`, gives its type), or `TYPE:IMAGE` for an image built on this machine without a folder (its
port is read from the image). `BENCHMARKS_DIR` sets the folder searched (default `benchmarks/`). Every name
must exist and every image must be built, or the measurement stops before it starts. A type or names on the
command line (`make run-static ...`) take precedence over `MEASURE` and `SERVERS`.

Without `CONFIG` the framework behaves as before: one pass, no waiting between runs, machine settings
untouched.

With a config, a measurement:

1. **Applies the machine settings** (CPU governor, turbo off, other Docker containers stopped, and
   optionally a fixed screen brightness, keyboard light off, Wi-Fi and Bluetooth off), checks that they
   took effect, and refuses to measure if they did not. They are restored at the end, also after an
   error or Ctrl-C. Each one can be `unchanged`, for machines you do not control.
2. **Keeps the machine awake.** Sleep and lid-close suspend are blocked while it runs. A laptop must run
   on its charger (`ON_BATTERY`): by default it waits for the charger before measuring, and a run during
   which the charger was unplugged is invalid and measured again (point 6).
   It also stops cleanly (resumable) before the disk fills up: below 2 GB free (`BENCH_MIN_FREE_GB`).
   Kept raw data takes about 45 KB per second of measuring, a few GB for a full campaign.
3. **Measures the resting state** of the machine (CPU temperature and CPU use) after a settle period.
4. **Repeats every measurement** `REPEATS` times. Each repeat is a full pass over all servers in a
   shuffled order (`SHUFFLE`, `SHUFFLE_SEED`), so slow drift such as heat is spread evenly.
5. **Waits until the machine is ready before every run**: CPU temperature back within a margin of the
   resting temperature, CPU use within a margin of the resting use, no thermal throttling, on the
   charger, CPU not capped below its expected speed. It checks again after the server has started, right
   before the load, because starting a server warms the CPU. `READY_ON_TIMEOUT` decides what happens if
   the machine does not become ready (keep waiting, stop, or measure and record why).
6. **Watches the machine during every load** (`tools/load_conditions.py`, the same for every server): the
   CPU speed limit and speed, throttling and the charger, every 0.5 s. A run that broke one of the rules
   above at any moment of its load is invalid: kept in `invalid_runs.csv` with its values and reason, not
   added to the results, and measured again after the readiness check (`INVALID_RUN_RETRIES`, default 3).
   The end of the run lists the invalid runs per server.
7. **Optionally warms the server up and measures it idle** (`WARMUP_SECONDS`, `IDLE_SECONDS`, both off
   by default). Order: start, warm-up (not measured), readiness check, idle window, load window.
8. **Records failed measurements** in `failures.csv` and continues; `FAILURES_STOP_AFTER` decides when
   to stop (1 = at the first failure).
9. **Writes statistics per configuration** at the end.

### Before leaving a long measurement alone

Connect the charger and close other programs. The minimal profile dims the screen and switches the
keyboard light, Wi-Fi and Bluetooth off (`ENV_SCREEN_BRIGHTNESS`, `ENV_KEYBOARD_LIGHT`, `ENV_WIFI`,
`ENV_BLUETOOTH`); otherwise leave them alone for the whole run. Their state is recorded at the start and end in `metadata.json`. They do not
affect the container's energy, which is its share of the CPU's power, but they do affect the whole
machine's energy (`Host Energy (J)`). The desktop may still dim or switch off the screen by itself.

### What a results folder contains

| File | Content |
|---|---|
| `static/`, `dynamic/`, `websocket/` `*.csv` | One row per run, in six blocks (below) |
| `<family>/summary.csv`, `*_summary.csv` | Per configuration: n, mean, sd, 95% confidence interval, median, Q1, Q3, IQR, min, max, CV%, and the energy mean after the IQR and Hampel outlier rules |
| `metadata.json` | How the measurement was made: framework version, Scaphandre (program and package version), Docker, Python, OS, kernel, CPU, memory, all settings, machine state at the start and end (governor, turbo, charger, battery, screen brightness, Wi-Fi, Bluetooth, temperature), and the exact ID of every image measured |
| `bench.config`, `bench.config.resolved` | The config used, and every setting with the value actually used (defaults included) |
| `schedule.txt` | Shuffle seed and the order of the servers in every pass |
| `progress.txt` | Finished measurements, used to resume |
| `failures.csv` | Failed measurements and why (only if any failed) |
| `raw/` | Scaphandre's raw power logs (plain JSON) with the load and idle windows of each run (`RAW_DATA=keep`) |

### Watching the progress

Before every measurement the run prints a short panel:

```
──────────────────────────────────────────────────────────────────────────
 static · repeat 2 of 5 · measurement 248 of 1210 (20% done)  ██████░░░░░░░░░░░░░░░░░░░░░░
 Time    spent 9h41m · left ~28h10m · done around Sat 04 Oct 14:20
 Now     st-elixir-cowboy-1-20-4-nobw · level 5/11 · 20000 requests · server 3 of 22 in this repeat
 Last    st-gleam-mist-1-19-0 · 80,000 requests · load 98.1 s · 816 req/s · container 211.4 J · machine 977 J · ok
 Health  no failures · CPU 41 °C · disk 4.9 GB free
──────────────────────────────────────────────────────────────────────────
```

The time left is estimated from the measurements already done (HTTP: overhead plus seconds per request,
fitted to the finished runs; WebSocket: mean per test family); until three are done it uses rough values
from the pilot and says so. The panel is printed only between measurements, plus one line per minute
during a long load: drawing on the screen costs CPU on the measured machine, so the output stays sparse.
`make status` prints the same panel on demand, from any terminal, and says whether the measurement is
running, finished or stopped. `VERBOSE=1` (`make run CONFIG=my.config VERBOSE=1`) shows every detail of
every measurement.

### Stopping and resuming

Ctrl-C first asks whether to stop, so an accidental one does not end a long campaign: `y` stops (the
machine settings are restored and the command to continue is printed); anything else, or no answer
within 30 s, continues, and the running measurement is not disturbed while it asks. A second Ctrl-C while
it asks stops at once; without a terminal (e.g. started in the background), a shutdown or `kill`, it
stops at once (`BENCH_STOP_CONFIRM_SECONDS=0` always stops at once). The restore itself cannot be cut
short by Ctrl-C. The machine settings are restored however the run ends; only `kill -9`, a crash or a
power loss leave them changed, and then the next run that would change them refuses to start and prints
the restore command.

Starting `make run CONFIG=...` again for a config that has an unfinished measurement asks: `c` continues
it in its folder, `n` starts from zero (the old folder is kept but marked abandoned: never resumed, skipped
by the graph window), `s` stops; no answer within 30 s, or no terminal, continues. A full campaign can take
days; after Ctrl-C, a crash or a reboot:

```bash
make resume                            # the most recent unfinished measurement
make resume RESUME=results/<folder>    # a specific one
```

This continues in the same folder with the same config, arguments and shuffle order, and skips the
measurements that already finished. One measurement is one server at one load level in one repeat. The
measurement that was running when it stopped is done again from its start, with a fresh container (its
energy must come from one unbroken recording); nothing of it reaches the CSV, and its half-written raw
log is moved to `raw/incomplete/`. Ctrl-C, a shutdown or `kill` remove the running container and stop
Scaphandre before the run ends.

A measurement is only continued with the tools it started with. If the framework (git commit),
Scaphandre, Docker, Python, OS, kernel, CPU, memory, a server image, or the config copies in the folder
changed in between, resume refuses and lists what changed, because results made with different tools
must not share one folder.
`RESUME_ANYWAY=1 make resume ...` continues anyway and records the differences in `metadata.json`.

### Reproducing a measurement

```bash
make reproduce FROM=results/<folder>
```

This makes the measurement again in a new folder, with every setting it used (`bench.config.resolved`,
shuffle seed included) and its original arguments. It prints what is different from the original
(framework, Scaphandre, Docker, OS, kernel, CPU, images) and which config the original used, and records
the differences in the new `metadata.json` together with the folder it reproduces. Server images are rebuilt from their Dockerfiles, so a base image
that received updates under the same name gives a different image ID; this is reported, not hidden.

### Recalculating energy from the raw logs

```bash
python3 tools/scaphandre_energy.py recompute results/<folder>/raw/<run>.json   # one run
python3 tools/recompute_results.py results/<folder> --reason "why"             # a whole measurement
```

The second rewrites the container energy in every CSV of the folder, keeps the old CSVs in
`superseded/<time>/`, rebuilds the summaries and notes it in `metadata.json`. Nothing is measured again.

### Energy calculation

Container energy is Scaphandre's power of the server container's processes, summed per sample and
integrated over exactly the load window (500 ms sampling by default, `SCAPH_STEP_MS`). Each process is
counted once: Scaphandre also lists the threads of a multi-threaded process (such as the BEAM's
schedulers) next to the process, whose entry already contains them, because Linux reports a process's
CPU time as the sum of its threads. Separate processes (Nginx or Apache workers) are all counted. During
a run, `/proc/<id>/status` tells them apart (Tgid equal to the ID = process) and the result is saved
in the run's `.window.json`, so recalculation is exact too. Entries that cannot be classified (logs from
before 2026-10-04, threads that ended early) use a fallback rule; `.window.json` says which was used
(`thread_handling`: `tgid`, `tgid+fallback` or `fallback`).
The container is found by its cgroup, not by process name, so any server works, whatever its language.
The HTTP client keeps one connection per worker (`HTTP_CONNECTION=reuse`); `per-request` reproduces the
client of earlier releases.

### The measurement CSV

One row per run. Every measured value says whose it is: `Container …` (the server's container),
`Host …` (the whole machine), `Energy …` (the energy sampling). The columns come in six blocks
(defined once in `tools/csv_columns.py`):

| Block | Columns |
|---|---|
| 1. What was measured | Container Name, Variant, Repeat, Session (1 = first start, 2 = after the first resume, ...), Measured At (UTC), then the workload (HTTP: Type, Total Requests, HTTP Max Workers, HTTP Connection Mode; WebSocket: Test Type, Pattern, Num Clients, Message Size (KB), Rate, Bursts, Interval, Duration) |
| 2. Performance | HTTP: Successful/Failed Requests, Execution Time (s), Requests/s; WebSocket: Total/Successful/Failed Messages, Execution Time (s), Messages/s, Throughput (MB/s), Avg/Min/Max Latency (ms) |
| 3. Container | Container CPU Limit (`none` = may use every host CPU), Container Energy (J), Container Avg Power (W), Container Avg/Peak/Total CPU, Container Avg/Peak/Total Mem |
| 4. Host | Host CPUs, Host Energy (J), Host Avg Power (W), Host CPU Temp Start/End (C), Host Throttled (ms) |
| 5. Idle | Idle Time (s), Container Idle Energy (J), Container Idle Avg Power (W), Host Idle Avg Power (W) |
| 6. How the run went | Warm-up (s), Waited Before Start/Load (s), Ready Check, Energy Samples, Energy Sampling Step (ms), Energy Window Coverage, Raw Log (the run's kept Scaphandre log, relative to the results folder) |

Renamed in this version (files of earlier releases are read under the new names by the statistics and the GUI;
they are never rewritten):

| Earlier | Now |
|---|---|
| Total Energy (J), Avg Power (W) | Container Energy (J), Container Avg Power (W) |
| Avg/Peak/Total CPU, Avg/Peak/Total Mem | Container Avg/Peak/Total CPU, Container Avg/Peak/Total Mem |
| Num CPUs | Host CPUs |
| CPU Temp Start/End (C), Throttled (ms) | Host CPU Temp Start/End (C), Host Throttled (ms) |
| Idle Energy (J), Idle Avg Power (W), Idle Host Avg Power (W) | Container Idle Energy (J), Container Idle Avg Power (W), Host Idle Avg Power (W) |
| Samples, Sampling Step (ms), Window Coverage | Energy Samples, Energy Sampling Step (ms), Energy Window Coverage |

### Graphs

`make gui` (or `make graph`) opens the graph window. Load a results folder (summary files are skipped: the
window computes the same statistics). Scope shows the container's values, the host's, or all. Repeats draws
the repeated runs as median and quartiles, mean and 95% confidence interval, every run, or one repeat only.
Statistics shows the table per server and load (the numbers of `summary.csv`). Run detail shows how one run
went: power over time from its raw log, with the load and idle periods, and its conditions.

### Checking a setup

```bash
python3 tools/check_environment.py                 # read-only report of the machine state
bash tests/real_sensor_check.sh                    # one short measurement with the real sensor
bash tests/config_check.sh                         # a small controlled campaign, checked end to end (sudo)
srv/bin/python -m unittest tests/test_changes.py   # unit tests, no sudo needed
```

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

Each directory containing a `Dockerfile` under `benchmarks/` is one benchmark. The **directory name** is the Docker image name (use unified naming: `<type>-<language>-<framework>-<version>`, e.g. `st-erlang-cowboy-27`). Type is inferred from the path (`benchmarks/websocket/...` → WebSocket test). See [docs/MINIMAL_BASES_AND_UNIFICATION.md](docs/MINIMAL_BASES_AND_UNIFICATION.md) for base images and Dockerfile structure.

## Adding a Server

Any server in any language can be measured. The server contract is the same for every language:
an image is a **self-contained bundle** that runs **only the server**.

- **Bundle:** the program *and its runtime* live under `/app` (a BEAM release with its own ERTS, a
  jar with its own Java runtime from `jlink`, a Go binary, ...), started by `/start.sh`, which
  finds them under `$APP_DIR` (default `/app`). Nothing has to be installed on the host.
- **Port:** listen on the port given in the environment variable `PORT` (default 8001 when it is
  unset), and `EXPOSE` the same port in the Dockerfile. The framework sets `PORT` to the exposed
  port and maps host port 8001 to it.
- **Runtime options from the environment:** the runtime's own variable (`ERL_FLAGS` for the BEAM,
  `JAVA_TOOL_OPTIONS` for the JVM) must reach it, so `VARIANTS` can change settings without
  touching the server. An image can name the variables it reads (`LABEL wseb.options="JAVA_TOOL_OPTIONS"`);
  a variant that sets another variable is then skipped for it (e.g. no busy-waiting variant for Java).
- **Only the server runs:** start it the way it is deployed (a release or `java -jar`, not a build
  tool such as `mix`, `gleam`, `rebar3`, Maven or Gradle), with no helper services and no
  keep-alive loops. BEAM servers run without a node name, so no `epmd` starts. A system that
  really needs a helper (e.g. a RabbitMQ cluster needs `epmd`) runs it inside the same image, so
  its energy is counted with the system, not hidden. The processes found in the server's box
  (container or scope) are recorded with every run (CSV column `Server Processes`), and known
  helpers are reported.
- **HTTP** (`static/`, `dynamic/`): answer `GET /` with status 200, and should support HTTP/1.1
  keep-alive (the client reuses connections).
- **WebSocket** (`websocket/`): accept a WebSocket on `/ws` and echo every message back.
- **Same OS as the host, for native mode:** natively the bundle still uses the host's system
  libraries (glibc, OpenSSL), so the image must be built on the host's OS and version (Debian 13
  here: `debian:trixie-slim`). Native mode (`DEPLOY`, `tools/native_server.py`) copies `/app` and
  `/start.sh` out of the image and runs the same script without Docker, in a systemd user scope;
  it refuses an image whose OS differs from the host's before anything starts.

Servers built before this contract listen on port 80 and ignore `PORT`; they keep working, because
the framework maps host port 8001 to whatever port the Dockerfile exposes.

Steps:

1. Create `benchmarks/<type>/<lang>/<framework>/<container>/` with a `Dockerfile`.
2. Follow the contract above: bundle under `/app`, `/start.sh` with `$APP_DIR`, `PORT` (default 8001)
   and `EXPOSE 8001`, only the server running. Ensure ulimit 100000 (health check enforces this).
3. Run `make build` → `make check-health` → `make run-super-quick`.

## Commands

| Task           | Command                         |
|----------------|---------------------------------|
| Build          | `make build`                    |
| Health check   | `make check-health` (no build; use after `make build`) |
| Container test | `make test` (build + health check; run before long `make run`) |
| Benchmarks     | `make run-quick`, `make run-all`|
| Graphs         | `make graph`                    |
| Clean results  | `make clean-results`            |
| Free disk space | `make tidy`: lists old server images, Docker build cache, stale native copies, empty or abandoned result folders, and deletes each group only after a yes (never results; `TIDY_ARGS=--dry-run` only lists). Runs delete their stale native copies themselves (`NATIVE_COPIES=prune`) |
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
| [docs/CONFIGURATION_PARITY.md](docs/CONFIGURATION_PARITY.md) | Canonical values (acceptors, max_connections, base image) |
| [docs/MINIMAL_BASES_AND_UNIFICATION.md](docs/MINIMAL_BASES_AND_UNIFICATION.md) | Unified Dockerfile stages and naming |
| [docs/BENCHMARKS_AUDIT.md](docs/BENCHMARKS_AUDIT.md) | Current BEAM benchmark set (34 containers) |
| [docs/RESULTS.md](docs/RESULTS.md) | CSV format, parameters, visualization |
| [docs/EXTENDING.md](docs/EXTENDING.md) | Adding new benchmark types (gRPC, etc.) |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Setup, health, Docker, energy, ulimit |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Version history |

## Energy Measurement

Scaphandre attributes energy by container. On systems where it reports `container: null` (e.g. cgroups v2, Debian 13), the scripts use a **cgroup fallback** (matching `/proc/<pid>/cgroup` to the container ID). See [Scaphandre issue #420](https://github.com/hubblo-org/scaphandre/issues/420). Ensure Scaphandre runs on the host with `sudo`. Debug: `python tools/debug_scaphandre_docker.py --server_image <image> --duration 10`.

## Troubleshooting

- **Setup fails**: `sudo apt install python3-venv`; if `srv/` is broken, `make clean-env && make setup`
- **Health check fails**: Check `docker logs health-check-<name>`, ensure port 8001 is free, verify ulimit inside container
- **"Container health check failed" during long runs**: BEAM containers may need more time to boot. Set `MEASURE_STARTUP_WAIT=25 MEASURE_HEALTH_RETRIES=30` before `make run` (or use higher values). The script will also print container logs on failure.
- **Avoid late failures**: Before a long benchmark run, use **`make test`** to build all images (log in `logs/build_*.log`) and run a quick health check on every container (log in `logs/test_*.log`). If the test passes, you can start `make run` with confidence.
- **Energy 0.00 J**: See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md#energy-measurement-000-j-container-not-found)
- **ulimit errors**: Increase system/Docker limits; health check requires 100000

Full troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## License

MIT. See `LICENSE`.
