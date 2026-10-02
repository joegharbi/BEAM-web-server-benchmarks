"""How a measurement tool ends when a measurement cannot be made.

Shared by measure_docker.py and measure_websocket.py. Exit codes, read by
run_benchmarks.sh:
  0  measured
  1  this measurement failed; the reason is written to $MEASURE_FAILURE_REASON_FILE
     (when set) and the campaign continues with the next measurement
  2  the readiness check timed out with READY_ON_TIMEOUT=stop; the campaign stops
  3  the setup is broken (e.g. Docker or Scaphandre missing); the campaign stops
"""
import logging
import os
import signal
import subprocess
import sys

logger = logging.getLogger()

MEASUREMENT_FAILED = 1
READINESS_STOP = 2
SETUP_BROKEN = 3

# What is running for the current measurement, so a failure can clean it up.
_running = {"container": None, "docker": None}


def started_container(name, docker_path):
    _running["container"], _running["docker"] = name, docker_path


def _cleanup():
    name, docker = _running["container"], _running["docker"]
    if name and docker:
        subprocess.run([docker, "rm", "-f", name], capture_output=True, text=True, check=False)
    subprocess.run(["sudo", "-n", "pkill", "-9", "scaphandre"], capture_output=True, text=True, check=False)


def fail(reason):
    """End the tool: this measurement failed for `reason`."""
    logger.error("Measurement failed: %s", reason)
    path = os.environ.get("MEASURE_FAILURE_REASON_FILE")
    if path:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(reason.replace("\n", " ").strip())
        except OSError:
            pass
    _cleanup()
    sys.exit(MEASUREMENT_FAILED)


def _terminated(signum, frame):
    """SIGTERM (a shutdown, `timeout`, `kill`): remove the container and stop Scaphandre, then exit at
    once. Waiting for the load to finish could take minutes, and without this both stay behind."""
    logger.error("Terminated (signal %s); removing the container and stopping Scaphandre", signum)
    _cleanup()
    os._exit(128 + signum)


def run(main):
    """Run a tool's main(); turn any unexpected error into a recorded failure."""
    signal.signal(signal.SIGTERM, _terminated)
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        _cleanup()
        raise
    except Exception as e:  # noqa: BLE001 - every error must end as a recorded failure
        fail(f"{type(e).__name__}: {e}")
