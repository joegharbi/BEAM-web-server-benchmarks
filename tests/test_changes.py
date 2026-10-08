"""Unit tests for the P0 changes (energy window, connection mode, CSV and aggregator keys).

Run from the repo root:  venv/bin/python -m unittest tests/test_changes.py -v
Needs no sudo, Docker or Scaphandre.
"""
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

# The tests never touch the real native/ copies: the run script and native_server use this folder
os.environ["MEASURE_NATIVE_DIR"] = tempfile.mkdtemp(prefix="wseb-test-native-")
import atexit  # noqa: E402
atexit.register(shutil.rmtree, os.environ["MEASURE_NATIVE_DIR"], True)

import scaphandre_energy as se  # noqa: E402

with open("/proc/sys/kernel/pid_max") as _fh:
    NOPID = int(_fh.read()) + 1000  # an ID no task can have


def entry(t, host_w, consumers):
    return {"host": {"consumption": host_w * 1e6, "timestamp": t, "components": {}},
            "consumers": consumers, "sockets": []}


def consumer(pid, watts, name=None):
    return {"pid": pid, "exe": "x", "cmdline": "x", "timestamp": 0,
            "consumption": watts * 1e6, "container": {"name": name} if name else None}


def write_json(entries):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(entries, fh)
    return path


class IntegrateWindow(unittest.TestCase):
    def test_constant_power(self):
        s = [(i * 0.3, 10.0, 20.0) for i in range(40)]
        c, h, n, cov = se.integrate_window(s, 2.0, 7.0)
        self.assertAlmostEqual(c, 50.0, places=6)
        self.assertAlmostEqual(h, 100.0, places=6)
        self.assertAlmostEqual(cov, 1.0, places=6)

    def test_samples_outside_window_ignored(self):
        # 100 W before and after the window, 5 W inside: only the 5 W counts.
        s = [(t, 100.0 if (t <= 2 or t > 6) else 5.0, 0.0) for t in range(0, 10)]
        c, *_ = se.integrate_window(s, 2.0, 6.0)
        self.assertAlmostEqual(c, 20.0)

    def test_zero_samples_count_as_zero(self):
        # Half the window at 0 W, half at 8 W: mean power must be 4 W, not 8 W.
        s = [(t, 0.0 if t <= 5 else 8.0, 0.0) for t in range(0, 11)]
        c, *_ = se.integrate_window(s, 0.0, 10.0)
        self.assertAlmostEqual(c / 10.0, 4.0)

    def test_partial_coverage_reported(self):
        c, h, n, cov = se.integrate_window([(0, 1, 1), (1, 1, 1)], 0.5, 2.0)
        self.assertAlmostEqual(cov, 1 / 3, places=6)

    def test_empty_or_bad_window(self):
        self.assertEqual(se.integrate_window([], 0, 1), (0.0, 0.0, 0, 0.0))
        self.assertEqual(se.integrate_window([(0, 1, 1), (1, 1, 1)], 1, 1), (0.0, 0.0, 0, 0.0))


class LoadSeries(unittest.TestCase):
    # IDs above the system's pid_max never exist, so a live /proc lookup cannot classify them:
    # these tests exercise the fallback rule, as for logs from before the classification was saved.
    def test_processes_are_summed_not_averaged(self):
        # Six processes of one container at 1 W each: the container draws 6 W.
        # The old code averaged the entries and reported 1 W.
        procs = [consumer(NOPID + i, 1.0, "srv") for i in range(6)]
        path = write_json([entry(0, 0, [])] + [entry(t, 20, procs) for t in range(1, 12)])
        r = se.compute_window_energy(path, "srv", 1.0, 11.0)
        self.assertAlmostEqual(r["avg_power_w"], 6.0)
        self.assertAlmostEqual(r["energy_j"], 60.0)
        self.assertAlmostEqual(r["host_energy_j"], 200.0)
        self.assertEqual(r["thread_handling"], "fallback")

    def test_fallback_process_and_its_threads_counted_once(self):
        # What Scaphandre writes for a BEAM: the process entry (6 W, all threads included)
        # plus one entry per scheduler thread (6 x 1 W). The container draws 6 W, not 12 W.
        beam = [consumer(NOPID, 6.0, "srv")] + [consumer(NOPID + 1 + i, 1.0, "srv") for i in range(6)]
        path = write_json([entry(0, 0, [])] + [entry(t, 20, beam) for t in range(1, 12)])
        r = se.compute_window_energy(path, "srv", 1.0, 11.0)
        self.assertAlmostEqual(r["avg_power_w"], 6.0)

    def test_fallback_thread_entries_missing_from_a_sample(self):
        # Some thread entries can be missing (top-N cut, zero power): the process entry still counts alone.
        beam = [consumer(NOPID, 6.0, "srv"), consumer(NOPID + 1, 2.0, "srv"), consumer(NOPID + 2, 1.0, "srv")]
        path = write_json([entry(0, 0, [])] + [entry(t, 20, beam) for t in range(1, 5)])
        r = se.compute_window_energy(path, "srv", 1.0, 4.0)
        self.assertAlmostEqual(r["avg_power_w"], 6.0)

    def test_different_programs_in_one_container_summed(self):
        # A server process and a helper program (epmd, a shell): different exe, both count.
        cs = [consumer(NOPID, 6.0, "srv"), dict(consumer(NOPID + 100, 0.5, "srv"), exe="epmd", cmdline="epmd")]
        path = write_json([entry(0, 0, [])] + [entry(t, 20, cs) for t in range(1, 5)])
        r = se.compute_window_energy(path, "srv", 1.0, 4.0)
        self.assertAlmostEqual(r["avg_power_w"], 6.5)


class ProcessesAndThreads(unittest.TestCase):
    """Live runs: /proc tells processes (Tgid == ID) from threads (Tgid != ID)."""

    def run_with_thread(self, entries_for):
        # A real thread of this test process, alive while the log is evaluated.
        box, stop = {}, threading.Event()
        t = threading.Thread(target=lambda: (box.setdefault("tid", threading.get_native_id()), stop.wait(10)))
        t.start()
        while "tid" not in box:
            time.sleep(0.01)
        try:
            path = write_json([entry(0, 0, [])] + [entry(s, 20, entries_for(os.getpid(), box["tid"])) for s in range(1, 5)])
            return se.compute_window_energy(path, "srv", 1.0, 4.0), box["tid"]
        finally:
            stop.set()
            t.join()

    def test_live_thread_entry_skipped(self):
        r, tid = self.run_with_thread(lambda pid, tid: [consumer(pid, 6.0, "srv"), consumer(tid, 1.0, "srv")])
        self.assertAlmostEqual(r["avg_power_w"], 6.0)
        self.assertEqual(r["thread_handling"], "tgid")
        self.assertEqual(r["thread_ids"], [tid])
        self.assertEqual(r["process_ids"], [os.getpid()])

    def test_live_equal_sibling_processes_both_counted(self):
        # Two processes of the same program, equally busy: the fallback rule alone would
        # drop one of them; known processes are never dropped.
        sibling = os.getppid()
        cs = [consumer(os.getpid(), 3.0, "srv"), consumer(sibling, 3.0, "srv")]
        path = write_json([entry(0, 0, [])] + [entry(s, 20, cs) for s in range(1, 5)])
        r = se.compute_window_energy(path, "srv", 1.0, 4.0)
        self.assertAlmostEqual(r["avg_power_w"], 6.0)
        self.assertEqual(r["thread_handling"], "tgid")

    def test_saved_classification_used_when_recalculating(self):
        # Recalculation never reads /proc (IDs are reused): the saved kinds decide.
        cs = [consumer(NOPID, 6.0, "srv"), consumer(NOPID + 1, 5.9, "srv"), consumer(NOPID + 2, 3.0, "srv")]
        path = write_json([entry(0, 0, [])] + [entry(s, 20, cs) for s in range(1, 5)])
        kinds = {NOPID: "process", NOPID + 1: "process", NOPID + 2: "thread"}
        r = se.compute_window_energy(path, "srv", 1.0, 4.0, pids={NOPID, NOPID + 1, NOPID + 2}, kinds=kinds)
        self.assertAlmostEqual(r["avg_power_w"], 11.9)
        self.assertEqual(r["thread_handling"], "tgid")

    def test_window_file_round_trip(self):
        # The classification is saved next to the raw log and recompute uses it.
        r, tid = self.run_with_thread(lambda pid, tid: [consumer(pid, 6.0, "srv"), consumer(tid, 1.0, "srv")])
        cs = [consumer(os.getpid(), 6.0, "srv"), consumer(tid, 1.0, "srv")]
        raw = write_json([entry(0, 0, [])] + [entry(s, 20, cs) for s in range(1, 5)])
        with open(se.window_path(raw), "w") as fh:
            json.dump(se.window_record("srv", "", 1.0, 4.0, r), fh)
        again = se.recompute(raw)
        self.assertAlmostEqual(again["avg_power_w"], 6.0)
        self.assertEqual(again["thread_handling"], "tgid")

    def test_unknown_thread_of_a_known_process_dropped(self):
        # A thread that ended before the lookup: the known process entry already contains it.
        cs = [consumer(os.getpid(), 6.0, "srv"), consumer(NOPID, 1.0, "srv")]
        path = write_json([entry(0, 0, [])] + [entry(s, 20, cs) for s in range(1, 5)])
        r = se.compute_window_energy(path, "srv", 1.0, 4.0)
        self.assertAlmostEqual(r["avg_power_w"], 6.0)
        self.assertEqual(r["thread_handling"], "tgid+fallback")

    def test_other_containers_ignored(self):
        cs = [consumer(1, 3.0, "srv"), consumer(2, 50.0, "other"), consumer(3, 7.0)]
        path = write_json([entry(0, 0, [])] + [entry(t, 20, cs) for t in range(1, 5)])
        r = se.compute_window_energy(path, "srv", 1.0, 4.0)
        self.assertAlmostEqual(r["avg_power_w"], 3.0)

    def test_first_empty_entry_skipped(self):
        path = write_json([entry(0, 0, []), entry(1, 20, [consumer(1, 2.0, "srv")])])
        series = se.load_power_series(path, "srv")
        self.assertEqual(series[0][0], 1)

    def test_cgroup_fallback(self):
        # Scaphandre reports container=null: attribute by /proc/<pid>/cgroup.
        # Use this test's own process and a substring of its own cgroup path.
        me = os.getpid()
        with open(f"/proc/{me}/cgroup") as fh:
            cg = fh.read().strip().split("::")[-1]
        token = cg.strip("/").split("/")[-1]
        cs = [consumer(me, 4.0), consumer(999999, 9.0)]  # second pid does not exist
        path = write_json([entry(0, 0, [])] + [entry(t, 20, cs) for t in range(1, 5)])
        r = se.compute_window_energy(path, "srv", 1.0, 4.0, container_id=token)
        self.assertAlmostEqual(r["avg_power_w"], 4.0)

    def test_no_samples_returns_zero(self):
        path = write_json([entry(0, 0, []), entry(1, 20, [])])
        r = se.compute_window_energy(path, "srv", 0.0, 1.0)
        self.assertEqual(r["energy_j"], 0.0)


class ScaphandreArgs(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("MEASURE_SCAPH_STEP_MS", None)
        os.environ.pop("MEASURE_SCAPH_MAX_TOP", None)

    def arg(self, a, name):
        return a[a.index(name) + 1]

    def test_default(self):
        a = se.scaphandre_json_args("o.json")
        self.assertEqual((self.arg(a, "--step"), self.arg(a, "--step-nano")), ("0", "500000000"))
        self.assertEqual(self.arg(a, "--max-top-consumers"), "50")
        self.assertEqual(self.arg(a, "-f"), "o.json")

    def test_override_above_one_second(self):
        os.environ["MEASURE_SCAPH_STEP_MS"] = "1500"
        a = se.scaphandre_json_args("o.json")
        self.assertEqual((self.arg(a, "--step"), self.arg(a, "--step-nano")), ("1", "500000000"))

    def test_bad_values_fall_back(self):
        os.environ["MEASURE_SCAPH_STEP_MS"] = "abc"
        os.environ["MEASURE_SCAPH_MAX_TOP"] = "xyz"
        a = se.scaphandre_json_args("o.json")
        self.assertEqual(self.arg(a, "--step-nano"), "500000000")
        self.assertEqual(self.arg(a, "--max-top-consumers"), "50")


class ConnectionMode(unittest.TestCase):
    def test_reuse_keeps_one_session_per_thread(self):
        import threading
        import plugins.workload.http as m
        seen = []

        class FakeSession:
            def get(self, url, timeout):
                seen.append((threading.get_ident(), id(self)))
                return type("R", (), {"status_code": 200, "content": b""})()

        orig = m.requests.Session
        m.requests.Session = FakeSession
        try:
            m._thread_local.__dict__.clear()
            for _ in range(3):
                m._get("http://x/", "reuse")
            t = threading.Thread(target=lambda: m._get("http://x/", "reuse"))
            t.start()
            t.join()
        finally:
            m.requests.Session = orig
        main_ids = {s for tid, s in seen if tid == threading.get_ident()}
        other_ids = {s for tid, s in seen if tid != threading.get_ident()}
        self.assertEqual(len(main_ids), 1)      # same session reused within a thread
        self.assertEqual(len(other_ids), 1)
        self.assertNotEqual(main_ids, other_ids)  # a separate session per thread

    def test_per_request_uses_plain_get(self):
        import plugins.workload.http as m
        calls = []
        orig = m.requests.get
        m.requests.get = lambda url, timeout: calls.append(url) or type("R", (), {"status_code": 200})()
        try:
            m._get("http://x/", "per-request")
        finally:
            m.requests.get = orig
        self.assertEqual(calls, ["http://x/"])


class CsvMigration(unittest.TestCase):
    """The shared CSV writer: new rows in the current layout; a file of an earlier release is
    rewritten once with the current header, its values kept under the new names."""
    def test_old_file_is_migrated_with_renamed_columns(self):
        import csv_columns
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Container Name", "Total Energy (J)", "Num CPUs"])
            w.writerow(["st-a", "12.5", "8"])
        csv_columns.append(path, csv_columns.HTTP_COLUMNS, {"Container Name": "st-a", "Container Energy (J)": 13.0})
        header, rows = csv_columns.read(path)
        self.assertEqual(header, csv_columns.HTTP_COLUMNS)
        self.assertEqual([(r["Container Energy (J)"], r["Host CPUs"]) for r in rows], [("12.5", "8"), ("13.0", "")])

    def test_same_header_appends(self):
        import csv_columns
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        csv_columns.append(path, csv_columns.WS_COLUMNS, {"Container Name": "ws-a"})
        csv_columns.append(path, csv_columns.WS_COLUMNS, {"Container Name": "ws-b"})
        with open(path, newline="") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual((rows[0], [r[0] for r in rows[1:]]), (csv_columns.WS_COLUMNS, ["ws-a", "ws-b"]))

    def test_results_doc_lists_every_column(self):
        import csv_columns
        with open(os.path.join(ROOT, "docs", "RESULTS.md")) as fh:
            doc = fh.read()
        for col in set(csv_columns.HTTP_COLUMNS) | set(csv_columns.WS_COLUMNS):
            self.assertIn(col, doc, f"docs/RESULTS.md does not mention {col}")

    def test_every_measured_value_says_whose_it_is(self):
        import csv_columns
        for col in csv_columns.CONTAINER:
            self.assertEqual(csv_columns.scope_of(col), "Container", col)
        for col in csv_columns.HOST:
            self.assertEqual(csv_columns.scope_of(col), "Host", col)
        for old, new in csv_columns.RENAMED.items():
            self.assertEqual(csv_columns.canonical(old), new)
            self.assertIn(new, csv_columns.HTTP_COLUMNS)
        self.assertEqual(len(set(csv_columns.HTTP_COLUMNS)), len(csv_columns.HTTP_COLUMNS))


class AggregatorKeys(unittest.TestCase):
    def run_agg(self, header, rows):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "in.csv")
        with open(src, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "aggregate_repeats.py"), src],
                       check=True, capture_output=True)
        with open(os.path.join(d, "in_summary.csv"), newline="") as fh:
            return list(csv.DictReader(fh))

    def test_connection_mode_splits_groups(self):
        h = ["Container Name", "Total Requests", "HTTP Connection Mode", "Total Energy (J)"]
        out = self.run_agg(h, [["s", "100", "reuse", "10"], ["s", "100", "reuse", "12"],
                               ["s", "100", "per-request", "30"]])
        self.assertEqual(len(out), 2)
        by = {r["HTTP Connection Mode"]: r for r in out}
        self.assertEqual(by["reuse"]["Repeats"], "2")
        self.assertAlmostEqual(float(by["reuse"]["Container Energy (J) mean"]), 11.0)   # old input name

    def test_interval_is_a_key_not_averaged(self):
        h = ["Container Name", "Pattern", "Interval (s)", "Total Energy (J)"]
        out = self.run_agg(h, [["s", "burst", "0", "5"], ["s", "burst", "0.5", "9"]])
        self.assertEqual(len(out), 2)
        self.assertNotIn("Interval (s) mean", out[0])


class Provenance(unittest.TestCase):
    """Provenance is written once per measurement to metadata.json, not per CSV row."""

    def setUp(self):
        import run_metadata
        self.rm = run_metadata
        self.dir = tempfile.mkdtemp()
        self.meta = os.path.join(self.dir, "metadata.json")

    def load(self):
        with open(self.meta) as fh:
            return json.load(fh)

    def test_start_records_software_machine_settings_state(self):
        self.rm.write_start(self.meta, {"quick": "1"})
        m = self.load()
        self.assertEqual(m["measurement"], os.path.basename(self.dir))
        for k in ("framework_version", "scaphandre_version", "docker_version", "python_version",
                  "os", "kernel", "cpu_model", "logical_cpus", "memory_gb"):
            self.assertIn(k, m["software_and_machine"])
        self.assertEqual(m["settings"]["quick"], "1")
        self.assertIn("scaphandre_step_ms", m["settings"])
        for k in ("time_utc", "cpu_governor", "turbo", "cpu_max_freq_mhz", "ac_power",
                  "cpu_package_temp_c", "load1"):
            self.assertIn(k, m["machine_state_start"])
        self.assertNotIn("finished_at_utc", m)       # not finished yet

    def test_end_adds_state_images_and_stability(self):
        self.rm.write_start(self.meta, {})
        c = os.path.join(self.dir, "static", "x.csv")
        os.makedirs(os.path.dirname(c))
        with open(c, "w", newline="") as fh:
            csv.writer(fh).writerows([["Container Name", "Total Energy (J)"], ["no-such-image-xyz", "1"]])
        path, stable = self.rm.write_end(self.meta, self.rm.csvs_in(self.dir))
        m = self.load()
        self.assertIn("finished_at_utc", m)
        self.assertIn("machine_state_end", m)
        self.assertEqual(m["images"], {"no-such-image-xyz": ""})   # unknown image -> empty, not guessed
        self.assertTrue(stable)

    def test_changed_conditions_are_flagged(self):
        self.rm.write_start(self.meta, {})
        m = self.load()
        m["machine_state_start"]["turbo"] = "changed"
        with open(self.meta, "w") as fh:
            json.dump(m, fh)
        _, stable = self.rm.write_end(self.meta, [])
        self.assertFalse(stable)
        self.assertFalse(self.load()["conditions_stable"])

    def test_repeat_metadata_named_after_csv(self):
        p = os.path.join(self.dir, "server_2026_repeats_metadata.json")
        self.rm.write_start(p, {})
        with open(p) as fh:
            self.assertEqual(json.load(fh)["measurement"], "server_2026_repeats")

    def test_framework_version_marks_uncommitted_changes(self):
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, cwd=ROOT).stdout.strip()
        self.assertEqual(self.rm.framework_version().endswith("-dirty"), bool(dirty))

    def test_csv_rows_carry_no_provenance(self):
        import measure_core, inspect
        import plugins.workload.http, plugins.workload.websocket
        for mod in (measure_core, plugins.workload.http, plugins.workload.websocket):
            self.assertNotIn("row_fields", inspect.getsource(mod))

    def test_cli_start_end(self):
        tool = os.path.join(ROOT, "tools", "run_metadata.py")
        subprocess.run([sys.executable, tool, "start", self.dir, "--set", "a=b"], check=True, capture_output=True)
        subprocess.run([sys.executable, tool, "end", self.dir], check=True, capture_output=True)
        m = self.load()
        self.assertEqual(m["settings"]["a"], "b")
        self.assertIn("finished_at_utc", m)

class BenchConfig(unittest.TestCase):
    def parse(self, text):
        import bench_config
        return bench_config.parse(text)

    def test_empty_file_gives_defaults(self):
        import bench_config
        cfg = self.parse("")
        for k, spec in bench_config.SCHEMA.items():
            default = spec["default"]
            if k != "SHUFFLE_SEED":
                self.assertEqual(cfg[k], default)
        self.assertTrue(cfg["SHUFFLE_SEED"].isdigit())          # random seed filled in, so it can be recorded

    def test_values_quotes_and_comments(self):
        cfg = self.parse('REPEATS=7  # seven\nENV_TURBO="on"\n# a comment\n\nHTTP_MAX_WORKERS=System\nSHUFFLE_SEED=42')
        self.assertEqual((cfg["REPEATS"], cfg["ENV_TURBO"], cfg["HTTP_MAX_WORKERS"], cfg["SHUFFLE_SEED"]),
                         ("7", "on", "system", "42"))

    def test_every_mistake_is_reported(self):
        import bench_config
        with self.assertRaises(bench_config.ConfigError) as cm:
            self.parse("REPEATS=3\nREPEATS=4\nSHUFLE=1\nENV_TURBO=maybe\nREADY_MIN_WAIT_SECONDS=abc\nREPEATS2\nREPEATS=0")
        msg = str(cm.exception)
        for part in ("set twice", "did you mean SHUFFLE", "must be one of", "whole number", "expected KEY=VALUE"):
            self.assertIn(part, msg)

    def test_zero_repeats_rejected(self):
        import bench_config
        with self.assertRaises(bench_config.ConfigError):
            self.parse("REPEATS=0")

    def test_ready_max_below_min_rejected(self):
        import bench_config
        with self.assertRaises(bench_config.ConfigError):
            self.parse("READY_MIN_WAIT_SECONDS=60\nREADY_MAX_WAIT_SECONDS=30")

    def test_example_file_parses_to_defaults(self):
        import bench_config
        cfg = self.parse(bench_config.example())
        self.assertEqual(cfg["REPEATS"], bench_config.SCHEMA["REPEATS"]["default"])

    def test_committed_example_matches_schema(self):
        import bench_config
        with open(os.path.join(ROOT, "bench.config.example")) as fh:
            self.assertEqual(fh.read().strip(), bench_config.example().strip())

    def test_shell_output_is_safely_quoted(self):
        tool = os.path.join(ROOT, "tools", "bench_config.py")
        d = tempfile.mkdtemp(); c = os.path.join(d, "c")
        with open(c, "w") as fh:
            fh.write("ENV_KEEP_CONTAINERS=a,b\n")
        out = subprocess.run([sys.executable, tool, c], capture_output=True, text=True, check=True).stdout
        r = subprocess.run(["bash", "-c", out + '\necho "$CFG_ENV_KEEP_CONTAINERS|$CFG_REPEATS"'],
                           capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.strip(), "a,b|5")


