#!/bin/bash
# Run benchmarks for discovered Docker containers (static, dynamic, websocket).
# Usage: ./scripts/run_benchmarks.sh [static|dynamic|websocket] [--quick|--super-quick]
# Called by: make run, make run-static, make run-quick, etc.
set -e
ORIGINAL_ARGS="$*"   # recorded in metadata.json before option parsing consumes them

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
        echo "  HTTP_MAX_WORKERS     Max HTTP client workers for measure_docker.py (default: 100)"
        echo "                       Set to 'system' to use Python ThreadPoolExecutor default (CSV: System default)."
        echo "                       Applies to HTTP (static/dynamic) only; WebSocket is unaffected."
        echo "  BENCH_MEASURE_QUIET  logs: 1=compact [MEASURE]+heartbeats (default), 0=verbose"
        echo "  MEASURE_HEARTBEAT_SEC  Seconds between quiet-mode load progress lines (default: 60, min: 10)"
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
        echo "  HTTP_MAX_WORKERS=system $0 static   # Use ThreadPoolExecutor default"
        echo "  BENCH_MEASURE_QUIET=0 $0 static # Verbose logs"
        echo "  BENCH_MEASURE_QUIET=1 MEASURE_HEARTBEAT_SEC=60 $0 static # Compact mode for both + heartbeat interval"
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
# Default HTTP client worker pool size for reproducible HTTP runs.
# Override with HTTP_MAX_WORKERS=N (for example 64 or 200),
# or HTTP_MAX_WORKERS=system to use ThreadPoolExecutor default (None).
HTTP_MAX_WORKERS="${HTTP_MAX_WORKERS:-100}"
HTTP_MAX_WORKERS_RAW="$HTTP_MAX_WORKERS"
case "${HTTP_MAX_WORKERS_RAW,,}" in
    system|none|default|system_default)
        HTTP_MAX_WORKERS=""
        ;;
