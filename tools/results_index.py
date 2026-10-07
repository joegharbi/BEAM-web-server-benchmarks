#!/usr/bin/env python3
"""The results index: what was measured, and where its data is, without guessing from names.

Two files, both only an index (the numbers stay in the CSVs and raw logs, so nothing can disagree):

  results/<run>/manifest.jsonl   one line per measurement, written the moment it is done (valid, or
                                 invalid and measured again): which server (and what it is: the image's
                                 wseb.* labels), variant, deploy, workload and its parameters, repeat,
                                 time, valid or why not, and where its CSV row and raw log are.
                                 One JSON object per line, flushed at once: a crash never breaks it.
  results/index.json             every measurement folder: config, status (running, finished,
                                 unfinished, abandoned), dates, machine, counts. Rebuilt from the
                                 folders at any time (make index), so it is never out of date for long.

The GUI, reports and scripts read these through runs(), measurements() and row().

  python3 tools/results_index.py index            rebuild results/index.json and list the runs
  python3 tools/results_index.py backfill FOLDER  write manifest.jsonl for a folder measured before it existed
"""
import csv
import datetime
import glob
import json
import os
import subprocess
import sys

SCHEMA = 1
MANIFEST = "manifest.jsonl"
CATALOG = "index.json"
FAMILIES = ("static", "dynamic", "websocket")
INVALID_FILE = "invalid_runs.csv"


# --- what a server is: the wseb.* labels of its image ---

