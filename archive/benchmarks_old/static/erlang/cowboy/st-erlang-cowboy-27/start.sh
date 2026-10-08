#!/bin/sh
ulimit -n 100000
exec /app/simple_cowboy_app/bin/simple_cowboy_app foreground 