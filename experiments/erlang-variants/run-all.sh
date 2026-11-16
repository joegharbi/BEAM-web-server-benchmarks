#!/usr/bin/env bash
# The two runs of experiments/erlang-variants, one after the other, unattended: the server designs (about 13 h),
# then the many connections run (about 5 h). The second starts also when the first stopped early; each writes its
# own results folder and restores the machine settings itself. Asks for the sudo password once, at the start.
#     ./experiments/erlang-variants/run-all.sh
cd "$(dirname "$0")/../.." || exit 1
for config in designs connections; do
    echo "=== $(date '+%F %T') $config.config"
    ./srv/bin/python3 experiments/load-tools/run.py --config "experiments/erlang-variants/$config.config"
    echo "=== $(date '+%F %T') $config.config ended with exit code $?"
done
