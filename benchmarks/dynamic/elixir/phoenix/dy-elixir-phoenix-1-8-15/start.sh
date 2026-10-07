#!/bin/sh
ulimit -n 100000
# Lean release (mix release), started directly: no mix, no build tools running next to the server.
# APP_DIR: /app in the container; native mode (tools/native_server.py) runs this same script on a copy.
exec "${APP_DIR:-/app}/phoenix_dynamic/bin/phoenix_dynamic" start
