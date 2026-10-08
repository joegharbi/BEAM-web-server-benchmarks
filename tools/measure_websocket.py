"""Measure one WebSocket server's energy (echo burst or stream): the measuring core
(tools/measure_core.py) with the websocket workload (tools/plugins/workload/websocket.py).

  python3 tools/measure_websocket.py --server_image ws-erlang-cowboy-29-1-1 --port_mapping 8001:8001 --pattern burst --clients 50

For real measurements: make run CONFIG=bench.config (machine settings, readiness checks, repeats).
"""
import measure_core
import measure_failure

if __name__ == "__main__":
    measure_failure.run(lambda: measure_core.main(__file__, "websocket",
                                                  "Measure WebSocket server energy with Scaphandre in Docker (echo burst/stream)"))
