#!/bin/bash
# Build Docker images for benchmark containers. Discovers types from benchmarks/* (static, dynamic, websocket, grpc, etc.).
# Called by: make build (default) or make clean-build (with 'clean' arg)

# Find all container dirs (benchmarks/type/.../container-name with Dockerfile)
function find_container_dirs() {
    local base="$1"
    [ -d "$base" ] || return
    find "$base" -mindepth 1 -type d -exec test -f {}/Dockerfile \; -print 2>/dev/null
}

# Discover benchmark types from benchmarks/ subdirs
function discover_benchmark_types() {
    [ -d ./benchmarks ] || return
    for d in ./benchmarks/*/; do
        [ -d "$d" ] || continue
        basename "$d"
    done
}

# Remove all containers and images related to the benchmark (safe)
function clean_benchmark_docker_images() {
    echo "Cleaning up benchmark Docker containers and images..."
    image_names=()
    for type_dir in ./benchmarks/*/; do
        [ -d "$type_dir" ] || continue
        while IFS= read -r d; do
            [ -n "$d" ] && image_names+=("$(basename "$d")")
        done < <(find_container_dirs "$type_dir")
    done

    # Remove containers using discovered image names
    for name in "${image_names[@]}"; do
        docker rm -f $(docker ps -aq --filter ancestor="$name") 2>/dev/null || true
    done

    # Remove images using discovered image names
    for name in "${image_names[@]}"; do
        docker rmi -f "$name" 2>/dev/null || true
    done

    # Also remove dangling images that might be related
    docker image prune -f
}

# Build Docker images for a benchmark type (static, dynamic, websocket, or any benchmarks/* subdir)
function process_container_folder() {
    local folder="$1"
    while IFS= read -r d; do
        [ -n "$d" ] || continue
        local name=$(basename "$d")
        echo "Building Docker image for $d/Dockerfile as $name"
        if ! docker build -t "$name" "$d"; then
            build_failures+=("$name")
        fi
    done < <(find_container_dirs "$folder")
}

# Track build failures
build_failures=()
did_clean=0
did_build=0

if [[ $# -gt 0 ]]; then
    for arg in "$@"; do
        case "$arg" in
            clean)
                clean_benchmark_docker_images
                did_clean=1
                ;;
            *)
                if [ -d "./benchmarks/$arg" ]; then
                    did_build=1
                    process_container_folder "./benchmarks/$arg"
                else
                    echo "Unknown type: $arg (no benchmarks/$arg/). Skipping."
                fi
                ;;
        esac
    done
else
    # Default: discover and build all types under benchmarks/
    did_build=1
    for type_dir in ./benchmarks/*/; do
        [ -d "$type_dir" ] || continue
        process_container_folder "$type_dir"
    done
fi

# Print summary only for actions that actually happened
if [ "$did_build" -eq 1 ]; then
    if [ ${#build_failures[@]} -eq 0 ]; then
        echo "\n[BUILD SUMMARY] All images built successfully."
    else
        echo "\n[BUILD SUMMARY] The following images failed to build:"
        for img in "${build_failures[@]}"; do
            echo "  - $img"
        done
    fi
fi

if [ "$did_clean" -eq 1 ]; then
    echo "\n[CLEAN SUMMARY] Benchmark Docker images/containers cleanup completed."
fi