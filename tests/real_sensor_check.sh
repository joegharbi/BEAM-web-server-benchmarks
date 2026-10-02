#!/usr/bin/env bash
# One short measurement with the real Scaphandre, to confirm the new energy code on
# real RAPL data. Run from the repo root:  bash tests/real_sensor_check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
sudo -v
OUT=$(mktemp -d)
MEASURE_STARTUP_WAIT=5 srv/bin/python tools/measure_docker.py \
  --server_image st-erlang-cowboy-28-4-3 --num_requests 5000 --max_workers 100 \
  --measurement_type static --output_csv "$OUT/run.csv" --output_json "$OUT/run.json"
srv/bin/python - "$OUT" <<'EOF'
import csv, json, statistics, sys
out = sys.argv[1]
r = list(csv.DictReader(open(f"{out}/run.csv")))[-1]
d = json.load(open(f"{out}/run.json"))
ts = [e["host"]["timestamp"] for e in d]
gaps = [b - a for a, b in zip(ts, ts[1:])]
checks = [
    ("all requests succeeded", r["Successful Requests"] == r["Total Requests"]),
    ("container energy > 0", float(r["Container Energy (J)"]) > 0),
    ("container power below host power", float(r["Container Avg Power (W)"]) < float(r["Host Avg Power (W)"])),
    ("window fully covered", float(r["Energy Window Coverage"]) >= 0.99),
    ("sampling well under 2 s", statistics.median(gaps) < 1.0),
]
for k in ("Execution Time (s)", "Container Energy (J)", "Container Avg Power (W)", "Host Energy (J)",
          "Host Avg Power (W)", "Energy Samples", "Energy Window Coverage", "HTTP Connection Mode"):
    print(f"  {k}: {r[k]}")
print(f"  median sample spacing: {statistics.median(gaps):.3f} s")
for name, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
print(f"  files: {out}")
sys.exit(0 if all(ok for _, ok in checks) else 1)
EOF
