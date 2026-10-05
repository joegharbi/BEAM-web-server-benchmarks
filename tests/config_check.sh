#!/usr/bin/env bash
# Real check of run_benchmarks.sh --config with the machine settings enforced and both
# readiness checks on: applies performance governor + turbo off + stops other containers,
# runs 2 servers x 2 repeats (super-quick), then checks the measurement ran under those
# settings, every run passed the readiness checks, the raw logs were kept and give the same
# energy when recalculated, the statistics were written, and the machine is restored exactly.
# Run from the repo root:  bash tests/config_check.sh   (about 6 minutes, needs sudo)
set -uo pipefail
cd "$(dirname "$0")/.."
BEAM_BENCH="${BEAM_BENCH:-benchmarks}"
TMP=$(mktemp -d)

# Two targets whose images are already built
mkdir -p "$TMP/bench/static/erlang/cowboy" "$TMP/bench/static/elixir/pure" "$TMP/bench/dynamic" "$TMP/bench/websocket"
cp -r "$BEAM_BENCH/static/erlang/cowboy/st-erlang-cowboy-28-4-3" "$TMP/bench/static/erlang/cowboy/"
cp -r "$BEAM_BENCH/static/elixir/pure/st-elixir-pure-1-19-5" "$TMP/bench/static/elixir/pure/"
cat > "$TMP/check.config" <<'EOF'
REPEATS=2
SHUFFLE=1
SETTLE_SECONDS=10
READY_CHECK_EVERY_SECONDS=5
READY_TEMP_MARGIN_C=3
# CPU check relative to the machine's own resting use, since VS Code may be open during this check
READY_CPU_BUSY_REFERENCE_PERCENT=
READY_CPU_BUSY_MARGIN_PERCENT=10
READY_MIN_WAIT_SECONDS=10
READY_MAX_WAIT_SECONDS=180
# stop instead of waiting forever, so this check can never hang
READY_ON_TIMEOUT=stop
ENV_GOVERNOR=performance
ENV_TURBO=off
ENV_STOP_CONTAINERS=1
RAW_DATA=keep
IDLE_SECONDS=5
WARMUP_SECONDS=3
# Laptop settings (Wi-Fi is left alone, so a remote session is not cut during the check)
ENV_SCREEN_BRIGHTNESS=20
ENV_KEYBOARD_LIGHT=off
ENV_BLUETOOTH=off
ENV_WIFI=unchanged
ON_BATTERY=wait
EOF

state() {
    srv/bin/python -c 'import sys; sys.path.insert(0, "tools"); import run_metadata as m; print(m.cpu_governor(), m.turbo_state(), "screen", m.screen_brightness_percent(), "kbd", m.keyboard_backlight_percent(), "bt", m.radios().get("bluetooth"))'
    docker ps --format '{{.Names}}' | sort | tr '\n' ' '
}
BEFORE=$(state)
echo "Before: $BEFORE"

