#!/bin/bash

# Install Docker images and run setup scripts for specified benchmark types

# Find all container dirs (benchmarks/type/language/framework/container-name with Dockerfile)
function find_container_dirs() {
    local base="$1"
    find "$base" -mindepth 1 -type d -exec test -f {}/Dockerfile \; -print 2>/dev/null
}

# Remove all containers and images related to the benchmark (safe)
function clean_benchmark_docker_images() {
    echo "Cleaning up benchmark Docker containers and images..."

    image_names=()
    for d in $(find_container_dirs ./benchmarks/static) $(find_container_dirs ./benchmarks/dynamic) $(find_container_dirs ./benchmarks/websocket); do
        [ -n "$d" ] && image_names+=("$(basename "$d")")
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

# Build WebSocket Docker images (recursive: benchmarks/websocket/language/framework/container/)
function process_websocket() {
    for d in $(find_container_dirs ./benchmarks/websocket); do
        [ -n "$d" ] || continue
        local name=$(basename "$d")
        echo "Building Docker image for $d/Dockerfile as $name"
        if ! docker build -t "$name" "$d"; then
            build_failures+=("$name")
        fi
    done
}

# Build Docker images for a type (static or dynamic); recursive discovery
function process_container_folder() {
    local folder="$1"
    for d in $(find_container_dirs "$folder"); do
        [ -n "$d" ] || continue
        local name=$(basename "$d")
        echo "Building Docker image for $d/Dockerfile as $name"
        if ! docker build -t "$name" "$d"; then
            build_failures+=("$name")
        fi
    done
}

# Track build failures
build_failures=()

if [[ $# -gt 0 ]]; then
    for arg in "$@"; do
        case "$arg" in
            clean)
                clean_benchmark_docker_images
                ;;
            websocket)
                process_websocket
                ;;
            static)
                process_container_folder "./benchmarks/static"
                ;;
            dynamic)
                process_container_folder "./benchmarks/dynamic"
                ;;
            *)
                echo "Unknown argument: $arg. Skipping."
                ;;
        esac
    done
else
    # Default: process websocket, static, and dynamic
    process_websocket
    process_container_folder "./benchmarks/static"
    process_container_folder "./benchmarks/dynamic"
fi

# Print build summary
if [ ${#build_failures[@]} -eq 0 ]; then
    echo "\n[BUILD SUMMARY] All images built successfully."
else
    echo "\n[BUILD SUMMARY] The following images failed to build:"
    for img in "${build_failures[@]}"; do
        echo "  - $img"
    done
fi