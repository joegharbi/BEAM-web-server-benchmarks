"""Measure one TCP echo server's energy (connect, burst or stream): the measuring core
(tools/measure_core.py) with the echo workload (tools/plugins/workload/echo.py).

  python3 tools/measure_echo.py --server_image ec-erlang-pure-29-1-1 --port_mapping 8001:8001 --pattern connect --clients 100 --spacing 0.1

For real measurements: make run CONFIG=bench.config (machine settings, readiness checks, repeats).
"""
import measure_core
import measure_failure

if __name__ == "__main__":
    measure_failure.run(lambda: measure_core.main(__file__, "echo",
                                                  "Measure TCP echo server energy with Scaphandre (connect, burst or stream)"))
