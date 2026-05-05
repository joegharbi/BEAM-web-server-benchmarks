#!/bin/sh
set -e
ulimit -n 100000
exec gleam run -m ws_gleam_mist/main "$@"
