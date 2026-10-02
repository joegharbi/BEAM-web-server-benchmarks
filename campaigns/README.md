# Full campaign

Three parts, run one after the other. Each part is its own results folder: it can be resumed
(`make resume`), checked and backed up on its own before the next one starts.

| Part | Command | Measurements | Time (estimate) | Raw data (estimate) |
|---|---|---|---|---|
| Static HTTP | `make run CONFIG=campaigns/static.config` | 1,210 | ~37 h | ~2.9 GB |
| Dynamic HTTP | `make run CONFIG=campaigns/dynamic.config` | 1,210 | ~37 h | ~2.9 GB |
| WebSocket | `make run CONFIG=campaigns/websocket.config` | 1,200 | ~28 h | ~1.4 GB |

Estimates from the pilot (about 67 s per measurement on top of the load itself, about 800 requests
per second) and the WebSocket runs of May 2026 (about 400 s of load per server and pass).

Every server is measured twice, as built and as `<server>-nobw` (BEAM scheduler busy-waiting off:
`+sbwt none +sbwtdcpu none +sbwtdio none`), in one shuffled order. Why: see experiments/busy-wait/.
Before a part: at least 5 GB free on the disk. After a part: copy its results folder to a backup.
