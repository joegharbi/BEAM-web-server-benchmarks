"""Deploy plugins: where the server runs. Each module has a class `Plugin(Deploy)`."""


class Deploy:
    """One server, started from its image.

    `name` is the measurement's name (the CSV's Container Name); `image` the image it comes from.
    """
    label = ""                 # how its start is shown, e.g. "Docker start"

    def __init__(self, image, name, port_mapping, network, docker_path):
        self.image, self.name, self.port_mapping, self.network, self.docker = image, name, port_mapping, network, docker_path

    def start(self):
        """Start the server (registered with measure_failure, so a failure stops it)."""
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def log_tail(self, lines):
        """The server's last output lines, for a failed health check."""
        raise NotImplementedError

    def energy_id(self):
        """What Scaphandre's processes are matched by (container ID, scope name); None = by name."""
        raise NotImplementedError

    def collect_stats(self, stop_event, interval=0.5):
        """CPU (% of one core) and memory (MB) every `interval` s until stop_event: two dicts
        {avg, peak, total}."""
        raise NotImplementedError

    def cpu_limit(self):
        """The CPU limit the server ran under (CSV column Container CPU Limit)."""
        raise NotImplementedError

    def box(self):
        """The server's cgroup folder: the processes found there are recorded (Server Processes)."""
        raise NotImplementedError