def server_facts(image, docker_path="docker"):
    """{language, language_version, runtime, runtime_version, kind, framework, framework_version} from
    the image's wseb.* labels (Dockerfile); {} when the image is gone or has none."""
    try:
        out = subprocess.run([docker_path, "image", "inspect", "--format", "{{json .Config.Labels}}", image],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        labels = json.loads(out) if out and out != "null" else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        labels = {}
    skip = {"wseb.recipe", "wseb.options"}                     # build fingerprint, variant rule
    return {k[len("wseb."):]: v for k, v in sorted(labels.items()) if k.startswith("wseb.") and k not in skip}


def server_name(image, variant):
    """The server (its folder in benchmarks/) of an image: a variant image is <server>-<variant>."""
    return image[:-len(variant) - 1] if variant and image.endswith("-" + variant) else image


# --- writing: one line per measurement ---

def run_folder(output_csv):
    """The measurement folder of a CSV in it (results/<run>/<family>/x.csv), or None for a CSV
    elsewhere (a manual measure_docker.py run): those get no manifest."""
    folder = os.path.dirname(os.path.dirname(os.path.abspath(output_csv)))
    return folder if os.path.isfile(os.path.join(folder, "metadata.json")) else None


def _data_rows(path):
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return max(sum(1 for _ in csv.reader(fh)) - 1, 0)
    except OSError:
        return 0


def entry(folder, values, image, workload, csv_path, valid=True, reason=""):
    """The manifest line of one measurement (a dict)."""
    variant = values.get("Variant", "") or ""
    return {
        "schema": SCHEMA,
        "measured_at_utc": values.get("Measured At (UTC)", ""),
        "server": server_name(image, variant),
        "image": image,
        "name": values.get("Container Name", ""),            # the CSV's Container Name (with -native etc.)
        "variant": variant,
        "deploy": values.get("Deploy", "") or "container",
        "repeat": values.get("Repeat", ""),
        "session": values.get("Session", ""),
        "facts": server_facts(image),
        "workload": workload,
        "valid": valid,
        "reason": reason,
        "csv": os.path.relpath(os.path.abspath(csv_path), folder),
        "csv_row": _data_rows(csv_path),                       # 1-based data row: the one just written
        "raw_log": values.get("Raw Log", ""),
    }


def append(folder, line):
    """Append one manifest line and flush it to disk at once."""
    with open(os.path.join(folder, MANIFEST), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def record(output_csv, values, image, workload, valid=True, reason="", csv_path=None):
    """After a measurement's row is written (valid: its CSV; invalid: invalid_runs.csv), add its line.
    Never stops a measurement: a problem here is only logged."""
    folder = run_folder(output_csv)
    if not folder:
        return
    try:
        append(folder, entry(folder, values, image, workload, csv_path or output_csv, valid, reason))
    except Exception as e:                                     # the index must never cost a measurement
        print(f"results index: could not record {values.get('Container Name', '')}: {e}", file=sys.stderr)


def http_workload(family, requests, workers, connection):
    return {"kind": "http", "family": family, "requests": requests, "workers": workers, "connection": connection}


def websocket_workload(test, pattern, clients, size_kb, rate=None, bursts=None, interval=None, duration=None):
    w = {"kind": "websocket", "family": "websocket", "test": test, "pattern": pattern, "clients": clients,
         "size_kb": size_kb}
    extra = {"rate_per_s": rate, "duration_s": duration} if pattern == "stream" else \
            {"bursts": bursts, "interval_s": interval} if pattern == "burst" else {}
    w.update({k: v for k, v in extra.items() if v is not None})
    return w


# --- reading ---

def measurements(folder, facts=True):
    """The manifest lines of a measurement folder; for a folder measured before manifests existed, the
    same lines made from its CSVs (see backfill; facts=False: without asking Docker for the labels)."""
    path = os.path.join(folder, MANIFEST)
    if not os.path.isfile(path):
        return from_csvs(folder, facts)
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:                             # a line cut by a crash: skip it
                    continue
    return out


def row(folder, m):
    """The CSV row (a dict) a manifest line points to, or {}."""
    try:
        with open(os.path.join(folder, m["csv"]), newline="", encoding="utf-8") as fh:
            for i, r in enumerate(csv.DictReader(fh), 1):
                if i == m["csv_row"]:
                    return r
    except (OSError, KeyError):
        pass
    return {}


def _json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def status(folder, meta):
    if "abandoned_at_utc" in meta or os.path.exists(os.path.join(folder, ".abandoned")):
        return "abandoned"
    if "finished_at_utc" in meta:
        return "finished"
    if os.path.exists(os.path.join(folder, ".running")):
        return "running"
    return "unfinished"


def run_summary(folder):
    """One catalog entry for a measurement folder, or None when it is not one."""
    meta = _json(os.path.join(folder, "metadata.json"))
    if not meta:
        return None
    plan = _json(os.path.join(folder, "plan.json"))
    ms = measurements(folder, facts=False)
    machine = meta.get("software_and_machine", {})
    settings = meta.get("settings", {})
    return {
        "folder": os.path.basename(folder),
        "config": plan.get("name", "") or os.path.basename(settings.get("config_file", "") or ""),
        "status": status(folder, meta),
        "started_at_utc": meta.get("started_at_utc", ""),
        "finished_at_utc": meta.get("finished_at_utc", ""),
        "planned": plan.get("total", ""),
        "measured": sum(1 for m in ms if m.get("valid")),
        "invalid": sum(1 for m in ms if not m.get("valid")),
        "repeats": plan.get("repeats", settings.get("repeats", "")),
        "families": sorted({m.get("workload", {}).get("family", "") for m in ms} - {""}),
        "servers": sorted({m.get("server", "") for m in ms} - {""}),
        "deploys": sorted({m.get("deploy", "") for m in ms} - {""}),
        "variants": sorted({m.get("variant", "") for m in ms} - {""}),
        "machine": {k: machine.get(k, "") for k in ("cpu_model", "logical_cpus", "memory_gb", "os", "kernel")},
        "framework_version": machine.get("framework_version", ""),
        "config_sha256": meta.get("config_sha256", ""),
        "manifest": os.path.isfile(os.path.join(folder, MANIFEST)),
    }


def catalog(results_root="results"):
    """Every measurement folder under results_root, oldest first."""
    runs = [s for s in (run_summary(os.path.dirname(p))
                        for p in sorted(glob.glob(os.path.join(results_root, "*", "metadata.json")))) if s]
    return {"schema": SCHEMA, "updated_at_utc": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds"), "runs": runs}


def write_catalog(results_root="results"):
    """Rebuild results/index.json (written to a temporary file first, then renamed: never half written)."""
    data = catalog(results_root)
    os.makedirs(results_root, exist_ok=True)
    path = os.path.join(results_root, CATALOG)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return path, data


def runs(results_root="results"):
    """The catalog's runs (from index.json when it is there, else built now)."""
    data = _json(os.path.join(results_root, CATALOG))
    return data.get("runs") if data.get("schema") == SCHEMA else catalog(results_root)["runs"]


# --- folders measured before manifests existed ---

def from_csvs(folder, with_facts=True):
    """Manifest lines made from a folder's CSV rows (older measurements): the same fields, with the
    workload read from the row; server facts from the image when it still exists (with_facts)."""
    out = []
    facts = {}
    for family in FAMILIES:
        for path in sorted(glob.glob(os.path.join(folder, family, "*.csv"))):
            base = os.path.basename(path)
            if base == "summary.csv" or base.endswith("_summary.csv"):
                continue
            with open(path, newline="", encoding="utf-8") as fh:
                for i, r in enumerate(csv.DictReader(fh), 1):
                    name = r.get("Container Name", "")
                    deploy = r.get("Deploy", "") or "container"
                    image = name[:-len("-native")] if deploy == "native" and name.endswith("-native") else name
                    if image not in facts:
                        facts[image] = server_facts(image) if with_facts else {}
                    variant = r.get("Variant", "") or ""
                    if family == "websocket":
                        workload = websocket_workload(r.get("Test Type", ""), r.get("Pattern", ""), r.get("Num Clients", ""),
                                                      r.get("Message Size (KB)", ""), r.get("Rate (msg/s)") or None,
                                                      r.get("Bursts") or None, r.get("Interval (s)") or None,
                                                      r.get("Duration (s)") or None)
                    else:
                        workload = http_workload(family, r.get("Total Requests", ""), r.get("HTTP Max Workers", ""),
                                                 r.get("HTTP Connection Mode", ""))
                    out.append({"schema": SCHEMA, "measured_at_utc": r.get("Measured At (UTC)", ""),
                                "server": server_name(image, variant), "image": image, "name": name,
                                "variant": variant, "deploy": deploy, "repeat": r.get("Repeat", ""),
                                "session": r.get("Session", ""), "facts": facts[image], "workload": workload,
                                "valid": True, "reason": "", "csv": os.path.relpath(path, folder), "csv_row": i,
                                "raw_log": r.get("Raw Log", ""), "backfilled": True})
    # Invalid runs (kept, measured again): their workload only as the text the run recorded
    path = os.path.join(folder, INVALID_FILE)
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for i, r in enumerate(csv.DictReader(fh), 1):
                name, deploy, variant = r.get("Container Name", ""), r.get("Deploy", "") or "container", r.get("Variant", "") or ""
                image = name[:-len("-native")] if deploy == "native" and name.endswith("-native") else name
                out.append({"schema": SCHEMA, "measured_at_utc": r.get("Measured At (UTC)", ""),
                            "server": server_name(image, variant), "image": image, "name": name, "variant": variant,
                            "deploy": deploy, "repeat": r.get("Repeat", ""), "session": "", "facts": {},
                            "workload": {"description": r.get("Measurement", "")}, "valid": False,
                            "reason": r.get("Reason", ""), "csv": INVALID_FILE, "csv_row": i,
                            "raw_log": r.get("Raw Log", ""), "backfilled": True})
    return sorted(out, key=lambda m: m["measured_at_utc"])


def backfill(folder):
    """Write manifest.jsonl for a folder that has none (never overwrites one)."""
    path = os.path.join(folder, MANIFEST)
    if os.path.exists(path):
        return path, 0
    lines = from_csvs(folder)
    with open(path, "w", encoding="utf-8") as fh:
        for m in lines:
            fh.write(json.dumps(m, sort_keys=True) + "\n")
    return path, len(lines)


def main():
    if sys.argv[1:2] == ["index"]:
        root = sys.argv[2] if len(sys.argv) > 2 else "results"
        path, data = write_catalog(root)
        if "--quiet" not in sys.argv:
            for r in data["runs"]:
                print(f"  {r['folder']}  {r['status']:<10} {r['config'] or '-':<22} "
                      f"{r['measured']}/{r['planned'] or '?'} measured, {r['invalid']} invalid")
            print(f"{len(data['runs'])} measurement folders; index: {path}")
        return
    if sys.argv[1:2] == ["backfill"] and len(sys.argv) == 3:
        path, n = backfill(sys.argv[2])
        print(f"{path}: {n} measurements" if n else f"{path} exists already; left as it is")
        return
    sys.exit("usage: results_index.py index [RESULTS_ROOT] [--quiet] | backfill FOLDER")


if __name__ == "__main__":
    main()
