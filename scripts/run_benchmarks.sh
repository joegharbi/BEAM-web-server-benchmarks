#!/bin/bash
# Run benchmarks for discovered Docker containers (static, dynamic, websocket).
# Usage: ./scripts/run_benchmarks.sh [static|dynamic|websocket] [--quick|--super-quick]
# Called by: make run, make run-static, make run-quick, etc.
set -e

ulimit -n 100000
# Prefer venv; fall back to system python3 if venv missing or not executable
# Use script location for repo root so paths work when repo path contains spaces or script is run from another dir
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1
if [ -x "$REPO_ROOT/srv/bin/python3" ]; then
    PYTHON_PATH="$REPO_ROOT/srv/bin/python3"
else
    PYTHON_PATH="python3"
fi

# Benchmark root directory (default: ./benchmarks)
# Override with BENCHMARKS_DIR=/path/to/benchmarks
BENCHMARKS_DIR="${BENCHMARKS_DIR:-./benchmarks}"
if [[ "$BENCHMARKS_DIR" != /* ]]; then
    BENCHMARKS_DIR="$REPO_ROOT/$BENCHMARKS_DIR"
fi
if [ ! -d "$BENCHMARKS_DIR" ]; then
    echo "[ERROR] Benchmarks directory not found: $BENCHMARKS_DIR"
    echo "Set BENCHMARKS_DIR to a valid path (default: ./benchmarks)."
    exit 1
fi

# Check for help first, before any other processing
case "${1:-}" in
    "help"|"--help"|"-h")
        echo "Usage: $0 [TYPE] [IMAGES...] [OPTIONS]"
        echo ""
        echo "Types:"
        echo "  static      Run static container benchmarks"
        echo "  dynamic     Run dynamic container benchmarks"
        echo "  websocket   Run WebSocket benchmarks"
        echo ""
        echo "Options:"
        echo "  --quick     Run quick benchmarks with reduced parameters"
        echo "  --super-quick Run super quick benchmarks with single test per type"
        echo "  --single IMAGE  Run a single server (e.g. --single ws-erlang-yaws-27)"
        echo "  --bench PATH    Benchmark root directory (default: ./benchmarks)"
        echo "Environment:"
        echo "  HTTP_MAX_WORKERS     Max HTTP client workers for measure_docker.py (default: unset; CSV: System default)"
        echo "                       Applies to HTTP (static/dynamic) only; WebSocket is unaffected."
        echo "  BENCH_MEASURE_QUIET  HTTP measure_docker logs: 1=compact + heartbeats (default), 0=verbose"
        echo "  MEASURE_HEARTBEAT_SEC  Seconds between quiet-mode load progress lines (default: 15, min: 10)"
        echo "  clean       Clean repository to fresh state"
        echo ""
        echo "Examples:"
        echo "  $0                    # Run all benchmarks"
        echo "  $0 static             # Run all static containers"
        echo "  $0 dynamic dy-erlang-pure-27   # Run specific container(s)"
        echo "  $0 --single ws-erlang-yaws-27   # Run single server (type auto-detected)"
        echo "  $0 --bench ./benchmarks static   # Run from custom benchmark root"
        echo "  $0 --quick static     # Quick static benchmarks"
        echo "  HTTP_MAX_WORKERS=100 $0 static   # Override HTTP worker count"
        echo "  BENCH_MEASURE_QUIET=0 $0 static # Verbose measure_docker.py (default is compact)"
        echo ""
        echo "Port Assignment:"
        echo "  - Fixed host port: ${HOST_PORT:-8001}"
        echo "  - Container port determined from Dockerfile EXPOSE directive"
        echo "  - Default container port: 80"
        echo "  - Benchmark root: ${BENCHMARKS_DIR}"
        exit 0
        ;;
    "concurrency")
        echo "Run Concurrency: test increasing client counts with fixed payload size."
        exit 0
        ;;
    "payload")
        echo "Run Payload: test increasing payload sizes with fixed client count."
        exit 0
        ;;
esac

# Fixed port for all containers (configurable via HOST_PORT env var)
HOST_PORT=${HOST_PORT:-8001}

# Full test parameters for HTTP benchmarks
full_http_requests=(100 1000 5000 8000 10000 15000 20000 30000 40000 50000 60000 70000 80000)

# Quick test parameters for HTTP benchmarks (3 request counts)
quick_http_requests=(1000 5000 10000)
# Super-quick: single request count
super_quick_http_requests=(1000)
# Optional HTTP client worker pool size for reproducible HTTP runs.
# If unset, measure_docker.py uses ThreadPoolExecutor default (None); CSV stores "System default".
# Example override: HTTP_MAX_WORKERS=100 make run
HTTP_MAX_WORKERS="${HTTP_MAX_WORKERS:-}"
# HTTP measurements: 1 = one-line measure_docker output (default); 0 = full logs.
BENCH_MEASURE_QUIET="${BENCH_MEASURE_QUIET:-1}"

# Full test parameters for WebSocket benchmarks (balanced set)
full_ws_burst_clients=(5 50 100)
full_ws_burst_sizes=(8 1024 65536)
full_ws_burst_bursts=(3)
full_ws_burst_intervals=(0.5)
full_ws_stream_clients=(5 50 100)
full_ws_stream_sizes=(8 1024 65536)
full_ws_stream_rates=(10)
full_ws_stream_durations=(5)

# Quick test parameters for WebSocket benchmarks (was super quick)
quick_ws_burst_clients=(5)
quick_ws_burst_sizes=(8)
quick_ws_burst_bursts=(1)
quick_ws_burst_intervals=(0.5)
quick_ws_stream_clients=(5)
quick_ws_stream_sizes=(8)
quick_ws_stream_rates=(1)
quick_ws_stream_durations=(1)
quick_concurrency_clients=(100)
quick_concurrency_size=8
quick_payload_clients=5
quick_payload_sizes=(8)

# Concurrency parameters (balanced)
concurrency_clients=(100 1000 5000)
concurrency_size=8

# Payload parameters (balanced)
payload_clients=5
payload_sizes=(8 1024 65536)

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Long-run progress (grep-friendly): [PROGRESS] step/total | elapsed | ETA | phase | detail
BENCH_RUN_T0=""
BENCH_STEP=0
BENCH_TOTAL_STEPS=0
BENCH_PHASE=""
# Resolved once in bench_init_run_plan; main() loops use these so step totals match execution.
BENCH_PLAN_STATIC=()
BENCH_PLAN_DYNAMIC=()
BENCH_PLAN_WEBSOCKET=()

print_status() {
    local status=$1
    local message=$2
    case $status in
        "INFO") printf "${BLUE}[INFO]${NC} %s\n" "$message" ;;
        "SUCCESS") printf "${GREEN}[SUCCESS]${NC} %s\n" "$message" ;;
        "WARNING") printf "${YELLOW}[WARNING]${NC} %s\n" "$message" ;;
        "ERROR") printf "${RED}[ERROR]${NC} %s\n" "$message" ;;
    esac
}

print_section() {
    local title=$1
    printf "\n${BLUE}=== %s ===${NC}\n" "$title"
}

bench_http_steps_per_container() {
    if [[ $SUPER_QUICK_BENCH -eq 1 ]]; then echo 1
    elif [[ $QUICK_BENCH -eq 1 ]]; then echo 3
    else echo 13
    fi
}

bench_ws_burst_stream_steps_per_container() {
    if [[ $SUPER_QUICK_BENCH -eq 1 ]]; then echo 2
    else
        local b s
        b=$((${#full_ws_burst_clients[@]} * ${#full_ws_burst_sizes[@]} * ${#full_ws_burst_bursts[@]} * ${#full_ws_burst_intervals[@]}))
        s=$((${#full_ws_stream_clients[@]} * ${#full_ws_stream_sizes[@]} * ${#full_ws_stream_rates[@]} * ${#full_ws_stream_durations[@]}))
        echo $((b + s))
    fi
}

bench_ws_concurrency_steps_per_container() {
    if [[ $SUPER_QUICK_BENCH -eq 1 ]]; then echo 1
    else echo ${#concurrency_clients[@]}
    fi
}

bench_ws_payload_steps_per_container() {
    if [[ $SUPER_QUICK_BENCH -eq 1 ]]; then echo 1
    else echo ${#payload_sizes[@]}
    fi
}

bench_elapsed_human() {
    [ -n "$BENCH_RUN_T0" ] || { printf "?"; return; }
    local sec=$(( $(date +%s) - BENCH_RUN_T0 ))
    local h=$(( sec / 3600 ))
    local m=$(( (sec % 3600) / 60 ))
    local s=$(( sec % 60 ))
    if [ "$h" -gt 0 ]; then printf "%dh%02dm" "$h" "$m"
    elif [ "$m" -gt 0 ]; then printf "%dm%02ds" "$m" "$s"
    else printf "%ds" "$s"
    fi
}

# ETA from average time per completed step (after step >= 1)
bench_eta_human() {
    local step=$1
    local total=$2
    if [ -z "$BENCH_RUN_T0" ] || [ "$step" -lt 1 ] || [ "$total" -lt 1 ] || [ "$step" -ge "$total" ]; then
        printf "%s" "—"
        return
    fi
    local now elapsed eta
    now=$(date +%s)
    elapsed=$((now - BENCH_RUN_T0))
    eta=$(( elapsed * (total - step) / step ))
    local eh=$(( eta / 3600 ))
    local em=$(( (eta % 3600) / 60 ))
    if [ "$eh" -gt 0 ]; then printf "~%dh%02dm" "$eh" "$em"
    elif [ "$em" -gt 0 ]; then printf "~%dm" "$em"
    else printf "~%ds" "$eta"
    fi
}

print_bench_progress() {
    local detail=$1
    BENCH_STEP=$((BENCH_STEP + 1))
    local step=$BENCH_STEP
    local total=$BENCH_TOTAL_STEPS
    local pct=0
    local rem=0
    local eta="—"
    if [ "$total" -gt 0 ]; then
        pct=$(( 100 * step / total ))
        rem=$(( total - step ))
        eta=$(bench_eta_human "$step" "$total")
    fi
    printf "${CYAN}[PROGRESS]${NC} %d/%d (%d%%) | elapsed %s | ETA %s | remaining %d | %s | %s\n" \
        "$step" "$total" "$pct" "$(bench_elapsed_human)" "$eta" "$rem" "${BENCH_PHASE:-?}" "$detail"
}

# Call once at start of main(): sets timers, step counter, total steps, prints plan + grep hints.
bench_init_run_plan() {
    BENCH_RUN_T0=$(date +%s)
    BENCH_STEP=0
    BENCH_PLAN_STATIC=()
    BENCH_PLAN_DYNAMIC=()
    BENCH_PLAN_WEBSOCKET=()
    local H bs c p ns nd nw
    H=$(bench_http_steps_per_container)
    bs=$(bench_ws_burst_stream_steps_per_container)
    c=$(bench_ws_concurrency_steps_per_container)
    p=$(bench_ws_payload_steps_per_container)
    ns=0
    nd=0
    nw=0

    if [[ $RUN_ALL -eq 1 ]]; then
        local sa da wa
        sa=($(discover_containers static))
        da=($(discover_containers dynamic))
        wa=($(discover_containers websocket))
        BENCH_PLAN_STATIC=("${sa[@]}")
        BENCH_PLAN_DYNAMIC=("${da[@]}")
        BENCH_PLAN_WEBSOCKET=("${wa[@]}")
        ns=${#sa[@]}
        nd=${#da[@]}
        nw=${#wa[@]}
        BENCH_TOTAL_STEPS=$(( ns * H + nd * H + nw * bs + nw * c + nw * p ))
    else
        case $TARGET_TYPE in
            "static"|"--static")
                local ta=("${TARGET_IMAGES[@]}")
                [ ${#ta[@]} -eq 0 ] && ta=($(discover_containers static))
                TARGET_IMAGES=("${ta[@]}")
                BENCH_PLAN_STATIC=("${ta[@]}")
                ns=${#ta[@]}
                BENCH_TOTAL_STEPS=$(( ns * H ))
                ;;
            "dynamic"|"--dynamic")
                local ta=("${TARGET_IMAGES[@]}")
                [ ${#ta[@]} -eq 0 ] && ta=($(discover_containers dynamic))
                TARGET_IMAGES=("${ta[@]}")
                BENCH_PLAN_DYNAMIC=("${ta[@]}")
                nd=${#ta[@]}
                BENCH_TOTAL_STEPS=$(( nd * H ))
                ;;
            "websocket"|"--websocket")
                local ta=("${TARGET_IMAGES[@]}")
                [ ${#ta[@]} -eq 0 ] && ta=($(discover_containers websocket))
                TARGET_IMAGES=("${ta[@]}")
                BENCH_PLAN_WEBSOCKET=("${ta[@]}")
                nw=${#ta[@]}
                BENCH_TOTAL_STEPS=$(( nw * bs ))
                ;;
            "concurrency")
                local ta=("${TARGET_IMAGES[@]}")
                [ ${#ta[@]} -eq 0 ] && ta=($(discover_containers websocket))
                TARGET_IMAGES=("${ta[@]}")
                BENCH_PLAN_WEBSOCKET=("${ta[@]}")
                nw=${#ta[@]}
                BENCH_TOTAL_STEPS=$(( nw * c ))
                ;;
            "payload")
                local ta=("${TARGET_IMAGES[@]}")
                [ ${#ta[@]} -eq 0 ] && ta=($(discover_containers websocket))
                TARGET_IMAGES=("${ta[@]}")
                BENCH_PLAN_WEBSOCKET=("${ta[@]}")
                nw=${#ta[@]}
                BENCH_TOTAL_STEPS=$(( nw * p ))
                ;;
            *)
                BENCH_TOTAL_STEPS=0
                ;;
        esac
    fi

    printf "\n${BLUE}──────────────── Run plan ─────────────────${NC}\n"
    print_status "INFO" "Log file (full output): $LOG_FILE"
    print_status "INFO" "Results directory: $RESULTS_DIR"
    printf "${BLUE}[INFO]${NC} Total measurement steps (each step = one HTTP or WebSocket measurement): ${GREEN}%s${NC}\n" "$BENCH_TOTAL_STEPS"
    if [[ $RUN_ALL -eq 1 ]]; then
        sa=("${BENCH_PLAN_STATIC[@]}")
        da=("${BENCH_PLAN_DYNAMIC[@]}")
        wa=("${BENCH_PLAN_WEBSOCKET[@]}")
        ns=${#sa[@]}
        nd=${#da[@]}
        nw=${#wa[@]}
        printf "${BLUE}[INFO]${NC}   · Static HTTP:     %s containers × %s levels = %s\n" "$ns" "$H" "$((ns * H))"
        printf "${BLUE}[INFO]${NC}   · Dynamic HTTP:    %s containers × %s levels = %s\n" "$nd" "$H" "$((nd * H))"
        printf "${BLUE}[INFO]${NC}   · WebSocket grid:  %s × %s (burst+stream invocations) = %s\n" "$nw" "$bs" "$((nw * bs))"
        printf "${BLUE}[INFO]${NC}   · WS concurrency:  %s × %s = %s\n" "$nw" "$c" "$((nw * c))"
        printf "${BLUE}[INFO]${NC}   · WS payload:      %s × %s = %s\n" "$nw" "$p" "$((nw * p))"
        if [[ $QUICK_BENCH -eq 1 ]] || [[ $SUPER_QUICK_BENCH -eq 1 ]]; then
            print_status "INFO" "Note: --quick / --super-quick only reduce HTTP request levels; WebSocket burst/stream grid uses full matrix unless --super-quick (then 1 burst + 1 stream per server)."
        fi
    fi
    if [ "${BENCH_MEASURE_QUIET:-1}" != "0" ]; then
        print_status "INFO" "HTTP measurement output is compact (BENCH_MEASURE_QUIET=1): magenta [MEASURE] lines + load heartbeats every ${MEASURE_HEARTBEAT_SEC:-15}s. BENCH_MEASURE_QUIET=0 or MEASURE_HEARTBEAT_SEC=3600 to reduce noise."
    fi
    printf "${BLUE}[INFO]${NC} While running: ${CYAN}tail -f %s${NC}\n" "$LOG_FILE"
    printf "${BLUE}[INFO]${NC} Milestones only: ${CYAN}grep -F '[PROGRESS]' %s${NC}\n" "$LOG_FILE"
    printf "${BLUE}────────────────────────────────────────────${NC}\n\n"
}

SUDO_KEEPALIVE_PID=""

cleanup_sudo_keepalive() {
    if [ -n "$SUDO_KEEPALIVE_PID" ]; then
        kill "$SUDO_KEEPALIVE_PID" >/dev/null 2>&1 || true
        wait "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
        SUDO_KEEPALIVE_PID=""
    fi
}

start_sudo_keepalive() {
    if ! command -v sudo >/dev/null 2>&1; then
        print_status "WARNING" "sudo not found; scaphandre calls may fail."
        return
    fi

    print_status "INFO" "Requesting sudo authentication once for long benchmark run..."
    if ! sudo -v; then
        print_status "ERROR" "Unable to authenticate sudo. Aborting."
        exit 1
    fi

    (
        while true; do
            sudo -n true >/dev/null 2>&1 || exit 0
            sleep 60
        done
    ) &
    SUDO_KEEPALIVE_PID=$!
    trap cleanup_sudo_keepalive EXIT INT TERM
}

# Find container dir by image name (benchmarks/type/language/framework/container-name)
find_container_dir() {
    local image_name="$1"
    find "$BENCHMARKS_DIR" -type d -name "$image_name" -exec test -f {}/Dockerfile \; -print 2>/dev/null | head -1
}

# Function to get container port mapping based on Dockerfile EXPOSE directive
get_container_port_mapping() {
    local image_name=$1
    local host_port=$2
    local container_dir
    container_dir=$(find_container_dir "$image_name")
    local container_port="80"
    if [ -n "$container_dir" ] && [ -f "${container_dir}/Dockerfile" ]; then
        local exposed_port=$(grep -i "^EXPOSE" "${container_dir}/Dockerfile" | head -1 | awk '{print $2}')
        if [ -n "$exposed_port" ]; then
            container_port="$exposed_port"
        fi
    fi
    echo "${host_port}:${container_port}"
}

# Auto-discover all containers (benchmarks/type/language/framework/container-name)
function discover_containers() {
    local container_type=$1
    local discovered=()
    local base=""
    case $container_type in
        "static")  base="$BENCHMARKS_DIR/static" ;;
        "dynamic") base="$BENCHMARKS_DIR/dynamic" ;;
        "websocket") base="$BENCHMARKS_DIR/websocket" ;;
    esac
    if [ -n "$base" ]; then
        while IFS= read -r d; do
            [ -n "$d" ] && discovered+=("$(basename "$d")")
        done < <(find "$base" -type d -exec test -f {}/Dockerfile \; -print 2>/dev/null)
    fi
    echo "${discovered[@]}"
}

clean_repo() {
  echo "Cleaning repository to bare minimum (fresh clone state)..."
  git clean -xfd
  git reset --hard
  echo "Repository is now clean."
}

RESULTS_PARENT_DIR="results"
TIMESTAMP=$(date +"%Y-%m-%d_%H%M%S")
RESULTS_DIR="$RESULTS_PARENT_DIR/$TIMESTAMP"
mkdir -p "$RESULTS_DIR/static" "$RESULTS_DIR/dynamic" "$RESULTS_DIR/websocket" logs

LOG_FILE="logs/run_${TIMESTAMP}.log"
echo "Logging to $LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

QUICK_BENCH=0
SUPER_QUICK_BENCH=0
args=()
while [[ $# -gt 0 ]]; do
    arg="$1"
    if [[ "$arg" == "--quick" ]]; then
        QUICK_BENCH=1
        shift
    elif [[ "$arg" == "--super-quick" ]]; then
        SUPER_QUICK_BENCH=1
        shift
    elif [[ "$arg" == "--bench" ]]; then
        if [[ -z "${2:-}" ]]; then
            echo -e "${RED}[ERROR]${NC} --bench requires a path argument"
            exit 1
        fi
        BENCHMARKS_DIR="$2"
        if [[ "$BENCHMARKS_DIR" != /* ]]; then
            BENCHMARKS_DIR="$REPO_ROOT/$BENCHMARKS_DIR"
        fi
        if [ ! -d "$BENCHMARKS_DIR" ]; then
            echo -e "${RED}[ERROR]${NC} Benchmarks directory not found: $BENCHMARKS_DIR"
            exit 1
        fi
        shift 2
    else
        args+=("$arg")
        shift
    fi
done
set -- "${args[@]}"

# Help/short-info check after option parsing (supports e.g. --bench PATH --help)
case "${1:-}" in
    "help"|"--help"|"-h")
        echo "Usage: $0 [TYPE] [IMAGES...] [OPTIONS]"
        echo ""
        echo "Types:"
        echo "  static      Run static container benchmarks"
        echo "  dynamic     Run dynamic container benchmarks"
        echo "  websocket   Run WebSocket benchmarks"
        echo ""
        echo "Options:"
        echo "  --quick     Run quick benchmarks with reduced parameters"
        echo "  --super-quick Run super quick benchmarks with single test per type"
        echo "  --single IMAGE  Run a single server (e.g. --single ws-erlang-yaws-27)"
        echo "  --bench PATH    Benchmark root directory (default: ./benchmarks)"
        echo "Environment:"
        echo "  HTTP_MAX_WORKERS     Max HTTP client workers for measure_docker.py (default: unset; CSV: System default)"
        echo "                       Applies to HTTP (static/dynamic) only; WebSocket is unaffected."
        echo "  BENCH_MEASURE_QUIET  HTTP measure_docker logs: 1=compact + heartbeats (default), 0=verbose"
        echo "  MEASURE_HEARTBEAT_SEC  Seconds between quiet-mode load progress lines (default: 15, min: 10)"
        echo "  clean       Clean repository to fresh state"
        echo ""
        echo "Examples:"
        echo "  $0                    # Run all benchmarks"
        echo "  $0 static             # Run all static containers"
        echo "  $0 dynamic dy-erlang-pure-27   # Run specific container(s)"
        echo "  $0 --single ws-erlang-yaws-27   # Run single server (type auto-detected)"
        echo "  $0 --bench ./benchmarks static   # Run from custom benchmark root"
        echo "  $0 --quick static     # Quick static benchmarks"
        echo "  HTTP_MAX_WORKERS=100 $0 static   # Override HTTP worker count"
        echo "  BENCH_MEASURE_QUIET=0 $0 static # Verbose measure_docker.py (default is compact)"
        echo ""
        echo "Port Assignment:"
        echo "  - Fixed host port: ${HOST_PORT:-8001}"
        echo "  - Container port determined from Dockerfile EXPOSE directive"
        echo "  - Default container port: 80"
        echo "  - Benchmark root: ${BENCHMARKS_DIR}"
        exit 0
        ;;
    "concurrency")
        echo "Run Concurrency: test increasing client counts with fixed payload size."
        exit 0
        ;;
    "payload")
        echo "Run Payload: test increasing payload sizes with fixed client count."
        exit 0
        ;;
esac

RUN_ALL=1
TARGET_TYPE=""
TARGET_IMAGES=()

if [[ $# -gt 0 ]]; then
    # Check for special commands first
    if [[ "$1" == "clean" ]]; then
        clean_repo
        exit 0
    fi
    RUN_ALL=0
    if [[ "$1" == "--single" && -n "${2:-}" ]]; then
        # Run a single server: --single ws-erlang-yaws-27
        SINGLE_IMAGE="$2"
        SINGLE_DIR=$(find_container_dir "$SINGLE_IMAGE")
        if [[ -z "$SINGLE_DIR" ]]; then
            echo -e "${RED}[ERROR]${NC} Container '$SINGLE_IMAGE' not found under $BENCHMARKS_DIR"
            echo "Use the Docker image name (e.g. ws-erlang-yaws-27, dy-erlang-pure-27, st-erlang-cowboy-27)"
            exit 1
        fi
        if [[ "$SINGLE_DIR" == *"/websocket/"* ]]; then
            TARGET_TYPE="websocket"
        elif [[ "$SINGLE_DIR" == *"/dynamic/"* ]]; then
            TARGET_TYPE="dynamic"
        elif [[ "$SINGLE_DIR" == *"/static/"* ]]; then
            TARGET_TYPE="static"
        else
            echo -e "${RED}[ERROR]${NC} Cannot infer type for '$SINGLE_IMAGE' (path: $SINGLE_DIR)"
            exit 1
        fi
        TARGET_IMAGES=("$SINGLE_IMAGE")
    else
        TARGET_TYPE="$1"
        shift
        TARGET_IMAGES=("$@")
    fi
fi

check_port_free() {
    local port=$1
    for i in {1..10}; do
        if ! ss -ltn | grep -q ":$port "; then
            return 0
        fi
        print_status "INFO" "Port $port is busy, waiting... ($i/10)"
        sleep 1
    done
    # Port is still busy after waiting - show what's using it
    print_status "ERROR" "Port $port is still in use after waiting. Checking what's using it..."
    printf "\n"
    echo "Processes using port $port:"
    ss -ltnp | grep ":$port " || echo "  (none found via ss)"
    printf "\n"
    echo "Docker containers using port $port:"
    docker ps --filter "publish=$port" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" 2>/dev/null || echo "  (none found)"
    printf "\n"
    echo "To free the port, you can:"
    echo "  1. Stop benchmark containers: make clean-port PORT=$port"
    echo "  2. Or manually: docker ps --filter 'publish=$port' -q | xargs docker stop"
    echo "  3. Or use a different port: HOST_PORT=8002 make run-super-quick"
    return 1
}

run_websocket_tests() {
    local image=$1
    local host_port=$2
    if [ ! -f "./tools/measure_websocket.py" ]; then
        echo "Error: ./tools/measure_websocket.py not found"
        return 1
    fi
    if [[ $SUPER_QUICK_BENCH -eq 1 ]]; then
        print_section "WebSocket Burst Test (Super Quick)"
        print_bench_progress "${image} | super-quick BURST | clients=${quick_ws_burst_clients[0]} size_kb=${quick_ws_burst_sizes[0]}"
        "$PYTHON_PATH" ./tools/measure_websocket.py \
            --server_image "$image" \
            --pattern burst \
            --mode echo \
            --clients ${quick_ws_burst_clients[0]} \
            --size_kb ${quick_ws_burst_sizes[0]} \
            --bursts ${quick_ws_burst_bursts[0]} \
            --interval ${quick_ws_burst_intervals[0]} \
            --output_csv "$RESULTS_DIR/websocket/${image}_burst.csv" \
            --measurement_type "burst_${quick_ws_burst_clients[0]}_${quick_ws_burst_sizes[0]}_${quick_ws_burst_bursts[0]}_${quick_ws_burst_intervals[0]}"
        print_csv_summary "$RESULTS_DIR/websocket/${image}_burst.csv"
        print_section "WebSocket Stream Test (Super Quick)"
        print_bench_progress "${image} | super-quick STREAM | clients=${quick_ws_stream_clients[0]} size_kb=${quick_ws_stream_sizes[0]}"
        "$PYTHON_PATH" ./tools/measure_websocket.py \
            --server_image "$image" \
            --pattern stream \
            --mode echo \
            --clients ${quick_ws_stream_clients[0]} \
            --size_kb ${quick_ws_stream_sizes[0]} \
            --rate ${quick_ws_stream_rates[0]} \
            --duration ${quick_ws_stream_durations[0]} \
            --output_csv "$RESULTS_DIR/websocket/${image}_stream.csv" \
            --measurement_type "stream_${quick_ws_stream_clients[0]}_${quick_ws_stream_sizes[0]}_${quick_ws_stream_rates[0]}_${quick_ws_stream_durations[0]}"
        print_csv_summary "$RESULTS_DIR/websocket/${image}_stream.csv"
    else
        burst_clients=("${full_ws_burst_clients[@]}")
        burst_sizes=("${full_ws_burst_sizes[@]}")
        burst_bursts=("${full_ws_burst_bursts[@]}")
        burst_intervals=("${full_ws_burst_intervals[@]}")
        stream_clients=("${full_ws_stream_clients[@]}")
        stream_sizes=("${full_ws_stream_sizes[@]}")
        stream_rates=("${full_ws_stream_rates[@]}")
        stream_durations=("${full_ws_stream_durations[@]}")
        echo "Running WebSocket tests for $image on port $host_port"
        local port_mapping=$(get_container_port_mapping "$image" "$host_port")
        local container_port=$(echo $port_mapping | cut -d: -f2)
        local ws_url="ws://localhost:$host_port/ws"
        local bn=0
        local bt=$((${#burst_clients[@]} * ${#burst_sizes[@]} * ${#burst_bursts[@]} * ${#burst_intervals[@]}))
        for clients in "${burst_clients[@]}"; do
            for size_kb in "${burst_sizes[@]}"; do
                for bursts in "${burst_bursts[@]}"; do
                    for interval in "${burst_intervals[@]}"; do
                        bn=$((bn + 1))
                        print_bench_progress "${image} | BURST ${bn}/${bt} | clients=$clients size_kb=$size_kb bursts=$bursts interval=${interval}s"
                        "$PYTHON_PATH" ./tools/measure_websocket.py \
                            --server_image "$image" \
                            --pattern burst \
                            --mode echo \
                            --clients $clients \
                            --size_kb $size_kb \
                            --bursts $bursts \
                            --interval $interval \
                            --output_csv "$RESULTS_DIR/websocket/${image}_burst.csv" \
                            --measurement_type "burst_${clients}_${size_kb}_${bursts}_${interval}"
                    done
                done
            done
        done
        local sn=0
        local st=$((${#stream_clients[@]} * ${#stream_sizes[@]} * ${#stream_rates[@]} * ${#stream_durations[@]}))
        for clients in "${stream_clients[@]}"; do
            for size_kb in "${stream_sizes[@]}"; do
                for rate in "${stream_rates[@]}"; do
                    for duration in "${stream_durations[@]}"; do
                        sn=$((sn + 1))
                        print_bench_progress "${image} | STREAM ${sn}/${st} | clients=$clients size_kb=$size_kb rate=$rate duration=${duration}s"
                        "$PYTHON_PATH" ./tools/measure_websocket.py \
                            --server_image "$image" \
                            --pattern stream \
                            --mode echo \
                            --clients $clients \
                            --size_kb $size_kb \
                            --rate $rate \
                            --duration $duration \
                            --output_csv "$RESULTS_DIR/websocket/${image}_stream.csv" \
                            --measurement_type "stream_${clients}_${size_kb}_${rate}_${duration}"
                    done
                done
            done
        done
    fi
}

# Helper to print a short summary from the last line of a CSV file
print_csv_summary() {
    local csv_file="$1"
    [ -f "$csv_file" ] || return
    local header last_row
    header=$(head -1 "$csv_file")
    last_row=$(tail -1 "$csv_file")
    IFS=',' read -r -a cols <<EOF
$header
EOF
    IFS=',' read -r -a vals <<EOF
$last_row
EOF
    total_idx=-1; fail_idx=-1; latency_idx=-1; throughput_idx=-1
    for i in $(seq 0 $((${#cols[@]} - 1))); do
        col="${cols[$i]}"
        case "$col" in
            Total\ Requests|Total\ Messages) total_idx=$i ;;
            Failed\ Requests|Failed\ Messages) fail_idx=$i ;;
            Avg\ Latency*) latency_idx=$i ;;
            Throughput*) throughput_idx=$i ;;
        esac
    done
    total="-"; fail="-"; latency="-"; throughput="-"
    [ $total_idx -ge 0 ] && total="${vals[$total_idx]}"
    [ $fail_idx -ge 0 ] && fail="${vals[$fail_idx]}"
    [ $latency_idx -ge 0 ] && latency="${vals[$latency_idx]}"
    [ $throughput_idx -ge 0 ] && throughput="${vals[$throughput_idx]}"
    if [[ "$total" =~ ^[0-9]+$ ]] && [[ "$fail" =~ ^[0-9]+$ ]] && [ "$total" -gt 0 ] && [ "$total" -eq "$fail" ]; then
        echo "  -> [WARNING] All failed ($fail/$total)"
    elif [[ "$total" =~ ^[0-9]+$ ]] && [[ "$fail" =~ ^[0-9]+$ ]] && [ "$total" -ge "$fail" ]; then
        if [ $latency_idx -lt 0 ] && [ $throughput_idx -lt 0 ]; then
            echo "  -> ok $((total-fail))/$total requests (see CSV for energy/CPU/mem)"
        else
            echo "  -> [SUCCESS] $((total-fail))/$total, Avg Latency: $latency ms, Throughput: $throughput MB/s"
        fi
    else
        echo "  -> [INFO] Total: $total, Failed: $fail, Avg Latency: $latency ms, Throughput: $throughput MB/s"
    fi
}

run_concurrency() {
    local image=$1
    local host_port=$2
    if [[ $SUPER_QUICK_BENCH -eq 1 ]]; then
        print_section "WebSocket Concurrency (Super Quick)"
        local csv_file="$RESULTS_DIR/websocket/${image}_concurrency.csv"
        print_bench_progress "${image} | super-quick concurrency | clients=${quick_concurrency_clients[0]} size_kb=$quick_concurrency_size"
        "$PYTHON_PATH" ./tools/measure_websocket.py \
            --server_image "$image" \
            --pattern burst \
            --mode echo \
            --clients ${quick_concurrency_clients[0]} \
            --size_kb $quick_concurrency_size \
            --bursts 1 \
            --interval 0.5 \
            --output_csv "$csv_file" \
            --measurement_type "concurrency_${quick_concurrency_clients[0]}_${quick_concurrency_size}"
        print_csv_summary "$csv_file"
        print_status "SUCCESS" "Concurrency completed for $image at $(date)"
        print_status "INFO" "Results saved to: $csv_file"
    else
        print_section "WebSocket Concurrency: $image"
        local port_mapping=$(get_container_port_mapping "$image" "$host_port")
        local ws_url="ws://localhost:$host_port/ws"
        local ntests=${#concurrency_clients[@]}
        local idx=1
        for clients in "${concurrency_clients[@]}"; do
            local csv_file="$RESULTS_DIR/websocket/${image}_concurrency.csv"
            print_bench_progress "${image} | concurrency ${idx}/${ntests} | clients=$clients size_kb=$concurrency_size"
            "$PYTHON_PATH" ./tools/measure_websocket.py \
                --server_image "$image" \
                --pattern burst \
                --mode echo \
                --clients $clients \
                --size_kb $concurrency_size \
                --bursts 3 \
                --interval 0.5 \
                --output_csv "$csv_file" \
                --measurement_type "concurrency_${clients}_${concurrency_size}"
            print_csv_summary "$csv_file"
            idx=$((idx+1))
        done
        print_status "SUCCESS" "Concurrency completed for $image at $(date)"
        print_status "INFO" "Results saved to: $RESULTS_DIR/websocket/${image}_concurrency.csv"
    fi
}

run_payload() {
    local image=$1
    local host_port=$2
    if [[ $SUPER_QUICK_BENCH -eq 1 ]]; then
        print_section "WebSocket Payload (Super Quick)"
        local csv_file="$RESULTS_DIR/websocket/${image}_payload.csv"
        print_bench_progress "${image} | super-quick payload | clients=$quick_payload_clients size_kb=${quick_payload_sizes[0]}"
        "$PYTHON_PATH" ./tools/measure_websocket.py \
            --server_image "$image" \
            --pattern burst \
            --mode echo \
            --clients $quick_payload_clients \
            --size_kb ${quick_payload_sizes[0]} \
            --bursts 1 \
            --interval 0.5 \
            --output_csv "$csv_file" \
            --measurement_type "payload_${quick_payload_clients}_${quick_payload_sizes[0]}"
        print_csv_summary "$csv_file"
        print_status "SUCCESS" "Payload completed for $image at $(date)"
        print_status "INFO" "Results saved to: $csv_file"
    else
        print_section "WebSocket Payload: $image"
        local port_mapping=$(get_container_port_mapping "$image" "$host_port")
        local ws_url="ws://localhost:$host_port/ws"
        local ntests=${#payload_sizes[@]}
        local idx=1
        for size_kb in "${payload_sizes[@]}"; do
            local csv_file="$RESULTS_DIR/websocket/${image}_payload.csv"
            print_bench_progress "${image} | payload ${idx}/${ntests} | clients=$payload_clients size_kb=$size_kb"
            "$PYTHON_PATH" ./tools/measure_websocket.py \
                --server_image "$image" \
                --pattern burst \
                --mode echo \
                --clients $payload_clients \
                --size_kb $size_kb \
                --bursts 3 \
                --interval 0.5 \
                --output_csv "$csv_file" \
                --measurement_type "payload_${payload_clients}_${size_kb}"
            print_csv_summary "$csv_file"
            idx=$((idx+1))
        done
        print_status "SUCCESS" "Payload completed for $image at $(date)"
        print_status "INFO" "Results saved to: $RESULTS_DIR/websocket/${image}_payload.csv"
    fi
}

# For static and dynamic runs, add test numbering and summary
run_docker_tests() {
    local image=$1
    local host_port=$2
    local test_type=$3
    local cpart="${BENCH_CIDX:-?}/${BENCH_CTOTAL:-?}"
    echo -e "${BLUE}Running $test_type tests for $image on port $host_port${NC} (${cpart})"
    local port_mapping=$(get_container_port_mapping "$image" "$host_port")
    local ntests=0
    local -a test_counts
    if [[ $SUPER_QUICK_BENCH -eq 1 ]]; then
        test_counts=("${super_quick_http_requests[@]}")
    elif [[ $QUICK_BENCH -eq 1 ]]; then
        test_counts=("${quick_http_requests[@]}")
    else
        test_counts=("${full_http_requests[@]}")
    fi
    ntests=${#test_counts[@]}
    local idx=1
    for num_requests in "${test_counts[@]}"; do
        local csv_file="$RESULTS_DIR/$test_type/${image}.csv"
        print_bench_progress "${image} | level ${idx}/${ntests} | ${num_requests} requests"
        local worker_arg=()
        if [ -n "$HTTP_MAX_WORKERS" ]; then
            worker_arg=(--max_workers "$HTTP_MAX_WORKERS")
        fi
        "$PYTHON_PATH" ./tools/measure_docker.py \
            --server_image "$image" \
            --port_mapping "$port_mapping" \
            --num_requests "$num_requests" \
            --output_csv "$csv_file" \
            --measurement_type "$test_type" \
            "${worker_arg[@]}"
        if [ "${BENCH_MEASURE_QUIET:-1}" = "0" ]; then
            print_csv_summary "$csv_file"
        fi
        idx=$((idx+1))
    done
}

# After all benchmarks are run, print a summary of containers with 100% failed requests
print_run_summary() {
    local failed_containers=()
    local results_dir="$RESULTS_DIR"
    for csv in "$results_dir"/static/*.csv "$results_dir"/dynamic/*.csv "$results_dir"/websocket/*.csv; do
        [ -f "$csv" ] || continue
        # Get the header and the last row (most recent run)
        header=$(head -1 "$csv")
        last_row=$(tail -1 "$csv")
        # Determine column indices
        IFS=',' read -r -a cols <<EOF
$header
EOF
        IFS=',' read -r -a vals <<EOF
$last_row
EOF
        total_idx=-1
        fail_idx=-1
        for i in $(seq 0 $((${#cols[@]} - 1))); do
            col="${cols[$i]}"
            case "$col" in
                Total\ Requests|Total\ Messages) total_idx=$i ;;
                Failed\ Requests|Failed\ Messages) fail_idx=$i ;;
            esac
        done
        if [[ $total_idx -ge 0 ]] && [[ $fail_idx -ge 0 ]]; then
            total="${vals[$total_idx]}"
            fail="${vals[$fail_idx]}"
            if [ -n "$total" ] && [ "$total" -gt 0 ] && [ "$total" = "$fail" ]; then
                container_name="${vals[0]}"
                failed_containers+=("$container_name ($csv)")
            fi
        fi
    done
    if [ ${#failed_containers[@]} -eq 0 ]; then
        printf "\n[RUN SUMMARY] All containers ran successfully (no 100%% failed requests).\n"
    else
        printf "\n[RUN SUMMARY] Containers with 100%% failures:\n"
        for c in "${failed_containers[@]}"; do
            echo "  - $c"
        done
    fi
}

main() {
    export BENCH_MEASURE_QUIET
    # Optional: concurrency/payload modes set this so the shared footer SUCCESS line matches the suite.
    BENCH_SUCCESS_TAIL=""
    start_sudo_keepalive
    print_status "INFO" "Starting benchmarks at $(date)"
    print_status "INFO" "Results will be saved to: $RESULTS_DIR"
    if [ -n "$HTTP_MAX_WORKERS" ]; then
        print_status "INFO" "HTTP client max workers: $HTTP_MAX_WORKERS (column \"HTTP Max Workers\" in static/dynamic CSVs)"
    else
        print_status "INFO" "HTTP client max workers: System default (column \"HTTP Max Workers\" in static/dynamic CSVs)"
    fi
    bench_init_run_plan
    local _si=0
    if [[ $RUN_ALL -eq 1 ]]; then
        print_status "INFO" "Running all benchmarks..."
        print_section "Static Container Tests"
        local static_containers=("${BENCH_PLAN_STATIC[@]}")
        BENCH_PHASE="static HTTP"
        BENCH_CTOTAL=${#static_containers[@]}
        _si=0
        for container in "${static_containers[@]}"; do
            _si=$((_si + 1))
            BENCH_CIDX=$_si
            if ! check_port_free "$HOST_PORT"; then
                echo -e "${RED}[ERROR]${NC} Port $HOST_PORT is already in use. Please free the port and rerun the benchmark."
                exit 1
            fi
            # Before starting each container:
            if docker ps -a --format '{{.Names}}' | grep -q "^$container$"; then
                echo -e "${BLUE}[INFO]${NC} Stopping and removing dangling container: $container"
                docker stop "$container" > /dev/null 2>&1 || true
                docker rm "$container" > /dev/null 2>&1 || true
                sleep 1
            fi
            run_docker_tests "$container" "$HOST_PORT" "static"
            sleep 1
        done
        print_section "Dynamic Container Tests"
        local dynamic_containers=("${BENCH_PLAN_DYNAMIC[@]}")
        BENCH_PHASE="dynamic HTTP"
        BENCH_CTOTAL=${#dynamic_containers[@]}
        _si=0
        for container in "${dynamic_containers[@]}"; do
            _si=$((_si + 1))
            BENCH_CIDX=$_si
            if ! check_port_free "$HOST_PORT"; then
                echo -e "${RED}[ERROR]${NC} Port $HOST_PORT is already in use. Please free the port and rerun the benchmark."
                exit 1
            fi
            # Before starting each container:
            if docker ps -a --format '{{.Names}}' | grep -q "^$container$"; then
                echo -e "${BLUE}[INFO]${NC} Stopping and removing dangling container: $container"
                docker stop "$container" > /dev/null 2>&1 || true
                docker rm "$container" > /dev/null 2>&1 || true
                sleep 1
            fi
            run_docker_tests "$container" "$HOST_PORT" "dynamic"
            sleep 1
        done
        print_section "WebSocket Tests"
        local websocket_containers=("${BENCH_PLAN_WEBSOCKET[@]}")
        BENCH_PHASE="WebSocket burst/stream"
        BENCH_CTOTAL=${#websocket_containers[@]}
        _si=0
        for container in "${websocket_containers[@]}"; do
            _si=$((_si + 1))
            BENCH_CIDX=$_si
            if ! check_port_free "$HOST_PORT"; then
                echo -e "${RED}[ERROR]${NC} Port $HOST_PORT is already in use. Please free the port and rerun the benchmark."
                exit 1
            fi
            # Before starting each container:
            if docker ps -a --format '{{.Names}}' | grep -q "^$container$"; then
                echo -e "${BLUE}[INFO]${NC} Stopping and removing dangling container: $container"
                docker stop "$container" > /dev/null 2>&1 || true
                docker rm "$container" > /dev/null 2>&1 || true
                sleep 1
            fi
            run_websocket_tests "$container" "$HOST_PORT"
            sleep 1
        done
        # Also run sweeps for all websocket servers
        BENCH_CTOTAL=${#websocket_containers[@]}
        _si=0
        for container in "${websocket_containers[@]}"; do
            _si=$((_si + 1))
            BENCH_CIDX=$_si
            if ! check_port_free "$HOST_PORT"; then
                echo -e "${RED}[ERROR]${NC} Port $HOST_PORT is already in use. Please free the port and rerun the benchmark."
                exit 1
            fi
            # Before starting each container:
            if docker ps -a --format '{{.Names}}' | grep -q "^$container$"; then
                echo -e "${BLUE}[INFO]${NC} Stopping and removing dangling container: $container"
                docker stop "$container" > /dev/null 2>&1 || true
                docker rm "$container" > /dev/null 2>&1 || true
                sleep 1
            fi
            BENCH_PHASE="WebSocket concurrency"
            run_concurrency "$container" "$HOST_PORT"
            BENCH_PHASE="WebSocket payload"
            run_payload "$container" "$HOST_PORT"
            sleep 1
        done
    else
        case $TARGET_TYPE in
            "static"|"--static")
                BENCH_PHASE="static HTTP"
                BENCH_CTOTAL=${#BENCH_PLAN_STATIC[@]}
                _si=0
                for container in "${BENCH_PLAN_STATIC[@]}"; do
                    _si=$((_si + 1))
                    BENCH_CIDX=$_si
                    if ! check_port_free "$HOST_PORT"; then
                        echo -e "${RED}[ERROR]${NC} Port $HOST_PORT is already in use. Please free the port and rerun the benchmark."
                        exit 1
                    fi
                    # Before starting each container:
                    if docker ps -a --format '{{.Names}}' | grep -q "^$container$"; then
                        echo -e "${BLUE}[INFO]${NC} Stopping and removing dangling container: $container"
                        docker stop "$container" > /dev/null 2>&1 || true
                        docker rm "$container" > /dev/null 2>&1 || true
                        sleep 1
                    fi
                    run_docker_tests "$container" "$HOST_PORT" "static"
                    sleep 1
                done
                ;;
            "dynamic"|"--dynamic")
                BENCH_PHASE="dynamic HTTP"
                BENCH_CTOTAL=${#BENCH_PLAN_DYNAMIC[@]}
                _si=0
                for container in "${BENCH_PLAN_DYNAMIC[@]}"; do
                    _si=$((_si + 1))
                    BENCH_CIDX=$_si
                    if ! check_port_free "$HOST_PORT"; then
                        echo -e "${RED}[ERROR]${NC} Port $HOST_PORT is already in use. Please free the port and rerun the benchmark."
                        exit 1
                    fi
                    # Before starting each container:
                    if docker ps -a --format '{{.Names}}' | grep -q "^$container$"; then
                        echo -e "${BLUE}[INFO]${NC} Stopping and removing dangling container: $container"
                        docker stop "$container" > /dev/null 2>&1 || true
                        docker rm "$container" > /dev/null 2>&1 || true
                        sleep 1
                    fi
                    run_docker_tests "$container" "$HOST_PORT" "dynamic"
                    sleep 1
                done
                ;;
            "websocket"|"--websocket")
                BENCH_PHASE="WebSocket burst/stream"
                BENCH_CTOTAL=${#BENCH_PLAN_WEBSOCKET[@]}
                _si=0
                for container in "${BENCH_PLAN_WEBSOCKET[@]}"; do
                    _si=$((_si + 1))
                    BENCH_CIDX=$_si
                    if ! check_port_free "$HOST_PORT"; then
                        echo -e "${RED}[ERROR]${NC} Port $HOST_PORT is already in use. Please free the port and rerun the benchmark."
                        exit 1
                    fi
                    # Before starting each container:
                    if docker ps -a --format '{{.Names}}' | grep -q "^$container$"; then
                        echo -e "${BLUE}[INFO]${NC} Stopping and removing dangling container: $container"
                        docker stop "$container" > /dev/null 2>&1 || true
                        docker rm "$container" > /dev/null 2>&1 || true
                        sleep 1
                    fi
                    run_websocket_tests "$container" "$HOST_PORT"
                    sleep 1
                done
                ;;
            "concurrency")
                TARGET_TYPE="websocket"
                BENCH_PHASE="WebSocket concurrency"
                BENCH_CTOTAL=${#BENCH_PLAN_WEBSOCKET[@]}
                _si=0
                for container in "${BENCH_PLAN_WEBSOCKET[@]}"; do
                    _si=$((_si + 1))
                    BENCH_CIDX=$_si
                    if ! check_port_free "$HOST_PORT"; then
                        echo -e "${RED}[ERROR]${NC} Port $HOST_PORT is already in use. Please free the port and rerun the benchmark."
                        exit 1
                    fi
                    # Before starting each container:
                    if docker ps -a --format '{{.Names}}' | grep -q "^$container$"; then
                        echo -e "${BLUE}[INFO]${NC} Stopping and removing dangling container: $container"
                        docker stop "$container" > /dev/null 2>&1 || true
                        docker rm "$container" > /dev/null 2>&1 || true
                        sleep 1
                    fi
                    run_concurrency "$container" "$HOST_PORT"
                    sleep 1
                done
                BENCH_SUCCESS_TAIL="Concurrency completed"
                ;;
            "payload")
                TARGET_TYPE="websocket"
                BENCH_PHASE="WebSocket payload"
                BENCH_CTOTAL=${#BENCH_PLAN_WEBSOCKET[@]}
                _si=0
                for container in "${BENCH_PLAN_WEBSOCKET[@]}"; do
                    _si=$((_si + 1))
                    BENCH_CIDX=$_si
                    if ! check_port_free "$HOST_PORT"; then
                        echo -e "${RED}[ERROR]${NC} Port $HOST_PORT is already in use. Please free the port and rerun the benchmark."
                        exit 1
                    fi
                    # Before starting each container:
                    if docker ps -a --format '{{.Names}}' | grep -q "^$container$"; then
                        echo -e "${BLUE}[INFO]${NC} Stopping and removing dangling container: $container"
                        docker stop "$container" > /dev/null 2>&1 || true
                        docker rm "$container" > /dev/null 2>&1 || true
                        sleep 1
                    fi
                    run_payload "$container" "$HOST_PORT"
                    sleep 1
                done
                BENCH_SUCCESS_TAIL="Payload completed"
                ;;
            *)
                echo "Unknown target type: $TARGET_TYPE"
                echo "Valid types: static, dynamic, websocket"
                exit 1
                ;;
        esac
    fi
    printf "\n"
    if [ "${BENCH_TOTAL_STEPS:-0}" -gt 0 ]; then
        print_status "INFO" "Measurement steps finished: ${BENCH_STEP}/${BENCH_TOTAL_STEPS} (total elapsed $(bench_elapsed_human))"
    fi
    if [ -n "$BENCH_SUCCESS_TAIL" ]; then
        print_status "SUCCESS" "$BENCH_SUCCESS_TAIL at $(date)"
    else
        print_status "SUCCESS" "Benchmarks completed at $(date)"
    fi
    print_status "INFO" "Results saved to: $RESULTS_DIR"
}

main "$@"
print_run_summary