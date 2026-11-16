#!/usr/bin/env bash
# The runs of experiments/erlang-variants, one after the other, unattended. Asks for the sudo password once.
#   1. a short check of the whole chain (machine settings, meter, recorder; about 5 minutes), verified by
#      verify_check.py; stops here if a check fails
#   2. the server designs (designs.config, about 17 h)
#   3. the many connections run (connections.config, about 8 h); starts also when the designs run stopped early
# Each run writes its own results folder and restores the machine settings itself.
#     ./experiments/erlang-variants/run-all.sh            everything
#     ./experiments/erlang-variants/run-all.sh --check    only step 1
cd "$(dirname "$0")/../.." || exit 1
run() { ./srv/bin/python3 experiments/load-tools/run.py --config "experiments/erlang-variants/$1.config" "${@:2}"; }
echo "=== $(date '+%F %T') short check"
if ! run connections --repeats 1 --seconds 20 --warmup-seconds 5 --servers cowboy-open --connection-levels 100 1000; then
    echo "the short check failed: nothing else was started"; exit 1
fi
if ! ./srv/bin/python3 experiments/erlang-variants/verify_check.py; then
    echo "the short check ran, but its data did not pass the checks above: nothing else was started"; exit 1
fi
echo "=== short check passed"
[ "$1" = "--check" ] && exit 0
for config in designs connections; do
    echo "=== $(date '+%F %T') $config.config"
    run "$config"
    echo "=== $(date '+%F %T') $config.config ended with exit code $?"
done
