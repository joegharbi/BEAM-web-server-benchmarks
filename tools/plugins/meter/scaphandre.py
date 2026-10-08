"""Meter: Scaphandre's per-process power (RAPL), from its JSON log (tools/scaphandre_energy.py)."""
import logging
import os
import subprocess
import time

import load_phases
from plugins.meter import Meter
from scaphandre_energy import compute_window_energy, finish_raw, scaphandre_json_args, window_record

logger = logging.getLogger()


class Plugin(Meter):
    tool = "scaphandre"

    def __init__(self, output_json):
        super().__init__(output_json)
        self.path = subprocess.run(["which", "scaphandre"], capture_output=True, text=True, check=True).stdout.strip()
        self.process = None

    def prepare(self):
        subprocess.run(["sudo", "pkill", "-9", "scaphandre"], capture_output=True, text=True, check=False)
        time.sleep(2)  # Ensure OS releases resources

    def start(self):
        os.makedirs("output", exist_ok=True)
        self.process = subprocess.Popen(["sudo", self.path] + scaphandre_json_args(self.output_json),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(2)
        if self.process.poll() is not None:
            out, err = self.process.communicate(timeout=1)
            logger.error("Scaphandre failed to start (exit code %s).", self.process.returncode)
            if (err or "").strip():
                logger.error("Scaphandre stderr: %s", err.strip())
            if (out or "").strip():
                logger.error("Scaphandre stdout: %s", out.strip())
            raise RuntimeError("Scaphandre failed to start")

    def stop(self):
        self.process.terminate()
        self.process.wait(timeout=5)
        time.sleep(2)  # Ensure OS releases resources

    def energy(self, name, energy_id, t0, t1):
        return compute_window_energy(self.output_json, name, t0, t1, container_id=energy_id)

    def finish(self, name, energy_id, t0, t1, energy, idle, warmup_s):
        phases = load_phases.idle_fields(self.output_json, name, energy_id, idle, warmup_s)
        record = window_record(name, energy_id, t0, t1, energy)
        if idle:
            record["idle_start_epoch"], record["idle_end_epoch"] = idle
        return phases, finish_raw(self.output_json, record)