class EnvironmentVerify(unittest.TestCase):
    def verify(self, *args):
        tool = os.path.join(ROOT, "tools", "prepare_environment.py")
        return subprocess.run([sys.executable, tool, "verify", *args], capture_output=True, text=True)

    def test_unchanged_always_passes(self):
        self.assertEqual(self.verify("--governor", "unchanged", "--turbo", "unchanged",
                                     "--no-stop-containers").returncode, 0)

    def test_mismatch_fails(self):
        import run_metadata
        wrong = "on" if run_metadata.turbo_state() == "off" else "off"
        r = self.verify("--governor", "unchanged", "--turbo", wrong, "--no-stop-containers")
        self.assertEqual(r.returncode, 1)
        self.assertIn("NOT AS REQUESTED", r.stdout)


class ReadyConfig(unittest.TestCase):
    def test_checks_can_be_switched_off_and_are_range_checked(self):
        import bench_config
        cfg = bench_config.parse("READY_TEMP_MARGIN_C=\nREADY_CPU_BUSY_MARGIN_PERCENT=")
        self.assertEqual((cfg["READY_TEMP_MARGIN_C"], cfg["READY_CPU_BUSY_MARGIN_PERCENT"]), ("", ""))
        for bad in ("READY_CPU_BUSY_MARGIN_PERCENT=150", "READY_CPU_BUSY_REFERENCE_PERCENT=-3", "READY_TEMP_MARGIN_C=-1", "READY_ON_TIMEOUT=later",
                    "READY_CHECK_EVERY_SECONDS=0", "SEED=1"):
            with self.assertRaises(bench_config.ConfigError):
                bench_config.parse(bad)


class ReadinessGate(unittest.TestCase):
    def setUp(self):
        import readiness, run_metadata
        self.r, self.m = readiness, run_metadata
        self.orig = (run_metadata.cpu_package_temp_c, run_metadata.cpu_times, run_metadata.throttle_counters,
                     run_metadata.ac_power)
        self.jiffies = [0, 0]           # busy, total; advanced by fake_times
        self.busy_pct = 0
        run_metadata.cpu_times = self.fake_times
        run_metadata.throttle_counters = lambda: (0, 0)
        run_metadata.ac_power = lambda: "yes"

    def tearDown(self):
        (self.m.cpu_package_temp_c, self.m.cpu_times, self.m.throttle_counters,
         self.m.ac_power) = self.orig

    def test_not_ready_on_battery(self):
        prev = {"busy": 0, "total": 0, "throttle": 0, "temp": 40}
        self.m.cpu_package_temp_c = lambda: 40.0
        self.m.ac_power = lambda: "no"
        self.assertIn("on battery (connect the charger)", self.r.check_once(prev, self.args())[1])
        self.m.ac_power = lambda: ""                         # no battery at all (desktop, server): not checked
        self.assertEqual(self.r.check_once(prev, self.args())[1], [])

    def fake_times(self):
        self.jiffies[0] += self.busy_pct
        self.jiffies[1] += 100
        return tuple(self.jiffies)

    def args(self, **kw):
        import argparse
        d = dict(temp_reference=40.0, temp_margin=2.0, cpu_reference=0.0, cpu_margin=5.0, no_throttling=1,
                 check_every=0.01, consecutive=2, min_wait=0, max_wait=0.3, on_timeout="wait")
        d.update(kw)
        return argparse.Namespace(**d)

    def test_ready_when_all_checks_pass(self):
        self.m.cpu_package_temp_c = lambda: 41.0
        self.assertEqual(self.r.wait(self.args())[1], "yes")

    def test_each_check_can_fail(self):
        prev = {"busy": 0, "total": 0, "throttle": 0, "temp": 50}
        self.m.cpu_package_temp_c = lambda: 45.0            # above 40 + 2
        self.busy_pct = 20                                   # 20% above 5%
        self.m.throttle_counters = lambda: (3, 10)           # 3 new throttle events
        _, fails = self.r.check_once(prev, self.args())
        text = "; ".join(fails)
        for part in ("temperature 45.0 C > 42.0 C", "CPU busy 20.0% > 5%", "throttling (3 new events)"):
            self.assertIn(part, text)

    def test_switched_off_checks_are_ignored(self):
        prev = {"busy": 0, "total": 0, "throttle": 0, "temp": 50}
        self.m.cpu_package_temp_c = lambda: 90.0
        self.busy_pct = 90
        self.m.throttle_counters = lambda: (5, 10)
        _, fails = self.r.check_once(prev, self.args(temp_margin=None, cpu_margin=None, no_throttling=0))
        self.assertEqual(fails, [])

    def test_min_wait_is_respected(self):
        self.m.cpu_package_temp_c = lambda: 41.0
        waited, _ = self.r.wait(self.args(min_wait=0.2))
        self.assertGreaterEqual(waited, 0.2)

    def test_timeout_measure_records_reason(self):
        self.m.cpu_package_temp_c = lambda: 60.0
        waited, ready = self.r.wait(self.args(on_timeout="measure"))
        self.assertTrue(ready.startswith("no: temperature 60.0 C > 42.0 C"), ready)
        self.assertGreaterEqual(waited, 0.3)

    def test_timeout_stop_exits_2(self):
        self.m.cpu_package_temp_c = lambda: 60.0
        with self.assertRaises(SystemExit) as cm:
            self.r.wait(self.args(on_timeout="stop"))
        self.assertEqual(cm.exception.code, 2)

    def test_timeout_wait_keeps_waiting_until_cool(self):
        temps = iter([60.0] * 60 + [41.0] * 100)             # hot for longer than max_wait, then cool
        self.m.cpu_package_temp_c = lambda: next(temps)
        waited, ready = self.r.wait(self.args(on_timeout="wait", max_wait=0.1))
        self.assertEqual(ready, "yes")
        self.assertGreater(waited, 0.1)

    def test_short_dip_does_not_count(self):
        # Readings: prev, then 41 (pass), 60 (fail, resets), 41, 41 -> ready on the 4th check.
        temps = iter([50.0, 41.0, 60.0, 41.0, 41.0, 41.0, 41.0])
        self.m.cpu_package_temp_c = lambda: next(temps)
        self.r.wait(self.args(max_wait=10))
        self.assertEqual(list(temps), [41.0, 41.0])          # exactly 5 readings were used


class ThermalColumns(unittest.TestCase):
    def test_fields(self):
        import argparse, measure_core
        a = argparse.Namespace(waited_s=12.5, ready_check="yes")
        f = measure_core.thermal_fields((45.0, 100), (52.0, 130), a, (3.0, "yes"))
        self.assertEqual(f, {"Host CPU Temp Start (C)": 45.0, "Host CPU Temp End (C)": 52.0, "Host Throttled (ms)": 30,
                             "Waited Before Start (s)": 12.5, "Waited Before Load (s)": 3.0,
                             "Ready Check": "yes"})

    def test_without_gate_says_not_checked(self):
        import argparse, measure_core
        a = argparse.Namespace(waited_s=None, ready_check="not checked")
        f = measure_core.thermal_fields(("", ""), ("", ""), a)
        self.assertEqual((f["Host Throttled (ms)"], f["Waited Before Start (s)"], f["Waited Before Load (s)"],
                          f["Ready Check"]), ("", "", "", "not checked"))

    def test_ready_check_names_the_failing_check(self):
        import readiness
        self.assertEqual(readiness.combine("yes", "no: temperature 61.0 C > 59.5 C"),
                         "no: before load: temperature 61.0 C > 59.5 C")
        self.assertEqual(readiness.combine("no: CPU busy 9% > 5%", "yes"), "no: before start: CPU busy 9% > 5%")


class PreLoadGate(unittest.TestCase):
    KEYS = ("MEASURE_READY_TEMP_REFERENCE_C", "MEASURE_READY_TEMP_MARGIN_C", "MEASURE_READY_NO_THROTTLING",
            "MEASURE_READY_CHECK_EVERY_SECONDS", "MEASURE_READY_CONSECUTIVE_CHECKS",
            "MEASURE_READY_MAX_WAIT_SECONDS", "MEASURE_READY_ON_TIMEOUT", "MEASURE_READY_CPU_SPEED")

    def setUp(self):
        import readiness, run_metadata
        self.r, self.m = readiness, run_metadata
        self.orig_temp = run_metadata.cpu_package_temp_c
        self.saved = {k: os.environ.pop(k, None) for k in self.KEYS}

    def tearDown(self):
        self.m.cpu_package_temp_c = self.orig_temp
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def set_env(self, on_timeout="wait"):
        os.environ.update({"MEASURE_READY_TEMP_REFERENCE_C": "40", "MEASURE_READY_TEMP_MARGIN_C": "2",
                           "MEASURE_READY_NO_THROTTLING": "1", "MEASURE_READY_CHECK_EVERY_SECONDS": "0.01",
                           "MEASURE_READY_CONSECUTIVE_CHECKS": "2", "MEASURE_READY_MAX_WAIT_SECONDS": "0.2",
                           "MEASURE_READY_ON_TIMEOUT": on_timeout,
                           "MEASURE_READY_CPU_SPEED": "off"})           # not this laptop's current CPU speed

    def test_off_without_config(self):
        self.assertEqual(self.r.pre_load_gate(), (None, "not checked"))

    def test_waits_for_temperature_after_boot(self):
        self.set_env()
        temps = iter([60.0] * 5 + [41.0] * 20)               # warm after boot, then cools
        self.m.cpu_package_temp_c = lambda: next(temps)
        waited, ready = self.r.pre_load_gate()
        self.assertEqual(ready, "yes")
        self.assertGreater(waited, 0)

    def test_ignores_cpu_busy(self):
        self.set_env()
        self.m.cpu_package_temp_c = lambda: 41.0
        orig = self.m.cpu_times
        n = [0]
        def busy():                                          # 100% busy the whole time
            n[0] += 100
            return n[0], n[0]
        self.m.cpu_times = busy
        try:
            self.assertEqual(self.r.pre_load_gate()[1], "yes")
        finally:
            self.m.cpu_times = orig

    def test_stop_mode_exits(self):
        self.set_env("stop")
        self.m.cpu_package_temp_c = lambda: 60.0
        with self.assertRaises(SystemExit):
            self.r.pre_load_gate()

class TemperatureReference(unittest.TestCase):
    def test_config_accepts_empty_or_number(self):
        import bench_config
        self.assertEqual(bench_config.parse("")["READY_TEMP_REFERENCE_C"], "")
        self.assertEqual(bench_config.parse("READY_TEMP_REFERENCE_C=50")["READY_TEMP_REFERENCE_C"], "50.0")
        with self.assertRaises(bench_config.ConfigError):
            bench_config.parse("READY_TEMP_REFERENCE_C=warm")

    def test_fixed_limit_reference_50_margin_0(self):
        import argparse, readiness
        a = argparse.Namespace(temp_reference=50.0, temp_margin=0.0, cpu_reference=None, cpu_margin=None, no_throttling=0)
        prev = {"busy": 0, "total": 0, "throttle": 0, "temp": 0}
        orig = readiness.run_metadata.cpu_package_temp_c
        try:
            readiness.run_metadata.cpu_package_temp_c = lambda: 50.0
            self.assertEqual(readiness.check_once(prev, a)[1], [])          # 50 is allowed
            readiness.run_metadata.cpu_package_temp_c = lambda: 51.0
            self.assertEqual(readiness.check_once(prev, a)[1], ["temperature 51.0 C > 50.0 C"])
        finally:
            readiness.run_metadata.cpu_package_temp_c = orig


class CpuReference(unittest.TestCase):
    def check(self, busy_pct, reference, margin):
        import argparse, readiness
        a = argparse.Namespace(temp_reference=None, temp_margin=None, cpu_reference=reference,
                               cpu_margin=margin, no_throttling=0)
        prev = {"busy": 0, "total": 0, "throttle": 0, "temp": 0}
        orig = readiness.run_metadata.cpu_times
        readiness.run_metadata.cpu_times = lambda: (busy_pct, 100)
        try:
            return readiness.check_once(prev, a)[1]
        finally:
            readiness.run_metadata.cpu_times = orig

    def test_fixed_reference_0_margin_5_is_a_plain_5_percent_limit(self):
        self.assertEqual(self.check(5, 0.0, 5.0), [])
        self.assertEqual(self.check(6, 0.0, 5.0), ["CPU busy 6.0% > 5%"])

    def test_measured_reference_adds_the_margin(self):
        self.assertEqual(self.check(19, 15.0, 5.0), [])                 # shared machine resting at 15%
        self.assertEqual(self.check(21, 15.0, 5.0), ["CPU busy 21.0% > 20%"])

    def test_limit_never_above_100(self):
        self.assertEqual(self.check(100, 98.0, 5.0), [])

    def test_empty_margin_switches_the_check_off(self):
        self.assertEqual(self.check(100, 0.0, None), [])

    def test_resting_state_reports_temperature_and_cpu(self):
        import readiness
        temp, cpu = readiness.resting_state(seconds=0.2, every=0.1)
        self.assertIsInstance(cpu, float)
        self.assertTrue(0.0 <= cpu <= 100.0)


class RestingMeasureSeconds(unittest.TestCase):
    def test_window_length_and_reading_count(self):
        import readiness, time
        calls = []
        orig = readiness.run_metadata.cpu_package_temp_c
        readiness.run_metadata.cpu_package_temp_c = lambda: calls.append(1) or 45.0
        try:
            t0 = time.monotonic()
            temp, _ = readiness.resting_state(seconds=0.5, every=0.1)
            took = time.monotonic() - t0
        finally:
            readiness.run_metadata.cpu_package_temp_c = orig
        self.assertEqual(len(calls), 6)                  # both ends included: 0.0, 0.1, ... 0.5
        self.assertGreaterEqual(took, 0.5)
        self.assertEqual(temp, 45.0)

    def test_config_default_and_check(self):
        import bench_config
        self.assertEqual(bench_config.parse("")["RESTING_MEASURE_SECONDS"], "10")
        with self.assertRaises(bench_config.ConfigError):
            bench_config.parse("RESTING_MEASURE_SECONDS=0")


class MeasurementFailure(unittest.TestCase):
    def setUp(self):
        import measure_failure
        self.mf = measure_failure
        self.reason = os.path.join(tempfile.mkdtemp(), "reason")
        os.environ["MEASURE_FAILURE_REASON_FILE"] = self.reason
        self.orig_cleanup = measure_failure._cleanup
        self.cleaned = []
        measure_failure._cleanup = lambda: self.cleaned.append(True)

    def tearDown(self):
        os.environ.pop("MEASURE_FAILURE_REASON_FILE", None)
        self.mf._cleanup = self.orig_cleanup

    def test_fail_writes_reason_cleans_up_and_exits_1(self):
        with self.assertRaises(SystemExit) as cm:
            self.mf.fail("health check failed: no HTTP 200")
        self.assertEqual(cm.exception.code, 1)
        with open(self.reason) as fh:
            self.assertEqual(fh.read(), "health check failed: no HTTP 200")
        self.assertEqual(self.cleaned, [True])

    def test_unexpected_error_becomes_a_recorded_failure(self):
        def boom():
            raise RuntimeError("Container failed to start")
        with self.assertRaises(SystemExit) as cm:
            self.mf.run(boom)
        self.assertEqual(cm.exception.code, 1)
        with open(self.reason) as fh:
            self.assertEqual(fh.read(), "RuntimeError: Container failed to start")

    def test_deliberate_exit_codes_pass_through(self):
        for code in (0, 2, 3):
            with self.assertRaises(SystemExit) as cm:
                self.mf.run(lambda: sys.exit(code))
            self.assertEqual(cm.exception.code, code)
        self.assertFalse(os.path.exists(self.reason))

    def test_success_writes_nothing(self):
        self.mf.run(lambda: None)
        self.assertFalse(os.path.exists(self.reason))


class ResumeInfo(unittest.TestCase):
    def folder(self, finished=False, config=True, args="--super-quick --config /tmp/x.config static"):
        import run_metadata
        d = tempfile.mkdtemp()
        meta = {"settings": {"arguments": args, "shuffle_seed": "77"}}
        if finished:
            meta["finished_at_utc"] = "2026-01-01T00:00:00+00:00"
        with open(os.path.join(d, "metadata.json"), "w") as fh:
            json.dump(meta, fh)
        if config:
            open(os.path.join(d, "bench.config"), "w").close()
        return d, run_metadata.resume_info(d)

    def run_bash(self, text):
        r = subprocess.run(["bash", "-c", text + '\necho "P=$RESUME_PROBLEMS|S=$RESUME_SEED|A=$*"'],
                           capture_output=True, text=True, check=True)
        return r.stdout.strip()

    def test_unfinished_uses_saved_config_seed_and_arguments(self):
        d, info = self.folder()
        out = self.run_bash(info)
        self.assertEqual(out, f"P=|S=77|A=--config {d}/bench.config --super-quick static")

    def test_finished_is_refused(self):
        _, info = self.folder(finished=True)
        self.assertIn("already finished", self.run_bash(info))

    def test_without_config_is_refused(self):
        _, info = self.folder(config=False)
        self.assertIn("no bench.config", self.run_bash(info))


class RawData(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in ("MEASURE_RAW_DIR", "MEASURE_RAW_DATA")}
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def make(self, mode):
        os.environ["MEASURE_RAW_DIR"] = os.path.join(self.dir, "raw")
        os.environ["MEASURE_RAW_DATA"] = mode
        p = se.raw_json_path("srv", "static")
        with open(p, "w") as fh:
            json.dump([{"x": 1}], fh)
        return p

    def test_path_inside_results_folder_and_named_after_run(self):
        p = self.make("keep")
        self.assertEqual(os.path.dirname(p), os.path.join(self.dir, "raw"))
        self.assertRegex(os.path.basename(p), r"^srv_static_\d{8}T\d{6}Z\.json$")

    def test_keep_leaves_the_plain_file_untouched(self):
        p = self.make("keep")
        out = se.finish_raw(p, {"container_name": "srv"})
        self.assertEqual(out, p)
        with open(out) as fh:
            self.assertEqual(json.load(fh), [{"x": 1}])
        self.assertTrue(os.path.exists(se.window_path(p)))

    def test_delete_removes_it(self):
        p = self.make("delete")
        self.assertEqual(se.finish_raw(p), "")
        self.assertFalse(os.path.exists(p))

    def test_without_config_nothing_changes(self):
        p = se.raw_json_path("srv", "static")
        self.assertTrue(p.startswith("output" + os.sep))
        self.assertEqual(se.finish_raw("/nonexistent.json"), "/nonexistent.json")


class Statistics(AggregatorKeys):
    def test_describe_known_values(self):
        import aggregate_repeats as ag
        st = dict(zip(ag.STATS, ag.describe([78, 80, 81, 79, 82])))
        self.assertEqual((st["mean"], st["median"], st["Q1"], st["Q3"], st["IQR"], st["min"], st["max"]),
                         (80, 80, 79, 81, 2, 78, 82))
        self.assertAlmostEqual(st["sd"], 1.5811, places=4)
        self.assertAlmostEqual(st["+/-95%"], 1.9632, places=4)      # t(4) = 2.776
        self.assertAlmostEqual(st["CV%"], 1.9764, places=4)

    def test_single_run_has_no_spread(self):
        import aggregate_repeats as ag
        st = dict(zip(ag.STATS, ag.describe([42.0])))
        self.assertEqual((st["mean"], st["median"], st["min"], st["max"]), (42.0, 42.0, 42.0, 42.0))
        self.assertEqual((st["sd"], st["+/-95%"], st["IQR"], st["CV%"]), ("", "", "", ""))

    def test_full_columns_and_clear_outlier_names(self):
        h = ["Container Name", "Total Requests", "Total Energy (J)"]
        out = self.run_agg(h, [["s", "100", v] for v in ("78", "80", "81", "79", "82", "120")])
        r = out[0]
        for stat in ("mean", "sd", "+/-95%", "median", "Q1", "Q3", "IQR", "min", "max", "CV%"):
            self.assertIn(f"Container Energy (J) {stat}", r)
        self.assertEqual(r["Container Energy (J) runs dropped, IQR rule"], "1")      # the 120 J run
        self.assertAlmostEqual(float(r["Container Energy (J) mean, IQR-filtered"]), 80.0)
        self.assertNotIn("Energy IQR mean", r)                                   # old misleading name gone

    def test_several_csvs_into_one_summary(self):
        d = tempfile.mkdtemp()
        for name, rows in (("a.csv", [["a", "100", "10"], ["a", "100", "12"]]),
                           ("b.csv", [["b", "100", "20"], ["b", "100", "22"]])):
            with open(os.path.join(d, name), "w", newline="") as fh:
                csv.writer(fh).writerows([["Container Name", "Total Requests", "Total Energy (J)"]] + rows)
        out = os.path.join(d, "summary.csv")
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "aggregate_repeats.py"),
                        os.path.join(d, "a.csv"), os.path.join(d, "b.csv"), "--output", out],
                       check=True, capture_output=True)
        rows = {r["Container Name"]: r for r in csv.DictReader(open(out))}
        self.assertEqual((rows["a"]["Container Energy (J) mean"], rows["b"]["Container Energy (J) mean"]), ("11.0", "21.0"))


