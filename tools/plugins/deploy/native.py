"""Deploy: the image's own program without Docker, in a systemd user scope (tools/native_server.py).

The scope name takes the place of the container ID: energy by its cgroup, stop, statistics.
"""
import measure_failure
import native_server
from plugins.deploy import Deploy


class Plugin(Deploy):
    label = "native start (no Docker)"

    def __init__(self, *args):
        super().__init__(*args)
        self.unit = native_server.unit_name(self.name)

    def start(self):
        measure_failure.started_native(self.unit)
        native_server.start(self.image, self.name, self.port_mapping.split(":")[0], self.docker)

    def stop(self):
        native_server.stop(self.unit)

    def log_tail(self, lines):
        return native_server.log_tail(self.unit, lines)

    def energy_id(self):
        return self.unit

    def collect_stats(self, stop_event, interval=0.5):
        return native_server.collect_stats(self.unit, stop_event, interval)

    def cpu_limit(self):
        return "none"

    def box(self):
        return native_server.cgroup_dir(self.unit)
