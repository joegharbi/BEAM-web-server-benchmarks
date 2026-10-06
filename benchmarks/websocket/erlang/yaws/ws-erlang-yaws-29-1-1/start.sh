#!/bin/sh
ulimit -n 100000
# Lean release, plain start script (no node name needed). It runs under bash: under dash
# (/bin/sh on Debian) its printf '\x1f' is not understood and arguments get split at "f".
# -noinput: no shell, no stdin; the release boots and serves.
# APP_DIR: /app in the container; native mode (tools/native_server.py) runs this same script on a copy.
exec bash "${APP_DIR:-/app}/bench_yaws/bin/bench_yaws" -noinput