class WorkloadConfig(unittest.TestCase):
    def test_defaults_equal_the_built_in_lists(self):
        import bench_config, re
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            sh = fh.read()
        def built_in(name):
            return re.search(rf"^{name}=\(?([^)\n]*)\)?$", sh, re.M).group(1).strip()
        cfg = bench_config.parse("")
        for key, var in (("HTTP_REQUESTS", "full_http_requests"), ("WS_BURST_CLIENTS", "full_ws_burst_clients"),
                         ("WS_BURST_SIZES_KB", "full_ws_burst_sizes"), ("WS_BURST_BURSTS", "full_ws_burst_bursts"),
                         ("WS_STREAM_CLIENTS", "full_ws_stream_clients"), ("WS_STREAM_SIZES_KB", "full_ws_stream_sizes"),
                         ("WS_STREAM_RATE_PER_SECOND", "full_ws_stream_rates"),
                         ("WS_STREAM_DURATION_SECONDS", "full_ws_stream_durations"),
                         ("WS_CONCURRENCY_CLIENTS", "concurrency_clients"), ("WS_CONCURRENCY_SIZE_KB", "concurrency_size"),
                         ("WS_PAYLOAD_CLIENTS", "payload_clients"), ("WS_PAYLOAD_SIZES_KB", "payload_sizes"),
                         ("WS_BURST_INTERVAL_SECONDS", "WS_BURST_INTERVAL")):
            self.assertEqual(cfg[key], built_in(var), key)

    def test_lists_are_checked(self):
        import bench_config
        self.assertEqual(bench_config.parse("HTTP_REQUESTS=200  300")["HTTP_REQUESTS"], "200 300")
        for bad in ("HTTP_REQUESTS=100,200", "HTTP_REQUESTS=", "WS_BURST_SIZES_KB=8 big", "WS_BURST_INTERVAL_SECONDS=-1"):
            with self.assertRaises(bench_config.ConfigError):
                bench_config.parse(bad)


class RecomputeCommand(unittest.TestCase):
    """Runs the documented command itself, not just the function (the function test missed a bug)."""
    def test_command_line_recompute(self):
        d = tempfile.mkdtemp()
        raw = os.path.join(d, "srv_static_20260101T000000Z.json")
        entries = [entry(0, 0, [])] + [entry(t, 10, [consumer(7, 2.0)]) for t in range(1, 6)]
        with open(raw, "w") as fh:
            json.dump(entries, fh)
        with open(se.window_path(raw), "w") as fh:
            json.dump({"container_name": "srv", "container_id": "", "load_start_epoch": 1,
                       "load_end_epoch": 4, "pids": [7]}, fh)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "scaphandre_energy.py"), "recompute", raw],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("container energy 6.000000 J", r.stdout)     # 2 W for 3 s
        self.assertIn("host energy 30.000000 J", r.stdout)         # 10 W for 3 s


class CtrlC(unittest.TestCase):
    """Ctrl-C during a controlled run must still restore the machine and say how to continue.

    Before the fix, Ctrl-C also stopped the log writer (tee); the first message of the clean-up
    then hit a closed pipe and killed the script before it restored anything.
    """
    def test_ctrl_c_restores_the_machine(self):
        import signal, time
        d = tempfile.mkdtemp()
        fakes = os.path.join(d, "bin")
        os.makedirs(fakes)
        calls = os.path.join(d, "sudo_calls.log")
        with open(os.path.join(fakes, "sudo"), "w") as fh:      # records machine-setting calls, runs the rest
            fh.write(f"""#!/bin/sh
while [ $# -gt 0 ]; do case "$1" in -*) shift ;; *) break ;; esac; done
[ $# -eq 0 ] && exit 0
case "$*" in *prepare_environment.py*) echo "$*" >> {calls}; exit 0 ;; esac
exec "$@"
""")
        with open(os.path.join(fakes, "docker"), "w") as fh:    # no other containers running
            fh.write("#!/bin/sh\nexit 0\n")
        for f in ("sudo", "docker"):
            os.chmod(os.path.join(fakes, f), 0o755)
        bench = os.path.join(d, "bench")
        for fam in ("static", "dynamic", "websocket"):
            os.makedirs(os.path.join(bench, fam))
        cfg = os.path.join(d, "c.config")
        with open(cfg, "w") as fh:
            fh.write("SETTLE_SECONDS=60\nENV_GOVERNOR=unchanged\nENV_TURBO=unchanged\nENV_POWER_PROFILE=unchanged\nENV_PAUSE_TIMERS=none\nENV_STOP_CONTAINERS=1\n"
                     "ENV_SCREEN_BRIGHTNESS=unchanged\nENV_KEYBOARD_LIGHT=unchanged\nENV_WIFI=unchanged\n"
                     "ENV_BLUETOOTH=unchanged\n")
        before = set(os.listdir(os.path.join(ROOT, "results"))) if os.path.isdir(os.path.join(ROOT, "results")) else set()
        env = dict(os.environ, PATH=fakes + os.pathsep + os.environ["PATH"])
        p = subprocess.Popen(["bash", "scripts/run_benchmarks.sh", "--super-quick", "--bench", bench, "--config", cfg, "static"],
                             cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             start_new_session=True)
        out = []
        try:
            for line in p.stdout:                       # wait until the machine settings are applied
                out.append(line)
                if "Letting the machine settle" in line:
                    break
            os.killpg(p.pid, signal.SIGINT)              # Ctrl-C reaches the whole process group
            out.append(p.stdout.read())
            p.wait(timeout=30)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
            p.stdout.close()
            for name in set(os.listdir(os.path.join(ROOT, "results"))) - before:
                shutil.rmtree(os.path.join(ROOT, "results", name), ignore_errors=True)
        text = "".join(out)
        log = text.split("Logging to ", 1)[1].split()[0] if "Logging to " in text else ""
        if log and os.path.isfile(os.path.join(ROOT, log)):
            os.remove(os.path.join(ROOT, log))
        self.assertEqual(p.returncode, 130, text)                       # not killed by a broken pipe (-13)
        self.assertIn("Restoring machine settings", text)
        with open(calls) as fh:
            self.assertIn("restore", fh.read())
        self.assertIn("make resume RESUME=results/", text)
        self.assertIn("Sleep and lid-close suspend are blocked", text)
        r = subprocess.run(["systemd-inhibit", "--list", "--no-pager"], capture_output=True, text=True)
        self.assertNotIn("web-server benchmarks", r.stdout)             # released again after the stop


class LaptopState(unittest.TestCase):
    """What can change the laptop's power draw is recorded at the start and end of a measurement."""
    def test_machine_state_records_power_screen_and_radios(self):
        import run_metadata
        state = run_metadata.machine_state()
        for key in ("ac_power", "battery", "screen_brightness_percent", "keyboard_backlight_percent",
                    "wifi", "bluetooth"):
            self.assertIn(key, state)

    def test_scaphandre_package_version_is_recorded(self):
        import run_metadata
        self.assertIn("scaphandre_package_version", run_metadata.software_and_machine())


class RunPlanCount(unittest.TestCase):
    """The progress counter and time estimate use the configured number of HTTP load levels."""
    def test_http_steps_follow_the_config(self):
        d = tempfile.mkdtemp()
        cfg = os.path.join(d, "c.config")
        with open(cfg, "w") as fh:
            fh.write("HTTP_REQUESTS=1000 20000 80000\n")
        sh = os.path.join(ROOT, "scripts", "run_benchmarks.sh")
        with open(sh) as fh:
            src = fh.read()
        func = src[src.index("bench_http_steps_per_container() {"):]
        func = func[:func.index("\n}\n") + 3]
        script = (f'cd "{ROOT}"\nPYTHON_PATH="{sys.executable}"\nSUPER_QUICK_BENCH=0; QUICK_BENCH=0\n'
                  'full_http_requests=(100 1000 5000 8000 10000 15000 20000 30000 40000 50000 60000 70000 80000)\n'
                  'echo "default=$(bench_http_steps_per_container)"\n'
                  f'eval "$("$PYTHON_PATH" ./tools/bench_config.py "{cfg}")"\n'
                  'read -r -a full_http_requests <<< "$CFG_HTTP_REQUESTS"\n'
                  'echo "config=$(bench_http_steps_per_container)"\n')
        r = subprocess.run(["bash", "-c", func + "\n" + script], capture_output=True, text=True)
        self.assertIn("default=13", r.stdout, r.stderr)
        self.assertIn("config=3", r.stdout, r.stderr)


class IdleAndWarmup(unittest.TestCase):
    def test_every_key_documented_once_and_example_short(self):
        import bench_config
        docs, example = bench_config.docs(), bench_config.example()
        for key in bench_config.SCHEMA:
            self.assertEqual(docs.count(f"### `{key}`"), 1, key)                 # every setting explained
            self.assertIn(key, bench_config.SHORT)
            in_example = example.count(f"\n{key}=")
            self.assertEqual(in_example, 0 if key in bench_config.MACHINE_KEYS else 1, key)   # machine keys: profiles
        self.assertLess(len(example.splitlines()), 85)                       # short: one line per setting

    def test_generated_files_are_up_to_date(self):
        import bench_config
        for path, text in (("bench.config.example", bench_config.example() + "\n"), ("docs/CONFIG.md", bench_config.docs())):
            with open(os.path.join(ROOT, path)) as fh:
                self.assertEqual(fh.read(), text, f"{path} is out of date: regenerate it with tools/bench_config.py")

    def test_off_by_default(self):
        import bench_config, load_phases
        cfg = bench_config.parse("")
        self.assertEqual((cfg["IDLE_SECONDS"], cfg["WARMUP_SECONDS"]), ("0", "0"))
        saved = {k: os.environ.pop(k, None) for k in ("MEASURE_IDLE_SECONDS", "MEASURE_WARMUP_SECONDS")}
        try:
            self.assertEqual((load_phases.default_idle_s(), load_phases.default_warmup_s()), (0.0, 0.0))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        f = load_phases.idle_fields("unused.json", "srv", None, None, 0)
        self.assertEqual((f["Idle Time (s)"], f["Container Idle Energy (J)"]), (0, ""))

    def test_idle_energy_from_the_same_log(self):
        import load_phases
        # 2 W idle for t=1..6, then 8 W under load
        entries = [entry(0, 0, [])] + [entry(t, 10, [consumer(7, 2.0 if t <= 6 else 8.0, "srv")]) for t in range(1, 12)]
        path = write_json(entries)
        f = load_phases.idle_fields(path, "srv", None, (1, 6), 3)
        self.assertAlmostEqual(f["Container Idle Energy (J)"], 10.0)         # 2 W x 5 s
        self.assertAlmostEqual(f["Container Idle Avg Power (W)"], 2.0)
        self.assertAlmostEqual(f["Host Idle Avg Power (W)"], 10.0)
        self.assertEqual((f["Idle Time (s)"], f["Warm-up (s)"]), (5, 3))

    def test_http_warmup_is_not_counted(self):
        import http.server, threading
        import plugins.workload.http as md

        class Ok(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            hits = 0

            def do_GET(self):
                Ok.hits += 1
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ok)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            import argparse
            w = md.Plugin()
            w.warm_up(argparse.Namespace(warmup_s=0.5, max_workers=4, connection="reuse"), f"http://127.0.0.1:{srv.server_port}/")
            self.assertGreater(Ok.hits, 0)                              # the server got traffic
            self.assertEqual(w.counts(), (0, 0))                          # but nothing was counted
        finally:
            srv.shutdown()


class LaptopSettings(unittest.TestCase):
    """Screen, keyboard light, Wi-Fi and Bluetooth: applied, verified and restored (on a fake /sys)."""
    def setUp(self):
        import prepare_environment as pe
        self.pe = pe
        self.root = tempfile.mkdtemp()
        def node(path, **files):
            os.makedirs(path, exist_ok=True)
            for name, value in files.items():
                with open(os.path.join(path, name), "w") as fh:
                    fh.write(value)
        node(f"{self.root}/backlight/intel", brightness="300", max_brightness="1000")
        node(f"{self.root}/leds/tpacpi::kbd_backlight", brightness="2", max_brightness="2")
        node(f"{self.root}/rfkill/rfkill0", type="wlan", soft="0", hard="0")
        node(f"{self.root}/rfkill/rfkill1", type="bluetooth", soft="0", hard="0")
        node(f"{self.root}/rfkill/rfkill2", type="bluetooth", soft="0", hard="0")
        self.saved = (pe.BACKLIGHT_GLOB, pe.KBD_LIGHT_GLOB, pe.RFKILL_GLOB, pe.require_root, pe.docker_running)
        pe.BACKLIGHT_GLOB, pe.KBD_LIGHT_GLOB = f"{self.root}/backlight/*", f"{self.root}/leds/*kbd_backlight*"
        pe.RFKILL_GLOB = f"{self.root}/rfkill/rfkill*"
        pe.require_root, pe.docker_running = (lambda: None), (lambda: [])

    def tearDown(self):
        (self.pe.BACKLIGHT_GLOB, self.pe.KBD_LIGHT_GLOB, self.pe.RFKILL_GLOB,
         self.pe.require_root, self.pe.docker_running) = self.saved

    def read(self, rel):
        with open(os.path.join(self.root, rel)) as fh:
            return fh.read()

    def args(self, **kw):
        import argparse
        d = dict(governor="unchanged", turbo="unchanged", cpu_speed="unchanged", power_profile="unchanged",
                 stop_containers=False, keep="", screen_brightness="20", keyboard_light="off", wifi="off", bluetooth="off",
                 state=os.path.join(self.root, "state.json"))
        d.update(kw)
        return argparse.Namespace(**d)

    def test_apply_verify_restore(self):
        self.pe.do_apply(self.args())
        self.assertEqual(self.read("backlight/intel/brightness"), "200")          # 20% of 1000
        self.assertEqual(self.read("leds/tpacpi::kbd_backlight/brightness"), "0")
        self.assertEqual([self.read(f"rfkill/rfkill{i}/soft") for i in range(3)], ["1", "1", "1"])
        with self.assertRaises(SystemExit) as e:
            self.pe.do_verify(self.args())
        self.assertEqual(e.exception.code, 0)
        self.pe.do_restore(self.args())
        self.assertEqual(self.read("backlight/intel/brightness"), "300")
        self.assertEqual(self.read("leds/tpacpi::kbd_backlight/brightness"), "2")
        self.assertEqual([self.read(f"rfkill/rfkill{i}/soft") for i in range(3)], ["0", "0", "0"])

    def test_verify_notices_a_radio_still_on(self):
        with self.assertRaises(SystemExit) as e:
            self.pe.do_verify(self.args(screen_brightness="unchanged", keyboard_light="unchanged", bluetooth="unchanged"))
        self.assertEqual(e.exception.code, 1)                                     # Wi-Fi is still on

    def test_unchanged_touches_nothing(self):
        self.pe.do_apply(self.args(screen_brightness="unchanged", keyboard_light="unchanged",
                                   wifi="unchanged", bluetooth="unchanged"))
        self.assertEqual(self.read("backlight/intel/brightness"), "300")
        self.assertEqual(self.read("rfkill/rfkill0/soft"), "0")

    def test_config_values(self):
        import bench_config
        cfg = bench_config.parse("ENV_SCREEN_BRIGHTNESS=20\nENV_WIFI=off")
        self.assertEqual((cfg["ENV_SCREEN_BRIGHTNESS"], cfg["ENV_WIFI"], cfg["ON_BATTERY"]), ("20", "off", "wait"))
        for bad in ("ENV_SCREEN_BRIGHTNESS=120", "ENV_WIFI=on", "ON_BATTERY=maybe"):
            with self.assertRaises(bench_config.ConfigError):
                bench_config.parse(bad)


class OnBattery(ReadinessGate):
    """ON_BATTERY: wait never measures on battery, stop stops, ignore measures."""
    def test_wait_ignores_ready_on_timeout_while_on_battery(self):
        self.m.cpu_package_temp_c = lambda: 40.0
        calls = [0]
        def ac():
            calls[0] += 1
            return "no" if calls[0] <= 40 else "yes"                              # charger back after 40 checks
        self.m.ac_power = ac
        waited, ready = self.r.wait(self.args(on_timeout="measure", on_battery="wait", max_wait=0.05))
        self.assertEqual(ready, "yes")                                            # never "no: on battery"
        self.assertGreater(calls[0], 40)

    def test_stop(self):
        self.m.cpu_package_temp_c = lambda: 40.0
        self.m.ac_power = lambda: "no"
        with self.assertRaises(SystemExit) as e:
            self.r.wait(self.args(on_battery="stop"))
        self.assertEqual(e.exception.code, 2)

    def test_ignore(self):
        prev = {"busy": 0, "total": 0, "throttle": 0, "temp": 40}
        self.m.cpu_package_temp_c = lambda: 40.0
        self.m.ac_power = lambda: "no"
        self.assertEqual(self.r.check_once(prev, self.args(on_battery="ignore"))[1], [])

    def test_unplugged_during_the_load_makes_the_run_invalid(self):
        import load_conditions as lc
        w = lc.Watch()
        w.power = ["yes", "no", "yes"]                                           # unplugged for a moment
        self.assertEqual(lc.problems(w, 0, {}), [])                              # no config: not judged
        self.assertEqual(lc.problems(w, 0, {"on_battery": "wait"}),
                         ["the laptop ran on battery during the load (charger unplugged)"])
        self.assertEqual(lc.problems(w, 0, {"on_battery": "ignore"}), [])
        w.power = ["", ""]                                                       # no battery at all
        self.assertEqual(lc.problems(w, 0, {"on_battery": "wait"}), [])


class SafeResumeAndReproduce(unittest.TestCase):
    """Resume only with the same tools; reproduce in a new folder and report what is different."""
    START = {"framework_version": "abc123", "scaphandre_version": "1.0.2",
             "scaphandre_package_version": "1.0.2-4+b1", "docker_version": "26.1", "python_version": "3.13.5",
             "os": "Debian 13", "kernel": "6.12", "cpu_model": "i7", "logical_cpus": 8, "memory_gb": 15.3}

    def setUp(self):
        import run_metadata
        self.m = run_metadata
        self.saved = (run_metadata.software_and_machine, run_metadata.image_id, os.environ.pop("RESUME_ANYWAY", None))
        self.now = dict(self.START)
        self.ids = {"st-a": "111111111111", "st-b": "222222222222"}
        run_metadata.software_and_machine = lambda: dict(self.now)
        run_metadata.image_id = lambda n: self.ids.get(n, "")

    def tearDown(self):
        self.m.software_and_machine, self.m.image_id, anyway = self.saved
        os.environ.pop("RESUME_ANYWAY", None)
        if anyway is not None:
            os.environ["RESUME_ANYWAY"] = anyway

    def folder(self, finished=False):
        d = tempfile.mkdtemp()
        meta = {"started_at_utc": "2026-10-01T10:00:00+00:00", "software_and_machine": dict(self.START),
                "images_at_start": {"st-a": "111111111111", "st-b": "222222222222"},
                "settings": {"arguments": "--config /tmp/x.config static", "shuffle_seed": "77"}}
        if finished:
            meta["finished_at_utc"] = "2026-10-01T12:00:00+00:00"
        with open(os.path.join(d, "metadata.json"), "w") as fh:
            json.dump(meta, fh)
        for f in ("bench.config", "bench.config.resolved"):
            open(os.path.join(d, f), "w").close()
        return d

    def test_same_tools_resume(self):
        self.assertEqual(self.m.differences(json.load(open(os.path.join(self.folder(), "metadata.json")))), [])
        self.assertIn("RESUME_PROBLEMS=''", self.m.resume_info(self.folder()))

    def test_new_scaphandre_or_rebuilt_image_is_refused(self):
        self.now["scaphandre_package_version"] = "1.0.3-2"
        self.ids["st-b"] = "999999999999"
        info = self.m.resume_info(self.folder())
        for part in ("these changed since the measurement started", "Scaphandre package", "1.0.2-4+b1  ->  1.0.3-2",
                     "Image st-b", "222222222222  ->  999999999999", "RESUME_ANYWAY=1"):
            self.assertIn(part, info)

    def test_resume_anyway_records_the_differences(self):
        self.now["kernel"] = "6.13"
        d = self.folder()
        os.environ["RESUME_ANYWAY"] = "1"
        self.assertIn("RESUME_PROBLEMS=''", self.m.resume_info(d))
        self.m.write_resume(os.path.join(d, "metadata.json"), {})
        rec = json.load(open(os.path.join(d, "metadata.json")))["resumes"][-1]
        self.assertEqual(rec["differences_from_start"], [{"what": "Kernel", "then": "6.12", "now": "6.13"}])

    def test_latest_unfinished(self):
        root = tempfile.mkdtemp()
        for name, started, finished in (("old", "2026-10-01T08:00", False), ("new", "2026-10-01T09:00", False),
                                        ("done", "2026-10-01T10:00", True)):
            d = os.path.join(root, name)
            os.makedirs(d)
            meta = {"started_at_utc": started}
            if finished:
                meta["finished_at_utc"] = "x"
            json.dump(meta, open(os.path.join(d, "metadata.json"), "w"))
            open(os.path.join(d, "bench.config"), "w").close()
        self.assertEqual(self.m.latest_unfinished(root), os.path.join(root, "new"))
        self.assertEqual(self.m.latest_unfinished(tempfile.mkdtemp()), "")

    def test_reproduce_uses_resolved_settings_and_reports_differences(self):
        self.now["cpu_model"] = "Ryzen 7"
        d = self.folder(finished=True)
        info = self.m.reproduce_info(d)
        r = subprocess.run(["bash", "-c", info + '\necho "P=$REPRODUCE_PROBLEMS|A=$*"; echo "$REPRODUCE_DIFFERENCES"'],
                           capture_output=True, text=True, check=True)
        self.assertIn(f"P=|A=--config {d}/bench.config.resolved static", r.stdout)
        self.assertIn("CPU  i7  ->  Ryzen 7", r.stdout)
        new = tempfile.mkdtemp()
        self.m.write_start(os.path.join(new, "metadata.json"), {"reproduces": d})
        meta = json.load(open(os.path.join(new, "metadata.json")))
        self.assertEqual(meta["reproduces"], d)
        self.assertEqual(meta["differences_from_original"], [{"what": "CPU", "then": "i7", "now": "Ryzen 7"}])

    def test_reproduce_needs_a_config_measurement(self):
        d = self.folder()
        os.remove(os.path.join(d, "bench.config.resolved"))
        self.assertIn("only measurements made with --config can be reproduced", self.m.reproduce_info(d))


