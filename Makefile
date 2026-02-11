# Web Server Benchmarks Makefile

.SHELL := /bin/bash

# Configuration
VENV_NAME ?= srv
VENV_PATH = $(VENV_NAME)/bin/activate

.PHONY: help install clean-build clean-repo clean-results clean-benchmarks clean-nuclear build run run-static run-dynamic run-websocket run-local run-quick run-super-quick setup graph validate check-health build-test-run run-single setup-local clean-local clean-all

# Color codes
GREEN=\033[0;32m
RED=\033[0;31m
YELLOW=\033[1;33m
NC=\033[0m

check-tools: ## Check for required tools (Python, pip, Docker, scaphandre)
	@command -v python3 >/dev/null 2>&1 || { printf "${RED}ERROR:${NC} python3 not found\n"; exit 1; }
	@(command -v pip >/dev/null 2>&1 || command -v pip3 >/dev/null 2>&1) || { printf "${RED}ERROR:${NC} pip or pip3 not found\n"; exit 1; }
	@command -v docker >/dev/null 2>&1 || { printf "${RED}ERROR:${NC} docker not found\n"; exit 1; }
	@command -v scaphandre >/dev/null 2>&1 || { printf "${YELLOW}WARNING:${NC} scaphandre not found (energy measurements will be skipped)\n"; }
	@printf "${GREEN}All required tools found.${NC}\n"

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

clean-build: ## Clean up Docker containers and images (use 'make clean-all' to also clean local servers)
	@bash scripts/install_benchmarks.sh clean

clean-local: ## BEAM-only framework: no local servers (no-op)
	@printf "${YELLOW}[INFO] BEAM-only framework: no local servers to uninstall.${NC}\n"

clean-all: ## Clean up Docker containers/images and uninstall local servers
	@$(MAKE) clean-build
	@$(MAKE) clean-local

clean-repo: ## Clean repository to bare minimum (git clean -xfd + reset --hard; use with care)
	@bash scripts/run_benchmarks.sh clean

# Remove only generated outputs (results, logs, graphs). Keeps benchmarks/, srv/, and repo state.
clean-results: ## Remove only generated outputs (results/, logs/, graphs/) for a fresh measurement
	@printf "${YELLOW}Removing generated outputs (results, logs, graphs)...${NC}\n"
	@rm -rf results logs graphs graphs_compressed output results_docker results_local results_websocket
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

run:  ## Run all benchmarks (static, dynamic, websocket, local)
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh

run-quick:  ## Quick test (3 request counts: 1000, 5000, 10000 per container)
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh --quick

run-super-quick:  ## Super-quick test (1 request count: 1000 per container)
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh --super-quick

run-single:  ## Run a single server benchmark (usage: make run-single SERVER=dy-erlang27)
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh --single $(SERVER)

run-static:  ## Run static server benchmarks only
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh --static

run-dynamic:  ## Run dynamic server benchmarks only
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh --dynamic

run-websocket:  ## Run WebSocket server benchmarks only
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh --websocket

run-local:  ## Run local server benchmarks only
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/run_benchmarks.sh --local

check-health:  ## Check health of all built containers (startup, HTTP response, stability)
	@for v in ./*/bin/activate; do \
		if [ -f "$$v" ]; then . "$$v"; break; fi; \
	done; \
	bash scripts/check_health.sh

build-test-run: check-env ## Build all containers, check health, and run all benchmarks
	@printf "${YELLOW}=== Building all containers ===${NC}\n"
	@$(MAKE) build
	@printf "\n"
	@printf "${YELLOW}=== Checking container health ===${NC}\n"
	@$(MAKE) check-health
	@printf "\n"
	@printf "${YELLOW}=== Running all benchmarks ===${NC}\n"
	@$(MAKE) run

graph:  ## Launch the GUI graph generator (uses srv venv if present)
	@if [ -f srv/bin/python3 ]; then srv/bin/python3 tools/gui_graph_generator.py; else python3 tools/gui_graph_generator.py; fi

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

setup:  ## Set up Python virtual environment and install dependencies
	@if [ ! -d srv ]; then \
		if ! python3 -m venv srv 2>/dev/null; then \
			echo "[ERROR] Failed to create venv. On Debian/Ubuntu run: sudo apt install python3-venv"; \
			exit 1; \
		fi; \
		echo "[INFO] Created Python virtual environment in ./srv"; \
	else \
		echo "[INFO] Python virtual environment already exists in ./srv"; \
	fi
	@if [ ! -f srv/bin/python3 ]; then \
		echo "[ERROR] srv/bin/python3 not found. Remove ./srv and run 'make setup' again (after installing python3-venv if needed)."; \
		exit 1; \
	fi
	@srv/bin/python3 -m pip install --upgrade pip -q && srv/bin/python3 -m pip install -r requirements.txt -q
	@echo "[INFO] Python environment is ready."

setup-local: ## BEAM-only framework: local servers not used (no-op)
	@printf "${YELLOW}[INFO] BEAM-only framework: local server setup skipped.${NC}\n"

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
	@printf "  %-22s %s\n" "init" "One-step setup: venv, install, build, local servers, validate (recommended for new users)"
	@printf "  %-22s %s\n" "setup" "Set up Python virtual environment and install dependencies"
	@printf "  %-22s %s\n" "setup-local" "(BEAM-only: no-op) Install/setup local servers"
	@printf "  %-22s %s\n" "build" "Build all Docker images for all discovered containers"
	@printf "\n"
	@printf "${YELLOW}Run Benchmarks:${NC}\n"
	@printf "  %-22s %s\n" "run" "Run the full benchmark suite"
	@printf "  %-22s %s\n" "run-all" "Alias for run (full benchmark suite)"
	@printf "  %-22s %s\n" "run-quick" "Quick test (3 request counts per container)"
	@printf "  %-22s %s\n" "run-super-quick" "Super-quick test (1 request count per container)"
	@printf "  %-22s %s\n" "run-single" "Run a single server benchmark (SERVER=dy-erlang27)"
	@printf "  %-22s %s\n" "run-static" "Run static server benchmarks only"
	@printf "  %-22s %s\n" "run-dynamic" "Run dynamic server benchmarks only"
	@printf "  %-22s %s\n" "run-websocket" "Run WebSocket server benchmarks only"
	@printf "  %-22s %s\n" "run-local" "(BEAM-only: no-op) Run local server benchmarks"
	@printf "\n"
	@printf "${YELLOW}Validation & Health:${NC}\n"
	@printf "  %-22s %s\n" "check-tools" "Check for required tools (Python, pip, Docker, scaphandre)"
	@printf "  %-22s %s\n" "check-env" "Check if virtual environment is active"
	@printf "  %-22s %s\n" "check-health" "Check health of all built containers (startup, HTTP response, stability)"
	@printf "  %-22s %s\n" "validate" "Validate all prerequisites and health"
	@printf "\n"
	@printf "${YELLOW}Build & Clean:${NC}\n"
	@printf "  %-22s %s\n" "build-test-run" "Build all containers, check health, and run all benchmarks"
	@printf "  %-22s %s\n" "clean-build" "Clean up Docker containers and images"
	@printf "  %-22s %s\n" "clean-results" "Remove only generated outputs (results/, logs/, graphs/) for fresh measurement"
	@printf "  %-22s %s\n" "clean-benchmarks" "Remove benchmarks/ folder (empty framework). Requires: CONFIRM=1"
	@printf "  %-22s %s\n" "clean-nuclear" "Full reset: results + Docker + benchmarks/. Requires: CONFIRM=1"
	@printf "  %-22s %s\n" "clean-local" "(BEAM-only: no-op) Uninstall local servers"
	@printf "  %-22s %s\n" "clean-all" "Clean up Docker containers/images and uninstall local servers"
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
	@printf "  - No naming conventions required - any directory name works\n"
	@printf "  - Port assignment based on Dockerfile EXPOSE directive\n"
	@printf "\n"
	@printf "${CYAN}Advanced:${NC}\n"
	@printf "  %-22s %s\n" "install" "(Advanced) Install Python dependencies in the active virtual environment only" 

init:  ## One-step setup: venv, install, build, validate (BEAM-only, no local servers)
	@$(MAKE) setup
	@$(MAKE) build
	@$(MAKE) validate
	@echo "[INFO] All setup complete! Your environment is ready to run benchmarks." 