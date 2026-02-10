#!/bin/sh

# Match benchmark expectations: high file descriptor limit
ulimit -n 100000

cd /app
export MIX_ENV=prod

exec mix run --no-halt

