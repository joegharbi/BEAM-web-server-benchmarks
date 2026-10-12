# Analysis

Scripts that turn results folders into the numbers of a paper, so every number can be rebuilt.
They only read `results/<folder>` (the per-container CSVs and `manifest.jsonl`); they change nothing.

    ./srv/bin/python3 analysis/check.py results/<folder> ...
    ./srv/bin/python3 analysis/busy_wait_http.py results/<static folder> results/<dynamic folder>
    ./srv/bin/python3 analysis/stats.py results/<static folder> results/<dynamic folder>
    ./srv/bin/python3 analysis/busy_wait_websocket.py results/<websocket folder> results/<mist re-run folder>

| Script | Step | Answers |
|---|---|---|
| `check.py` | 1 | Is the folder complete? Same conditions in every run? Odd runs? Enough meter readings per run? |
| `busy_wait_http.py` | 2 | BEAM busy-waiting on vs off per server, deploy and load: server and machine energy, spread between repeats, CPU ratio, throughput |
| `stats.py` | 3 | Exact Mann-Whitney p, Cliff's delta and bootstrap 95% intervals per server; sign test across servers |
| `busy_wait_websocket.py` | 4 | Busy-waiting on vs off for WebSocket; only the streams are tested (the bursts are too short for the meter) |
| `data.py` | | Shared: reading rows, server name, on/off, load level |

Busy-wait paper (MIPRO, v1.6.0/v1.6.1): static `results/2026-10-08_231638`, dynamic `results/2026-10-10_044609`,
WebSocket `results/2026-10-11_101743`, mist without compression `results/2026-10-11_203912`.

Known limit (results up to v1.6.1; fixed in v1.6.2): `Container Total CPU (%*s)` assumed one CPU sample every 0.5 s, but `docker stats` is
slower than that, so absolute CPU time is too low for Docker and inexact natively. Only on/off ratios within one
deploy are used. Energy is not affected (Scaphandre timestamps, coverage 1.0).