source srv/bin/activate
bash scripts/run_benchmarks.sh --super-quick --bench "$TMP/bench" --config "$TMP/check.config" static > "$TMP/run.out" 2>&1 &
RUN=$!
# A background job of a script ignores Ctrl-C, so pass it on: TERM makes the run stop and restore
# the machine settings itself; wait for that before leaving.
trap 'echo "Stopping the check run (it restores the machine settings) ..."; kill -TERM "$RUN" 2>/dev/null; wait "$RUN"; exit 130' INT TERM
sleep 20
INHIBITED=$(systemd-inhibit --list --no-pager | grep -c "web-server benchmarks")
wait $RUN
RC=$?
cat "$TMP/run.out"
AFTER=$(state)
echo "After:  $AFTER"
D=$(ls -d results/*/ | tail -1)

srv/bin/python - "$D" "$RC" "$BEFORE" "$AFTER" "$INHIBITED" <<'EOF'
import csv, glob, json, os, sys
sys.path.insert(0, "tools")
import scaphandre_energy as se
d, rc, before, after, inhibited = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5])
m = json.load(open(os.path.join(d, "metadata.json")))
s = m["machine_state_start"]
runs = [r for f in glob.glob(os.path.join(d, "static", "*.csv"))
        if not f.endswith("_summary.csv") and os.path.basename(f) != "summary.csv"
        for r in csv.DictReader(open(f))]
rows = len(runs)
limit = float(m["settings"]["ready_temp_reference_c"]) + float(m["settings"]["ready_temp_margin_c"])
for r in runs:
    print(f"  {r['Container Name']:<26} load started at {r['Host CPU Temp Start (C)']} C (limit {limit}), "
          f"waited {r['Waited Before Start (s)']}s + {r['Waited Before Load (s)']}s, "
          f"throttled {r['Host Throttled (ms)']} ms, ready: {r['Ready Check']}")
raw = sorted(g for g in glob.glob(os.path.join(d, "raw", "*.json")) if not g.endswith(".window.json"))
same = []
for gz in raw:
    w = json.load(open(se.window_path(gz)))
    csv_e = [float(r["Container Energy (J)"]) for r in runs if r["Container Name"] == w["container_name"]]
    re_e = se.recompute(gz)["energy_j"]
    same.append(any(abs(e - re_e) < 1e-9 for e in csv_e))
    print(f"  raw {os.path.basename(gz)}: recalculated {re_e:.6f} J, in CSV: {'yes' if same[-1] else 'NO'}")
progress = open(os.path.join(d, "progress.txt")).read().splitlines() if os.path.exists(os.path.join(d, "progress.txt")) else []
checks = [
    ("run finished without error", rc == 0),
    ("measured under governor=performance", s["cpu_governor"] == "performance"),
    ("measured with turbo off", s["turbo"] == "off"),
    ("conditions stable start to end", m.get("conditions_stable") is True),
    ("4 measurements (2 servers x 2 repeats)", rows == 4),
    ("every run passed both readiness checks", all(r["Ready Check"] == "yes" for r in runs)),
    ("every load started within the temperature limit", all(float(r["Host CPU Temp Start (C)"]) <= limit for r in runs)),
    ("no throttling during any run", all(r["Host Throttled (ms)"] in ("0", "") for r in runs)),
    ("no failed measurements", not os.path.exists(os.path.join(d, "failures.csv"))),
    ("progress.txt lists the 4 measurements", len(progress) == 4),
    ("4 raw logs kept, with window files", len(raw) == 4 and all(os.path.exists(se.window_path(g)) for g in raw)),
    ("energy recalculated from raw logs = CSV energy", len(same) == 4 and all(same)),
    ("statistics written (per server and summary.csv)", len(glob.glob(os.path.join(d, "static", "*_summary.csv"))) == 2
     and os.path.isfile(os.path.join(d, "static", "summary.csv"))),
    ("bench.config.resolved saved", os.path.isfile(os.path.join(d, "bench.config.resolved"))),
    ("schedule and config saved", os.path.isfile(os.path.join(d, "schedule.txt")) and os.path.isfile(os.path.join(d, "bench.config"))),
    ("machine restored exactly (governor, turbo, containers, screen, keyboard light, Bluetooth)", before == after),
    ("screen at 20%, keyboard light and Bluetooth off while measuring",
     abs(int(s["screen_brightness_percent"] or -99) - 20) <= 2 and s["keyboard_backlight_percent"] in (0, "")
     and s["bluetooth"] in ("off", "")),
    ("image IDs recorded at the start", len(m.get("images_at_start", {})) == 2 and all(m["images_at_start"].values())),
    ("sleep was blocked while measuring", inhibited >= 1),
    ("warm-up of 3 s before every run", all(float(r["Warm-up (s)"]) == 3 for r in runs)),
    # A quiet server can use no CPU at all while idle, and then its idle energy is truly 0 J
    ("idle measured for 5 s (energy recorded, 0 J or more)", all(abs(float(r["Idle Time (s)"]) - 5) < 0.5
                                                                 and float(r["Container Idle Energy (J)"]) >= 0 for r in runs)),
    ("laptop state recorded (charger, battery, screen, radios)",
     all(k in s for k in ("ac_power", "battery", "screen_brightness_percent", "wifi", "bluetooth"))),
    ("CSV in the new layout (tools/csv_columns.py)", all(list(r) == __import__("csv_columns").HTTP_COLUMNS for r in runs)),
    ("Repeat 1-2, Session 1 and Measured At in every row", sorted({r["Repeat"] for r in runs}) == ["1", "2"]
     and {r["Session"] for r in runs} == {"1"} and all(r["Measured At (UTC)"] for r in runs)),
    ("Raw Log of every row points to its kept raw file", all(os.path.isfile(os.path.join(d, r["Raw Log"])) for r in runs)),
    ("Container CPU Limit and Host CPUs recorded", all(r["Container CPU Limit"] == "none" and int(r["Host CPUs"]) > 0
                                                        for r in runs)),
    ("Scaphandre package version recorded", bool(m.get("software_and_machine", m).get("scaphandre_package_version", ""))),
]
for name, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
print(f"  results: {d}")
sys.exit(0 if all(ok for _, ok in checks) else 1)
EOF
