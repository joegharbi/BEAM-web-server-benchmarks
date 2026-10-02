# Campaigns

Every server is measured twice, as built and as `<server>-nobw` (BEAM scheduler busy-waiting off:
`+sbwt none +sbwtdcpu none +sbwtdio none`), in one shuffled order. Why: experiments/busy-wait/.
Each part is its own results folder: it can be resumed (`make resume`), checked and backed up on its own.

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
