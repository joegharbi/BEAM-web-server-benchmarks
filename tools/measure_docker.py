"""Measure one HTTP server's energy (static or dynamic pages): the measuring core
(tools/measure_core.py) with the http workload (tools/plugins/workload/http.py).

  python3 tools/measure_docker.py --server_image st-erlang-cowboy-29-1-1 --port_mapping 8001:8001 --num_requests 1000

For real measurements: make run CONFIG=bench.config (machine settings, readiness checks, repeats).
"""
import measure_core
import measure_failure

if __name__ == "__main__":
    measure_failure.run(lambda: measure_core.main(__file__, "http", "Measure web server energy with Scaphandre in Docker"))