class MinimalDefaultsAndProfiles(unittest.TestCase):
    def test_defaults_are_minimal(self):
        import bench_config
        cfg = bench_config.parse("")
        self.assertEqual([cfg[k] for k in ("ENV_SCREEN_BRIGHTNESS", "ENV_KEYBOARD_LIGHT", "ENV_WIFI", "ENV_BLUETOOTH")],
                         ["1", "off", "off", "off"])

    def profile(self, name):
        import bench_config
        with open(os.path.join(ROOT, "configs", "machine", f"{name}.config")) as fh:
            return bench_config.parse("", fh.read())

    def test_profiles(self):
        import bench_config
        minimal, tolerable, untouched = (self.profile(n) for n in ("minimal", "tolerable", "untouched"))
        self.assertEqual(minimal, {**bench_config.parse(""), "SHUFFLE_SEED": minimal["SHUFFLE_SEED"]})   # = defaults
        self.assertEqual({k for k in minimal if minimal[k] != tolerable[k] and k != "SHUFFLE_SEED"}, {"ENV_WIFI"})
        self.assertEqual(tolerable["ENV_WIFI"], "unchanged")
        for key in ("ENV_GOVERNOR", "ENV_TURBO", "ENV_SCREEN_BRIGHTNESS", "ENV_KEYBOARD_LIGHT", "ENV_WIFI", "ENV_BLUETOOTH"):
            self.assertEqual(untouched[key], "unchanged", key)
        self.assertEqual((untouched["ENV_STOP_CONTAINERS"], untouched["READY_ON_TIMEOUT"]), ("0", "measure"))
        remote, cloud = self.profile("remote"), self.profile("cloud")
        # remote: full CPU control, never cuts the network, stops (resumable) instead of waiting forever
        self.assertEqual([remote[k] for k in ("ENV_GOVERNOR", "ENV_TURBO", "ENV_STOP_CONTAINERS", "ENV_WIFI",
                                              "READY_ON_TIMEOUT")], ["performance", "off", "1", "unchanged", "stop"])
        # cloud: CPU settings where allowed, nothing physical touched, measures and records on shared hardware
        self.assertEqual([cloud[k] for k in ("ENV_GOVERNOR", "ENV_STOP_CONTAINERS", "ENV_SCREEN_BRIGHTNESS", "ENV_WIFI",
                                             "ENV_BLUETOOTH", "READY_ON_TIMEOUT")],
                         ["performance", "1", "unchanged", "unchanged", "unchanged", "measure"])

    def test_virtual_machine_and_rapl_are_recorded(self):
        import run_metadata
        info = run_metadata.software_and_machine()
        self.assertIn(info["rapl_available"], (True, False))
        self.assertTrue(info["virtualization"])

    def test_ssh_over_wifi_is_detected(self):
        import prepare_environment as pe
        saved = (pe._route_dev, pe._is_wireless)
        try:
            pe._route_dev = lambda ip: {"10.0.0.5": "wlp0s20f3", "10.0.1.5": "enp0s31f6"}.get(ip, "")
            pe._is_wireless = lambda dev: dev.startswith("wl")
            self.assertFalse(pe.remote_over_wifi({}))                                          # local session
            self.assertTrue(pe.remote_over_wifi({"SSH_CONNECTION": "10.0.0.5 51000 10.0.0.2 22"}))
            self.assertFalse(pe.remote_over_wifi({"SSH_CONNECTION": "10.0.1.5 51000 10.0.1.2 22"}))  # cable
        finally:
            pe._route_dev, pe._is_wireless = saved


class ConfigFingerprint(SafeResumeAndReproduce):
    def test_resume_refuses_an_edited_config(self):
        d = self.folder()
        with open(os.path.join(d, "bench.config"), "w") as fh:
            fh.write("REPEATS=5\n")
        meta_path = os.path.join(d, "metadata.json")
        meta = json.load(open(meta_path))
        meta["config_sha256"] = self.m.file_sha256(os.path.join(d, "bench.config"))
        json.dump(meta, open(meta_path, "w"))
        self.assertIn("RESUME_PROBLEMS=''", self.m.resume_info(d))                # unchanged: resumes
        with open(os.path.join(d, "bench.config"), "a") as fh:
            fh.write("REPEATS=3\n")
        self.assertIn("Config (bench.config in the folder, edited)", self.m.resume_info(d))

    def test_start_records_the_fingerprint(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "bench.config"), "w") as fh:
            fh.write("REPEATS=5\n")
        self.m.write_start(os.path.join(d, "metadata.json"), {})
        meta = json.load(open(os.path.join(d, "metadata.json")))
        self.assertEqual(meta["config_sha256"], self.m.file_sha256(os.path.join(d, "bench.config")))


class FixesFromTheRealCheck(LaptopSettings):
    def test_bluetooth_chip_that_disappears_is_restored(self):
        """Switching Bluetooth off removes the chip's rfkill device; it comes back with a new number."""
        import types
        real_time = self.pe.time
        self.pe.time = types.SimpleNamespace(sleep=lambda s: None)              # only inside prepare_environment
        self.addCleanup(setattr, self.pe, "time", real_time)
        args = self.args(screen_brightness="unchanged", keyboard_light="unchanged", wifi="unchanged")
        self.pe.do_apply(args)
        shutil.rmtree(os.path.join(self.root, "rfkill", "rfkill2"))                 # the chip disappears
        os.makedirs(os.path.join(self.root, "rfkill", "rfkill3"))                   # and comes back, renumbered
        for name, value in (("type", "bluetooth"), ("soft", "1"), ("hard", "0")):
            with open(os.path.join(self.root, "rfkill", "rfkill3", name), "w") as fh:
                fh.write(value)
        self.pe.do_restore(args)
        self.assertEqual((self.read("rfkill/rfkill1/soft"), self.read("rfkill/rfkill3/soft")), ("0", "0"))
        self.assertEqual(self.read("rfkill/rfkill0/soft"), "0")                     # Wi-Fi untouched

    def test_idle_zero_is_not_a_warning(self):
        import load_phases
        # As on this laptop: Scaphandre lists other processes only (the idle server used no CPU), and the
        # container is found by its cgroup (container ID given); PID 1 is not in that container
        path = write_json([entry(0, 0, [])] + [entry(t, 5, [consumer(1, 0.5)]) for t in range(1, 8)])
        with self.assertNoLogs(level="WARNING"):
            f = load_phases.idle_fields(path, "srv", "0123456789abcdef", (1, 6), 0)
        self.assertEqual(f["Container Idle Energy (J)"], 0.0)

    def test_full_disk_stops_before_measuring(self):
        d = tempfile.mkdtemp()
        bench = os.path.join(d, "bench")
        for fam in ("static", "dynamic", "websocket"):
            os.makedirs(os.path.join(bench, fam))
        cfg = os.path.join(d, "c.config")
        with open(cfg, "w") as fh:
            fh.write("ENV_GOVERNOR=unchanged\nENV_TURBO=unchanged\nENV_POWER_PROFILE=unchanged\nENV_PAUSE_TIMERS=none\nENV_STOP_CONTAINERS=0\nENV_SCREEN_BRIGHTNESS=unchanged\n"
                     "ENV_KEYBOARD_LIGHT=unchanged\nENV_WIFI=unchanged\nENV_BLUETOOTH=unchanged\n")
        before = set(os.listdir(os.path.join(ROOT, "results")))
        env = dict(os.environ, PATH=os.path.join(ROOT, "tests", "fakes") + os.pathsep + os.environ["PATH"],
                   BENCH_MIN_FREE_GB="999999")
        out_file = os.path.join(d, "out.txt")
        try:
            # Output to a file, not a pipe: the sudo keepalive's `sleep 60` would hold a pipe open
            with open(out_file, "w") as fh:
                r = subprocess.run(["bash", "scripts/run_benchmarks.sh", "--super-quick", "--bench", bench, "--config",
                                    cfg, "static"], cwd=ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=60)
        finally:
            for name in set(os.listdir(os.path.join(ROOT, "results"))) - before:
                shutil.rmtree(os.path.join(ROOT, "results", name), ignore_errors=True)
        with open(out_file) as fh:
            out = fh.read()
        log = out.split("Logging to ", 1)[1].split()[0] if "Logging to " in out else ""
        if log and os.path.isfile(os.path.join(ROOT, log)):
            os.remove(os.path.join(ROOT, log))
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("stopping before it fills up", out)
        self.assertNotIn("Letting the machine settle", out)


class WhatToMeasure(unittest.TestCase):
    """MEASURE, SERVERS and BENCHMARKS_DIR in the config; checked before anything starts."""
    build_fail = ""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.bin)
        with open(os.path.join(self.bin, "sudo"), "w") as fh:          # machine settings are not touched here
            fh.write('#!/bin/sh\nwhile [ $# -gt 0 ]; do case "$1" in -*) shift ;; *) break ;; esac; done\n'
                     '[ $# -eq 0 ] && exit 0\ncase "$*" in *prepare_environment.py*) exit 0 ;; esac\nexec "$@"\n')
        with open(os.path.join(self.bin, "docker"), "w") as fh:        # "built" images: $FAKE_IMAGES
            fh.write('#!/bin/sh\nif [ "$1" = "build" ]; then cat > "$FAKE_BUILDS.$$"; [ -z "$FAKE_BUILD_FAIL" ]; exit $?; fi\n'
                     'if [ "$1 $2" = "image inspect" ]; then\n'
                     '  case "$*" in *ExposedPorts*) echo "8080/tcp "; exit 0 ;; esac\n'
                     '  case "$*" in *wseb.options*) [ "$5" = "$FAKE_OPTIONS_IMAGE" ] && echo "$FAKE_OPTIONS" '
                     '|| echo "<no value>"; exit 0 ;; esac\n'
                     '  for i in $FAKE_IMAGES; do [ "$i" = "$3" ] && exit 0; done; exit 1\nfi\nexit 0\n')
        for f in ("sudo", "docker"):
            os.chmod(os.path.join(self.bin, f), 0o755)
        self.bench = os.path.join(self.d, "bench")
        for rel in ("static/erlang/cowboy/st-a", "static/elixir/pure/st-b", "dynamic/erlang/pure/dy-c",
                    "websocket/erlang/cowboy/ws-d"):
            os.makedirs(os.path.join(self.bench, rel))
            open(os.path.join(self.bench, rel, "Dockerfile"), "w").close()

    @staticmethod
    def config_text(config, config_cpu_speed="unchanged"):
        return ("SETTLE_SECONDS=0\nRESTING_MEASURE_SECONDS=1\nENV_GOVERNOR=unchanged\nENV_TURBO=unchanged\n"
                f"ENV_CPU_SPEED={config_cpu_speed}\nENV_POWER_PROFILE=unchanged\nENV_PAUSE_TIMERS=none\n"
                "ENV_STOP_CONTAINERS=0\nENV_SCREEN_BRIGHTNESS=unchanged\nENV_KEYBOARD_LIGHT=unchanged\n"
                "ENV_WIFI=unchanged\nENV_BLUETOOTH=unchanged\n" + config)

    def run_until_plan(self, config, args=(), images="st-a st-b dy-c ws-d my-img", config_cpu_speed="unchanged",
                       extra_env=None):
        import signal
        cfg = os.path.join(self.d, "c.config")
        with open(cfg, "w") as fh:
            fh.write(self.config_text(config, config_cpu_speed))
        before = set(os.listdir(os.path.join(ROOT, "results")))
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], FAKE_IMAGES=images,
                   FAKE_BUILDS=os.path.join(self.d, "build"), FAKE_BUILD_FAIL=self.build_fail, **(extra_env or {}))
        out_file = os.path.join(self.d, "out.txt")
        with open(out_file, "w") as fh:
            p = subprocess.Popen(["bash", "scripts/run_benchmarks.sh", "--bench", self.bench, "--config", cfg, *args],
                                 cwd=ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            for _ in range(300):                                          # stop once the plan is printed
                time.sleep(0.1)
                with open(out_file) as fh:
                    text = fh.read()
                if p.poll() is not None or "Results directory:" in text and "measurements in total" in text \
                        or "WS payload:" in text:
                    break
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGINT)
                p.wait(timeout=30)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
            for name in set(os.listdir(os.path.join(ROOT, "results"))) - before:
                shutil.rmtree(os.path.join(ROOT, "results", name), ignore_errors=True)
        with open(out_file) as fh:
            text = fh.read()
        log = text.split("Logging to ", 1)[1].split()[0] if "Logging to " in text else ""
        if log and os.path.isfile(os.path.join(ROOT, log)):
            os.remove(os.path.join(ROOT, log))
        return p.returncode, text

    def test_selected_servers_and_kinds(self):
        rc, out = self.run_until_plan("MEASURE=static\nSERVERS=st-a static:my-img\nHTTP_REQUESTS=1000 2000 3000\n")
        self.assertIn("Static HTTP:     2 containers × 3 levels = 6", out)
        self.assertIn("Dynamic HTTP:    0 containers", out)
        self.assertIn("WS concurrency:  0 ×", out)

    def test_unknown_name_stops_before_anything(self):
        rc, out = self.run_until_plan("SERVERS=st-zzz\n")
        self.assertEqual(rc, 1)
        self.assertIn("'st-zzz' is not a server folder", out)
        self.assertIn("static:st-zzz", out)
        self.assertNotIn("Sleep and lid-close", out)                             # nothing started:
        self.assertNotIn("Machine settings", out)                                # machine untouched

    def test_early_stop_leaves_no_empty_results_folder(self):
        before = set(os.listdir(os.path.join(ROOT, "results")))
        self.run_until_plan("SERVERS=st-zzz\n")
        self.assertEqual(set(os.listdir(os.path.join(ROOT, "results"))), before)

    def test_image_not_built_stops_before_anything(self):
        rc, out = self.run_until_plan("MEASURE=static\n", images="st-a")
        self.assertEqual(rc, 1)
        self.assertIn("These images are not built: st-b", out)

    def test_command_line_wins(self):
        rc, out = self.run_until_plan("MEASURE=websocket\n", args=("static",))
        self.assertIn("The command line chooses what to measure (static); MEASURE, SERVERS, VARIANTS and DEPLOY of the config are not used", out)

    def test_port_of_an_image_without_folder(self):
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            sh = fh.read()
        funcs = "".join(sh[sh.index(f"{f}() {{"):][:sh[sh.index(f"{f}() {{"):].index("\n}\n") + 3]
                        for f in ("find_container_dir", "get_container_port_mapping"))
        r = subprocess.run(["bash", "-c", f'BENCHMARKS_DIR="{self.bench}"\n{funcs}\n'
                            'get_container_port_mapping my-img 8001; get_container_port_mapping st-a 8001'],
                           capture_output=True, text=True, env=dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"]))
        self.assertEqual(r.stdout.split(), ["8001:8080", "8001:80"])


class TwoLayerConfig(unittest.TestCase):
    """Defaults < machine profile (MACHINE=...) < measurement file."""
    def write(self, d, name, text):
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def test_layers(self):
        import bench_config
        d = tempfile.mkdtemp()
        self.write(d, "lab.config", "ENV_WIFI=unchanged\nREADY_TEMP_MARGIN_C=5\n")
        path = self.write(d, "m.config", "MACHINE=lab.config\nMEASURE=static\nREADY_TEMP_MARGIN_C=2\n")
        cfg, profile = bench_config.load(path)
        self.assertEqual(profile, os.path.join(d, "lab.config"))
        self.assertEqual(cfg["ENV_WIFI"], "unchanged")                 # from the profile
        self.assertEqual(cfg["READY_TEMP_MARGIN_C"], "2.0")            # the measurement file overrides it
        self.assertEqual(cfg["ENV_BLUETOOTH"], "off")                  # default
        self.assertEqual(cfg["MEASURE"], "static")

    def test_named_profiles_and_none(self):
        import bench_config
        d = tempfile.mkdtemp()
        cfg, profile = bench_config.load(self.write(d, "m.config", "MACHINE=untouched\n"))
        self.assertTrue(profile.endswith(os.path.join("configs", "machine", "untouched.config")))
        self.assertEqual((cfg["ENV_GOVERNOR"], cfg["READY_ON_TIMEOUT"]), ("unchanged", "measure"))
        cfg, profile = bench_config.load(self.write(d, "n.config", "MACHINE=none\n"))
        self.assertEqual((profile, cfg["ENV_GOVERNOR"]), ("", "performance"))

    def test_unknown_profile_and_wrong_key_in_profile(self):
        import bench_config
        d = tempfile.mkdtemp()
        with self.assertRaises(bench_config.ConfigError) as e:
            bench_config.load(self.write(d, "m.config", "MACHINE=lab\n"))
        self.assertIn("minimal", str(e.exception))                      # lists the profiles there are
        self.write(d, "bad.config", "REPEATS=3\n")
        with self.assertRaises(bench_config.ConfigError) as e:
            bench_config.load(self.write(d, "m2.config", "MACHINE=bad.config\n"))
        self.assertIn("REPEATS is not a machine setting", str(e.exception))

    def test_resume_uses_the_copy_in_the_results_folder(self):
        import bench_config
        d = tempfile.mkdtemp()
        copy = self.write(d, "machine.config", "ENV_WIFI=unchanged\n")
        path = self.write(d, "bench.config", "MACHINE=minimal\n")
        os.environ["BENCH_MACHINE_FILE"] = copy
        try:
            cfg, profile = bench_config.load(path)
        finally:
            del os.environ["BENCH_MACHINE_FILE"]
        self.assertEqual((profile, cfg["ENV_WIFI"]), (copy, "unchanged"))

    def test_edited_machine_copy_stops_a_resume(self):
        import run_metadata
        d = tempfile.mkdtemp()
        for name in ("bench.config", "machine.config"):
            self.write(d, name, "x\n")
        run_metadata.write_start(os.path.join(d, "metadata.json"), {})
        meta = json.load(open(os.path.join(d, "metadata.json")))
        self.assertTrue(meta["machine_config_sha256"])
        self.write(d, "machine.config", "ENV_WIFI=unchanged\n")
        diffs = [w for w, _, _ in run_metadata.differences(meta, d)]
        self.assertIn("Config (machine.config in the folder, edited)", diffs)


class Variants(WhatToMeasure):
    """VARIANTS: each server also measured as <server>-<name>, built from it, in the same shuffled order."""
    def test_variants_join_the_plan_and_are_built(self):
        rc, out = self.run_until_plan("MEASURE=static\nSERVERS=st-a st-b\nHTTP_REQUESTS=1000\n"
                                      "VARIANTS=nobw:ERL_FLAGS=+sbwt none +sbwtdcpu none +sbwtdio none\n")
        self.assertIn("Static HTTP:     4 containers × 1 levels = 4", out)
        builds = [open(os.path.join(self.d, f)).read() for f in os.listdir(self.d) if f.startswith("build.")]
        self.assertEqual(sorted(b.splitlines()[0] for b in builds), ["FROM st-a", "FROM st-b"])
        self.assertTrue(all('ENV ERL_FLAGS="+sbwt none +sbwtdcpu none +sbwtdio none"' in b for b in builds))

    def test_variant_skipped_for_a_server_that_does_not_read_its_variable(self):
        # st-b stands for a Java server: it reads JAVA_TOOL_OPTIONS, so an ERL_FLAGS variant would only
        # measure the same server again under another name
        rc, out = self.run_until_plan("MEASURE=static\nSERVERS=st-a st-b\nHTTP_REQUESTS=1000\n"
                                      "VARIANTS=nobw:ERL_FLAGS=+sbwt none\n",
                                      extra_env={"FAKE_OPTIONS_IMAGE": "st-b", "FAKE_OPTIONS": "JAVA_TOOL_OPTIONS"})
        self.assertIn("VARIANTS: nobw does not apply to st-b (it reads JAVA_TOOL_OPTIONS); skipped", out)
        self.assertIn("Static HTTP:     3 containers × 1 levels = 3", out)        # st-a, st-a-nobw, st-b
        builds = [open(os.path.join(self.d, f)).read() for f in os.listdir(self.d) if f.startswith("build.")]
        self.assertEqual([b.splitlines()[0] for b in builds], ["FROM st-a"])

    def test_failed_build_stops_before_anything(self):
        self.build_fail = "1"
        rc, out = self.run_until_plan("MEASURE=static\nSERVERS=st-a\nVARIANTS=nobw:ERL_FLAGS=+sbwt none\n")
        self.assertEqual(rc, 1)
        self.assertIn("VARIANTS: could not build st-a-nobw from st-a", out)
        self.assertNotIn("Machine settings", out)

    def test_config_checks(self):
        import bench_config
        self.assertEqual(bench_config.parse("VARIANTS=a:X=1|Y=2 ; b:Z=3")["VARIANTS"], "a:X=1|Y=2;b:Z=3")
        for bad in ("VARIANTS=NoBW:X=1", "VARIANTS=nobw", "VARIANTS=nobw:+sbwt none"):
            with self.assertRaises(bench_config.ConfigError):
                bench_config.parse(bad)




class LeftoverSettings(unittest.TestCase):
    """A run that never restored the machine (kill -9, crash, power loss) blocks the next one that would
    change the settings: it would save the changed settings as the state before."""

    def setUp(self):
        self.h = WhatToMeasure("test_selected_servers_and_kinds")  # its harness only, not its tests
        self.h.setUp()
        self.run_until_plan = self.h.run_until_plan
        self.old = os.path.join(ROOT, "results", "2000-01-01_000000-leftover-test")
        os.makedirs(self.old)
        with open(os.path.join(self.old, ".environment_state.json"), "w") as fh:
            fh.write("{}")

    def tearDown(self):
        shutil.rmtree(self.old, ignore_errors=True)

    def test_refuses_to_change_settings(self):
        rc, out = self.run_until_plan("MEASURE=static\nSERVERS=st-a\n", config_cpu_speed="max")
        self.assertEqual(rc, 1)
        self.assertIn("The machine settings of an earlier run were never restored", out)
        self.assertIn("prepare_environment.py restore --state results/2000-01-01_000000-leftover-test/.environment_state.json", out)
        self.assertNotIn("CPU governor: set", out)

    def test_unchanged_settings_still_run(self):
        rc, out = self.run_until_plan("MEASURE=static\nSERVERS=st-a\nHTTP_REQUESTS=1000\n")
        self.assertNotIn("never restored", out)
        self.assertIn("Static HTTP:     1 containers", out)



class CpuCapClues(unittest.TestCase):
    """The waiting message names what can make the firmware cap the CPU, from what the machine reports."""

    def test_charger_offer_and_battery_drain(self):
        import run_metadata as m
        from unittest import mock
        files = {"/sys/devices/platform/thinkpad_acpi/dytc_lapmode": "1",
                 "/p/usbc/online": "1", "/p/usbc/type": "USB", "/p/usbc/voltage_now": "5000000",
                 "/p/usbc/voltage_max": "20000000", "/p/usbc/current_max": "3250000",
                 "/sys/firmware/acpi/platform_profile": "balanced",
                 "/p/BAT0/status": "Discharging", "/p/BAT0/power_now": "12300000"}
        globs = {"/sys/class/power_supply/*": ["/p/usbc", "/p/BAT0"], "/sys/class/power_supply/BAT*": ["/p/BAT0"]}
        with mock.patch.object(m, "_read", files.get), mock.patch.object(m.glob, "glob", lambda g: globs.get(g, [])), \
                mock.patch.object(m, "cpu_package_temp_c", lambda: 41.0), \
                mock.patch.object(m, "_power_profiles_daemon", lambda *a: None):            # not this machine's
            clues = m.cpu_cap_clues()
        # the offer (20 V x 3.25 A), not the unreliable voltage_now (5 V)
        self.assertEqual(clues, "lap mode on, USB-C charger up to 20 V 3.25 A (65 W), power profile balanced, "
                                "battery discharging 12.3 W, CPU 41 C")


class PowerProfile(unittest.TestCase):
    """ENV_POWER_PROFILE: the standard power profile, through power-profiles-daemon or the kernel file."""

    DAEMON_LIST = ("  performance:\n    CpuDriver:\tintel_pstate\n    Degraded:   {deg}\n\n"
                   "* balanced:\n    CpuDriver:\tintel_pstate\n\n  power-saver:\n    CpuDriver:\tintel_pstate\n")

    def firmware(self, now="balanced", choices="low-power balanced performance"):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "platform_profile")
        for f, v in ((path, now), (path + "_choices", choices)):
            with open(f, "w") as fh:
                fh.write(v + "\n")
        return d, path

    def test_kernel_file_set_and_restored(self):
        import prepare_environment as pe
        import run_metadata as m
        from unittest import mock
        d, path = self.firmware()
        state = {}
        with mock.patch.object(m, "PLATFORM_PROFILE", path), mock.patch.object(m, "_power_profiles_daemon", lambda *a: None):
            pe.set_power_profile("performance", state)
            self.assertEqual(m.power_profile(), ("performance", "firmware"))
            self.assertEqual(state["power_profile"], {"prev": "balanced", "manager": "firmware"})
            self.assertTrue(pe.write_power_profile("balanced", "firmware"))            # the restore
            self.assertEqual(m.power_profile(), ("balanced", "firmware"))
        shutil.rmtree(d)

    def test_not_offered_or_not_there(self):
        import prepare_environment as pe
        import run_metadata as m
        from unittest import mock
        d, path = self.firmware(choices="low-power balanced")
        state = {}
        with mock.patch.object(m, "PLATFORM_PROFILE", path), mock.patch.object(m, "_power_profiles_daemon", lambda *a: None):
            pe.set_power_profile("performance", state)                             # not offered: left as it is
            self.assertEqual(m.power_profile()[0], "balanced")
        with mock.patch.object(m, "PLATFORM_PROFILE", os.path.join(d, "none")), \
                mock.patch.object(m, "_power_profiles_daemon", lambda *a: None):
            self.assertEqual(m.power_profile(), ("", ""))                          # a desktop, server or VM
            pe.set_power_profile("performance", state)
        self.assertEqual(state, {})                                                # nothing to restore
        shutil.rmtree(d)

    def test_through_the_daemon_never_behind_its_back(self):
        import prepare_environment as pe
        import run_metadata as m
        from unittest import mock
        answers = {("get",): "balanced\n", ("list",): self.DAEMON_LIST.format(deg="no")}
        calls = []
        with mock.patch.object(m, "_power_profiles_daemon", lambda *a: answers.get(a)), \
                mock.patch.object(pe.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or mock.Mock(returncode=0)):
            self.assertEqual(m.power_profile_choices(), ["performance", "balanced", "low-power"])
            state = {}
            pe.set_power_profile("performance", state)
            pe.write_power_profile("low-power", "daemon")
        self.assertEqual(calls, [["powerprofilesctl", "set", "performance"], ["powerprofilesctl", "set", "power-saver"]])
        self.assertEqual(state["power_profile"], {"prev": "balanced", "manager": "daemon"})

    def test_degraded_reason_is_a_clue(self):
        import run_metadata as m
        from unittest import mock
        with mock.patch.object(m, "_power_profiles_daemon",
                               lambda *a: self.DAEMON_LIST.format(deg="yes (lap-detected)") if a == ("list",) else "balanced\n"):
            self.assertEqual(m.performance_degraded(), "lap-detected")
        with mock.patch.object(m, "_power_profiles_daemon", lambda *a: None):
            self.assertEqual(m.performance_degraded(), "")

    def test_config_and_profiles(self):
        import bench_config
        self.assertEqual(bench_config.parse("")["ENV_POWER_PROFILE"], "performance")
        with self.assertRaises(bench_config.ConfigError):
            bench_config.parse("ENV_POWER_PROFILE=turbo")
        for name, want in (("minimal", "performance"), ("untouched", "unchanged")):
            with open(os.path.join(ROOT, "configs", "machine", f"{name}.config")) as fh:
                self.assertIn(f"ENV_POWER_PROFILE={want}\n", fh.read())
        import run_metadata
        self.assertIn("power_profile", run_metadata.STABLE_KEYS)                  # a change mid-run is recorded


