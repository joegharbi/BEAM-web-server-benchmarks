#!/bin/bash
# Single script for container testing:
#   - Default: build all images, then run health check on every container (logs in logs/).
#   - --no-build: only run health check (assumes images already built).
# Usage: ./scripts/test_containers.sh [--no-build]   or   make test   /   make check-health
set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

NO_BUILD=0
for arg in "$@"; do
    case "$arg" in
        --no-build) NO_BUILD=1 ;;
        --help|-h)
            echo "Usage: $0 [--no-build]"
            echo "  (default) Build all images, then run health check on every container."
            echo "  --no-build  Skip build; only run health check on already-built images."
            exit 0
            ;;
    esac
done

mkdir -p logs
TS=$(date +%Y-%m-%d_%H%M%S)
BUILD_LOG="logs/build_${TS}.log"
TEST_LOG="logs/test_${TS}.log"

# Startup wait for health check (shorter than full benchmark)
STARTUP_WAIT="${TEST_STARTUP_WAIT:-8}"

# Ensure venv has dependencies (required for WebSocket health check)
VENV_PYTHON=""
if [ -f "$REPO_ROOT/srv/bin/python3" ] || [ -x "$REPO_ROOT/srv/bin/python3" ]; then
    VENV_PYTHON="$REPO_ROOT/srv/bin/python3"
    if ! "$VENV_PYTHON" -c "import websockets" 2>/dev/null; then
        echo "Installing Python dependencies (required for WebSocket health check)..."
        "$VENV_PYTHON" -m pip install -r "$REPO_ROOT/requirements.txt" -q
        echo ""
    fi
fi

if [ "$NO_BUILD" -eq 0 ]; then
    echo "=== Test: build then health check (logs in logs/) ==="
    echo ""
    echo "[1/2] Building all Docker images... (log: $BUILD_LOG)"
    bash scripts/install_benchmarks.sh 2>&1 | tee "$BUILD_LOG"
    BUILD_EXIT=${PIPESTATUS[0]}
    if [ "$BUILD_EXIT" -ne 0 ]; then
        echo ""
        echo "Test FAILED: one or more images failed to build. See $BUILD_LOG"
        exit 1
    fi
    echo ""
    echo "Build finished successfully."
    echo ""
    echo "[2/2] Running health check on all containers (startup wait: ${STARTUP_WAIT}s)... (log: $TEST_LOG)"
else
    echo "=== Health check only (no build) ==="
    echo ""
    echo "Running health check on all built containers (startup wait: ${STARTUP_WAIT}s)... (log: $TEST_LOG)"
fi

{
    echo "Health check started at $(date -Iseconds)"
    echo "Startup wait: ${STARTUP_WAIT}s per container"
    echo ""
} | tee "$TEST_LOG"

[ -n "$VENV_PYTHON" ] && export PYTHON_WS_FOR_HEALTH="$VENV_PYTHON"
bash scripts/check_health.sh --startup "$STARTUP_WAIT" 2>&1 | tee -a "$TEST_LOG"
HEALTH_EXIT=${PIPESTATUS[0]}
if [ "$HEALTH_EXIT" -ne 0 ]; then
    echo ""
    echo "Health check FAILED: one or more containers failed. See $TEST_LOG"
    exit 1
fi

echo ""
echo "=== All containers healthy ==="
[ "$NO_BUILD" -eq 0 ] && echo "  Build log:  $BUILD_LOG"
echo "  Test log:   $TEST_LOG"
echo "  You can run the full benchmark: make run"
echo ""
