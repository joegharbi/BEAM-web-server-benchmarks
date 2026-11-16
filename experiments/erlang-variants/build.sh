#!/bin/sh
# Builds the images of experiments/erlang-variants: the plain Java event loop (lt-java-loop) and one release with all variants (lt-erlang-variants),
# then per variant a..f an image that only sets HELLO_VARIANT, with busy-waiting on (lt-erlang-<v>) and off
# (lt-erlang-<v>-nobw, the framework's three +sbwt flags).
set -e
cd "$(dirname "$0")"
docker build -q -t lt-erlang-variants server
for v in a b c d e f; do
    printf 'FROM lt-erlang-variants\nENV HELLO_VARIANT=%s\n' "$v" | docker build -q -t "lt-erlang-$v" -
    printf 'FROM lt-erlang-variants\nENV HELLO_VARIANT=%s\nENV ERL_FLAGS="+sbwt none +sbwtdcpu none +sbwtdio none"\n' "$v" \
        | docker build -q -t "lt-erlang-$v-nobw" -
done
docker build -q -t lt-java-loop java-loop
docker images --format '{{.Repository}}' | grep '^lt-' | sort