class ResultsIndex(unittest.TestCase):
    """manifest.jsonl (one line per measurement) and results/index.json (every folder): an index only."""

    FACTS = {"language": "erlang", "kind": "pure", "framework": "none"}

    def run_dir(self, root, name, meta=None, plan=None):
        d = os.path.join(root, name)
        os.makedirs(os.path.join(d, "static"))
        with open(os.path.join(d, "metadata.json"), "w") as fh:
            json.dump(meta if meta is not None else {"started_at_utc": "2026-10-07T10:00:00Z"}, fh)
        if plan:
            with open(os.path.join(d, "plan.json"), "w") as fh:
                json.dump(plan, fh)
        return d

    def csv_row(self, path, values):
        new = not os.path.exists(path)
        with open(path, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(values))
            if new:
                w.writeheader()
            w.writerow(values)

    VALUES = {"Container Name": "st-x-nobw-native", "Variant": "nobw", "Deploy": "native", "Repeat": 2, "Session": 1,
              "Measured At (UTC)": "2026-10-07T10:05:00Z", "Raw Log": "raw/a.json", "Total Requests": 1000,
              "Type": "static", "HTTP Max Workers": "100", "HTTP Connection Mode": "reuse"}

    def test_record_valid_and_invalid(self):
        import results_index as ri
        import load_conditions as lc
        from unittest import mock
        root = tempfile.mkdtemp()
        d = self.run_dir(root, "2026-10-07_100000")
        out = os.path.join(d, "static", "st-x-nobw-native.csv")
        self.csv_row(out, self.VALUES)
        w = ri.http_workload("static", 1000, "100", "reuse")
        with mock.patch.object(ri, "server_facts", lambda image: self.FACTS):
            ri.record(out, self.VALUES, "st-x-nobw", w)
            # an invalid run: kept in invalid_runs.csv and listed too, through the real judge
            with mock.patch.object(lc, "problems", lambda *a: ["CPU speed capped at 800 MHz"]), \
                    mock.patch.object(lc, "rules", lambda: {}), self.assertRaises(SystemExit):
                lc.judge(None, self.VALUES, out, "static 1000 requests", "st-x-nobw", w)
        lines = ri.measurements(d)
        self.assertEqual(len(lines), 2)
        good, bad = lines
        self.assertEqual((good["server"], good["image"], good["variant"], good["deploy"]), ("st-x", "st-x-nobw", "nobw", "native"))
        self.assertEqual((good["csv"], good["csv_row"], good["valid"], good["facts"]), ("static/st-x-nobw-native.csv", 1, True, self.FACTS))
        self.assertEqual(ri.row(d, good)["Container Name"], "st-x-nobw-native")
        self.assertEqual((bad["valid"], bad["csv"], bad["reason"]), (False, "invalid_runs.csv", "CPU speed capped at 800 MHz"))
        self.assertEqual(ri.row(d, bad)["Reason"], "CPU speed capped at 800 MHz")
        shutil.rmtree(root)

    def test_nothing_outside_a_measurement_folder_and_cut_lines(self):
        import results_index as ri
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "loose", "static"))
        ri.record(os.path.join(root, "loose", "static", "a.csv"), self.VALUES, "st-x", {})   # a manual run
        self.assertFalse(os.path.exists(os.path.join(root, "loose", ri.MANIFEST)))
        d = self.run_dir(root, "r")
        with open(os.path.join(d, ri.MANIFEST), "w") as fh:
            fh.write('{"server": "st-a", "valid": true}\n{"server": "st-b", "va')      # a crash cut the last line
        self.assertEqual([m["server"] for m in ri.measurements(d)], ["st-a"])
        shutil.rmtree(root)

    def test_catalog_statuses(self):
        import results_index as ri
        root = tempfile.mkdtemp()
        self.run_dir(root, "a-finished", {"started_at_utc": "1", "finished_at_utc": "2"}, {"name": "paper", "total": 4})
        open(os.path.join(self.run_dir(root, "b-running"), ".running"), "w").close()
        self.run_dir(root, "c-unfinished")
        self.run_dir(root, "d-abandoned", {"started_at_utc": "1", "abandoned_at_utc": "2"})
        os.makedirs(os.path.join(root, "not-a-run"))
        path, data = ri.write_catalog(root)
        self.assertEqual([(r["folder"], r["status"]) for r in data["runs"]],
                         [("a-finished", "finished"), ("b-running", "running"), ("c-unfinished", "unfinished"),
                          ("d-abandoned", "abandoned")])
        self.assertEqual((data["runs"][0]["config"], data["runs"][0]["planned"]), ("paper", 4))
        self.assertEqual([r["folder"] for r in ri.runs(root)], [r["folder"] for r in data["runs"]])
        self.assertFalse(os.path.exists(path + ".tmp"))                       # written whole, then renamed
        shutil.rmtree(root)

    def test_backfill_an_older_folder(self):
        import results_index as ri
        from unittest import mock
        root = tempfile.mkdtemp()
        d = self.run_dir(root, "old")
        self.csv_row(os.path.join(d, "static", "st-x-nobw-native.csv"), self.VALUES)
        self.csv_row(os.path.join(d, "static", "st-x_summary.csv"), {"a": 1})               # statistics: not runs
        self.csv_row(os.path.join(d, "invalid_runs.csv"), {**self.VALUES, "Measurement": "static 1000 requests",
                                                           "Reason": "too hot"})
        with mock.patch.object(ri, "server_facts", lambda image: self.FACTS):
            path, n = ri.backfill(d)
            self.assertEqual(n, 2)
            self.assertEqual(ri.backfill(d), (path, 0))                       # never overwrites
        lines = ri.measurements(d)
        self.assertEqual([(m["server"], m["valid"], m["workload"].get("requests")) for m in lines],
                         [("st-x", True, "1000"), ("st-x", False, None)])
        self.assertTrue(all(m["backfilled"] for m in lines))
        shutil.rmtree(root)

    def test_measuring_scripts_record_every_measurement(self):
        with open(os.path.join(ROOT, "tools", "measure_core.py")) as fh:        # every workload, every deploy
            src = fh.read()
        self.assertIn("results_index.record(output_csv, values, args.server_image, index)", src)
        self.assertIn("workload.measurement(args), args.server_image, index)", src)   # invalid runs too, via judge
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            self.assertEqual(fh.read().count("./tools/results_index.py index"), 2)  # at the start and the end


class ServerLabels(unittest.TestCase):
    """Every server says what it is (wseb.* labels) and pins its dependencies (a lock file)."""

    def test_every_server_is_labelled_and_locked(self):
        import re
        folders = [r for r, _, f in os.walk(os.path.join(ROOT, "benchmarks")) if "Dockerfile" in f]
        self.assertGreaterEqual(len(folders), 34)
        for folder in folders:
            with open(os.path.join(folder, "Dockerfile")) as fh:
                text = fh.read().replace("\\\n", " ")
            labels = dict(re.findall(r'wseb\.([a-z_]+)="([^"]*)"', text))
            name = os.path.basename(folder)
            for key in ("language", "language_version", "runtime", "runtime_version", "kind", "framework", "framework_version"):
                self.assertIn(key, labels, f"{name}: LABEL wseb.{key} missing")
            self.assertIn(labels["kind"], ("pure", "index", "framework"), name)
            self.assertIn(labels["runtime"], ("beam", "jvm"), name)
            self.assertEqual(labels["framework"] == "none", labels["kind"] != "framework", name)
            self.assertIn(f"-{labels['language']}-", name)
            locks = [f for f in ("rebar.lock", "mix.lock", "manifest.toml", "pom.xml") if os.path.exists(os.path.join(folder, f))]
            mix = os.path.join(folder, "mix.exs")
            no_deps = os.path.exists(mix) and re.search(r"deps(: |, do: )\[\]", open(mix).read())
            self.assertTrue(locks or no_deps, f"{name}: no lock file (and it has dependencies)")
            if "rebar.lock" in locks:
                self.assertIn("COPY rebar.config rebar.lock ./", text, f"{name}: the lock is not used by the build")


class CpuCapAdvice(unittest.TestCase):
    """A capped CPU's waiting message says what a person can do about it."""

    def advice(self, files, degraded=""):
        import run_metadata as m
        from unittest import mock
        with mock.patch.object(m, "_read", files.get), mock.patch.object(m, "performance_degraded", lambda: degraded), \
                mock.patch.object(m.glob, "glob", lambda g: ["/p/BAT0"] if g.endswith("BAT*") else []):
            return m.cpu_cap_advice()

    def test_lap_heat_and_weak_charger(self):
        self.assertIn("lap sensor is on", self.advice({"/sys/devices/platform/thinkpad_acpi/dytc_lapmode": "1"}))
        self.assertIn("lap sensor is on", self.advice({}, degraded="lap-detected"))          # any brand, via the daemon
        self.assertIn("ENV_CPU_SPEED=800", self.advice({}, degraded="lap-detected"))
        self.assertIn("too hot", self.advice({}, degraded="high-operating-temperature"))
        self.assertIn("battery drains", self.advice({"/p/BAT0/status": "Discharging"}))
        self.assertEqual(self.advice({"/p/BAT0/status": "Full"}), "")                      # nothing known: no advice


class QuietMachine(unittest.TestCase):
    """Who keeps the machine busy (reported), maintenance timers and named services paused and restored."""

    def test_busy_programs_from_proc(self):
        import readiness
        d = tempfile.mkdtemp()

        def proc(pid, name, ticks, cmdline=b"x\0"):
            os.makedirs(os.path.join(d, str(pid)), exist_ok=True)
            with open(os.path.join(d, str(pid), "stat"), "w") as fh:            # fields 14, 15: utime, stime
                fh.write(f"{pid} ({name}) S 1 1 1 0 -1 0 0 0 0 0 {ticks} 0 0 0 20 0\n")
            with open(os.path.join(d, str(pid), "cmdline"), "wb") as fh:
                fh.write(cmdline)
        proc(10, "code", 100)
        proc(11, "code", 50)
        proc(12, "Web (Content)", 5)
        proc(13, "kworker/0:1", 0, cmdline=b"")                                # a kernel thread: left out
        before = readiness.process_cpu_ticks(d)
        self.assertEqual(sorted(before), [10, 11, 12])
        proc(10, "code", 300)                                                   # +200 + 100 ticks in 10 s
        proc(11, "code", 150)
        proc(12, "Web (Content)", 15)
        busy = readiness.busy_programs(before, readiness.process_cpu_ticks(d), 10, ticks_per_s=100, skip={12})
        self.assertEqual(busy, [("code", 30.0)])                                # 30 % of one core
        shutil.rmtree(d)

    def test_pause_and_restore(self):
        import prepare_environment as pe
        import run_metadata as m
        from unittest import mock
        active = {"apt-daily.timer", "fstrim.timer", "cups.service"}
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[:2] == ["systemctl", "stop"]:
                active.difference_update(cmd[2:])
            if cmd[:2] == ["systemctl", "start"]:
                active.update(n for n in cmd[2:] if n != "broken.service")
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(pe.subprocess, "run", run), \
                mock.patch.object(m, "active_units", lambda names: [n for n in names if n in active]):
            state = {}
            pe.pause_units("Maintenance timers", pe.units_from("maintenance", m.MAINTENANCE_TIMERS), state)
            pe.pause_units("Services", pe.service_names("cups"), state)
            self.assertEqual(state["paused_units"], ["apt-daily.timer", "fstrim.timer", "cups.service"])
            self.assertEqual(active, set())
            self.assertNotIn("disable", " ".join(" ".join(c) for c in calls))   # stopped only, never disabled
            with self.assertRaises(SystemExit):
                pe.service_names("dbus")                                          # protected: refused
        self.assertEqual(pe.units_from("none", ("a",)), [])
        self.assertEqual(pe.units_from("apt-daily.timer, fstrim.timer", ()), ["apt-daily.timer", "fstrim.timer"])

    def test_protected_services(self):
        import run_metadata as m
        import bench_config
        for name in ("dbus", "systemd-journald", "user@1000.service", "NetworkManager", "docker", "ssh",
                     "power-profiles-daemon", "gdm"):
            self.assertTrue(m.protected_service(name), name)
        for name in ("cups", "packagekit.service", "fwupd", "colord"):
            self.assertFalse(m.protected_service(name), name)
        with self.assertRaises(bench_config.ConfigError):
            bench_config.parse("ENV_PAUSE_SERVICES=cups docker")
        with self.assertRaises(bench_config.ConfigError):
            bench_config.parse("ENV_PAUSE_TIMERS=apt-daily")                      # must end in .timer
        cfg = bench_config.parse("")
        self.assertEqual((cfg["ENV_PAUSE_TIMERS"], cfg["ENV_PAUSE_SERVICES"]), ("maintenance", ""))
        with open(os.path.join(ROOT, "configs", "machine", "untouched.config")) as fh:
            self.assertIn("ENV_PAUSE_TIMERS=none\n", fh.read())


class MeasuringCore(unittest.TestCase):
    """One measuring core for every workload and deploy; the parts that differ are plugins."""

    def test_plugins_are_found_by_file_name(self):
        import plugins
        self.assertEqual(plugins.names("deploy"), ["container", "native"])
        self.assertEqual(plugins.names("workload"), ["http", "websocket"])
        self.assertEqual(plugins.names("meter"), ["scaphandre"])
        with self.assertRaises(ValueError):
            plugins.load("deploy", "kubernetes")                               # not there (yet): a clear error
        from plugins.deploy import Deploy
        from plugins.workload import Workload
        for kind, base in (("deploy", Deploy), ("workload", Workload)):
            for name in plugins.names(kind):
                cls = plugins.load(kind, name)
                self.assertTrue(issubclass(cls, base), name)
                for method in vars(base):                                       # the whole interface, no gaps
                    if callable(getattr(base, method)) and not method.startswith("_") and method not in ("add_arguments", "warm_up", "summary"):
                        self.assertIsNot(getattr(cls, method), getattr(base, method), f"{kind}/{name} lacks {method}")

    def test_entry_points_are_thin(self):
        for name, workload in (("measure_docker.py", '"http"'), ("measure_websocket.py", '"websocket"')):
            with open(os.path.join(ROOT, "tools", name)) as fh:
                src = fh.read()
            self.assertIn(f"measure_core.main(__file__, {workload}", src)
            self.assertLess(len(src.splitlines()), 20)                           # nothing of the method lives here

    def test_same_options_as_before(self):
        import subprocess
        for name, opts in (("measure_docker.py", ["--num_requests", "--max_workers", "--connection", "--measurement_type"]),
                           ("measure_websocket.py", ["--pattern", "--clients", "--size_kb", "--rate", "--bursts", "--interval",
                                                     "--duration", "--url"])):
            out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", name), "--help"], capture_output=True, text=True).stdout
            for opt in opts + ["--server_image", "--container_name", "--port_mapping", "--deploy", "--output_csv",
                               "--warmup_s", "--idle_s", "--waited_s", "--ready_check", "--repeat", "--cooldown"]:
                self.assertIn(opt, out, f"{name} {opt}")


