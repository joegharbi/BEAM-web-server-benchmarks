#!/bin/sh
# Builds the images of experiments/erlang-variants.
#   lt-erlang-variants  the plain Erlang server with all its variants (server/)
#   lt-java-loop        the plain Java event loop (java-loop/)
#   lt-netty            the framework's st-java-netty server with netty/Server.java (adds WORKERS)
#   lt-cowboy           the framework's st-erlang-cowboy server with cowboy/simple_cowboy_app.erl (adds the limits)
# and then every line of variants.list: an image that only adds settings (ENV) to one of these.
set -e
cd "$(dirname "$0")"
STATIC=../../benchmarks/static
docker build -q -t lt-erlang-variants server
docker build -q -t lt-java-loop java-loop
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp -r "$STATIC/java/netty/st-java-netty-4-2-19" "$tmp/netty"
cp netty/Server.java "$tmp/netty/src/main/java/bench/Server.java"
docker build -q -t lt-netty "$tmp/netty"
cp -r "$STATIC/erlang/cowboy/st-erlang-cowboy-29-1-1" "$tmp/cowboy"
cp cowboy/simple_cowboy_app.erl "$tmp/cowboy/src/simple_cowboy_app.erl"
docker build -q -t lt-cowboy "$tmp/cowboy"
grep -v '^ *#' variants.list | grep . | while read -r image base settings; do
    {
        echo "FROM $base"
        for s in $settings; do
            if [ "$s" = NOBW ]; then echo 'ENV ERL_FLAGS="+sbwt none +sbwtdcpu none +sbwtdio none"'; else echo "ENV $s"; fi
        done
    } | docker build -q -t "$image" - >/dev/null
    echo "$image = $base + $settings"
done
