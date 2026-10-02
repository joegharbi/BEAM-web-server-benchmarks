#!/usr/bin/env bash
# Builds "<server>-nobw" images: the same server image with BEAM scheduler busy-waiting switched off
# (+sbwt none +sbwtdcpu none +sbwtdio none). The original images are not changed.
# Run from the repo root:  bash experiments/busy-wait/build.sh
set -euo pipefail
SERVERS="st-elixir-cowboy-1-19-5 st-elixir-pure-1-19-5 st-erlang-cowboy-28-4-3"
for s in $SERVERS; do
    printf 'FROM %s\nENV ERL_FLAGS="+sbwt none +sbwtdcpu none +sbwtdio none"\n' "$s" \
        | docker build -q -t "$s-nobw" - >/dev/null
    echo "built $s-nobw"
done
