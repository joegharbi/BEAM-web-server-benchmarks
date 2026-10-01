#!/usr/bin/env bash
# Choose the Scaphandre sampling step from data: the same workload at several steps,
# a few runs each, reporting container energy (mean and spread) and Scaphandre's own
# power draw. Run from the repo root:  bash tests/step_sweep.sh   (about 10 minutes)
# Close the browser and other busy programs first.
set -euo pipefail
cd "$(dirname "$0")/.."
STEPS=${STEPS:-"100 250 500 1000 2000"}
RUNS=${RUNS:-3}
REQS=${REQS:-20000}
IMAGE=${IMAGE:-st-erlang-cowboy-28-4-3}
OUT=$(mktemp -d)
sudo -v
( while true; do sudo -n true; sleep 50; done ) 2>/dev/null &
KEEPALIVE=$!
trap 'kill $KEEPALIVE 2>/dev/null' EXIT
for step in $STEPS; do
  for i in $(seq 1 "$RUNS"); do
    echo "== step ${step} ms, run ${i}/${RUNS}"
    MEASURE_SCAPH_STEP_MS=$step MEASURE_STARTUP_WAIT=5 srv/bin/python tools/measure_docker.py \
      --server_image "$IMAGE" --num_requests "$REQS" --max_workers 100 --measurement_type static \
      --output_csv "$OUT/step_${step}.csv" --output_json "$OUT/step_${step}_${i}.json"
    sleep 20
  done
done
srv/bin/python - "$OUT" "$STEPS" "$RUNS" <<'EOF'
import csv, json, statistics as st, sys
out, steps, runs = sys.argv[1], sys.argv[2].split(), int(sys.argv[3])
print(f"\n{'step ms':>8} {'container J':>18} {'CV %':>6} {'host W':>7} {'scaph W idle':>12} {'scaph W load':>12} {'spacing s':>9}")
for s in steps:
    rows = list(csv.DictReader(open(f"{out}/step_{s}.csv")))
    e = [float(r["Total Energy (J)"]) for r in rows]
    hw = [float(r["Host Avg Power (W)"]) for r in rows]
    idle, load, gaps = [], [], []
    for i in range(1, runs + 1):
        d = json.load(open(f"{out}/step_{s}_{i}.json"))
        ts = [x["host"]["timestamp"] for x in d]
        gaps += [b - a for a, b in zip(ts, ts[1:])]
        for x in d[1:]:
            sc = sum(c["consumption"] for c in x["consumers"] if "scaphandre" in (c.get("exe") or "")) * 1e-6
            busy = any("beam.smp" in (c.get("exe") or "") and c["consumption"] > 1e5 for c in x["consumers"])
            (load if busy else idle).append(sc)
    m = st.mean(e); cv = 100 * st.stdev(e) / m if len(e) > 1 and m else 0
    med = lambda v: st.median(v) if v else float("nan")
    print(f"{s:>8} {m:>9.2f} +/- {st.stdev(e) if len(e)>1 else 0:>5.2f} {cv:>6.1f} {st.mean(hw):>7.2f} {med(idle):>12.2f} {med(load):>12.2f} {med(gaps):>9.3f}")
print(f"\nfiles: {out}")
EOF
