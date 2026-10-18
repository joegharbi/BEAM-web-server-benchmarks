# Analysis

Scripts that turn results folders into the numbers of a paper, so every number can be rebuilt.
They only read `results/<folder>` (the per-container CSVs and `manifest.jsonl`); they change nothing.

    ./srv/bin/python3 analysis/check.py results/<folder> ...
    ./srv/bin/python3 analysis/check_attribution.py results/<folder> ...
    ./srv/bin/python3 analysis/busy_wait_http.py results/<static folder> results/<dynamic folder>
    ./srv/bin/python3 analysis/stats.py results/<static folder> results/<dynamic folder>
    ./srv/bin/python3 analysis/busy_wait_websocket.py results/<websocket folder> results/<mist re-run folder>
    ./srv/bin/python3 analysis/echo_figures.py --new results/<echo-new> --rerun results/<docker nosleep re-run> --old results/<echo-old>
    ./srv/bin/python3 analysis/figures.py [--width one|two] [--format png|jpg|svg] [--dpi 300] results/<static> results/<dynamic> results/<websocket> results/<mist re-run>

| Script | Step | Answers |
|---|---|---|
| `check.py` | 1 | Is the folder complete? Same conditions in every run? Odd runs? Enough meter readings per run? |
| `busy_wait_http.py` | 2 | BEAM busy-waiting on vs off per server, deploy and load: server and machine energy, spread between repeats, CPU ratio, throughput |
| `stats.py` | 3 | Exact Mann-Whitney p, Cliff's delta and bootstrap 95% intervals per server; sign test across servers |
| `busy_wait_websocket.py` | 4 | Busy-waiting on vs off for WebSocket; only the streams are tested (the bursts are too short for the meter) |
| `table.py` | 5 | Table I of the paper (LaTeX): the busy-wait effect per workload and deploy, with ranges, CIs and spread |
| `figures.py` | 5 | The paper's figures (1-4 HTTP, 5 WebSocket streams) as images (one column 8.8 cm or two columns 18.1 cm, no title unless `--title`), `figures.json` with the same numbers for the web page, and `FIGURES.md` (what every image shows, draft caption; also stored inside PNG/SVG) |
| `echo_figures.py` | 6 | The echo figures (C, Java, Erlang TCP echo servers): busy-waiting on vs off per test (`echo1`), all servers per test (`echo2`, hollow where the meter fell behind), old vs new setup on the shared tests (`echo3`); `echo_figures.json`, `FIGURES.md`. `--rerun` replaces the runs it measured again |
| `check_attribution.py` | 1b | Independent check of the server energy from the raw logs: recomputed = CSV, no double counting of threads, no missed server processes, no gaps, never above the platform |
| `data.py` | | Shared: reading rows, server name, on/off, load level |

Busy-wait paper (MIPRO, v1.6.0/v1.6.1): static `results/2026-10-08_231638`, dynamic `results/2026-10-10_044609`,
WebSocket `results/2026-10-11_101743`, mist without compression `results/2026-10-11_203912`.

Known limit (results up to v1.6.1; fixed in v1.6.2): `Container Total CPU (%*s)` assumed one CPU sample every 0.5 s, but `docker stats` is
slower than that, so absolute CPU time is too low for Docker and inexact natively. Only on/off ratios within one
deploy are used. Energy is not affected (Scaphandre timestamps, coverage 1.0).
