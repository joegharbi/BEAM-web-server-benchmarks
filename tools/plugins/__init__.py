"""Plugins of the measuring core (tools/measure_core.py), one module per plugin, found by its file name.

  deploy/    where the server runs:      container (Docker), native (systemd user scope)
  workload/  what load it gets:          http, websocket
  meter/     how its energy is measured: scaphandre

Every measurement takes the same steps in the same order (measure_core.measure); only these three
parts differ. A new one (e.g. deploy/kubernetes.py, workload/kafka.py, meter/rapl.py) is one file with
a class `Plugin` that has the methods of the base class in its folder's __init__.py; nothing else
changes, and `--deploy` lists it by itself.
"""
import importlib
import os
import pkgutil


def names(kind):
    """The plugins of one kind (deploy, workload, meter), by file name."""
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), kind)
    return sorted(m.name for m in pkgutil.iter_modules([folder]) if not m.name.startswith("_"))


def load(kind, name):
    """The Plugin class of plugins/<kind>/<name>.py."""
    if name not in names(kind):
        raise ValueError(f"no {kind} plugin '{name}' (available: {', '.join(names(kind))})")
    return importlib.import_module(f"plugins.{kind}.{name}").Plugin
