#!/bin/sh
set -e
ulimit -n 100000
exec gleam run -m gleam_index_static/main "$@"
