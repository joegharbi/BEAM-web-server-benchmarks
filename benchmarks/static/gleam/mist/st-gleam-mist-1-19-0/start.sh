#!/bin/sh
ulimit -n 100000
# Erlang shipment (gleam export erlang-shipment) run by its own Erlang runtime under APP_DIR: no gleam
# build tool next to the server, no node name (no epmd).
# APP_DIR: /app in the container; native mode (tools/native_server.py) runs this same script on a copy.
APP_DIR="${APP_DIR:-/app}"
PATH="$APP_DIR/erlang/bin:$PATH" exec "$APP_DIR/shipment/entrypoint.sh" run
