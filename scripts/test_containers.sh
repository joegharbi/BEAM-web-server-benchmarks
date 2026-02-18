#!/bin/bash
# Build all images (with build log) and run a quick health check on every container.
# Logs go to logs/ so you can confirm everything works before starting a long run.
# Usage: ./scripts/test_containers.sh  or  make test
set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs
TS=$(date +%Y-%m-%d_%H%M%S)
BUILD_LOG="logs/build_${TS}.log"
TEST_LOG="logs/test_${TS}.log"

# Shorter startup wait (faster; full run uses longer wait in measure_docker.py)
STARTUP_WAIT="${TEST_STARTUP_WAIT:-8}"

echo "=== Test: build + quick health check (logs in logs/) ==="
echo ""

# Ensure venv has dependencies (required for WebSocket health check in Phase 2)
VENV_PYTHON=""
if [ -f "$REPO_ROOT/srv/bin/python3" ] || [ -x "$REPO_ROOT/srv/bin/python3" ]; then
    VENV_PYTHON="$REPO_ROOT/srv/bin/python3"
    echo "[0/2] Ensuring Python dependencies (required for WebSocket health check)..."
    "$VENV_PYTHON" -m pip install -r "$REPO_ROOT/requirements.txt" -q
    echo ""
fi

# --- Phase 1: Build all images (full log) ---
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

# --- Phase 2: Quick health check on every built container ---
echo "[2/2] Running quick health check on all containers (startup wait: ${STARTUP_WAIT}s)... (log: $TEST_LOG)"
{
    echo "Container test started at $(date -Iseconds)"
    echo "Startup wait: ${STARTUP_WAIT}s per container"
    echo ""
} | tee "$TEST_LOG"

# Force check_health to use the venv Python we installed into (so WebSocket tests see websockets)
[ -n "$VENV_PYTHON" ] && export PYTHON_WS_FOR_HEALTH="$VENV_PYTHON"
bash scripts/check_health.sh --startup "$STARTUP_WAIT" 2>&1 | tee -a "$TEST_LOG"
HEALTH_EXIT=${PIPESTATUS[0]}
if [ "$HEALTH_EXIT" -ne 0 ]; then
    echo ""
    echo "Test FAILED: one or more containers failed the health check. See $TEST_LOG"
    exit 1
fi

echo ""
echo "=== Test PASSED ==="
echo "  Build log:  $BUILD_LOG"
echo "  Test log:   $TEST_LOG"
echo "  You can run the full benchmark: make run"
echo ""
