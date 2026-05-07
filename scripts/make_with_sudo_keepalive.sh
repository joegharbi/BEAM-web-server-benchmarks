#!/bin/bash
# Run a command while keeping sudo auth alive.
# Usage: ./scripts/make_with_sudo_keepalive.sh <cmd> [args...]
set -euo pipefail

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 <cmd> [args...]"
    exit 1
fi

if ! command -v sudo >/dev/null 2>&1; then
    "$@"
    exit $?
fi

echo "[INFO] Requesting sudo authentication once for this make flow..."
sudo -v

SUDO_KEEPALIVE_PID=""
cleanup() {
    if [ -n "$SUDO_KEEPALIVE_PID" ]; then
        kill "$SUDO_KEEPALIVE_PID" >/dev/null 2>&1 || true
        wait "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
        SUDO_KEEPALIVE_PID=""
    fi
}
trap cleanup EXIT INT TERM

(
    while true; do
        sudo -n true >/dev/null 2>&1 || exit 0
        sleep 60
    done
) &
SUDO_KEEPALIVE_PID=$!

"$@"
