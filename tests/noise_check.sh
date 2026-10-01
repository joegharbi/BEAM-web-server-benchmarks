#!/usr/bin/env bash
# How much run-to-run noise is left once the environment is controlled?
# Applies prepare_environment (performance governor, turbo off, other containers
# stopped), runs one configuration RUNS times with a cooldown, prints the spread,
# then restores everything. Compare the CV with the 13% of the unprepared step sweep.
# Run from the repo root:  bash tests/noise_check.sh   (about 6 minutes)
# Close the browser first. Keep the laptop on AC power.
set -euo pipefail
cd "$(dirname "$0")/.."
RUNS=${RUNS:-6}
REQS=${REQS:-20000}
COOLDOWN=${COOLDOWN:-30}
IMAGE=${IMAGE:-st-erlang-cowboy-28-4-3}
OUT=$(mktemp -d)
sudo -v
( while true; do sudo -n true; sleep 50; done ) 2>/dev/null &
KEEPALIVE=$!
restore() { sudo srv/bin/python tools/prepare_environment.py restore || true; kill $KEEPALIVE 2>/dev/null || true; }
trap restore EXIT
srv/bin/python tools/check_environment.py || true
sudo srv/bin/python tools/prepare_environment.py apply
srv/bin/python tools/check_environment.py || true
MEASURE_STARTUP_WAIT=5 srv/bin/python tools/measure_docker.py \
  --server_image "$IMAGE" --num_requests "$REQS" --max_workers 100 --measurement_type static \
  --repeat "$RUNS" --cooldown "$COOLDOWN" --output_csv "$OUT/noise.csv"
srv/bin/python - "$OUT/noise.csv" <<'EOF'
import csv, statistics as st, sys
rows = list(csv.DictReader(open(sys.argv[1])))
def show(col):
    v = [float(r[col]) for r in rows]
    m = st.mean(v); cv = 100 * st.stdev(v) / m
    print(f"  {col:<22} mean {m:9.2f}  CV {cv:5.1f}%  runs {[round(x, 2) for x in v]}")
for c in ("Total Energy (J)", "Avg Power (W)", "Avg CPU (%)", "Execution Time (s)", "Host Avg Power (W)"):
    show(c)
v = [float(r["Total Energy (J)"]) for r in rows]
cv = st.stdev(v) / st.mean(v)
print(f"\n  repeats needed for a +/-5% 95% CI at this CV: about {max(3, round((2.1 * cv / 0.05) ** 2))}")
print(f"  files: {sys.argv[1]}")
EOF
