"""Unit tests for the P0 changes (energy window, connection mode, CSV and aggregator keys).

Run from the repo root:  venv/bin/python -m unittest tests/test_changes.py -v
Needs no sudo, Docker or Scaphandre.
"""
import csv
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
        import measure_docker as m
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
        import measure_docker as m
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
        import measure_docker, measure_websocket, inspect
        for mod in (measure_docker, measure_websocket):
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
        import argparse, measure_docker
        a = argparse.Namespace(waited_s=12.5, ready_check="yes")
        f = measure_docker.thermal_fields((45.0, 100), (52.0, 130), a, (3.0, "yes"))
        self.assertEqual(f, {"Host CPU Temp Start (C)": 45.0, "Host CPU Temp End (C)": 52.0, "Host Throttled (ms)": 30,
                             "Waited Before Start (s)": 12.5, "Waited Before Load (s)": 3.0,
                             "Ready Check": "yes"})

    def test_without_gate_says_not_checked(self):
        import argparse, measure_websocket
        a = argparse.Namespace(waited_s=None, ready_check="not checked")
        f = measure_websocket.thermal_fields(("", ""), ("", ""), a)
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
            "MEASURE_READY_MAX_WAIT_SECONDS", "MEASURE_READY_ON_TIMEOUT")

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
                           "MEASURE_READY_ON_TIMEOUT": on_timeout})

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
            fh.write("SETTLE_SECONDS=60\nENV_GOVERNOR=unchanged\nENV_TURBO=unchanged\nENV_STOP_CONTAINERS=1\n"
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
        self.assertLess(len(example.splitlines()), 80)

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
        import http.server, threading, measure_docker as md

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
            md.results_counter.update(success=0, failure=0, total=0)
            md.warm_up(f"http://127.0.0.1:{srv.server_port}/", 0.5, 4, "reuse")
            self.assertGreater(Ok.hits, 0)                              # the server got traffic
            self.assertEqual(md.results_counter["total"], 0)            # but nothing was counted
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
        d = dict(governor="unchanged", turbo="unchanged", stop_containers=False, keep="",
                 screen_brightness="20", keyboard_light="off", wifi="off", bluetooth="off",
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

    def test_unplugged_during_the_load_fails_the_run(self):
        import load_phases
        saved = os.environ.pop("MEASURE_ON_BATTERY", None)
        try:
            self.assertFalse(load_phases.charger_unplugged("yes", "no"))           # no config: not checked
            os.environ["MEASURE_ON_BATTERY"] = "wait"
            self.assertTrue(load_phases.charger_unplugged("yes", "no"))
            self.assertFalse(load_phases.charger_unplugged("yes", "yes"))
            self.assertFalse(load_phases.charger_unplugged("", ""))                # no battery at all
            os.environ["MEASURE_ON_BATTERY"] = "ignore"
            self.assertFalse(load_phases.charger_unplugged("yes", "no"))
        finally:
            os.environ.pop("MEASURE_ON_BATTERY", None)
            if saved is not None:
                os.environ["MEASURE_ON_BATTERY"] = saved


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
            fh.write("ENV_GOVERNOR=unchanged\nENV_TURBO=unchanged\nENV_STOP_CONTAINERS=0\nENV_SCREEN_BRIGHTNESS=unchanged\n"
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
                     '  for i in $FAKE_IMAGES; do [ "$i" = "$3" ] && exit 0; done; exit 1\nfi\nexit 0\n')
        for f in ("sudo", "docker"):
            os.chmod(os.path.join(self.bin, f), 0o755)
        self.bench = os.path.join(self.d, "bench")
        for rel in ("static/erlang/cowboy/st-a", "static/elixir/pure/st-b", "dynamic/erlang/pure/dy-c",
                    "websocket/erlang/cowboy/ws-d"):
            os.makedirs(os.path.join(self.bench, rel))
            open(os.path.join(self.bench, rel, "Dockerfile"), "w").close()

    def run_until_plan(self, config, args=(), images="st-a st-b dy-c ws-d my-img"):
        import signal
        cfg = os.path.join(self.d, "c.config")
        with open(cfg, "w") as fh:
            fh.write("SETTLE_SECONDS=0\nRESTING_MEASURE_SECONDS=1\nENV_GOVERNOR=unchanged\nENV_TURBO=unchanged\n"
                     "ENV_STOP_CONTAINERS=0\nENV_SCREEN_BRIGHTNESS=unchanged\nENV_KEYBOARD_LIGHT=unchanged\n"
                     "ENV_WIFI=unchanged\nENV_BLUETOOTH=unchanged\n" + config)
        before = set(os.listdir(os.path.join(ROOT, "results")))
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], FAKE_IMAGES=images,
                   FAKE_BUILDS=os.path.join(self.d, "build"), FAKE_BUILD_FAIL=self.build_fail)
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
        self.assertIn("The command line chooses what to measure (static); MEASURE, SERVERS and VARIANTS of the config are not used", out)

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
        run_metadata._run = lambda cmd, cwd=None: "2026-09-21T14:13:20.123456789+00:00"               # = built
        try:
            stale = run_metadata.stale_images(d, ["st-a", "st-b", "st-a-nobw"])
        finally:
            run_metadata._run = saved
        self.assertEqual(stale, [("st-b", os.path.join("static", "x", "st-b", "Dockerfile"))])


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
        import measure_docker, measure_websocket
        for module in (measure_docker, measure_websocket):
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
        with mock.patch.object(run_metadata, "_min_mhz", side_effect=fake):
            with mock.patch.object(run_metadata, "turbo_state", return_value="off"):
                self.assertEqual(run_metadata.expected_cpu_speed_mhz(), 1800)
            with mock.patch.object(run_metadata, "turbo_state", return_value="on"):
                self.assertEqual(run_metadata.expected_cpu_speed_mhz(), 4900)

    def test_config_setting(self):
        import bench_config
        check = bench_config.SCHEMA["READY_CPU_SPEED"]["check"]
        self.assertEqual(check("auto"), "auto")
        self.assertEqual(check("off"), "off")
        self.assertEqual(check("1800"), "1800")
        with self.assertRaises(ValueError):
            check("fast")


class PinMinSpeed(unittest.TestCase):
    """Environment: with the performance governor the minimum speed is pinned to the maximum."""

    def test_pinned_saved_and_restorable(self):
        import prepare_environment as pe
        from unittest import mock
        d = tempfile.mkdtemp()
        maxes = []
        for i, (top, low) in enumerate([("1800000", "400000"), ("1800000", "400000")]):
            os.makedirs(os.path.join(d, f"cpu{i}"))
            for name, v in (("scaling_max_freq", top), ("scaling_min_freq", low)):
                with open(os.path.join(d, f"cpu{i}", name), "w") as fh:
                    fh.write(v)
            maxes.append(os.path.join(d, f"cpu{i}", "scaling_max_freq"))
        saved = {}
        with mock.patch.object(pe.glob, "glob", return_value=maxes):
            pe.pin_min_speed(saved)
        for m in maxes:
            with open(m.replace("max", "min")) as fh:
                self.assertEqual(fh.read(), "1800000")
        self.assertEqual(set(saved.values()), {"400000"})
        shutil.rmtree(d)


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
