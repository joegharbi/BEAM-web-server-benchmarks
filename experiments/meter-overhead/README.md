# Meter overhead: does Scaphandre change what it measures?

## Why

In the echo campaigns (2026-10-16/17), only the C echo server in the nosleep test (10,000 clients at once, 5
rounds) made Scaphandre fall behind: a reading every 5–9 s instead of every 0.5 s, in all 20 such runs and in no
other of the 2,900 runs. C starts one OS thread per connection, about 2,000 new threads per second there; Erlang
and Java keep a handful of threads. During those runs the laptop drew 1–1.8 W more than with the other servers,
although C used the least CPU, and Scaphandre's report about its own consumption implied up to 8 CPUs (normally
0.2), which cannot be right. Whether the meter itself raises the laptop's energy, and by how much, can only be
shown by measuring the same test without it.

## Design

| | |
|---|---|
| Question | Does running Scaphandre raise the laptop's energy during the load, and for which server? |
| Conditions | meter **on** (Scaphandre every 0.5 s, as in the campaigns) vs **off** (no Scaphandre) |
| Measured in both | the laptop's energy over exactly the load window, from the RAPL psys counter (the one Scaphandre reads), read every 0.1 s by `psys_sampler.py` in both conditions |
| Also recorded | Scaphandre's real CPU time (kernel counter of its process); the server's exact CPU time; load time; failed messages; Scaphandre's report about itself (on) |
| Servers | Erlang with busy-waiting on and off, Java, C (controls: Erlang and Java, which never stalled the meter) |
| Test | nosleep: 10,000 clients at once, 5 rounds, 15-byte message |
| Deploy | Docker and native |
| Repeats | 5, shuffled within each repeat (seed in metadata.json) |
| Machine | as the echo campaigns: minimal profile, CPU at 1800 MHz, turbo off, early port reuse for Docker (`ENV_TCP_TW_REUSE=1`), readiness gate before every run |
| Size | 4 servers × 2 meter states × 2 deploys × 5 repeats = 80 measurements, about 1.5 h |

Proof: if the controls show the same laptop power with the meter on and off, and C shows a clear difference, the
difference is caused by the meter together with C's way of handling connections. The comparison is power (W) over
the load, because load times may differ; energy (J) is reported too.

## Run and read

    ./srv/bin/python3 experiments/meter-overhead/run.py
    ./srv/bin/python3 experiments/meter-overhead/analyze.py results/<folder>

`run.py` asks for sudo once (machine settings, Scaphandre, the psys counter), restores the machine at the end or
on Ctrl-C, and writes `results/<time>/` (marked by `EXPERIMENT.txt`): `echo/<server>_meter-<on|off>.csv` (the
framework's usual rows; with the meter off the server energy is 0 and Host Energy is the psys counter),
`meter_overhead.jsonl` (per run: psys energy, load time, Scaphandre's CPU time), `samples.txt` (the 0.1 s
samples), `metadata.json`. `analyze.py` prints and writes `meter_overhead.json`: per server and deploy, the power
off → on, the extra watts with a bootstrap 95% range and the exact Mann-Whitney p, the energy, and Scaphandre's
CPU use, real vs self-reported.

The framework is not changed: `measure.py` is the framework's measuring core with one extra meter, `off`, added
only inside its own process.
