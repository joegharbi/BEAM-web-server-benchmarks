# Campaigns

Every server is measured twice, as built and as `<server>-nobw` (BEAM scheduler busy-waiting off:
`+sbwt none +sbwtdcpu none +sbwtdio none`). Order within each repeat (VARIANT_ORDER=separate, the default): all
servers as built, then all `-nobw`, shuffled within each group; which group goes first switches every repeat.
Why both: experiments/busy-wait/.
Each part is its own results folder: it can be resumed (`make resume`), checked and backed up on its own.

## Paper (MIPRO busy-wait paper: 4 HTTP load levels, Docker and native, CPU at 1800 MHz)

Every server as built and `-nobw` (not Java), each in Docker and natively (DEPLOY=container native).
WebSocket: burst and stream with 100 clients, 8 KB and 1 MB messages.

| Part | Command | Measurements | Time (estimate) | Disk (estimate) |
|---|---|---|---|---|
| Pilot (done 2026-10-08) | `make run CONFIG=campaigns/paper/pilot.config` | 40 | 1 h 13 min | small; timed every kind of step |
| Static HTTP | `make run CONFIG=campaigns/paper/static.config` | 1,000 | ~27 h | ~2.4 GB + ~1.2 GB native copies |
| Dynamic HTTP | `make run CONFIG=campaigns/paper/dynamic.config` | 1,000 | ~27 h | ~2.4 GB + ~1.2 GB native copies |
| WebSocket | `make run CONFIG=campaigns/paper/websocket.config` | 440 | ~10 h | ~0.5 GB + ~0.5 GB native copies |

Time, from the pilot (one measurement, overhead included): HTTP 5k / 20k / 40k / 80k requests 69 / 86 / 110 /
158 s (~800 requests/s); a WebSocket step ~78 s. About 64 h for the three parts. Native copies (each image's /app,
50-180 MB, one per server; its -nobw variant uses the same copy) stay for the next run while the image is
unchanged; NATIVE_COPIES=prune removes only copies whose image is gone or rebuilt. A part stops (resumable) when
less than 2 GB is free.

## Short (5 HTTP load levels: 5k, 10k, 20k, 40k, 80k)

| Part | Command | Measurements | Time (estimate) | Raw data (estimate) |
|---|---|---|---|---|
| Static HTTP | `make run CONFIG=campaigns/short/static.config` | 550 | ~16 h | ~1.3 GB |
| Dynamic HTTP | `make run CONFIG=campaigns/short/dynamic.config` | 550 | ~16 h | ~1.3 GB |
| WebSocket | `make run CONFIG=campaigns/short/websocket.config` | 1,200 | ~28 h | ~1.4 GB |

## Full (11 HTTP load levels: 5k, 8k, 10k, 15k, 20k, 30k ... 80k)

| Part | Command | Measurements | Time (estimate) | Raw data (estimate) |
|---|---|---|---|---|
| Static HTTP | `make run CONFIG=campaigns/full/static.config` | 1,210 | ~37 h | ~2.9 GB |
| Dynamic HTTP | `make run CONFIG=campaigns/full/dynamic.config` | 1,210 | ~37 h | ~2.9 GB |
| WebSocket | `make run CONFIG=campaigns/full/websocket.config` | 1,200 | ~28 h | ~1.4 GB |

The WebSocket part is the same in both (the published WebSocket workloads).

Estimates from the pilot (about 67 s per measurement on top of the load itself, about 800 requests
per second) and the WebSocket runs of May 2026 (about 400 s of load per server and pass).
Before a part: at least 5 GB free on the disk. After a part: copy its results folder to a backup.
`make status` shows the progress from any terminal.
