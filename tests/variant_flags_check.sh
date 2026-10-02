#!/usr/bin/env bash
# Checks that every server honours ERL_FLAGS: builds <server>-nobw (busy-waiting off), starts it, and
# looks for the flags on the running BEAM's command line. A server that ignored them would make its
# "nobw" variant identical to the default. Run from the repo root (Docker only, no sudo, ~4 minutes):
#   bash tests/variant_flags_check.sh
set -uo pipefail
cd "$(dirname "$0")/.."
FLAGS="+sbwt none +sbwtdcpu none +sbwtdio none"
WANT="-sbwt none -sbwtdcpu none -sbwtdio none"
ok=0; bad=()
for d in benchmarks/*/*/*/*/; do
    [ -f "$d/Dockerfile" ] || continue
    s=$(basename "$d")
    printf 'FROM %s\nENV ERL_FLAGS="%s"\n' "$s" "$FLAGS" | docker build -q -t "$s-nobw" - >/dev/null
    docker rm -f flagcheck >/dev/null 2>&1
    docker run -d --name flagcheck "$s-nobw" >/dev/null
    sleep 7
    if docker exec flagcheck sh -c 'for p in /proc/[0-9]*; do tr "\0" " " < $p/cmdline 2>/dev/null; echo; done' 2>/dev/null \
            | grep -q -- "$WANT"; then
        ok=$((ok + 1)); echo "  [PASS] $s"
    else
        bad+=("$s"); echo "  [FAIL] $s: the flags did not reach the BEAM"
    fi
done
docker rm -f flagcheck >/dev/null 2>&1
echo "$ok servers passed, ${#bad[@]} failed"
[ ${#bad[@]} -eq 0 ]