class UnfinishedMeasurement(unittest.TestCase):
    """A fresh run of a config with an unfinished measurement asks: continue (default), from zero, stop."""
    CONFIG = "MEASURE=static\nSERVERS=st-a\nHTTP_REQUESTS=1000\n# unfinished-test\n"

    def setUp(self):
        self.h = WhatToMeasure("test_selected_servers_and_kinds")
        self.h.setUp()
        self.old = os.path.join(ROOT, "results", "2000-01-01_000000-unfinished-test")
        os.makedirs(self.old)
        with open(os.path.join(self.old, "bench.config"), "w") as fh:
            fh.write(WhatToMeasure.config_text(self.CONFIG))
        with open(os.path.join(self.old, "metadata.json"), "w") as fh:
            json.dump({"started_at_utc": "2000-01-01T00:00:00Z"}, fh)
        with open(os.path.join(self.old, "progress.txt"), "w") as fh:
            fh.write("pass 1 | a\npass 1 | b\n")
        with open(os.path.join(self.old, "plan.json"), "w") as fh:
            json.dump({"total": 48}, fh)

    def tearDown(self):
        shutil.rmtree(self.old, ignore_errors=True)

    def test_found_with_progress(self):
        import run_metadata
        cfg = os.path.join(self.h.d, "same.config")
        with open(cfg, "w") as fh:
            fh.write(WhatToMeasure.config_text(self.CONFIG))
        self.assertEqual(run_metadata.unfinished(os.path.join(ROOT, "results"), cfg),
                         f"SAME {self.old} 2 48 yes")
        with open(cfg, "a") as fh:
            fh.write("REPEATS=2\n")                                       # another config: only a notice
        self.assertTrue(run_metadata.unfinished(os.path.join(ROOT, "results"), cfg).startswith("OTHER "))

    def test_stop(self):
        rc, out = self.h.run_until_plan(self.CONFIG, extra_env={"BENCH_UNFINISHED_ANSWER": "s"})
        self.assertEqual(rc, 0)
        self.assertIn("Stopped. Continue later with: make resume RESUME=results/2000-01-01_000000-unfinished-test", out)

    def test_from_zero_marks_the_old_one_abandoned(self):
        import run_metadata
        rc, out = self.h.run_until_plan(self.CONFIG, extra_env={"BENCH_UNFINISHED_ANSWER": "n"})
        self.assertIn("is marked abandoned", out)
        self.assertIn("Static HTTP:     1 containers", out)                   # the new run went on
        self.assertTrue(os.path.exists(os.path.join(self.old, ".abandoned")))
        with open(os.path.join(self.old, "metadata.json")) as fh:
            self.assertIn("abandoned_at_utc", json.load(fh))
        self.assertEqual(run_metadata.latest_unfinished(os.path.join(ROOT, "results")).endswith("unfinished-test"), False)

    def test_no_answer_continues(self):
        # no terminal in the test (like a run started in the background): continue the unfinished one
        rc, out = self.h.run_until_plan(self.CONFIG)
        self.assertIn("Continuing the unfinished measurement results/2000-01-01_000000-unfinished-test", out)

    def make_unresumable(self):
        with open(os.path.join(self.old, "metadata.json"), "w") as fh:     # an image it used is gone now
            json.dump({"started_at_utc": "2000-01-01T00:00:00Z", "images_at_start": {"st-zzz-gone": "sha256:x"}}, fh)

    def test_cannot_be_continued_starts_from_zero(self):
        import run_metadata
        self.make_unresumable()
        cfg = os.path.join(self.h.d, "same.config")
        with open(cfg, "w") as fh:
            fh.write(WhatToMeasure.config_text(self.CONFIG))
        self.assertEqual(run_metadata.unfinished(os.path.join(ROOT, "results"), cfg), f"SAME {self.old} 2 48 no")
        rc, out = self.h.run_until_plan(self.CONFIG)                         # no answer: from zero, not continue
        self.assertIn("cannot be continued", out)
        self.assertIn("is marked abandoned", out)
        self.assertNotIn("Continuing the unfinished measurement", out)
        self.assertIn("Static HTTP:     1 containers", out)

    def test_cannot_be_continued_even_when_asked(self):
        self.make_unresumable()
        rc, out = self.h.run_until_plan(self.CONFIG, extra_env={"BENCH_UNFINISHED_ANSWER": "c"})
        self.assertIn("is marked abandoned", out)
        self.assertNotIn("Continuing the unfinished measurement", out)

    def test_graphs_skip_an_abandoned_measurement(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import gui_graph_generator as g
        f = os.path.join(self.old, "static", "st-a.csv")
        self.assertFalse(g.in_abandoned_measurement(f))
        open(os.path.join(self.old, ".abandoned"), "w").close()
        self.assertTrue(g.in_abandoned_measurement(f))


class Deploy(WhatToMeasure):
    """DEPLOY: each server (and variant) also measured natively as <server>-native, in the same order."""
    start_script = "APP_DIR"

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.bin, "docker")) as fh:         # + docker cp: /app and /start.sh of the image
            fake = fh.read()
        fake = fake.replace("#!/bin/sh\n", '#!/bin/sh\nif [ "$1" = "cp" ]; then case "$2" in *:/app) mkdir -p "$3" ;; '
                            '*:/start.sh) printf "%s" "$FAKE_START" > "$3" ;; esac; exit 0; fi\n'
                            'if [ "$1 $2 $4" = "image inspect {{.Id}}" ]; then echo "sha256:id-$5"; exit 0; fi\n'
                            # the files: a -nobw variant has its server's layers
                            'if [ "$1 $2 $4" = "image inspect {{json .RootFS.Layers}}" ]; then '
                            'echo "[\\"sha256:l-${5%-nobw}\\"]"; exit 0; fi\n', 1)
        with open(os.path.join(self.bin, "docker"), "w") as fh:
            fh.write(fake)
        self.native = os.path.join(self.d, "native")

    def run_until_plan(self, config, args=(), images="st-a st-b dy-c ws-d my-img"):
        from unittest import mock
        with mock.patch.dict(os.environ, {"MEASURE_NATIVE_DIR": self.native, "FAKE_START": self.start_script}):
            return super().run_until_plan(config, args, images)

    def test_both_ways(self):
        rc, out = self.run_until_plan("MEASURE=static\nSERVERS=st-a st-b\nHTTP_REQUESTS=1000\nDEPLOY=container native\n"
                                      "VARIANTS=nobw:ERL_FLAGS=+sbwt none\n")
        self.assertIn("Static HTTP:     8 containers × 1 levels = 8", out)       # 2 servers x 2 variants x 2 ways
        # one copy per server: its -nobw variant has the same files (the copy is named after whichever came first)
        copies = sorted(os.listdir(self.native))
        self.assertEqual(len(copies), 2)
        self.assertEqual(sorted(c.replace("-nobw", "") for c in copies), ["st-a", "st-b"])
        self.assertEqual(out.count("Preparing native copies"), 4)                # every image is still checked

    def test_native_only(self):
        rc, out = self.run_until_plan("MEASURE=static\nSERVERS=st-a\nHTTP_REQUESTS=1000\nDEPLOY=native\n")
        self.assertIn("Static HTTP:     1 containers × 1 levels = 1", out)

    def test_container_is_the_default(self):
        rc, out = self.run_until_plan("MEASURE=static\nSERVERS=st-a\nHTTP_REQUESTS=1000\n")
        self.assertIn("Static HTTP:     1 containers × 1 levels = 1", out)
        self.assertFalse(os.path.exists(self.native))

    def test_server_without_the_contract_stops_before_anything(self):
        self.start_script = "exec /app/server"
        rc, out = self.run_until_plan("MEASURE=static\nSERVERS=st-a\nDEPLOY=container native\n")
        self.assertEqual(rc, 1)
        self.assertIn("DEPLOY=native: these servers cannot run without Docker", out)
        self.assertIn("st-a: its start.sh does not use APP_DIR", out)
        self.assertNotIn("Machine settings", out)

    def test_config_checks(self):
        import bench_config
        self.assertEqual(bench_config.parse("DEPLOY=native  container")["DEPLOY"], "native container")
        self.assertEqual(bench_config.parse("")["DEPLOY"], "container")
        for bad in ("DEPLOY=", "DEPLOY=docker", "DEPLOY=native native"):
            with self.assertRaises(bench_config.ConfigError):
                bench_config.parse(bad)

    def test_native_items_run_their_image(self):
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            src = fh.read()
        self.assertIn('[ "$prev" = "--server_image" ] && a="$native_of"', src)
        self.assertIn('tool_args+=(--container_name "$m_server" --deploy native)', src)

    def test_csv_column_and_summary_key(self):
        import aggregate_repeats
        import csv_columns
        self.assertEqual(csv_columns.RUN[:3], ["Container Name", "Variant", "Deploy"])
        self.assertEqual(csv_columns.run_fields("native")["Deploy"], "native")
        self.assertIn("Deploy", aggregate_repeats.KEY_COLS)

    def test_image_id_of_a_native_item(self):
        import run_metadata
        from unittest import mock
        ids = {"st-a": "sha256:0123456789abcdef"}
        with mock.patch.object(run_metadata, "_run", lambda cmd: ids.get(cmd[-1], "")):
            self.assertEqual(run_metadata.image_id("st-a-native"), "0123456789ab")
            self.assertEqual(run_metadata.image_id("st-zzz"), "")



class ServerContract(unittest.TestCase):
    """README server contract: only the server runs in its box (recorded), native only on the host's OS."""

    def test_processes_of_a_box(self):
        import server_box
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "cgroup.procs"), "w") as fh:
            fh.write(f"{os.getpid()}\n{NOPID}\n")                  # a gone process is skipped
        with open(f"/proc/{os.getpid()}/comm") as fh:
            me = fh.read().strip()
        self.assertEqual(server_box.processes(d), [me])
        self.assertEqual(server_box.processes(os.path.join(d, "missing")), [])
        shutil.rmtree(d)

    def test_description_and_helpers(self):
        import server_box
        names = ["sh", "beam.smp", "epmd", "sh", "erl_child_setup"]
        self.assertEqual(server_box.describe(names), "beam.smp, epmd, erl_child_setup, 2x sh")
        self.assertEqual(server_box.helpers(names), ["epmd (Erlang port mapper (the node has a name))"])
        self.assertEqual(server_box.helpers(["beam.smp", "erl_child_setup"]), [])
        self.assertEqual(server_box.helpers(["java"]), [])

    def test_recorded_with_every_run(self):
        import csv_columns
        self.assertEqual(csv_columns.RUN_QUALITY[-1], "Server Processes")
        self.assertIn("Server Processes", csv_columns.NOT_MEASURED)
        with open(os.path.join(ROOT, "tools", "measure_core.py")) as fh:
            src = fh.read()
        self.assertIn('"Server Processes": server_box.describe(box_processes)', src)
        self.assertLess(src.index("box_processes = server_box.processes(deploy.box())"), src.index("\n    deploy.stop()\n"))

    def bundle(self, start="exec $APP_DIR/bin/x", os_release='ID=debian\nVERSION_ID="13"\n'):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "start.sh"), "w") as fh:
            fh.write(start)
        with open(os.path.join(d, "os-release"), "w") as fh:
            fh.write(os_release)
        host = os.path.join(d, "host-os-release")
        with open(host, "w") as fh:
            fh.write('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\nVERSION_ID="13"\n')
        return d, host

    def problem(self, **kw):
        import native_server
        from unittest import mock
        d, host = self.bundle(**kw)
        with mock.patch.object(native_server, "unpack", return_value=d):
            why = native_server.problem("img", host_os_release=host)
        shutil.rmtree(d)
        return why

    def test_native_same_os(self):
        self.assertEqual(self.problem(), "")
        self.assertEqual(self.problem(os_release=""), "")                    # no OS in the image (FROM scratch)
        self.assertEqual(self.problem(os_release='ID=alpine\nVERSION_ID=3.23.6\n'),
                         "built on alpine 3.23.6, but this machine runs debian 13 (build it on the host's OS)")
        self.assertIn("debian 12", self.problem(os_release='ID=debian\nVERSION_ID="12"\n'))

    def test_native_all_problems_listed(self):
        why = self.problem(start="exec /app/bin/x", os_release="ID=alpine\nVERSION_ID=3.23.6\n")
        self.assertIn("does not use APP_DIR", why)
        self.assertIn("built on alpine", why)



class LoadConditions(unittest.TestCase):
    """tools/load_conditions.py: the machine is watched during every load; a run that broke a rule is
    kept in invalid_runs.csv and measured again (exit code 4), the same way for every server."""

    def watch(self, limits=(1800, 1800), speeds=(1795.0, 1801.0), power=("yes", "yes")):
        import load_conditions as lc
        w = lc.Watch()
        w.limits, w.speeds, w.power = list(limits), list(speeds), list(power)
        return w

    def test_samples_during_the_load(self):
        import load_conditions as lc
        from unittest import mock
        limits = iter([1800, 800, 1800, 1800, 1800, 1800])
        with mock.patch.object(lc.run_metadata, "cpu_speed_limit_mhz", lambda: next(limits, 1800)), \
                mock.patch.object(lc, "cpu_avg_speed_mhz", lambda: 1800.0), \
                mock.patch.object(lc.run_metadata, "ac_power", lambda: "yes"):
            w = lc.Watch(interval=0.01).start()
            time.sleep(0.1)
            w.stop()
        self.assertEqual(w.fields(), {"Host CPU Speed Limit Min (MHz)": 800, "Host CPU Avg Speed (MHz)": 1800})

    def test_rules(self):
        import load_conditions as lc
        rule = {"cpu_speed": "auto", "no_throttling": True, "on_battery": "wait"}
        self.assertEqual(lc.problems(self.watch(), 0, rule, expected_mhz=1800), [])
        self.assertEqual(lc.problems(self.watch(limits=(1800, 800, 1800)), 0, rule, expected_mhz=1800),
                         ["CPU speed capped at 800 MHz < 1800 MHz during the load (charger or firmware)"])
        self.assertEqual(lc.problems(self.watch(), 12, rule, expected_mhz=1800),
                         ["thermal throttling during the load (12 ms)"])
        self.assertEqual(lc.problems(self.watch(limits=(800,)), 12, {}), [])          # no config: not judged
        off = {"cpu_speed": "off", "no_throttling": False, "on_battery": "ignore"}
        self.assertEqual(lc.problems(self.watch(limits=(800,), power=("no",)), 12, off), [])
        self.assertEqual(lc.problems(self.watch(limits=(1500,)), 0, {"cpu_speed": "1400"}), [])   # fixed MHz

    def test_rules_come_from_the_config(self):
        import load_conditions as lc
        self.assertEqual(lc.rules({}), {})
        env = {"MEASURE_READY_ON_TIMEOUT": "wait", "MEASURE_READY_CPU_SPEED": "auto",
               "MEASURE_READY_NO_THROTTLING": "0", "MEASURE_ON_BATTERY": "stop"}
        self.assertEqual(lc.rules(env), {"cpu_speed": "auto", "no_throttling": False, "on_battery": "stop"})

    def test_invalid_run_is_kept_not_added(self):
        import load_conditions as lc
        from unittest import mock
        d = tempfile.mkdtemp()
        out = os.path.join(d, "static", "st-x.csv")
        os.makedirs(os.path.dirname(out))
        values = {"Container Name": "st-x", "Variant": "nobw", "Deploy": "native", "Requests/s": 314.0,
                  "Host CPU Speed Limit Min (MHz)": 800, "Host Throttled (ms)": 0, "Raw Log": "raw/a.json"}
        env = {"MEASURE_READY_ON_TIMEOUT": "wait", "MEASURE_READY_CPU_SPEED": "1800"}
        with mock.patch.dict(os.environ, env), self.assertRaises(SystemExit) as ended, \
                self.assertLogs(level="WARNING"):
            lc.judge(self.watch(limits=(800,)), values, out, "static 20000 requests")
        self.assertEqual(ended.exception.code, lc.EXIT_INVALID)
        self.assertFalse(os.path.exists(out))                                        # not in the results
        with open(os.path.join(d, "invalid_runs.csv")) as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["Container Name"], rows[0]["Deploy"], rows[0]["Rate (/s)"], rows[0]["Measurement"]),
                         ("st-x", "native", "314.0", "static 20000 requests"))
        self.assertIn("CPU speed capped at 800 MHz", rows[0]["Reason"])
        with mock.patch.dict(os.environ, env):
            lc.judge(self.watch(), values, out, "static 20000 requests")              # valid: returns
        shutil.rmtree(d)

    def test_wired_into_every_measurement(self):
        import csv_columns
        self.assertIn("Host CPU Speed Limit Min (MHz)", csv_columns.HOST)
        self.assertIn("Host CPU Avg Speed (MHz)", csv_columns.NOT_MEASURED)
        with open(os.path.join(ROOT, "tools", "measure_core.py")) as fh:
            src = fh.read()
        self.assertIn("watch = load_conditions.Watch().start()", src)
        self.assertLess(src.index("load_conditions.judge("), src.index("csv_columns.append(output_csv, workload.columns, values)"))
        self.assertNotIn("charger_unplugged", src)
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            src = fh.read()
        self.assertIn('[ "$rc" = 4 ] || break', src)
        self.assertIn('if [ "$tries" -gt "${CFG_INVALID_RUN_RETRIES:-3}" ]; then', src)

    def measure(self, invalid_times, retries=3):
        """Run the script's own bench_measure with a fake tool that ends invalid `invalid_times` times."""
        d = tempfile.mkdtemp()
        tool = os.path.join(d, "tool")
        with open(tool, "w") as fh:
            fh.write(f'#!/bin/sh\nn=$(cat "{d}/n" 2>/dev/null || echo 0); echo $((n + 1)) > "{d}/n"\n'
                     f'[ "$n" -lt {invalid_times} ] && exit 4\nexit 0\n')
        os.chmod(tool, 0o755)
        harness = f"""
            print_status() {{ echo "[$1] $2"; }}
            bench_ready_gate() {{ echo gate >> "{d}/gates"; BENCH_GATE_ARGS=(); }}
            bench_family() {{ echo static; }}
            bench_record_failure() {{ echo "failure: $2" >> "{d}/failures"; }}
            declare -A BENCH_NATIVE_OF=() BENCH_VARIANT_OF=()
            RESULTS_DIR="{d}"; PYTHON_PATH="{tool}"; CONFIG_FILE=x; BENCH_PASS=1; BENCH_FAILURES=0
            BENCH_FAILED_IN_A_ROW=0; CFG_FAILURES_STOP_AFTER=0; CFG_INVALID_RUN_RETRIES={retries}
            eval "$(sed -n '/^BENCH_CHILD=""$/,/^bench_restore_environment() {{$/p' "{ROOT}/scripts/run_benchmarks.sh" | sed '$d')"
            eval "$(sed -n '/^bench_measure() {{/,/^}}/p' "{ROOT}/scripts/run_benchmarks.sh")"
            bench_measure ./tools/measure_docker.py --server_image st-x --num_requests 10 --output_csv "{d}/x.csv"
            echo "failures=$BENCH_FAILURES"
        """
        out = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, timeout=30).stdout

        def read(name):
            if not os.path.exists(os.path.join(d, name)):
                return ""
            with open(os.path.join(d, name)) as fh:
                return fh.read()
        result = (int(read("n") or 0), read("gates").count("gate"), read("progress.txt"), read("failures"), out)
        shutil.rmtree(d)
        return result

    def test_invalid_run_is_measured_again(self):
        tries, gates, progress, failures, out = self.measure(invalid_times=2)
        self.assertEqual(tries, 3)                                   # 2 invalid, then a valid one
        self.assertEqual(gates, 3)                                   # readiness checked before every try
        self.assertIn("--server_image st-x", progress)              # done: counted once
        self.assertEqual(failures, "")
        self.assertEqual(out.count("measuring it again"), 2)

    def test_still_invalid_after_the_retries_is_a_failure(self):
        tries, gates, progress, failures, out = self.measure(invalid_times=10, retries=2)
        self.assertEqual(tries, 3)                                   # the first try + 2 retries
        self.assertEqual(progress, "")                              # not done: resume measures it again
        self.assertIn("broke a rule in 3 tries", failures)
        self.assertIn("failures=1", out)

    def test_run_lists_are_not_results(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import run_metadata
        d = tempfile.mkdtemp()
        for name in ("static/st-x.csv", "invalid_runs.csv", "failures.csv"):
            os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
            open(os.path.join(d, name), "w").close()
        self.assertEqual([os.path.relpath(p, d) for p in run_metadata.csvs_in(d)], ["static/st-x.csv"])
        shutil.rmtree(d)




class RestoreContainers(unittest.TestCase):
    """The restore checks that every stopped container runs again; if not, it says so and keeps the state."""

    def run_restore(self, can_start):
        import prepare_environment as pe
        from unittest import mock
        fd, state = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"governors": {}, "turbo": None, "files": {}, "radios": {},
                       "stopped_containers": ["a", "b"]}, fh)
        running, starts = set(), []

        def run(cmd, **kw):                                  # a fake docker start: some containers fail
            starts.append(cmd)
            running.update(n for n in cmd[2:] if n in can_start)
            bad = [n for n in cmd[2:] if n not in can_start]
            return mock.Mock(returncode=1 if bad else 0, stderr="Error: network not found" if bad else "")
        out = []
        with mock.patch.object(pe, "require_root"), mock.patch.object(pe.subprocess, "run", run), \
                mock.patch.object(pe, "docker_running", lambda: sorted(running)), \
                mock.patch.object(pe.time, "sleep"), mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            try:
                pe.do_restore(mock.Mock(state=state))
                code = 0
            except SystemExit as e:
                code = e.code
        kept = os.path.exists(state)
        if kept:
            os.remove(state)
        return code, kept, "\n".join(out), starts

    def test_all_running_again(self):
        code, kept, out, starts = self.run_restore(["a", "b"])
        self.assertEqual((code, kept), (0, False))
        self.assertIn("Restarted containers: a, b", out)

    def test_one_did_not_start(self):
        code, kept, out, starts = self.run_restore(["a"])
        self.assertEqual((code, kept), (1, True))                       # state kept: the next run refuses
        self.assertIn("Restarted containers: a", out)
        self.assertIn("NOT restarted: b (docker: Error: network not found)", out)
        self.assertIn("docker start b", out)
        self.assertEqual(starts, [["docker", "start", "a", "b"], ["docker", "start", "b"]])   # b tried once more



