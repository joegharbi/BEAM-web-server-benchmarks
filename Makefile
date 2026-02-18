# Web Server Benchmarks Makefile
# Framework-agnostic: discovers benchmarks/static, dynamic, websocket (any language/framework).

.SHELL := /bin/bash

# --- Configuration ---
VENV_NAME ?= srv
VENV_PATH = $(VENV_NAME)/bin/activate

.PHONY: help install clean-build clean-repo clean-results clean-benchmarks clean-env clean-nuclear build run setup graph validate check-health build-test-run run-single clean-all clean-build-run clean-all-build-run test

# --- Colors ---
GREEN=\033[0;32m
RED=\033[0;31m
YELLOW=\033[1;33m
NC=\033[0m

# Detect Python venv package name for this system (e.g. python3.13-venv)
PYVENV_PKG := $(shell python3 -c "import sys; print('python3.{}-venv'.format(sys.version_info.minor))" 2>/dev/null || echo "python3-venv")

check-tools: ## Check for required tools (Python, venv, Docker, make)
	@command -v python3 >/dev/null 2>&1 || { printf "${RED}ERROR:${NC} python3 not found. Install: sudo apt install python3 python3-venv\n"; exit 1; }
	@python3 -c "import ensurepip" 2>/dev/null || { \
		printf "${RED}ERROR:${NC} python3-venv not installed. Venv creation requires pip (ensurepip).\n"; \
		printf "  On Debian/Ubuntu run: sudo apt install $(PYVENV_PKG)\n"; \
		printf "  Or try the generic: sudo apt install python3-venv\n"; \
		exit 1; }
	@command -v docker >/dev/null 2>&1 || { printf "${RED}ERROR:${NC} docker not found. Install: sudo apt install docker.io\n"; exit 1; }
	@command -v make >/dev/null 2>&1 || { printf "${RED}ERROR:${NC} make not found. Install: sudo apt install make\n"; exit 1; }
	@command -v scaphandre >/dev/null 2>&1 || { printf "${YELLOW}WARNING:${NC} scaphandre not found (energy measurements will be skipped)\n"; }
	@printf "${GREEN}Required tools found.${NC}\n"

check-env:  ## Check if Python virtual environment exists
	@if [ ! -d srv ]; then \
		echo "ERROR: Python virtual environment not found"; \
		echo "Please run: make setup"; \
		exit 1; \
	fi

install: check-env ## Install Python dependencies into srv (virtual environment)
	@printf "${YELLOW}Installing dependencies in virtual environment...${NC}\n"
	@srv/bin/python3 -m pip install -r requirements.txt -q
	@printf "${GREEN}Dependencies installed!${NC}\n"

build: ## Build all Docker images for all discovered containers (requires Docker only)
	@command -v docker >/dev/null 2>&1 || { printf "${RED}ERROR:${NC} docker not found\n"; exit 1; }
	@printf "${YELLOW}Building all Docker images...${NC}\n"
	@bash scripts/install_benchmarks.sh
	@printf "${GREEN}Docker images built!${NC}\n"

clean-build: ## Remove Docker containers and images only
	@bash scripts/install_benchmarks.sh clean

# Full clean for fresh run: remove results, Docker, and Python env (keeps benchmarks/)
clean-all: ## Remove results, Docker, and Python env (clean-results + clean-env + clean-build)
	@$(MAKE) clean-results
	@$(MAKE) clean-env
	@$(MAKE) clean-build

clean-repo: ## Clean repository to bare minimum (git clean -xfd + reset --hard; use with care)
	@bash scripts/run_benchmarks.sh clean

# Remove only generated outputs (results, logs, graphs, __pycache__). Keeps benchmarks/, srv/, and repo state.
clean-results: ## Remove only generated outputs (results/, logs/, graphs/, __pycache__) for a fresh measurement
	@printf "${YELLOW}Removing generated outputs (results, logs, graphs, __pycache__)...${NC}\n"
	@rm -rf results logs graphs graphs_compressed output results_docker results_websocket
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@printf "${GREEN}Generated outputs removed. Benchmarks and venv (srv) are unchanged.${NC}\n"

# Remove the entire benchmarks/ folder. Use when you want an empty framework ready to add new benchmarks.
# Requires CONFIRM=1 to avoid accidental deletion (e.g. make clean-benchmarks CONFIRM=1).
clean-benchmarks: ## Remove benchmarks/ folder (empty framework). Requires: make clean-benchmarks CONFIRM=1
	@if [ "$(CONFIRM)" != "1" ]; then \
		printf "${RED}ERROR:${NC} This removes the entire benchmarks/ folder. To confirm, run: make clean-benchmarks CONFIRM=1\n"; \
		exit 1; \
	fi
	@printf "${YELLOW}Removing benchmarks/ folder...${NC}\n"
	@rm -rf benchmarks
	@mkdir -p benchmarks
	@printf "${GREEN}Benchmarks folder cleared. Framework is ready to receive new benchmark definitions.${NC}\n"

# Nuclear option: clean results + Docker images + benchmarks folder. Empty BEAM framework.
# Requires CONFIRM=1 (e.g. make clean-nuclear CONFIRM=1).
clean-nuclear: ## Full reset: remove results, Docker images, and benchmarks/ (empty framework). Requires CONFIRM=1
	@if [ "$(CONFIRM)" != "1" ]; then \
		printf "${RED}ERROR:${NC} This removes all generated data and the benchmarks/ folder. To confirm: make clean-nuclear CONFIRM=1\n"; \
		exit 1; \
	fi
	@printf "${YELLOW}Nuclear clean: results, Docker, and benchmarks...${NC}\n"
	@$(MAKE) clean-results
	@$(MAKE) clean-build
	@rm -rf benchmarks
	@mkdir -p benchmarks
	@printf "${GREEN}Framework is now empty and ready for any benchmarks.${NC}\n"

# Remove Python venv (srv) and all __pycache__ directories. Run 'make setup' to recreate.
clean-env: ## Remove Python venv (srv) and __pycache__ directories
	@printf "${YELLOW}Removing Python environment (srv) and __pycache__...${NC}\n"
	@rm -rf srv
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@printf "${GREEN}Python environment cleaned. Run 'make setup' to recreate.${NC}\n"

clean-port: ## Stop containers using a port (usage: make clean-port PORT=8001)
	@if [ -z "$(PORT)" ]; then \
		printf "${RED}ERROR:${NC} PORT not specified. Usage: make clean-port PORT=8001\n"; \
		exit 1; \
	fi
	@printf "${YELLOW}Checking port $(PORT)...${NC}\n"
	@if ! ss -ltn | grep -q ":$(PORT) "; then \
		printf "${GREEN}Port $(PORT) is free.${NC}\n"; \
		exit 0; \
	fi
	@printf "${YELLOW}Port $(PORT) is in use. Stopping Docker containers using it...${NC}\n"
	@containers=$$(docker ps -q --filter "publish=$(PORT)" 2>/dev/null); \
	if [ -n "$$containers" ]; then \
		echo "$$containers" | xargs docker stop 2>/dev/null && \
		echo "$$containers" | xargs docker rm 2>/dev/null && \
		printf "${GREEN}Stopped and removed containers using port $(PORT).${NC}\n"; \
	else \
		printf "${YELLOW}No Docker containers found using port $(PORT).${NC}\n"; \
		printf "${YELLOW}If a non-Docker process is using it, stop it manually or use a different port.${NC}\n"; \
	fi

run: check-env ## Run all benchmarks (static, dynamic, websocket)
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh

run-single: check-env ## Run a single server (e.g. make run-single SERVER=dy-erlang-pure-27)
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh --single $(SERVER)

# Pattern rule: make run-static, run-dynamic, run-websocket, run-quick, run-grpc, etc.
# Adding benchmarks/<type>/ + measure script gives you make run-<type> automatically.
run-%: check-env
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh --$*

check-health: check-env ## Health check only: run health check on all already-built containers (no build). Log in logs/test_*.log.
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/test_containers.sh --no-build

test: check-env install ## Build all images, then run health check on every container. Run before long benchmark. Logs in logs/.
	@printf "${YELLOW}Test: build then health check (logs in logs/)...${NC}\n"
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/test_containers.sh

build-test-run: check-env ## Build all containers, check health, and run all benchmarks
	@printf "${YELLOW}=== Building all containers ===${NC}\n"
	@$(MAKE) build
	@printf "\n"
	@printf "${YELLOW}=== Checking container health ===${NC}\n"
	@$(MAKE) check-health
	@printf "\n"
	@printf "${YELLOW}=== Running all benchmarks ===${NC}\n"
	@$(MAKE) run

clean-build-run: check-env ## Clean results, build, and run (keeps srv/ and Docker cache)
	@printf "${YELLOW}=== Cleaning results ===${NC}\n"
	@$(MAKE) clean-results
	@printf "\n"
	@$(MAKE) build
	@printf "\n"
	@$(MAKE) run

# Fully clean (results + env + Docker), recreate env, build, and run.
clean-all-build-run: ## Completely clean (results, srv, Docker), then setup, build, and run
	@printf "${YELLOW}=== Full clean (results, venv, Docker) ===${NC}\n"
	@$(MAKE) clean-all
	@printf "\n"
	@printf "${YELLOW}=== Recreating environment (srv) ===${NC}\n"
	@$(MAKE) setup
	@printf "\n"
	@$(MAKE) build
	@printf "\n"
	@$(MAKE) run

graph:  ## Launch the GUI graph generator (uses PyQt5 from requirements.txt)
	@$(MAKE) check-env
	@# Suppress benign Qt/Wayland warnings
	@if [ "$${XDG_SESSION_TYPE}" = "wayland" ]; then export QT_QPA_PLATFORM=wayland; fi; \
	QT_LOGGING_RULES="default.warning=false" srv/bin/python3 tools/gui_graph_generator.py

validate: check-tools
	@if [ ! -d srv ]; then \
		echo "[INFO] Python virtual environment not found. Running 'make setup'..."; \
		$(MAKE) setup; \
	fi
	@$(MAKE) check-env
	@BUILT_CONTAINERS=$$(docker images --format '{{.Repository}}' | grep -E 'st-|dy-|ws-' || true); \
	if [ -z "$$BUILT_CONTAINERS" ]; then \
		echo "[INFO] No built containers found. Run 'make build' to build containers before validating health."; \
	else \
		$(MAKE) check-health; \
	fi
	@printf "${GREEN}Validation complete!${NC}\n"

run-all: run  ## Run the full benchmark suite (alias)

setup: check-tools ## Set up Python virtual environment and install dependencies
	@if [ -d srv ]; then \
		if ! srv/bin/python3 -c "import pip" 2>/dev/null; then \
			printf "${YELLOW}[INFO] Removing broken venv (./srv had no pip).${NC}\n"; \
			rm -rf srv; \
		fi; \
	fi
	@if [ ! -d srv ]; then \
		printf "${YELLOW}Creating Python virtual environment in ./srv...${NC}\n"; \
		if ! python3 -m venv srv; then \
			printf "${RED}ERROR:${NC} Failed to create venv.\n"; \
			printf "  On Debian/Ubuntu run: sudo apt install $(PYVENV_PKG)\n"; \
			printf "  Then run: make setup\n"; \
			exit 1; \
		fi; \
		printf "${GREEN}Created virtual environment in ./srv${NC}\n"; \
	else \
		printf "${GREEN}Virtual environment already exists in ./srv${NC}\n"; \
	fi
	@if [ ! -f srv/bin/python3 ]; then \
		printf "${RED}ERROR:${NC} srv/bin/python3 not found. Run: make clean-env && make setup\n"; \
		exit 1; \
	fi
	@srv/bin/python3 -m pip install --upgrade pip -q && srv/bin/python3 -m pip install -r requirements.txt -q
	@printf "${GREEN}[INFO] Python environment is ready.${NC}\n"

# Aliases for backward compatibility (not shown in help)
# Removed: setup-docker

# Set help as the default goal
.DEFAULT_GOAL := help

# Color variables
YELLOW=\033[1;33m
CYAN=\033[1;36m
GREEN=\033[1;32m
NC=\033[0m

# Removed: concise-help

help:  ## Show this help message
	@printf "${YELLOW}Web Server Benchmarks - Available Commands:${NC}\n\n"
	@printf "${CYAN}Environment:${NC} srv (change with VENV_NAME=name)\n\n"
	@printf "${YELLOW}Setup:${NC}\n"
	@printf "  %-22s %s\n" "init" "One-step setup: venv, deps, build, validate (recommended for new users)"
	@printf "  %-22s %s\n" "setup" "Set up Python virtual environment and install dependencies"
	@printf "  %-22s %s\n" "build" "Build all Docker images for all discovered containers"
	@printf "\n"
	@printf "${YELLOW}Run Benchmarks:${NC}\n"
	@printf "  %-22s %s\n" "run" "Run the full benchmark suite"
	@printf "  %-22s %s\n" "run-all" "Alias for run (full benchmark suite)"
	@printf "  %-22s %s\n" "run-quick" "Quick test (3 request counts per container)"
	@printf "  %-22s %s\n" "run-super-quick" "Super-quick test (1 request count per container)"
	@printf "  %-22s %s\n" "run-single" "Run a single server (e.g. SERVER=dy-erlang-pure-27)"
	@printf "  %-22s %s\n" "run-static/dynamic/websocket" "Run a specific type only"
	@printf "  %-22s %s\n" "run-<type>" "New types (e.g. gRPC): add benchmarks/<type>/ and measure script → make run-<type> works"
	@printf "\n"
	@printf "${YELLOW}Validation & Health:${NC}\n"
	@printf "  %-22s %s\n" "check-tools" "Check for required tools (Python, pip, Docker, scaphandre)"
	@printf "  %-22s %s\n" "check-env" "Check if virtual environment is active"
	@printf "  %-22s %s\n" "check-health" "Health check only on already-built containers (no build). Log: logs/test_*.log"
	@printf "  %-22s %s\n" "test" "Build all images, then health check on every container. Run before make run. Logs in logs/"
	@printf "  %-22s %s\n" "validate" "Validate all prerequisites and health"
	@printf "\n"
	@printf "${YELLOW}Build & Clean:${NC}\n"
	@printf "  %-22s %s\n" "build-test-run" "Build all containers, check health, and run all benchmarks"
	@printf "  %-22s %s\n" "clean-build-run" "Clean results, build, and run (keeps srv/ and Docker cache)"
	@printf "  %-22s %s\n" "clean-all-build-run" "Full clean (results + srv + Docker), setup, build, and run"
	@printf "  %-22s %s\n" "clean-build" "Clean up Docker containers and images"
	@printf "  %-22s %s\n" "clean-results" "Remove only generated outputs (results/, logs/, graphs/, __pycache__) for fresh measurement"
	@printf "  %-22s %s\n" "clean-env" "Remove Python venv (srv) and __pycache__. Run 'make setup' to recreate"
	@printf "  %-22s %s\n" "clean-benchmarks" "Remove benchmarks/ folder (empty framework). Requires: CONFIRM=1"
	@printf "  %-22s %s\n" "clean-nuclear" "Full reset: results + Docker + benchmarks/. Requires: CONFIRM=1"
	@printf "  %-22s %s\n" "clean-all" "Remove results, env, and Docker (fresh run; run 'make setup' after)"
	@printf "  %-22s %s\n" "clean-port" "Stop containers using a port (usage: make clean-port PORT=8001)"
	@printf "  %-22s %s\n" "clean-repo" "Git clean + reset (bare minimum; use with care)"
	@printf "\n"
	@printf "${YELLOW}Other:${NC}\n"
	@printf "  %-22s %s\n" "graph" "Launch the GUI graph generator"
	@printf "  %-22s %s\n" "help" "Show this help message"
	@printf "\n"
	@printf "${GREEN}Quick Start:${NC}\n"
	@printf "  make init    # One-step setup for new users (recommended)\n"
	@printf "  make run     # Run all benchmarks\n"
	@printf "  make run-quick # Quick test (3 request counts)\n"
	@printf "  make run-super-quick # Fastest validation (1 request count)\n"
	@printf "\n"
	@printf "${CYAN}Auto-Discovery:${NC}\n"
	@printf "  - Add new servers: benchmarks/type/language/framework/container-name/ with Dockerfile\n"
	@printf "  - Framework automatically detects, builds, tests, and benchmarks all containers\n"
	@printf "  - Use unified naming (e.g. st-erlang-cowboy-27) for clear CSV/graph labels; see docs/MINIMAL_BASES_AND_UNIFICATION.md\n"
	@printf "  - Port assignment based on Dockerfile EXPOSE directive\n"
	@printf "\n"
	@printf "${CYAN}Advanced:${NC}\n"
	@printf "  %-22s %s\n" "install" "(Advanced) Install Python dependencies in the active virtual environment only" 

init:  ## One-step setup: venv, install, build, validate
	@$(MAKE) setup
	@$(MAKE) build
	@$(MAKE) validate
	@echo "[INFO] All setup complete! Your environment is ready to run benchmarks." 