esac
if [ -n "$HTTP_MAX_WORKERS" ] && ! [[ "$HTTP_MAX_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
    echo "[ERROR] HTTP_MAX_WORKERS must be a positive integer or 'system'. Got: $HTTP_MAX_WORKERS_RAW"
    exit 1
fi
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

# Pause between WebSocket bursts (also concurrency and payload tests); WS_BURST_INTERVAL_SECONDS in a config
WS_BURST_INTERVAL=0.5

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
    if [[ $SUPER_QUICK_BENCH -eq 1 ]]; then echo ${#super_quick_http_requests[@]}
    elif [[ $QUICK_BENCH -eq 1 ]]; then echo ${#quick_http_requests[@]}
    else echo ${#full_http_requests[@]}   # the config's HTTP_REQUESTS when one is used
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
        if [ "$BENCH_SELECTED" = 1 ]; then
            sa=("${SELECT_STATIC[@]}"); da=("${SELECT_DYNAMIC[@]}"); wa=("${SELECT_WEBSOCKET[@]}")
        else
            sa=($(discover_containers static))
            da=($(discover_containers dynamic))
            wa=($(discover_containers websocket))
        fi
        BENCH_PLAN_STATIC=("${sa[@]}")
        BENCH_PLAN_DYNAMIC=("${da[@]}")
        BENCH_PLAN_WEBSOCKET=("${wa[@]}")
        ns=${#sa[@]}
        nd=${#da[@]}
        nw=${#wa[@]}
        bs=$(( bs * BENCH_DO_WS )); c=$(( c * BENCH_DO_CONC )); p=$(( p * BENCH_DO_PAYLOAD ))
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
        print_status "INFO" "Measurement output is compact (BENCH_MEASURE_QUIET=1): magenta [MEASURE] lines + load heartbeats every ${MEASURE_HEARTBEAT_SEC:-60}s for HTTP+WebSocket. BENCH_MEASURE_QUIET=0 or MEASURE_HEARTBEAT_SEC=3600 to reduce noise."
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
    else
        # An image without a server folder (SERVERS=type:image): the port it exposes, e.g. 8080/tcp
        local image_port
        image_port=$(docker image inspect --format '{{range $p, $_ := .Config.ExposedPorts}}{{$p}} {{end}}' "$image_name" 2>/dev/null \
            | tr ' ' '\n' | grep -m1 -o '^[0-9]*' || true)
        [ -n "$image_port" ] && container_port="$image_port"
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

# --resume DIR: continue an unfinished measurement in its own folder, with its own saved
# config, original arguments and shuffle seed; measurements listed in progress.txt are skipped.
RESUME_DIR=""
for ((_i = 1; _i <= $#; _i++)); do
    if [ "${!_i}" = "--resume" ]; then _j=$((_i + 1)); RESUME_DIR="${!_j:-}"; fi
done
if [ -n "$RESUME_DIR" ]; then
    RESUME_DIR="${RESUME_DIR%/}"
    eval "$("$PYTHON_PATH" "$REPO_ROOT/tools/run_metadata.py" resume-info "$RESUME_DIR")"
    if [ -n "$RESUME_PROBLEMS" ]; then
        echo "[ERROR] Cannot resume $RESUME_DIR: $RESUME_PROBLEMS"
        exit 1
    fi
    # The machine profile as it was at the start: the copy kept in the results folder
    [ -f "$RESUME_DIR/machine.config" ] && export BENCH_MACHINE_FILE="$RESUME_DIR/machine.config"
    ORIGINAL_ARGS="$*"
fi

# --reproduce DIR: make a finished (or unfinished) measurement again, in a new folder, with every
# setting it used (bench.config.resolved, shuffle seed included) and its original arguments.
REPRODUCE_DIR=""
for ((_i = 1; _i <= $#; _i++)); do
    if [ "${!_i}" = "--reproduce" ]; then _j=$((_i + 1)); REPRODUCE_DIR="${!_j:-}"; fi
done
if [ -n "$REPRODUCE_DIR" ]; then
    REPRODUCE_DIR="${REPRODUCE_DIR%/}"
    eval "$("$PYTHON_PATH" "$REPO_ROOT/tools/run_metadata.py" reproduce-info "$REPRODUCE_DIR")"
    if [ -n "$REPRODUCE_PROBLEMS" ]; then
        echo "[ERROR] Cannot reproduce $REPRODUCE_DIR: $REPRODUCE_PROBLEMS"
        exit 1
    fi
    echo "[INFO] Reproducing $REPRODUCE_DIR in a new folder, with the settings and arguments it used"
    echo "[INFO] The original was made with the config: ${REPRODUCE_CONFIG:-unknown} (every value is in its bench.config.resolved)"
    if [ -n "$REPRODUCE_DIFFERENCES" ]; then
        echo "[INFO] Different from the original (recorded in metadata.json):"
        echo "$REPRODUCE_DIFFERENCES"
    else
        echo "[INFO] Same software, machine and images as the original"
    fi
    ORIGINAL_ARGS="$*"
fi

RESULTS_PARENT_DIR="results"
TIMESTAMP=$(date +"%Y-%m-%d_%H%M%S")
RESULTS_DIR="${RESUME_DIR:-$RESULTS_PARENT_DIR/$TIMESTAMP}"
mkdir -p "$RESULTS_DIR/static" "$RESULTS_DIR/dynamic" "$RESULTS_DIR/websocket" logs

LOG_FILE="logs/run_${TIMESTAMP}.log"
echo "Logging to $LOG_FILE"
# tee ignores Ctrl-C, so the log keeps working while the script restores the machine after Ctrl-C
exec > >(trap '' INT TERM; exec tee -a "$LOG_FILE") 2>&1

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
    elif [[ "$arg" == "--config" ]]; then
        if [[ -z "${2:-}" ]]; then
            echo -e "${RED}[ERROR]${NC} --config requires a file argument"
            exit 1
        fi
        CONFIG_FILE="$2"
        shift 2
    elif [[ "$arg" == "--bench" ]]; then
        if [[ -z "${2:-}" ]]; then
            echo -e "${RED}[ERROR]${NC} --bench requires a path argument"
            exit 1
        fi
        BENCHMARKS_DIR="$2"
        BENCH_DIR_FROM_CLI=1
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

# Benchmark configuration (--config FILE). Without a config the run behaves as before:
# one pass, no rest between measurements, machine settings left unchanged.
if [ -n "${CONFIG_FILE:-}" ]; then
    if [ ! -f "$CONFIG_FILE" ]; then
        echo -e "${RED}[ERROR]${NC} Config file not found: $CONFIG_FILE"
        exit 1
    fi
    cfg_out=$("$PYTHON_PATH" ./tools/bench_config.py "$CONFIG_FILE") || exit 1
    eval "$cfg_out"
    if [ "$CFG_HTTP_MAX_WORKERS" = "system" ]; then HTTP_MAX_WORKERS=""; else HTTP_MAX_WORKERS="$CFG_HTTP_MAX_WORKERS"; fi
    export MEASURE_SCAPH_STEP_MS="$CFG_SCAPH_STEP_MS"
    # The config's server folder, unless --bench was given on the command line
    if [ -n "$CFG_BENCHMARKS_DIR" ] && [ "${BENCH_DIR_FROM_CLI:-0}" != "1" ]; then
        BENCHMARKS_DIR="$CFG_BENCHMARKS_DIR"
        [[ "$BENCHMARKS_DIR" != /* ]] && BENCHMARKS_DIR="$REPO_ROOT/$BENCHMARKS_DIR"
        if [ ! -d "$BENCHMARKS_DIR" ]; then
            echo -e "${RED}[ERROR]${NC} BENCHMARKS_DIR of the config not found: $BENCHMARKS_DIR"
            exit 1
        fi
    fi
    export MEASURE_IDLE_SECONDS="$CFG_IDLE_SECONDS"
    export MEASURE_ON_BATTERY="$CFG_ON_BATTERY"
    export MEASURE_WARMUP_SECONDS="$CFG_WARMUP_SECONDS"
    # Workloads of full runs (defaults equal the built-in lists)
    read -r -a full_http_requests <<< "$CFG_HTTP_REQUESTS"
    read -r -a full_ws_burst_clients <<< "$CFG_WS_BURST_CLIENTS"
    read -r -a full_ws_burst_sizes <<< "$CFG_WS_BURST_SIZES_KB"
    read -r -a full_ws_burst_bursts <<< "$CFG_WS_BURST_BURSTS"
    full_ws_burst_intervals=("$CFG_WS_BURST_INTERVAL_SECONDS")
    quick_ws_burst_intervals=("$CFG_WS_BURST_INTERVAL_SECONDS")
    WS_BURST_INTERVAL="$CFG_WS_BURST_INTERVAL_SECONDS"
    read -r -a full_ws_stream_clients <<< "$CFG_WS_STREAM_CLIENTS"
    read -r -a full_ws_stream_sizes <<< "$CFG_WS_STREAM_SIZES_KB"
    read -r -a full_ws_stream_rates <<< "$CFG_WS_STREAM_RATE_PER_SECOND"
    read -r -a full_ws_stream_durations <<< "$CFG_WS_STREAM_DURATION_SECONDS"
    read -r -a concurrency_clients <<< "$CFG_WS_CONCURRENCY_CLIENTS"
    concurrency_size="$CFG_WS_CONCURRENCY_SIZE_KB"
    payload_clients="$CFG_WS_PAYLOAD_CLIENTS"
    read -r -a payload_sizes <<< "$CFG_WS_PAYLOAD_SIZES_KB"
    # Raw Scaphandre logs go into this measurement's own folder; keep or delete after parsing
    export MEASURE_RAW_DIR="$RESULTS_DIR/raw"
    export MEASURE_RAW_DATA="$CFG_RAW_DATA"
    [ -n "$RESUME_DIR" ] && [ -n "$RESUME_SEED" ] && CFG_SHUFFLE_SEED="$RESUME_SEED"
else
    CFG_REPEATS=1; CFG_SHUFFLE=0; CFG_SHUFFLE_SEED=""; CFG_SETTLE_SECONDS=0; CFG_FAILURES_STOP_AFTER=0
    CFG_ENV_GOVERNOR=unchanged; CFG_ENV_TURBO=unchanged; CFG_ENV_STOP_CONTAINERS=0; CFG_ENV_KEEP_CONTAINERS=""
    CFG_ENV_SCREEN_BRIGHTNESS=unchanged; CFG_ENV_KEYBOARD_LIGHT=unchanged; CFG_ENV_WIFI=unchanged; CFG_ENV_BLUETOOTH=unchanged
    CFG_ON_BATTERY=ignore
    CFG_HTTP_CONNECTION=reuse
fi

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
        echo "  HTTP_MAX_WORKERS     Max HTTP client workers for measure_docker.py (default: 100)"
        echo "                       Set to 'system' to use Python ThreadPoolExecutor default (CSV: System default)."
        echo "                       Applies to HTTP (static/dynamic) only; WebSocket is unaffected."
        echo "  BENCH_MEASURE_QUIET  logs: 1=compact [MEASURE]+heartbeats (default), 0=verbose"
        echo "  MEASURE_HEARTBEAT_SEC  Seconds between quiet-mode load progress lines (default: 60, min: 10)"
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
        echo "  HTTP_MAX_WORKERS=system $0 static   # Use ThreadPoolExecutor default"
        echo "  BENCH_MEASURE_QUIET=0 $0 static # Verbose logs"
        echo "  BENCH_MEASURE_QUIET=1 MEASURE_HEARTBEAT_SEC=60 $0 static # Compact mode for both + heartbeat interval"
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

# What to measure, from the config (MEASURE, SERVERS) when the command line does not choose.
# Resolved and checked here, before anything starts: every name must exist, every image be built.
BENCH_DO_STATIC=1; BENCH_DO_DYNAMIC=1; BENCH_DO_WS=1; BENCH_DO_CONC=1; BENCH_DO_PAYLOAD=1
BENCH_SELECTED=0
SELECT_STATIC=(); SELECT_DYNAMIC=(); SELECT_WEBSOCKET=()

bench_type_of_dir() {
    case "$1" in
        */websocket/*) echo websocket ;;
        */dynamic/*) echo dynamic ;;
        */static/*) echo static ;;
    esac
}

# Stop before anything started: also remove the still empty results folder of this run
bench_stop_early() {
    [ -z "$RESUME_DIR" ] && [ -z "$(find "$RESULTS_DIR" -type f 2>/dev/null | head -1)" ] && rm -rf "$RESULTS_DIR"
    exit 1
}

# VARIANTS: for every server in the named list, also an image <server>-<name> = the server's image plus
# environment variables, built here (Docker reuses it when nothing changed) and measured like any other
# server, in the same shuffled order. Stops before anything starts if a build fails.
bench_add_variants() {
    local -n _list=$1
    local img v name envs e dockerfile out=()
    local -a _variants _envs
    IFS=';' read -r -a _variants <<< "$CFG_VARIANTS"
    for img in "${_list[@]}"; do
        out+=("$img")
        for v in "${_variants[@]}"; do
            name="${v%%:*}"; envs="${v#*:}"
            dockerfile="FROM $img"
            IFS='|' read -r -a _envs <<< "$envs"
            for e in "${_envs[@]}"; do
                dockerfile+=$'\n'"ENV ${e%%=*}=\"${e#*=}\""
            done
            if ! printf '%s\n' "$dockerfile" | docker build -q -t "$img-$name" - >/dev/null 2>&1; then
                echo -e "${RED}[ERROR]${NC} VARIANTS: could not build $img-$name from $img."
                bench_stop_early
            fi
            out+=("$img-$name")
        done
    done
    _list=("${out[@]}")
}

bench_select_from_config() {
    local kinds=" $CFG_MEASURE " s t img dir missing=()
    [[ "$kinds" == *" static "* ]] || BENCH_DO_STATIC=0
    [[ "$kinds" == *" dynamic "* ]] || BENCH_DO_DYNAMIC=0
    [[ "$kinds" == *" websocket "* ]] || BENCH_DO_WS=0
    [[ "$kinds" == *" concurrency "* ]] || BENCH_DO_CONC=0
    [[ "$kinds" == *" payload "* ]] || BENCH_DO_PAYLOAD=0
    if [ -z "$CFG_SERVERS" ]; then
        read -r -a SELECT_STATIC <<< "$(discover_containers static)"
        read -r -a SELECT_DYNAMIC <<< "$(discover_containers dynamic)"
        read -r -a SELECT_WEBSOCKET <<< "$(discover_containers websocket)"
    else
        for s in $CFG_SERVERS; do
            if [[ "$s" == *:* ]]; then
                t="${s%%:*}"; img="${s#*:}"
            else
                img="$s"; dir=$(find_container_dir "$img")
                if [ -z "$dir" ]; then
                    echo -e "${RED}[ERROR]${NC} SERVERS: '$s' is not a server folder under $BENCHMARKS_DIR."
                    echo "For an image built on this machine without a folder, give its type: static:$s, dynamic:$s or websocket:$s"
                    bench_stop_early
                fi
                t=$(bench_type_of_dir "$dir")
            fi
            case "$t" in
                static) SELECT_STATIC+=("$img") ;;
                dynamic) SELECT_DYNAMIC+=("$img") ;;
                websocket) SELECT_WEBSOCKET+=("$img") ;;
            esac
        done
    fi
    [ "$BENCH_DO_STATIC" = 1 ] || SELECT_STATIC=()
    [ "$BENCH_DO_DYNAMIC" = 1 ] || SELECT_DYNAMIC=()
    [ "$BENCH_DO_WS$BENCH_DO_CONC$BENCH_DO_PAYLOAD" != "000" ] || SELECT_WEBSOCKET=()
    if [ $(( ${#SELECT_STATIC[@]} + ${#SELECT_DYNAMIC[@]} + ${#SELECT_WEBSOCKET[@]} )) -eq 0 ]; then
        echo -e "${RED}[ERROR]${NC} Nothing to measure: no server of the kinds in MEASURE ($CFG_MEASURE) among SERVERS (${CFG_SERVERS:-all found in $BENCHMARKS_DIR})."
        bench_stop_early
    fi
    for img in "${SELECT_STATIC[@]}" "${SELECT_DYNAMIC[@]}" "${SELECT_WEBSOCKET[@]}"; do
        docker image inspect "$img" >/dev/null 2>&1 || missing+=("$img")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        echo -e "${RED}[ERROR]${NC} These images are not built: ${missing[*]}. Build them first (make build)."
        bench_stop_early
    fi
    if [ -n "${CFG_VARIANTS:-}" ]; then
        bench_add_variants SELECT_STATIC
        bench_add_variants SELECT_DYNAMIC
        bench_add_variants SELECT_WEBSOCKET
    fi
    BENCH_SELECTED=1
}

if [ -n "${CONFIG_FILE:-}" ]; then
    if [ "$RUN_ALL" -eq 0 ]; then
        echo -e "${BLUE}[INFO]${NC} The command line chooses what to measure ($TARGET_TYPE${TARGET_IMAGES[*]:+ ${TARGET_IMAGES[*]}}); MEASURE, SERVERS and VARIANTS of the config are not used."
    else
        bench_select_from_config
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
        bench_measure ./tools/measure_websocket.py \
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
        bench_measure ./tools/measure_websocket.py \
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
                        bench_measure ./tools/measure_websocket.py \
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
                        bench_measure ./tools/measure_websocket.py \
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
    [ -f "$csv_file" ] || return 0
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
        bench_measure ./tools/measure_websocket.py \
            --server_image "$image" \
            --pattern burst \
            --mode echo \
            --clients ${quick_concurrency_clients[0]} \
            --size_kb $quick_concurrency_size \
            --bursts 1 \
            --interval "$WS_BURST_INTERVAL" \
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
            bench_measure ./tools/measure_websocket.py \
                --server_image "$image" \
                --pattern burst \
                --mode echo \
                --clients $clients \
                --size_kb $concurrency_size \
                --bursts 3 \
                --interval "$WS_BURST_INTERVAL" \
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
        bench_measure ./tools/measure_websocket.py \
            --server_image "$image" \
            --pattern burst \
            --mode echo \
            --clients $quick_payload_clients \
            --size_kb ${quick_payload_sizes[0]} \
            --bursts 1 \
            --interval "$WS_BURST_INTERVAL" \
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
            bench_measure ./tools/measure_websocket.py \
                --server_image "$image" \
                --pattern burst \
                --mode echo \
                --clients $payload_clients \
                --size_kb $size_kb \
                --bursts 3 \
                --interval "$WS_BURST_INTERVAL" \
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
        bench_measure ./tools/measure_docker.py \
            --server_image "$image" \
            --port_mapping "$port_mapping" \
            --num_requests "$num_requests" \
            --output_csv "$csv_file" \
            --measurement_type "$test_type" \
            --connection "$CFG_HTTP_CONNECTION" \
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

# ---- Repeats, rest and machine settings (from --config) ----
BENCH_ENV_APPLIED=0
BENCH_ENV_STATE=""
BENCH_RESTING_TEMP=""
BENCH_TEMP_REFERENCE=""
BENCH_RESTING_CPU=""
BENCH_CPU_REFERENCE=""
BENCH_GATE_ARGS=()

# Readiness gate before every run (only with --config): waits until the CPU is near its
# temperature reference + margin, the machine is not busy and the CPU is not throttling, checked
# every READY_CHECK_EVERY_SECONDS (see tools/readiness.py). The result goes into the
# CSV row of the run ("Waited (s)", "Ready Check").
# Free disk space (GB) where the results are written
bench_free_gb() {
    df -Pk "$RESULTS_DIR" | awk 'NR == 2 { printf "%.1f", $4 / 1048576 }'
}

# Stop cleanly (machine restored, resumable) before a full disk breaks Docker or the CSV files.
# The raw Scaphandre data kept with RAW_DATA=keep is about 45 KB per second of measuring.
BENCH_MIN_FREE_GB="${BENCH_MIN_FREE_GB:-2}"
bench_check_disk() {
    local free
    free=$(bench_free_gb)
    if awk -v f="$free" -v m="$BENCH_MIN_FREE_GB" 'BEGIN { exit !(f < m) }'; then
        print_status "ERROR" "Only ${free} GB free on the disk (minimum ${BENCH_MIN_FREE_GB} GB); stopping before it fills up. Free some space, then: make resume RESUME=$RESULTS_DIR"
        exit 1
    fi
}

bench_ready_gate() {
    BENCH_GATE_ARGS=()
    [ -n "${CONFIG_FILE:-}" ] || return 0
    bench_check_disk
    local result="$RESULTS_DIR/.ready.json" rc=0
    "$PYTHON_PATH" ./tools/readiness.py wait --result "$result" \
        --temp-reference "$BENCH_TEMP_REFERENCE" --temp-margin "$CFG_READY_TEMP_MARGIN_C" \
        --cpu-reference "$BENCH_CPU_REFERENCE" --cpu-margin "$CFG_READY_CPU_BUSY_MARGIN_PERCENT" \
        --no-throttling "$CFG_READY_NO_THROTTLING" \
        --check-every "$CFG_READY_CHECK_EVERY_SECONDS" --consecutive "$CFG_READY_CONSECUTIVE_CHECKS" \
        --min-wait "$CFG_READY_MIN_WAIT_SECONDS" --max-wait "$CFG_READY_MAX_WAIT_SECONDS" \
        --on-timeout "$CFG_READY_ON_TIMEOUT" --on-battery "$CFG_ON_BATTERY" || rc=$?
    if [ "$rc" -ne 0 ]; then
        print_status "ERROR" "Machine not ready (reason above); stopping the measurement."
        exit 1
    fi
    local g
    mapfile -t g < <("$PYTHON_PATH" -c 'import json, sys
d = json.load(open(sys.argv[1]))
print(d["waited_s"]); print(d["ready_check"])' "$result")
    rm -f "$result"
    [ "${g[1]}" != "yes" ] && print_status "WARNING" "Measuring although not ready: ${g[1]}"
    BENCH_GATE_ARGS=(--waited_s "${g[0]}" --ready_check "${g[1]}")
}

# Failed measurements: recorded in failures.csv, the campaign continues (see tools/measure_failure.py).
BENCH_PASS=1
BENCH_FAILED_IN_A_ROW=0
BENCH_FAILURES=0

bench_record_failure() {
    local image=$1 reason=$2 measurement=$3
    local f="$RESULTS_DIR/failures.csv"
    [ -f "$f" ] || echo "Time (UTC),Pass,Container Name,Measurement,Reason" > "$f"
    "$PYTHON_PATH" -c 'import csv, sys, datetime
csv.writer(sys.stdout).writerow([datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")] + sys.argv[1:])' \
        "$BENCH_PASS" "$image" "$measurement" "$reason" >> "$f"
}

bench_measure() {
    # What this measurement is: pass + tool arguments (without the output path)
    local measurement key
    measurement=$(printf '%s ' "$@" | sed -E 's# --output_csv [^ ]+##; s#\./tools/##; s/ +$//')
    key="pass $BENCH_PASS | $measurement"
    if [ -f "$RESULTS_DIR/progress.txt" ] && grep -Fxq "$key" "$RESULTS_DIR/progress.txt"; then
        print_status "INFO" "Already measured, skipping: $key"
        return 0
    fi
    bench_ready_gate
    local reason_file="$RESULTS_DIR/.failure_reason" rc=0
    rm -f "$reason_file"
    MEASURE_FAILURE_REASON_FILE="$reason_file" "$PYTHON_PATH" "$@" "${BENCH_GATE_ARGS[@]}" || rc=$?
    case "$rc" in
        0)
            BENCH_FAILED_IN_A_ROW=0
            [ -n "${CONFIG_FILE:-}" ] && echo "$key" >> "$RESULTS_DIR/progress.txt"
            ;;
        2)
            print_status "ERROR" "Machine not ready before the load (READY_ON_TIMEOUT=stop); stopping."
            exit 1
            ;;
        3)
            print_status "ERROR" "The measurement setup is broken (see above); stopping."
            exit 1
            ;;
        *)
            # This measurement failed: record why, remove what is left, continue with the next one.
            local image="" a prev="" reason
            for a in "$@"; do [ "$prev" = "--server_image" ] && image="$a"; prev="$a"; done
            reason=$(cat "$reason_file" 2>/dev/null || true)
            [ -n "$reason" ] || reason="tool exited with code $rc (see log)"
            rm -f "$reason_file"
            bench_record_failure "$image" "$reason" "$measurement"
            [ -n "$image" ] && docker rm -f "$image" >/dev/null 2>&1 || true
            BENCH_FAILURES=$((BENCH_FAILURES + 1))
            BENCH_FAILED_IN_A_ROW=$((BENCH_FAILED_IN_A_ROW + 1))
            print_status "WARNING" "Measurement failed ($reason); recorded in failures.csv, continuing"
            if [ "$CFG_FAILURES_STOP_AFTER" -gt 0 ] && [ "$BENCH_FAILED_IN_A_ROW" -ge "$CFG_FAILURES_STOP_AFTER" ]; then
                print_status "ERROR" "$BENCH_FAILED_IN_A_ROW measurements failed in a row (FAILURES_STOP_AFTER); stopping."
                exit 1
            fi
            ;;
    esac
    return 0
}

bench_report_failures() {
    [ "$BENCH_FAILURES" -gt 0 ] || return 0
    print_status "WARNING" "$BENCH_FAILURES measurement(s) failed; see $RESULTS_DIR/failures.csv"
}

# Deterministic shuffle of the arguments for one pass (seed + pass number).
bench_shuffle() {
    local pass=$1
    shift
    [ $# -eq 0 ] && return 0
    "$PYTHON_PATH" -c 'import random, sys
items = sys.argv[3:]
random.Random(f"{sys.argv[1]}-{sys.argv[2]}").shuffle(items)
print("\n".join(items))' "$CFG_SHUFFLE_SEED" "$pass" "$@"
}

bench_apply_environment() {
    if [ "$CFG_ENV_GOVERNOR" = "unchanged" ] && [ "$CFG_ENV_TURBO" = "unchanged" ] && [ "$CFG_ENV_STOP_CONTAINERS" = "0" ] \
            && [ "$CFG_ENV_SCREEN_BRIGHTNESS" = "unchanged" ] && [ "$CFG_ENV_KEYBOARD_LIGHT" = "unchanged" ] \
            && [ "$CFG_ENV_WIFI" = "unchanged" ] && [ "$CFG_ENV_BLUETOOTH" = "unchanged" ]; then
        print_status "INFO" "Machine settings: left unchanged"
        return 0
    fi
    local env_args=(--governor "$CFG_ENV_GOVERNOR" --turbo "$CFG_ENV_TURBO" --keep "$CFG_ENV_KEEP_CONTAINERS"
        --screen-brightness "$CFG_ENV_SCREEN_BRIGHTNESS" --keyboard-light "$CFG_ENV_KEYBOARD_LIGHT"
        --wifi "$CFG_ENV_WIFI" --bluetooth "$CFG_ENV_BLUETOOTH")
    [ "$CFG_ENV_STOP_CONTAINERS" = "0" ] && env_args+=(--no-stop-containers)
    # Checked here, not under sudo: sudo drops SSH_CONNECTION
    if [ "$CFG_ENV_WIFI" = "off" ] && ! "$PYTHON_PATH" -c 'import sys; sys.path.insert(0, "tools"); import prepare_environment as p; sys.exit(1 if p.remote_over_wifi() else 0)'; then
        print_status "ERROR" "This session is connected over SSH through Wi-Fi; ENV_WIFI=off would cut it. Set ENV_WIFI=unchanged in the config."
        exit 1
    fi
    BENCH_ENV_STATE="$RESULTS_DIR/.environment_state.json"
    print_status "INFO" "Machine settings: governor=$CFG_ENV_GOVERNOR turbo=$CFG_ENV_TURBO stop_containers=$CFG_ENV_STOP_CONTAINERS screen=$CFG_ENV_SCREEN_BRIGHTNESS keyboard_light=$CFG_ENV_KEYBOARD_LIGHT wifi=$CFG_ENV_WIFI bluetooth=$CFG_ENV_BLUETOOTH"
    sudo "$PYTHON_PATH" ./tools/prepare_environment.py apply "${env_args[@]}" --state "$BENCH_ENV_STATE"
    BENCH_ENV_APPLIED=1
    if ! "$PYTHON_PATH" ./tools/prepare_environment.py verify "${env_args[@]}"; then
        print_status "ERROR" "Machine settings could not be applied as configured; not measuring."
        exit 1
    fi
}

bench_restore_environment() {
    [ "$BENCH_ENV_APPLIED" -eq 1 ] || return 0
    BENCH_ENV_APPLIED=0
    print_status "INFO" "Restoring machine settings ..."
    sudo -n "$PYTHON_PATH" ./tools/prepare_environment.py restore --state "$BENCH_ENV_STATE" \
        || print_status "WARNING" "Restore failed; run: sudo python3 tools/prepare_environment.py restore --state $BENCH_ENV_STATE"
}

# Keep the machine awake (no sleep, also not when the laptop lid is closed) while measuring
bench_block_sleep() {
    if ! command -v systemd-inhibit >/dev/null 2>&1; then
        print_status "WARNING" "systemd-inhibit not found; make sure the machine does not go to sleep while measuring"
        return 0
    fi
    systemd-inhibit --what=sleep:handle-lid-switch --mode=block --who="web-server benchmarks" \
        --why="energy measurement running" sleep infinity &
    BENCH_INHIBIT_PID=$!
    print_status "INFO" "Sleep and lid-close suspend are blocked until the measurement ends"
}

bench_unblock_sleep() {
    [ -n "${BENCH_INHIBIT_PID:-}" ] || return 0
    kill "$BENCH_INHIBIT_PID" >/dev/null 2>&1 || true
    BENCH_INHIBIT_PID=""
}

# ON_BATTERY at the start: wait for the charger, stop, or ignore (machines without a battery: no check)
bench_on_battery() {
    [ "$("$PYTHON_PATH" -c 'import sys; sys.path.insert(0, "tools"); import run_metadata; print(run_metadata.ac_power())')" = "no" ]
}

bench_wait_for_charger() {
    [ "$CFG_ON_BATTERY" = "ignore" ] && return 0
    bench_on_battery || return 0
    if [ "$CFG_ON_BATTERY" = "stop" ]; then
        print_status "ERROR" "The laptop is on battery (ON_BATTERY=stop). Connect the charger and start again."
        exit 1
    fi
    print_status "WARNING" "The laptop is on battery. Connect the charger; the measurement starts when it is connected (ON_BATTERY=wait)."
    local n=0
    while bench_on_battery; do
        sleep 5
        n=$((n + 1))
        [ $((n % 12)) -eq 0 ] && print_status "WARNING" "Still on battery; waiting for the charger ..."
    done
    print_status "INFO" "Charger connected."
}

bench_on_exit() {
    # A closed terminal must not stop the restore (a write to it would end the script)
    trap '' PIPE
    bench_restore_environment
    bench_unblock_sleep
    if [ "${BENCH_INTERRUPTED:-0}" = "1" ]; then
        print_status "WARNING" "Stopped (Ctrl-C). Finished measurements are kept in $RESULTS_DIR"
        if [ -n "${CONFIG_FILE:-}" ]; then
            print_status "INFO" "To continue where it stopped: make resume RESUME=$RESULTS_DIR"
        fi
    fi
    cleanup_sudo_keepalive
}

# Statistics per configuration: <csv>_summary.csv per server, and one summary.csv per family
# folder (static, dynamic, websocket) with all servers in one table.
bench_write_summaries() {
    [ "$CFG_REPEATS" -gt 1 ] || return 0
    local csv family files
    while IFS= read -r csv; do
        "$PYTHON_PATH" ./tools/aggregate_repeats.py "$csv" >/dev/null \
            || print_status "WARNING" "Could not summarise $csv"
    done < <(bench_measurement_csvs "$RESULTS_DIR")
    for family in "$RESULTS_DIR"/static "$RESULTS_DIR"/dynamic "$RESULTS_DIR"/websocket; do
        mapfile -t files < <(bench_measurement_csvs "$family")
        [ ${#files[@]} -gt 0 ] || continue
        "$PYTHON_PATH" ./tools/aggregate_repeats.py "${files[@]}" --output "$family/summary.csv" >/dev/null \
            || print_status "WARNING" "Could not summarise $family"
    done
    print_status "INFO" "Statistics per configuration: <family>/summary.csv (all servers) and <server>_summary.csv"
}

# The measurement CSVs in a folder (not summaries, failures or raw data)
bench_measurement_csvs() {
    find "$1" -path "$1/raw" -prune -o -name '*.csv' ! -name '*_summary.csv' ! -name 'summary.csv' \
        ! -name 'failures.csv' -print 2>/dev/null | sort
}

main() {
    export BENCH_MEASURE_QUIET
    # Optional: concurrency/payload modes set this so the shared footer SUCCESS line matches the suite.
    BENCH_SUCCESS_TAIL=""
    start_sudo_keepalive
    # Restore machine settings on any exit (normal end, error, Ctrl-C), then stop the sudo keepalive.
    trap bench_on_exit EXIT
    trap 'BENCH_INTERRUPTED=1; exit 130' INT TERM
    bench_block_sleep
    if [ -n "${CONFIG_FILE:-}" ]; then
        # Without RAPL (most cloud virtual machines) Scaphandre measures nothing: stop now, not after hours
        if ! "$PYTHON_PATH" -c 'import sys; sys.path.insert(0, "tools"); import run_metadata as m; sys.exit(0 if m.rapl_available() else 1)'; then
            print_status "ERROR" "This machine shows no CPU energy counters (RAPL in /sys/class/powercap), so Scaphandre cannot measure energy. This is usual in cloud virtual machines; use a bare-metal machine or instance."
            exit 1
        fi
        bench_wait_for_charger
        bench_check_disk
        print_status "INFO" "Free disk space: $(bench_free_gb) GB (raw data kept: ~45 KB per second of measuring; RAW_DATA=$CFG_RAW_DATA)"
    fi
    bench_apply_environment
    if [ -n "${CONFIG_FILE:-}" ]; then
        if [ "$CFG_SETTLE_SECONDS" -gt 0 ]; then
            print_status "INFO" "Letting the machine settle for ${CFG_SETTLE_SECONDS}s ..."
            sleep "$CFG_SETTLE_SECONDS"
        fi
        print_status "INFO" "Measuring the resting state for ${CFG_RESTING_MEASURE_SECONDS}s ..."
        read -r BENCH_RESTING_TEMP BENCH_RESTING_CPU < <("$PYTHON_PATH" ./tools/readiness.py baseline --seconds "$CFG_RESTING_MEASURE_SECONDS")
        # References the readiness checks compare to: fixed if configured, else the measured resting values
        BENCH_TEMP_REFERENCE="${CFG_READY_TEMP_REFERENCE_C:-$BENCH_RESTING_TEMP}"
        BENCH_CPU_REFERENCE="${CFG_READY_CPU_BUSY_REFERENCE_PERCENT:-$BENCH_RESTING_CPU}"
        if [ -z "$CFG_READY_CPU_BUSY_MARGIN_PERCENT" ]; then
            print_status "INFO" "Resting CPU use: ${BENCH_RESTING_CPU:-unknown}% (CPU check off)"
        else
            print_status "INFO" "Resting CPU use: ${BENCH_RESTING_CPU:-unknown}%; ready when CPU use <= ${BENCH_CPU_REFERENCE:-0} + ${CFG_READY_CPU_BUSY_MARGIN_PERCENT}%"
        fi
        if [ -n "$BENCH_RESTING_CPU" ] && awk -v c="$BENCH_RESTING_CPU" 'BEGIN{exit !(c > 20)}'; then
            print_status "WARNING" "The machine is busy at rest (${BENCH_RESTING_CPU}% CPU); other programs may disturb the measurement"
        fi
        if [ -z "$CFG_READY_TEMP_MARGIN_C" ]; then
            print_status "INFO" "Resting CPU temperature: ${BENCH_RESTING_TEMP:-unknown} C (temperature check off)"
        else
            print_status "INFO" "Resting CPU temperature: ${BENCH_RESTING_TEMP:-unknown} C; ready when CPU <= ${BENCH_TEMP_REFERENCE:-?} + ${CFG_READY_TEMP_MARGIN_C} C"
        fi
        # Settings for readiness check 2, done by the measurement tools right before the load
        export MEASURE_READY_TEMP_REFERENCE_C="$BENCH_TEMP_REFERENCE"
        export MEASURE_READY_TEMP_MARGIN_C="$CFG_READY_TEMP_MARGIN_C"
        export MEASURE_READY_NO_THROTTLING="$CFG_READY_NO_THROTTLING"
        export MEASURE_READY_CHECK_EVERY_SECONDS="$CFG_READY_CHECK_EVERY_SECONDS"
        export MEASURE_READY_CONSECUTIVE_CHECKS="$CFG_READY_CONSECUTIVE_CHECKS"
        export MEASURE_READY_MAX_WAIT_SECONDS="$CFG_READY_MAX_WAIT_SECONDS"
        export MEASURE_READY_ON_TIMEOUT="$CFG_READY_ON_TIMEOUT"
    fi
    # Provenance of this measurement, written once to $RESULTS_DIR/metadata.json
    local meta_phase=start
    if [ -n "$RESUME_DIR" ]; then
        meta_phase=resume
        print_status "INFO" "Resuming $RESUME_DIR: measurements in progress.txt are skipped"
        echo "resumed: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RESULTS_DIR/schedule.txt"
    elif [ -n "${CONFIG_FILE:-}" ]; then
        cp "$CONFIG_FILE" "$RESULTS_DIR/bench.config"
        # The machine profile it used (MACHINE=...), kept so a resume uses exactly this one
        [ -n "${BENCH_MACHINE_FILE:-}" ] && cp "$BENCH_MACHINE_FILE" "$RESULTS_DIR/machine.config"
        # Every key with the value actually used (defaults, machine profile and the drawn seed included)
        { echo "# Values used by this measurement (defaults, machine profile ${BENCH_MACHINE_FILE:-none} and $CONFIG_FILE)"
          printf '%s\n' "$cfg_out" | grep '^CFG_' | sed 's/^CFG_//'; } > "$RESULTS_DIR/bench.config.resolved"
    fi
    "$PYTHON_PATH" ./tools/run_metadata.py "$meta_phase" "$RESULTS_DIR" \
        --set quick="$QUICK_BENCH" --set super_quick="$SUPER_QUICK_BENCH" \
        --set http_max_workers="${HTTP_MAX_WORKERS:-System default}" \
        --set benchmarks_dir="${BENCHMARKS_DIR:-}" --set arguments="$ORIGINAL_ARGS" \
        --set config_file="${CONFIG_FILE:-}" --set repeats="$CFG_REPEATS" --set shuffle="$CFG_SHUFFLE" \
        --set shuffle_seed="$CFG_SHUFFLE_SEED" --set settle_s="$CFG_SETTLE_SECONDS" \
        --set resting_measure_s="${CFG_RESTING_MEASURE_SECONDS:-}" \
        --set resting_temp_c="$BENCH_RESTING_TEMP" --set ready_temp_reference_c="$BENCH_TEMP_REFERENCE" \
        --set ready_check_every_s="${CFG_READY_CHECK_EVERY_SECONDS:-}" --set ready_temp_margin_c="${CFG_READY_TEMP_MARGIN_C:-}" \
        --set resting_cpu_busy_percent="$BENCH_RESTING_CPU" --set ready_cpu_busy_reference_percent="$BENCH_CPU_REFERENCE" \
        --set ready_cpu_busy_margin_percent="${CFG_READY_CPU_BUSY_MARGIN_PERCENT:-}" --set ready_no_throttling="${CFG_READY_NO_THROTTLING:-}" \
        --set ready_consecutive_checks="${CFG_READY_CONSECUTIVE_CHECKS:-}" --set ready_min_wait_s="${CFG_READY_MIN_WAIT_SECONDS:-}" \
        --set ready_max_wait_s="${CFG_READY_MAX_WAIT_SECONDS:-}" --set ready_on_timeout="${CFG_READY_ON_TIMEOUT:-}" \
        --set env_governor="$CFG_ENV_GOVERNOR" \
        --set env_turbo="$CFG_ENV_TURBO" --set env_stop_containers="$CFG_ENV_STOP_CONTAINERS" \
        --set http_connection="$CFG_HTTP_CONNECTION" --set failures_stop_after="$CFG_FAILURES_STOP_AFTER" \
        --set idle_s="${CFG_IDLE_SECONDS:-0}" --set warmup_s="${CFG_WARMUP_SECONDS:-0}" \
        --set reproduces="${REPRODUCE_DIR:-}" \
        --set measure="${CFG_MEASURE:-}" --set servers="${CFG_SERVERS:-}" --set variants="${CFG_VARIANTS:-}" \
        --set machine="${CFG_MACHINE:-}" --set machine_file="${BENCH_MACHINE_FILE:-}" \
        --set env_screen_brightness="${CFG_ENV_SCREEN_BRIGHTNESS:-}" --set env_keyboard_light="${CFG_ENV_KEYBOARD_LIGHT:-}" \
        --set env_wifi="${CFG_ENV_WIFI:-}" --set env_bluetooth="${CFG_ENV_BLUETOOTH:-}" --set on_battery="${CFG_ON_BATTERY:-}" \
        || print_status "WARNING" "Could not write $RESULTS_DIR/metadata.json"
    print_status "INFO" "Starting benchmarks at $(date)"
    print_status "INFO" "Results will be saved to: $RESULTS_DIR"
    if [ -n "$HTTP_MAX_WORKERS" ]; then
        print_status "INFO" "HTTP client max workers: $HTTP_MAX_WORKERS (column \"HTTP Max Workers\" in static/dynamic CSVs; override with HTTP_MAX_WORKERS=N or HTTP_MAX_WORKERS=system)"
    else
        print_status "INFO" "HTTP client max workers: System default (column \"HTTP Max Workers\" in static/dynamic CSVs; set HTTP_MAX_WORKERS=100 for reproducible runs)"
    fi
    bench_init_run_plan
    # The ID of every image before the first run, so a resume can tell whether one was rebuilt since
    if [ -z "$RESUME_DIR" ]; then
        "$PYTHON_PATH" ./tools/run_metadata.py images "$RESULTS_DIR" \
            "${BENCH_PLAN_STATIC[@]}" "${BENCH_PLAN_DYNAMIC[@]}" "${BENCH_PLAN_WEBSOCKET[@]}" \
            || print_status "WARNING" "Could not record the image IDs in metadata.json"
    fi
    BENCH_TOTAL_STEPS=$(( BENCH_TOTAL_STEPS * CFG_REPEATS ))
    if [ "$CFG_REPEATS" -gt 1 ]; then
        print_status "INFO" "Repeats: $CFG_REPEATS passes (shuffle=$CFG_SHUFFLE seed=$CFG_SHUFFLE_SEED), $BENCH_TOTAL_STEPS measurements in total"
    fi
    local base_static=("${BENCH_PLAN_STATIC[@]}")
    local base_dynamic=("${BENCH_PLAN_DYNAMIC[@]}")
    local base_websocket=("${BENCH_PLAN_WEBSOCKET[@]}")
    local base_target_type="$TARGET_TYPE"
    local pass
    for ((pass = 1; pass <= CFG_REPEATS; pass++)); do
        BENCH_PASS=$pass
        TARGET_TYPE="$base_target_type"
        if [ "$CFG_SHUFFLE" = "1" ]; then
            mapfile -t BENCH_PLAN_STATIC < <(bench_shuffle "$pass" "${base_static[@]}")
            mapfile -t BENCH_PLAN_DYNAMIC < <(bench_shuffle "$pass" "${base_dynamic[@]}")
            mapfile -t BENCH_PLAN_WEBSOCKET < <(bench_shuffle "$pass" "${base_websocket[@]}")
        fi
        # The order of every pass, so a drift over time can be checked afterwards.
        if [ -n "${CONFIG_FILE:-}" ]; then
            local sched="$RESULTS_DIR/schedule.txt"
            grep -q "^seed: " "$sched" 2>/dev/null || echo "seed: $CFG_SHUFFLE_SEED (shuffle=$CFG_SHUFFLE)" >> "$sched"
            local order=("${BENCH_PLAN_STATIC[@]}" "${BENCH_PLAN_DYNAMIC[@]}" "${BENCH_PLAN_WEBSOCKET[@]}")
            # Written once per pass, also when a resumed measurement reaches a pass for the first time
            grep -q "^pass $pass: " "$sched" 2>/dev/null || echo "pass $pass: ${order[*]}" >> "$sched"
        fi
        [ "$CFG_REPEATS" -gt 1 ] && print_section "Repeat $pass of $CFG_REPEATS"
        bench_run_pass
    done
    bench_finish_main
}

# One pass over every planned target (the measurement loop of a single repeat).
bench_run_pass() {
    local _si=0
    if [[ $RUN_ALL -eq 1 ]]; then
        print_status "INFO" "Running all benchmarks..."
        local static_containers=("${BENCH_PLAN_STATIC[@]}")
        [ ${#static_containers[@]} -gt 0 ] && print_section "Static Container Tests"
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
        local dynamic_containers=("${BENCH_PLAN_DYNAMIC[@]}")
        [ ${#dynamic_containers[@]} -gt 0 ] && print_section "Dynamic Container Tests"
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
        local websocket_containers=("${BENCH_PLAN_WEBSOCKET[@]}")
        [ "$BENCH_DO_WS" = 1 ] || websocket_containers=()
        [ ${#websocket_containers[@]} -gt 0 ] && print_section "WebSocket Tests"
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
        websocket_containers=("${BENCH_PLAN_WEBSOCKET[@]}")
        [ "$BENCH_DO_CONC$BENCH_DO_PAYLOAD" != "00" ] || websocket_containers=()
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
            if [ "$BENCH_DO_CONC" = 1 ]; then
                BENCH_PHASE="WebSocket concurrency"
                run_concurrency "$container" "$HOST_PORT"
            fi
            if [ "$BENCH_DO_PAYLOAD" = 1 ]; then
                BENCH_PHASE="WebSocket payload"
                run_payload "$container" "$HOST_PORT"
            fi
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
}

bench_finish_main() {
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
bench_report_failures
bench_write_summaries
"$PYTHON_PATH" ./tools/run_metadata.py end "$RESULTS_DIR" \
    || echo "[WARN] Could not complete $RESULTS_DIR/metadata.json"
