#!/bin/sh
ulimit -n 100000
# Lean release, plain start script (no node name needed). It runs under bash: under dash
# (/bin/sh on Debian) its printf '\x1f' is not understood and arguments get split at "f".
# -noinput: no shell, no stdin; the release boots and serves.
exec bash /app/simple_cowboy_app/bin/simple_cowboy_app -noinput
