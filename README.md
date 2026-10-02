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
SERVERS=st-erlang-cowboy-28-4-3 static:my-nginx   # empty (default) = every server found in BENCHMARKS_DIR
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
server also as `<server>-nobw`: an image built at the start from the server's image plus these environment
variables, measured in the same shuffled order as the server itself, so both versions share the same
conditions. `tests/variant_flags_check.sh` checks that every server's BEAM receives `ERL_FLAGS`.

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
   which the charger was unplugged is recorded as failed.
   It also stops cleanly (resumable) before the disk fills up: below 2 GB free (`BENCH_MIN_FREE_GB`).
   Kept raw data takes about 45 KB per second of measuring, a few GB for a full campaign.
3. **Measures the resting state** of the machine (CPU temperature and CPU use) after a settle period.
4. **Repeats every measurement** `REPEATS` times. Each repeat is a full pass over all servers in a
   shuffled order (`SHUFFLE`, `SHUFFLE_SEED`), so slow drift such as heat is spread evenly.
5. **Waits until the machine is ready before every run**: CPU temperature back within a margin of the
   resting temperature, CPU use within a margin of the resting use, no thermal throttling, on the
   charger. It checks again after the server has started, right before the load, because starting a
   server warms the CPU. `READY_ON_TIMEOUT` decides what happens if the machine does not become ready
   (keep waiting, stop, or measure and record why).
6. **Optionally warms the server up and measures it idle** (`WARMUP_SECONDS`, `IDLE_SECONDS`, both off
   by default). Order: start, warm-up (not measured), readiness check, idle window, load window.
7. **Records failed measurements** in `failures.csv` and continues; `FAILURES_STOP_AFTER` decides when
   to stop (1 = at the first failure).
8. **Writes statistics per configuration** at the end.

### Before leaving a long measurement alone

Connect the charger and close other programs. The minimal profile dims the screen and switches the
keyboard light, Wi-Fi and Bluetooth off (`ENV_SCREEN_BRIGHTNESS`, `ENV_KEYBOARD_LIGHT`, `ENV_WIFI`,
`ENV_BLUETOOTH`); otherwise leave them alone for the whole run. Their state is recorded at the start and end in `metadata.json`. They do not
affect the container's energy, which is its share of the CPU's power, but they do affect the whole
machine's energy (`Host Energy (J)`). The desktop may still dim or switch off the screen by itself.

### What a results folder contains

| File | Content |
|---|---|
| `static/`, `dynamic/`, `websocket/` `*.csv` | One row per run: the columns of earlier releases, plus whole-machine energy, CPU temperature at the start and end of the load, throttling, readiness waits and result, and the warm-up and idle columns |
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
 Now     st-elixir-cowboy-1-19-5-nobw · level 5/11 · 20000 requests · server 3 of 22 in this repeat
 Last    st-gleam-mist-1-15-2 · 80,000 requests · load 98.1 s · 816 req/s · container 211.4 J · machine 977 J · ok
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

Ctrl-C stops the measurement, restores the machine settings and prints the command to continue. A full
campaign can take days; after Ctrl-C, a crash or a reboot:

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
python3 tools/scaphandre_energy.py recompute results/<folder>/raw/<run>.json
```

### Energy calculation

Container energy is Scaphandre's power of all processes and threads of the server container, summed
per sample and integrated over exactly the load window (500 ms sampling by default, `SCAPH_STEP_MS`).
The container is found by its cgroup, not by process name, so any server works, whatever its language.
The HTTP client keeps one connection per worker (`HTTP_CONNECTION=reuse`); `per-request` reproduces the
client of earlier releases.

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

Any server in any language can be measured. It only has to:

- **HTTP** (`static/`, `dynamic/`): answer `GET /` with status 200 on the port it exposes, and
  should support HTTP/1.1 keep-alive (the client reuses connections).
- **WebSocket** (`websocket/`): accept a WebSocket on `/ws` and echo every message back.

Steps:

1. Create `benchmarks/<type>/<lang>/<framework>/<container>/` with a `Dockerfile`.
2. Add `EXPOSE 80` (or your port). Ensure ulimit 100000 (health check enforces this).
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