class Tidy(unittest.TestCase):
    """NATIVE_COPIES at the end of a run, and make tidy: only what can be regenerated, never results."""

    def native_dir(self):
        d = tempfile.mkdtemp()
        for name, fid in (("st-current", "sha256:aaa"), ("st-rebuilt", "sha256:old"), ("st-gone", "sha256:x"),
                          ("st-older", ""), ("st-docker-down", "sha256:d")):
            os.makedirs(os.path.join(d, name, "app"))
            if fid:
                with open(os.path.join(d, name, ".files_id"), "w") as fh:
                    fh.write(fid)
        os.makedirs(os.path.join(d, ".st-current-cut"))                  # an unpacking that was cut off
        open(os.path.join(d, "wseb-st-current.scope.log"), "w").close()
        return d

    def fake_docker(self):
        ids = {"st-current": "sha256:aaa", "st-rebuilt": "sha256:new", "st-older": "sha256:o"}
        ns = __import__("native_server")
        from unittest import mock

        def files_id(name, docker_path="docker"):
            if name == "st-docker-down":
                raise ns.subprocess.CalledProcessError(1, "docker", stderr="Cannot connect to the Docker daemon")
            if name not in ids:
                raise ns.subprocess.CalledProcessError(1, "docker", stderr=f"Error response from daemon: No such image: {name}")
            return ids[name]
        return mock.patch.object(ns, "files_id", files_id)

    def test_prune_deletes_only_copies_never_used_again(self):
        import native_server as ns
        d = self.native_dir()
        with self.fake_docker():
            why = {os.path.basename(f): r for f, _, r in ns.stale_copies(root=d)}
            self.assertEqual(why, {"st-rebuilt": "its image was rebuilt", "st-gone": "its image no longer exists",
                                   "st-older": "copied by an older version (made again when needed)",
                                   ".st-current-cut": "unpacking was cut off"})
            self.assertEqual(ns.tidy("prune", root=d)[0], 4)
        # a Docker problem is no proof that the image is gone: that copy stays
        self.assertEqual(sorted(os.listdir(d)), ["st-current", "st-docker-down", "wseb-st-current.scope.log"])
        self.assertEqual(ns.tidy("keep", root=d), (0, 0))
        self.assertEqual(ns.tidy("delete", root=d)[0], 2)
        self.assertEqual(os.listdir(d), ["wseb-st-current.scope.log"])   # small logs stay
        shutil.rmtree(d)

    def test_docker_without_answer_deletes_nothing(self):
        import native_server as ns
        from unittest import mock
        d = self.native_dir()
        # a fake or broken docker that exits 0 and prints nothing (as in the script tests)
        with mock.patch.object(ns.subprocess, "run", lambda cmd, **kw: mock.Mock(returncode=0, stdout="", stderr="")):
            self.assertEqual([f for f, _, why in ns.stale_copies(root=d) if why != "unpacking was cut off"], [])
        shutil.rmtree(d)

    def test_script_tests_use_their_own_native_folder(self):
        self.assertNotEqual(os.path.realpath(os.environ["MEASURE_NATIVE_DIR"]), os.path.realpath(os.path.join(ROOT, "native")))
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            self.assertIn('if [ -d "${MEASURE_NATIVE_DIR:-native}" ]; then', fh.read())

    def test_never_deletes_outside_its_folder(self):
        import native_server as ns
        import tidy
        d = tempfile.mkdtemp()
        with self.assertRaises(ValueError):
            ns.remove_copy(os.path.join(d, ".."), root=d)
        with self.assertRaises(ValueError):
            tidy.remove_result_folder("/tmp", results_dir=d)
        shutil.rmtree(d)

    def test_old_server_images(self):
        import tidy
        folders = {"st-erlang-cowboy-29-1-1", "dy-gleam-pure-1-19-0"}
        images = ["st-erlang-cowboy-29-1-1", "st-erlang-cowboy-29-1-1-nobw", "dy-gleam-pure-1-15-2",
                  "st-erlang-cowboy-28-4-3-fix", "green-coding-nginx", "erlang", "ws-elixir-bandit-1-8-5"]
        self.assertEqual(tidy.old_server_images(images, folders),
                         ["dy-gleam-pure-1-15-2", "st-erlang-cowboy-28-4-3-fix", "ws-elixir-bandit-1-8-5"])

    def test_results_only_empty_or_abandoned(self):
        import tidy
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "empty", "static"))
        os.makedirs(os.path.join(d, "done", "static"))
        open(os.path.join(d, "done", "static", "a.csv"), "w").close()
        os.makedirs(os.path.join(d, "old"))
        open(os.path.join(d, "old", ".abandoned"), "w").close()
        found = {os.path.basename(f): why for f, _, why in tidy.empty_or_abandoned_results(d)}
        self.assertEqual(found, {"empty": "empty", "old": "abandoned"})            # "done" is a result: never
        shutil.rmtree(d)

    def test_run_end_and_config(self):
        import bench_config
        self.assertEqual(bench_config.parse("")["NATIVE_COPIES"], "prune")
        with self.assertRaises(bench_config.ConfigError):
            bench_config.parse("NATIVE_COPIES=all")
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            self.assertIn('./tools/native_server.py tidy "${CFG_NATIVE_COPIES:-prune}" || true', fh.read())


class ConfirmStop(unittest.TestCase):
    """Ctrl-C asks whether to stop (y = stop, else or no answer = continue); the running step goes on."""

    def harness(self, answer=None, tty_exists=True, body=""):
        d = tempfile.mkdtemp()
        tty = os.path.join(d, "tty")
        if tty_exists:
            with open(tty, "w") as fh:
                fh.write(answer or "")
        script = f"""
            set -e                                                  # as in run_benchmarks.sh
            print_status() {{ echo "[$1] $2"; }}
            BENCH_TTY="{tty}"; BENCH_STOP_CONFIRM_SECONDS=2
            eval "$(sed -n '/^BENCH_CHILD=""$/,/^bench_restore_environment() {{$/p' "{ROOT}/scripts/run_benchmarks.sh" | sed '$d')"
            trap 'echo "exit $? interrupted=${{BENCH_INTERRUPTED:-0}}"' EXIT
            {body}
        """
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                             start_new_session=True).stdout
        shutil.rmtree(d)
        return out

    def test_y_stops(self):
        out = self.harness("y\n", body="bench_on_ctrl_c; echo after")
        self.assertIn("Stopping (Ctrl-C confirmed)", out)
        self.assertNotIn("after", out)
        self.assertIn("exit 130 interrupted=1", out)

    def test_anything_else_or_no_answer_continues(self):
        for answer in ("n\n", "", "\n"):
            out = self.harness(answer, body="bench_on_ctrl_c; echo after")
            self.assertIn("continuing (nothing was interrupted)", out, repr(answer))
            self.assertIn("after", out)

    def test_timeout_continues_under_set_e(self):
        d = tempfile.mkdtemp()
        fifo = os.path.join(d, "tty")
        os.mkfifo(fifo)
        script = f"""
            set -e
            print_status() {{ echo "[$1] $2"; }}
            BENCH_TTY="{fifo}"; BENCH_STOP_CONFIRM_SECONDS=1
            eval "$(sed -n '/^BENCH_CHILD=""$/,/^bench_restore_environment() {{$/p' "{ROOT}/scripts/run_benchmarks.sh" | sed '$d')"
            sleep 5 <> "{fifo}" >/dev/null 2>&1 &              # a terminal nobody types on
            bench_ask_stop; echo after
        """
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                             start_new_session=True).stdout
        shutil.rmtree(d)
        self.assertIn("continuing", out)
        self.assertIn("after", out)

    @unittest.skipUnless(shutil.which("systemd-inhibit") and shutil.which("setsid"), "needs systemd-inhibit")
    def test_sleep_block_survives_ctrl_c(self):
        script = f"""
            set -e
            print_status() {{ :; }}
            eval "$(sed -n '/^bench_block_sleep() {{/,/^}}/p;/^bench_unblock_sleep() {{/,/^}}/p' "{ROOT}/scripts/run_benchmarks.sh")"
            trap ':' INT
            bench_block_sleep
            sleep 1
            kill -INT -$$; sleep 1                                  # a terminal Ctrl-C to the whole group
            kill -0 "$BENCH_INHIBIT_PID" && echo "inhibitor alive after Ctrl-C"
            pid=$BENCH_INHIBIT_PID
            bench_unblock_sleep; sleep 1
            pgrep -g "$pid" >/dev/null && echo "group left behind" || echo "group gone"
        """
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                             start_new_session=True).stdout
        self.assertIn("inhibitor alive after Ctrl-C", out)
        self.assertIn("group gone", out)

    def test_without_a_terminal_stops_at_once(self):
        out = self.harness(tty_exists=False, body="bench_on_ctrl_c; echo after")
        self.assertNotIn("after", out)
        self.assertIn("exit 130 interrupted=1", out)

    def test_the_step_goes_on_and_its_exit_code_comes_back(self):
        # a Ctrl-C (SIGINT to the script) during a step, answered "no": the step is not disturbed
        body = """trap bench_on_ctrl_c INT
            ( sleep 1; kill -INT -$$ ) &                     # the whole group, as a terminal Ctrl-C
            rc=0; bench_run sh -c 'sleep 2; exit 7' || rc=$?; echo "step exit $rc" """
        out = self.harness("n\n", body=body)
        self.assertIn("continuing", out)
        self.assertIn("step exit 7", out)

    def test_stop_ends_the_step(self):
        body = """trap bench_on_ctrl_c INT
            ( sleep 1; kill -INT -$$ ) &
            bench_run sleep 20; echo "not reached" """
        out = self.harness("y\n", body=body)
        self.assertNotIn("not reached", out)
        self.assertIn("exit 130 interrupted=1", out)

    def test_second_ctrl_c_while_asking_stops(self):
        d = tempfile.mkdtemp()
        fifo = os.path.join(d, "tty")
        os.mkfifo(fifo)
        script = f"""
            set -e
            print_status() {{ echo "[$1] $2"; }}
            BENCH_TTY="{fifo}"; BENCH_STOP_CONFIRM_SECONDS=10
            eval "$(sed -n '/^BENCH_CHILD=""$/,/^bench_restore_environment() {{$/p' "{ROOT}/scripts/run_benchmarks.sh" | sed '$d')"
            trap 'echo "exit $? interrupted=${{BENCH_INTERRUPTED:-0}}"' EXIT
            sleep 8 <> "{fifo}" >/dev/null 2>&1 &       # a terminal nobody types on
            trap bench_on_ctrl_c INT
            ( sleep 1; kill -INT -$$; sleep 1; kill -INT -$$ ) >/dev/null 2>&1 &
            bench_run sleep 20; echo "not reached"
        """
        t0 = time.monotonic()
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                             start_new_session=True).stdout
        shutil.rmtree(d)
        self.assertIn("exit 130 interrupted=1", out)
        self.assertNotIn("not reached", out)
        self.assertLess(time.monotonic() - t0, 8)                  # stopped by the second Ctrl-C, not the timeout

    def stop_in(self, files):
        d = tempfile.mkdtemp()
        res = os.path.join(d, "run")
        for sub in ("static", "dynamic", "websocket"):
            os.makedirs(os.path.join(res, sub))
        for f in files:
            open(os.path.join(res, f), "w").close()
        script = f"""
            print_status() {{ echo "[$1] $2"; }}
            eval "$(sed -n '/^bench_on_exit() {{/,/^}}/p;/^bench_unblock_sleep() {{/,/^}}/p' "{ROOT}/scripts/run_benchmarks.sh")"
            bench_restore_environment() {{ :; }}; cleanup_sudo_keepalive() {{ :; }}
            RESULTS_DIR="{res}"; RESUME_DIR=""; CONFIG_FILE=x; BENCH_INTERRUPTED=1
            bench_on_exit
        """
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30).stdout
        exists = os.path.isdir(res)
        shutil.rmtree(d)
        return exists, out

    def test_stopped_before_anything_was_written_leaves_no_folder(self):
        exists, out = self.stop_in([".running"])
        self.assertFalse(exists)
        self.assertIn("Nothing was measured yet; removed the empty", out)

    def test_stopped_later_keeps_its_folder(self):
        exists, out = self.stop_in(["metadata.json", "bench.config"])
        self.assertTrue(exists)
        self.assertIn("Finished measurements are kept", out)

    def test_restore_cannot_be_reached_by_ctrl_c(self):
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            src = fh.read()
        self.assertIn("""( trap '' INT TERM; exec sudo -n "$PYTHON_PATH" ./tools/prepare_environment.py restore""", src)
        self.assertNotIn("setsid -w sudo", src)                          # sudo would ask for the password again
        self.assertIn("trap bench_on_ctrl_c INT", src)
        self.assertIn("trap bench_stop TERM", src)


class TerminatedMeasurement(unittest.TestCase):
    """A measurement stopped by SIGTERM (shutdown, timeout, kill) leaves no container or Scaphandre behind."""
    def test_sigterm_removes_container_and_stops_scaphandre(self):
        import signal
        d = tempfile.mkdtemp()
        calls = os.path.join(d, "calls.log")
        for name in ("docker", "sudo"):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(f'#!/bin/sh\necho "{name} $*" >> {calls}\n')
            os.chmod(os.path.join(d, name), 0o755)
        prog = ("import sys, time; sys.path.insert(0, %r); import measure_failure as mf\n"
                "def main():\n    mf.started_container('srv', 'docker')\n    print('running', flush=True)\n    time.sleep(60)\n"
                "mf.run(main)\n") % os.path.join(ROOT, "tools")
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"])
        p = subprocess.Popen([sys.executable, "-c", prog], env=env, stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "running")
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        p.stdout.close()
        self.assertEqual(p.returncode, 128 + signal.SIGTERM)
        with open(calls) as fh:
            log = fh.read()
        self.assertIn("docker rm -f srv", log)
        self.assertIn("pkill -9 scaphandre", log)


class InterruptedLoad(unittest.TestCase):
    """Ctrl-C during a load ends the measurement at once, although worker threads are still running."""
    def test_ctrl_c_with_running_threads_exits_at_once(self):
        import signal
        d = tempfile.mkdtemp()
        calls = os.path.join(d, "calls.log")
        for name in ("docker", "sudo"):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(f'#!/bin/sh\necho "{name} $*" >> {calls}\n')
            os.chmod(os.path.join(d, name), 0o755)
        # Like a load: a non-daemon thread that never stops by itself (the CPU statistics collector)
        prog = ("import sys, threading, time; sys.path.insert(0, %r); import measure_failure as mf\n"
                "def main():\n    mf.started_container('srv', 'docker')\n"
                "    threading.Thread(target=lambda: time.sleep(600)).start()\n"
                "    print('running', flush=True)\n    time.sleep(600)\n"
                "mf.run(main)\n") % os.path.join(ROOT, "tools")
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"])
        p = subprocess.Popen([sys.executable, "-c", prog], env=env, stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "running")
        p.send_signal(signal.SIGINT)
        try:
            p.wait(timeout=10)
        finally:
            if p.poll() is None:
                p.kill()
            p.stdout.close()
        self.assertEqual(p.returncode, 130)
        with open(calls) as fh:
            self.assertIn("docker rm -f srv", fh.read())


class GuiRepeats(unittest.TestCase):
    """The GUI shows repeated runs as one point per load with their spread, the same statistics as summary.csv."""
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt5.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt5 not installed")
        cls.app = QApplication.instance() or QApplication([])
        import gui_graph_generator
        cls.g = gui_graph_generator

    def test_one_point_per_load_with_quartiles(self):
        import aggregate_repeats
        x = [1000, 2000, 1000, 2000, 1000, 2000]
        y = [10.0, 21.0, 12.0, 20.0, 11.0, 30.0]
        xs, centre, low, high, counts, raw = self.g.aggregate_points(x, y, self.g.REPEATS_MEDIAN_IQR)
        self.assertEqual((xs, centre, counts), ([1000, 2000], [11.0, 21.0], [3, 3]))
        st = dict(zip(aggregate_repeats.STATS, aggregate_repeats.describe([20.0, 21.0, 30.0])))
        self.assertAlmostEqual(low[1], st["Q1"], places=4)
        self.assertAlmostEqual(high[1], st["Q3"], places=4)
        xs, centre, low, high, _, _ = self.g.aggregate_points(x, y, self.g.REPEATS_MEAN_CI)
        st = dict(zip(aggregate_repeats.STATS, aggregate_repeats.describe([10.0, 12.0, 11.0])))
        self.assertAlmostEqual(high[0] - centre[0], st["+/-95%"], places=4)

    def test_summary_files_are_not_plotted(self):
        self.assertTrue(self.g.is_summary_csv("results/x/static/summary.csv"))
        self.assertTrue(self.g.is_summary_csv("results/x/static/st-a_summary.csv"))
        self.assertFalse(self.g.is_summary_csv("results/x/static/st-a.csv"))

    def test_plots_real_results_and_pairs_variants(self):
        import glob
        g = self.g
        saved = g.QMessageBox.information
        g.QMessageBox.information = lambda *a, **k: None
        try:
            w = g.BenchmarkGrapher()
            E = os.path.join(ROOT, "experiments", "busy-wait", "evidence", "busywait-2026-10-02_090615", "static")
            w.add_files(sorted(glob.glob(os.path.join(E, "*.csv"))))
            self.assertEqual(len(w.files), 6)                                     # summaries skipped
            self.assertIn("Skipped 7 statistics file(s)", w.summary_label.text())  # a note, no window
            self.assertTrue(w._render_plot(w.files, "Container Energy (J)", g.WS_PLOT_MULTILINE, enable_interactivity=False))
            self.assertIn("median of 5 runs", w.summary_label.text())
            lines = {l.get_label(): l for l in w.ax.get_lines() if not l.get_label().startswith("_")}
            base, var = lines["st-elixir-cowboy-1-19-5"], lines["st-elixir-cowboy-1-19-5-nobw"]
            self.assertEqual(base.get_color(), var.get_color())                  # same colour
            self.assertEqual((base.get_linestyle(), var.get_linestyle()), ("-", "--"))
            self.assertEqual(list(base.get_xdata()), [20000.0, 80000.0])         # one point per load
            self.assertTrue(w._render_plot(w.files, "Host Energy (J)", g.WS_PLOT_BAR, enable_interactivity=False))
            self.assertEqual(sorted({round(t) for t in w.ax.get_xticks()}), [0, 1])  # grouped per load level
            # Scope selector: only that scope's values in the metric list
            w.scope_selector.set_current(g.SCOPE_HOST)
            w.update_metric_options()
            self.assertTrue(w.metric_selector._options and all(m.startswith("Host ") for m in w.metric_selector._options))
            w.scope_selector.set_current(g.SCOPE_ALL)
            w.update_metric_options()
            # Statistics table = summary.csv (old file: "Total Energy (J)" is read as "Container Energy (J)")
            row = [r for r in w.statistics_rows("Container Energy (J)")
                   if r[0] == "st-elixir-cowboy-1-19-5" and r[1] == 80000][0]
            with open(os.path.join(E, "summary.csv")) as fh:
                ref = [r for r in csv.DictReader(fh)
                       if r["Container Name"] == "st-elixir-cowboy-1-19-5" and r["Total Requests"] == "80000"][0]
            self.assertEqual((row[2], row[3], row[6]), (5, float(ref["Total Energy (J) median"]),
                                                        float(ref["Total Energy (J) mean"])))
            # As in the window: with hover on, for every plot type and every way of showing repeats
            for mode in g.REPEATS_OPTIONS:
                w.repeats_selector.set_current(mode)
                for kind in (g.WS_PLOT_MULTILINE, g.WS_PLOT_BAR):
                    self.assertTrue(w._render_plot(w.files, "Container Energy (J)", kind, enable_interactivity=True),
                                    (mode, kind))
        finally:
            g.QMessageBox.information = saved


class VariantOrder(unittest.TestCase):
    """VARIANT_ORDER=separate: one group after the other within a repeat; the first group rotates."""
    def order(self, pass_no, items):
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            sh = fh.read()
        f = sh[sh.index("bench_group_variants() {"):]
        f = f[:f.index("\n}\n") + 3]
        script = (f + '\nCFG_VARIANTS="nobw:ERL_FLAGS=+sbwt none;fast:X=1"\ndeclare -A BENCH_VARIANT_OF=('
                  '[a-nobw]=nobw [b-nobw]=nobw [a-fast]=fast [b-fast]=fast)\n'
                  f'bench_group_variants {pass_no} ' + " ".join(items))
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout.split()

    def test_groups_rotate_and_keep_the_shuffled_order(self):
        shuffled = ["b-nobw", "a", "a-fast", "b", "a-nobw", "b-fast"]
        self.assertEqual(self.order(1, shuffled), ["a", "b", "b-nobw", "a-nobw", "a-fast", "b-fast"])
        self.assertEqual(self.order(2, shuffled), ["b-nobw", "a-nobw", "a-fast", "b-fast", "a", "b"])
        self.assertEqual(self.order(3, shuffled), ["a-fast", "b-fast", "a", "b", "b-nobw", "a-nobw"])

    def test_config(self):
        import bench_config
        self.assertEqual(bench_config.parse("")["VARIANT_ORDER"], "separate")
        self.assertEqual(bench_config.parse("VARIANT_ORDER=mixed")["VARIANT_ORDER"], "mixed")
        with self.assertRaises(bench_config.ConfigError):
            bench_config.parse("VARIANT_ORDER=random")


class StaleImages(unittest.TestCase):
    """An image built before its recipe last changed is reported (the run stops before it starts)."""
    def test_newer_recipe_file_is_reported(self):
        import run_metadata
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "static", "x", "st-a"))
        os.makedirs(os.path.join(d, "static", "x", "st-b"))
        for name in ("st-a", "st-b"):
            open(os.path.join(d, "static", "x", name, "Dockerfile"), "w").close()
        built = 1_790_000_000
        os.utime(os.path.join(d, "static", "x", "st-a", "Dockerfile"), (built - 100, built - 100))   # older: fine
        os.utime(os.path.join(d, "static", "x", "st-b", "Dockerfile"), (built + 100, built + 100))   # changed after
        saved = run_metadata._run
        # no fingerprint label (built by hand): the times are compared
        run_metadata._run = lambda cmd, cwd=None: "" if "Labels" in cmd[4] else "2026-09-21T14:13:20.123456789+00:00"
        try:
            stale = run_metadata.stale_images(d, ["st-a", "st-b", "st-a-nobw"])
        finally:
            run_metadata._run = saved
        self.assertEqual(stale, [("st-b", os.path.join("static", "x", "st-b", "Dockerfile") + " changed after the image was built")])

    def folder(self, files):
        d = tempfile.mkdtemp()
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, rel)) or d, exist_ok=True)
            with open(os.path.join(d, rel), "w") as fh:
                fh.write(text)
        return d

    def test_fingerprint_follows_content_not_time(self):
        import run_metadata
        d = self.folder({"Dockerfile": "FROM x", "src/a.erl": "-module(a)."})
        h = run_metadata.recipe_hash(d)
        os.utime(os.path.join(d, "Dockerfile"), (1, 1))                       # only the time changes
        self.assertEqual(run_metadata.recipe_hash(d), h)
        os.makedirs(os.path.join(d, "_build"))                                # build output is not the recipe
        open(os.path.join(d, "_build", "x.beam"), "w").close()
        self.assertEqual(run_metadata.recipe_hash(d), h)
        with open(os.path.join(d, "src/a.erl"), "a") as fh:                  # the content changes
            fh.write("\n")
        changed = run_metadata.recipe_hash(d)
        self.assertNotEqual(changed, h)
        os.rename(os.path.join(d, "src/a.erl"), os.path.join(d, "src/b.erl"))   # a rename changes it too
        self.assertNotEqual(run_metadata.recipe_hash(d), changed)
        shutil.rmtree(d)

    def test_image_with_fingerprint_is_compared_exactly(self):
        import run_metadata
        from unittest import mock
        d = self.folder({"static/x/st-a/Dockerfile": "FROM x", "static/x/st-b/Dockerfile": "FROM y"})
        current = run_metadata.recipe_hash(os.path.join(d, "static/x/st-a"))
        labels = {"st-a": current, "st-b": "0" * 64}

        def run(cmd, cwd=None):
            if "Labels" in cmd[4]:
                return labels[cmd[-1]]
            return "2000-01-01T00:00:00Z"                                    # an old time: never used here
        with mock.patch.object(run_metadata, "_run", run):
            stale = run_metadata.stale_images(d, ["st-a", "st-b"])
        self.assertEqual(stale, [("st-b", "its folder changed after the image was built")])
        shutil.rmtree(d)

    def test_build_stores_the_fingerprint(self):
        with open(os.path.join(ROOT, "scripts", "install_benchmarks.sh")) as fh:
            src = fh.read()
        self.assertIn('docker build -t "$name" --label "wseb.recipe=$recipe" "$d"', src)
        self.assertIn('recipe=$("$PYTHON" ./tools/run_metadata.py recipe-hash "$d")', src)


