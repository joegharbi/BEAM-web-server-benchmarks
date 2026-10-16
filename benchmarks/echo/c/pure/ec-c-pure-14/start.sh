#!/bin/sh
ulimit -n 100000
# APP_DIR: /app in the container; native mode (tools/native_server.py) runs this same script on a copy.
exec "${APP_DIR:-/app}/server"
