"""Meter plugins: how a server's energy is measured. Each module has a class `Plugin(Meter)`."""


class Meter:
    """Records power over the whole measurement; the energy of a time window is worked out afterwards."""
    tool = ""                   # the program it needs on PATH (checked before anything starts)

    def __init__(self, output_json):
        self.output_json = output_json

    def prepare(self):
        """Before the server starts: nothing of an earlier run may still be recording."""

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def energy(self, name, energy_id, t0, t1):
        """The server's and the host's energy over [t0, t1] (dict, see scaphandre_energy.compute_window_energy)."""
        raise NotImplementedError

    def finish(self, name, energy_id, t0, t1, energy, idle, warmup_s):
        """After the energy is worked out: (the idle and warm-up CSV fields, the raw log's final path)."""
        raise NotImplementedError