if __name__ == "__main__":
    unittest.main()


class ServerPort(unittest.TestCase):
    """Server contract: the framework tells the server its port in PORT (README, Adding a Server)."""

    def started_with(self, module, mapping, network="bridge"):
        from unittest import mock
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(module.subprocess, "run", return_value=done) as run, \
                mock.patch.object(module.time, "sleep"), \
                mock.patch.object(module, "cleanup_existing_container"):
            module.start_server_container("img", mapping, "c", "docker", network)
        return [c.args[0] for c in run.call_args_list if c.args and "run" in c.args[0]][-1]

    def test_port_is_the_container_side_of_the_mapping(self):
        import plugins.deploy.container                                           # every workload starts it here
        for module in (plugins.deploy.container,):
            cmd = self.started_with(module, "8001:80")
            self.assertIn("PORT=80", cmd)
            self.assertEqual(cmd[cmd.index("PORT=80") - 1], "-e")
            self.assertIn("PORT=8001", self.started_with(module, "8001:8001"))
            self.assertIn("PORT=80", self.started_with(module, "8001:80", network="host"))

    def test_health_check_passes_port(self):
        with open(os.path.join(ROOT, "scripts", "check_health.sh")) as fh:
            self.assertIn('-e "PORT=${port_mapping##*:}"', fh.read())


class CpuSpeedGuard(unittest.TestCase):
    """Readiness: not ready while the firmware caps the CPU below its expected speed."""

    def problem(self, setting, limit, expected=1800):
        import readiness
        from unittest import mock
        with mock.patch.object(readiness.run_metadata, "cpu_speed_limit_mhz", return_value=limit), \
                mock.patch.object(readiness.run_metadata, "expected_cpu_speed_mhz", return_value=expected):
            return readiness.cpu_speed_problem(setting)

    def test_capped_cpu_is_not_ready(self):
        self.assertIn("capped at 800 MHz < 1800 MHz", self.problem("auto", 800))

    def test_expected_speed_is_ready(self):
        self.assertEqual(self.problem("auto", 1800), "")
        self.assertEqual(self.problem("auto", 4900), "")

    def test_off_and_unknown_never_block(self):
        self.assertEqual(self.problem("off", 800), "")
        self.assertEqual(self.problem("auto", None), "")
        self.assertEqual(self.problem("auto", 800, expected=None), "")

    def test_fixed_speed_in_mhz(self):
        self.assertIn("< 2000 MHz", self.problem("2000", 1800))
        self.assertEqual(self.problem("1500", 1800), "")

    def test_expected_speed_follows_turbo(self):
        import run_metadata
        from unittest import mock
        mins = {"base_frequency": 1800, "cpuinfo_max_freq": 4900}
        fake = lambda pattern: next(v for k, v in mins.items() if k in pattern)  # noqa: E731
        with mock.patch.object(run_metadata, "_min_mhz", side_effect=fake), \
                mock.patch.object(run_metadata, "rated_cpu_speed_mhz", return_value=None):
            with mock.patch.object(run_metadata, "turbo_state", return_value="off"):
                self.assertEqual(run_metadata.expected_cpu_speed(), (None, ""))   # base_frequency is never used
            with mock.patch.object(run_metadata, "turbo_state", return_value="on"):
                self.assertEqual(run_metadata.expected_cpu_speed(), (4900, "maximum"))

    def test_rated_speed_does_not_move_with_the_cap(self):
        # As seen on 2026-10-06: under the firmware cap base_frequency read 800 MHz too, so a cap at
        # 800 MHz looked like the expected speed and three capped runs were accepted
        import run_metadata
        from unittest import mock
        d = tempfile.mkdtemp()
        info = os.path.join(d, "cpuinfo")
        with open(info, "w") as fh:
            fh.write("processor\t: 0\nmodel name\t: Intel(R) Core(TM) i7-10610U CPU @ 1.80GHz\n")
        self.assertEqual(run_metadata.rated_cpu_speed_mhz(info), 1800)
        with open(info, "w") as fh:
            fh.write("model name\t: AMD Ryzen 7 PRO 4750U with Radeon Graphics\n")       # no rated speed
        self.assertIsNone(run_metadata.rated_cpu_speed_mhz(info))
        shutil.rmtree(d)
        capped = {"base_frequency": 800, "cpuinfo_max_freq": 4900}
        with mock.patch.object(run_metadata, "_min_mhz", side_effect=lambda p: next(v for k, v in capped.items() if k in p)), \
                mock.patch.object(run_metadata, "rated_cpu_speed_mhz", return_value=1800), \
                mock.patch.object(run_metadata, "turbo_state", return_value="off"):
            self.assertEqual(run_metadata.expected_cpu_speed(), (1800, "rated"))

    def test_expected_speed_fixed_once_per_run(self):
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            src = fh.read()
        self.assertIn('export MEASURE_READY_CPU_SPEED="$BENCH_CPU_SPEED"', src)           # checks during the load
        self.assertIn('--cpu-speed "${BENCH_CPU_SPEED:-$CFG_READY_CPU_SPEED}"', src)    # checks before the start
        self.assertIn('--set ready_cpu_speed_expected_mhz="${BENCH_CPU_SPEED:-}"', src)  # recorded

    def test_config_setting(self):
        import bench_config
        check = bench_config.SCHEMA["READY_CPU_SPEED"]["check"]
        self.assertEqual(check("auto"), "auto")
        self.assertEqual(check("off"), "off")
        self.assertEqual(check("1800"), "1800")
        with self.assertRaises(ValueError):
            check("fast")


class SetCpuSpeed(unittest.TestCase):
    """ENV_CPU_SPEED: every core fixed at one speed (lowest = highest), saved, verified and restored."""

    def cores(self, low="400000", top="1800000", hw="1800000", n=2):
        d = tempfile.mkdtemp()
        for i in range(n):
            f = os.path.join(d, f"cpu{i}", "cpufreq")
            os.makedirs(f)
            for name, v in (("scaling_min_freq", low), ("scaling_max_freq", top), ("cpuinfo_max_freq", hw)):
                with open(os.path.join(f, name), "w") as fh:
                    fh.write(v)
        return d, os.path.join(d, "cpu*", "cpufreq")

    def limits(self, d):
        out = set()
        for f in sorted(glob.glob(os.path.join(d, "cpu*", "cpufreq"))):
            with open(os.path.join(f, "scaling_min_freq")) as a, open(os.path.join(f, "scaling_max_freq")) as b:
                out.add((a.read(), b.read()))
        return out

    def test_max_fixes_at_the_hardware_maximum(self):
        import prepare_environment as pe
        d, pattern = self.cores(top="800000")                       # applied while the firmware capped it
        saved = {}
        pe.set_cpu_speed(None, saved, pattern)
        self.assertEqual(self.limits(d), {("1800000", "1800000")})  # not the cap's 800 MHz
        self.assertEqual(sorted(set(saved.values())), ["400000", "800000"])
        shutil.rmtree(d)

    def test_lower_speed_and_restore_in_any_order(self):
        import prepare_environment as pe
        from unittest import mock
        d, pattern = self.cores(low="1500000")
        saved = {}
        real_write = pe.write

        def write(path, value):                                     # the kernel refuses min > max and max < min
            folder = os.path.dirname(path)
            other = "scaling_max_freq" if path.endswith("scaling_min_freq") else "scaling_min_freq"
            if os.path.exists(os.path.join(folder, other)):
                with open(os.path.join(folder, other)) as fh:
                    o = int(fh.read())
                if (path.endswith("min_freq") and int(value) > o) or (path.endswith("max_freq") and int(value) < o):
                    return False
            return real_write(path, value)
        with mock.patch.object(pe, "write", write):
            pe.set_cpu_speed(1200, saved, pattern)                  # below the minimum: minimum first
            self.assertEqual(self.limits(d), {("1200000", "1200000")})
            failed = [(p, v) for p, v in saved.items() if not pe.write(p, v)]
            failed = [(p, v) for p, v in failed if not pe.write(p, v)]   # the second pass of the restore
        self.assertEqual(failed, [])
        self.assertEqual(self.limits(d), {("1500000", "1800000")})
        shutil.rmtree(d)

    def test_saves_the_limits_from_before_turbo_changed(self):
        # As on 2026-10-07: turbo off made the kernel report the highest speed as 1800 MHz; saving that
        # restored 1800 MHz as a permanent limit instead of the real 4900 MHz
        import prepare_environment as pe
        d, pattern = self.cores(top="1800000", hw="4900000")         # now: clamped by turbo off
        before = {os.path.join(d, f"cpu{i}", "cpufreq", n): v for i in range(2)
                  for n, v in (("scaling_max_freq", "4900000"), ("scaling_min_freq", "400000"))}
        saved = {}
        pe.set_cpu_speed(None, saved, pattern, before=before)
        self.assertEqual(set(v for p, v in saved.items() if p.endswith("max_freq")), {"4900000"})
        with open(os.path.join(ROOT, "tools", "prepare_environment.py")) as fh:
            src = fh.read()
        self.assertLess(src.index("limits_before = {path: read(path)"), src.index('if args.turbo == "unchanged":'))
        shutil.rmtree(d)

    def test_restore_has_two_passes(self):
        with open(os.path.join(ROOT, "tools", "prepare_environment.py")) as fh:
            self.assertIn("failed = [(path, prev) for path, prev in failed if not write(path, prev)]", fh.read())

    def test_verify_and_config(self):
        import bench_config
        import prepare_environment as pe
        from unittest import mock
        check = bench_config.SCHEMA["ENV_CPU_SPEED"]["check"]
        self.assertEqual([check("max"), check("unchanged"), check("1200")], ["max", "unchanged", "1200"])
        for bad in ("fast", "50"):
            with self.assertRaises(ValueError):
                check(bad)
        args = mock.Mock(governor="unchanged", turbo="unchanged", cpu_speed="1200", screen_brightness="unchanged",
                         keyboard_light="unchanged", wifi="unchanged", bluetooth="unchanged", stop_containers=False)
        import run_metadata
        with mock.patch.object(run_metadata, "cpu_max_freq_mhz", return_value="1800"), \
                self.assertRaises(SystemExit) as ended, mock.patch("builtins.print") as out:
            pe.do_verify(args)
        self.assertEqual(ended.exception.code, 1)
        self.assertIn("CPU speed is 1800 MHz, expected 1200 MHz", str(out.call_args_list))

    def test_yardstick_is_the_speed_we_set(self):
        import run_metadata
        from unittest import mock
        with mock.patch.object(run_metadata, "rated_cpu_speed_mhz", return_value=1800), \
                mock.patch.object(run_metadata, "turbo_state", return_value="off"):
            self.assertEqual(run_metadata.expected_cpu_speed("1200"), (1200, "set"))
            self.assertEqual(run_metadata.expected_cpu_speed("max"), (1800, "rated"))
        with mock.patch.object(run_metadata, "rated_cpu_speed_mhz", return_value=None), \
                mock.patch.object(run_metadata, "turbo_state", return_value="off"):
            self.assertEqual(run_metadata.expected_cpu_speed("max"), (None, ""))       # unknown: ask, never guess


class SafeInterrupts(unittest.TestCase):
    """One run at a time; Ctrl-C stops a check's run; a restore cannot be cut short."""

    def refuse(self, marker_pid):
        with open(os.path.join(ROOT, "scripts", "run_benchmarks.sh")) as fh:
            src = fh.read()
        start = src.index("bench_refuse_parallel_run() {")
        func = src[start:src.index("\n}\n", start) + 3]
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "2026-01-01_000000"))
        with open(os.path.join(d, "2026-01-01_000000", ".running"), "w") as fh:
            fh.write(str(marker_pid))
        script = f'print_status() {{ echo "$2"; }}\nRESULTS_PARENT_DIR="{d}"\n{func}\nbench_refuse_parallel_run\necho started'
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        shutil.rmtree(d)
        return r

    def test_refuses_while_another_run_is_alive(self):
        other = subprocess.Popen(["bash", "-c", "exec -a run_benchmarks.sh sleep 30"])
        try:
            time.sleep(0.2)
            r = self.refuse(other.pid)
            self.assertEqual(r.returncode, 1)
            self.assertIn("Another measurement is running", r.stdout)
        finally:
            other.kill()
            other.wait()

    def test_starts_when_the_marker_is_stale(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        r = self.refuse(dead.pid)
        self.assertEqual(r.returncode, 0)
        self.assertIn("started", r.stdout)

    def test_check_passes_ctrl_c_to_its_run(self):
        with open(os.path.join(ROOT, "tests", "config_check.sh")) as fh:
            self.assertIn('kill -TERM "$RUN"', fh.read())

    def test_restore_ignores_ctrl_c(self):
        import signal
        import prepare_environment as pe
        from unittest import mock
        fd, state = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"governors": {}, "turbo": None, "files": {}, "radios": {}, "stopped_containers": []}, fh)
        old_int, old_term = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
        during = []
        real_restore = pe._restore

        def restore(args):
            during.append((signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)))
            real_restore(args)
        try:
            with mock.patch.object(pe, "require_root"), mock.patch.object(pe, "_restore", restore):
                pe.do_restore(mock.Mock(state=state))
            self.assertEqual(during, [(signal.SIG_IGN, signal.SIG_IGN)])         # ignored while restoring
            # and back afterwards: an ignored SIGTERM would be inherited by every process started later
            self.assertEqual((signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)), (old_int, old_term))
        finally:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)


class NativeServer(unittest.TestCase):
    """Native mode (tools/native_server.py): the image's own program without Docker, in a systemd scope."""

    def test_scope_name_matches_only_its_own_server(self):
        import native_server as ns
        plain, nobw = ns.unit_name("st-x"), ns.unit_name("st-x-nobw")
        self.assertEqual(plain, "wseb-st-x.scope")
        line = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/" + nobw
        self.assertNotIn(plain, line)                       # the energy match is by this name in /proc/<pid>/cgroup
        self.assertIn(nobw, line)

    def test_image_settings_without_path(self):
        import native_server as ns
        from unittest import mock
        env = '["PATH=/usr/local/lib/erlang/bin:/usr/bin","ERL_FLAGS=+sbwt none +sbwtdcpu none","A=b=c"]'
        with mock.patch.object(ns, "_docker", return_value=env):
            self.assertEqual(ns.image_env("img"), {"ERL_FLAGS": "+sbwt none +sbwtdcpu none", "A": "b=c"})

    def test_command_has_only_the_image_settings(self):
        import native_server as ns
        from unittest import mock
        with mock.patch.dict(os.environ, {"MEASURE_SECRET": "leak"}):
            cmd = ns.command("wseb-s.scope", "/n/s", {"ERL_FLAGS": "+sbwt none"}, 8001)
        self.assertEqual(cmd[:6], ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--unit=wseb-s.scope"])
        self.assertEqual(cmd[6:8], ["env", "-i"])           # nothing of the measuring tool's environment
        self.assertEqual(cmd[-1], "/n/s/start.sh")
        self.assertIn("ERL_FLAGS=+sbwt none", cmd)
        self.assertIn("PORT=8001", cmd)
        self.assertIn("APP_DIR=/n/s/app", cmd)
        self.assertIn("PATH=" + ns.HOST_PATH, cmd)
        self.assertFalse([a for a in cmd if "MEASURE_SECRET" in a])

    def test_statistics_from_the_cgroup(self):
        import native_server as ns
        import threading
        from unittest import mock
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "memory.current"), "w") as fh:
            fh.write(str(100 * 1024 * 1024))
        clock = [0.0]

        def cpu(folder):                                    # half a core: 0.5 s of CPU per second
            clock[0] += 1.0
            return int(clock[0] * 0.5e6)
        stop = threading.Event()
        calls = [0]

        def tick(_):
            calls[0] += 1
            if calls[0] >= 4:
                stop.set()
        with mock.patch.object(ns, "cgroup_dir", return_value=d), mock.patch.object(ns, "_cpu_usec", cpu), \
                mock.patch.object(ns.time, "monotonic", lambda: clock[0]), mock.patch.object(ns.time, "sleep", tick):
            cpu_stats, mem = ns.collect_stats("u", stop)
        shutil.rmtree(d)
        self.assertAlmostEqual(cpu_stats["avg"], 50.0)          # % of one core, as docker stats
        self.assertAlmostEqual(mem["peak"], 100.0)              # MB

    def fake_images(self, layers):
        """A fake docker: image inspect answers the layers (or fails: no such image); create/cp/rm make a copy."""
        ns = __import__("native_server")
        from unittest import mock
        copies = []

        def docker(docker_path, *args):
            if args[:2] == ("image", "inspect"):
                if args[-1] not in layers:
                    raise ns.subprocess.CalledProcessError(1, "docker")
                return layers[args[-1]]
            if args[0] == "create":
                copies.append(args[1])
                return "box"
            if args[0] == "cp":
                os.makedirs(os.path.dirname(args[2]), exist_ok=True)
                if args[2].endswith("app"):
                    os.makedirs(args[2])
                else:
                    open(args[2], "w").close()
            return ""

        def run(cmd, **kw):                                     # docker cp -L of os-release, docker rm
            if cmd[1:3] == ["cp", "-L"]:
                open(cmd[-1], "w").close()
            return mock.Mock(returncode=0, stdout="")
        return copies, mock.patch.object(ns, "_docker", docker), mock.patch.object(ns.subprocess, "run", run)

    def test_variant_uses_its_servers_copy(self):
        import native_server as ns
        d = tempfile.mkdtemp()
        copies, p1, p2 = self.fake_images({"st-x": '["sha256:l1","sha256:l2"]', "st-x-nobw": '["sha256:l1","sha256:l2"]'})
        with p1, p2:
            first = ns.unpack("st-x", root=d)
            self.assertEqual(ns.unpack("st-x-nobw", root=d), first)        # same files: no second copy
            self.assertEqual(copies, ["st-x"])
            self.assertEqual(ns.stale_copies(root=d), [])
        self.assertEqual(sorted(os.listdir(d)), ["st-x"])
        shutil.rmtree(d)

    def test_variant_first_and_other_files(self):
        import native_server as ns
        d = tempfile.mkdtemp()
        copies, p1, p2 = self.fake_images({"st-x": '["sha256:l1"]', "st-x-nobw": '["sha256:l1"]', "st-y": '["sha256:l9"]'})
        with p1, p2:
            shared = ns.unpack("st-x-nobw", root=d)                      # shuffled order: the variant can come first
            self.assertEqual(ns.unpack("st-x", root=d), shared)
            self.assertNotEqual(ns.unpack("st-y", root=d), shared)        # other files: its own copy
            self.assertEqual(copies, ["st-x-nobw", "st-y"])
        shutil.rmtree(d)

    def test_variant_keeps_its_own_settings(self):
        with open(os.path.join(ROOT, "tools", "native_server.py")) as fh:
            src = fh.read()
        # the copy may be shared, the settings are always the started image's own (ERL_FLAGS of -nobw)
        self.assertIn("command(unit, folder, image_env(image, docker_path), port)", src)

    def test_failure_stops_the_scope(self):
        import measure_failure
        from unittest import mock
        with mock.patch.object(measure_failure.subprocess, "run") as run:
            measure_failure.started_native("wseb-s.scope")
            try:
                measure_failure._cleanup()
            finally:
                measure_failure._running["native"] = None
        self.assertIn(["systemctl", "--user", "stop", "wseb-s.scope"], [c.args[0] for c in run.call_args_list])

    def test_measure_docker_has_deploy(self):
        import plugins
        self.assertEqual(plugins.names("deploy"), ["container", "native"])      # --deploy lists the plugins
        native = plugins.load("deploy", "native")("img", "st-x-native", "8001:8001", "bridge", "docker")
        self.assertEqual(native.energy_id(), "wseb-st-x-native.scope")         # energy by the scope's cgroup

    @unittest.skipUnless(shutil.which("systemd-run") and shutil.which("docker"), "needs systemd and Docker")
    def test_real_server_runs_and_stops_completely(self):
        import native_server as ns
        import subprocess
        import urllib.request
        img = "st-erlang-cowboy-29-1-1"
        if subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode != 0:
            self.skipTest(f"image {img} not built")
        if subprocess.run(["ss", "-ltn"], capture_output=True, text=True).stdout.count(":8001 "):
            self.skipTest("port 8001 in use")
        from unittest import mock
        folder = tempfile.mkdtemp()
        with mock.patch.object(ns, "NATIVE_DIR", folder):
            unit = ns.start(img, "test-" + img, 8001)
        try:
            pids = []
            for _ in range(30):
                try:
                    with urllib.request.urlopen("http://localhost:8001/", timeout=2) as r:
                        self.assertEqual(r.status, 200)
                    break
                except OSError:
                    time.sleep(1)
            with open(os.path.join(ns.cgroup_dir(unit), "cgroup.procs")) as fh:
                pids = fh.read().split()
            self.assertTrue(pids)
        finally:
            ns.stop(unit)
        time.sleep(1)
        shutil.rmtree(folder)
        self.assertFalse([p for p in pids if os.path.exists(f"/proc/{p}")])   # nothing outlives the stop
