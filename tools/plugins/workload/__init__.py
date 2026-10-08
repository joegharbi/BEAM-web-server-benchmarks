"""Workload plugins: what load the server gets. Each module has a class `Plugin(Workload)`."""


class Workload:
    """One kind of load: its options, its health probe, the load itself and its CSV columns."""
    name = ""                  # in messages: "HTTP", "WebSocket"
    columns = []               # the CSV layout (tools/csv_columns.py)
    rate_unit = ""             # "req/s", "msg/s"
    idle_note = ""             # what the idle phase is without: "no requests"

    def add_arguments(self, parser):
        """This workload's command-line options (the shared ones are added by measure_core)."""

    def url(self, args):
        raise NotImplementedError

    def probe(self, url):
        """One health probe: True when the server answers correctly."""
        raise NotImplementedError

    def health_failure(self):
        """Why a server that never answered failed (measure_failure)."""
        raise NotImplementedError

    def warm_up(self, args, url):
        """Unmeasured load for args.warmup_s seconds."""

    def describe(self, args, url):
        """The load in one line, e.g. "300 GET -> http://localhost:8001/"."""
        raise NotImplementedError

    def run(self, args, url):
        """The measured load (blocks until it is done)."""
        raise NotImplementedError

    def progress(self, args):
        """How far the load is, for the heartbeat line."""
        raise NotImplementedError

    def counts(self):
        """(successful, total) operations of the load."""
        raise NotImplementedError

    def values(self, args, runtime):
        """This workload's CSV columns of the run (counts, rate, its parameters)."""
        raise NotImplementedError

    def index(self, args):
        """The workload entry of the results index (results_index.http_workload, ...)."""
        raise NotImplementedError

    def measurement(self, args):
        """The run in words, for invalid_runs.csv."""
        raise NotImplementedError

    def repeat_args(self, args):
        """This workload's options again, for each run of --repeat."""
        raise NotImplementedError

    def summary(self, args, runtime):
        """Extra lines of the full (not quiet) summary."""
        return []
