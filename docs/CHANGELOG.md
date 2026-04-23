# Changelog

## v2.3 (runtime OS cohort unification)

- **Unified Debian runtime cohort**: All non-Gleam benchmark Dockerfiles now use `debian:trixie-slim` in runtime stages to remove Bookworm/Trixie split in measured containers.
- **Aligned Debian runtime libs**: Runtime apt dependency set was normalized to Trixie-compatible packages (`libncurses6`, `libssl3t64`, `ca-certificates`) across Erlang, Elixir, and Yaws benchmarks.
- **Consistency cleanup**: Legacy Dockerfile/runtime inconsistencies between old and latest lanes were standardized so benchmark infrastructure conditions are uniform across cohorts.

## v2.2 (unified naming and minimal bases)

- **Unified container naming**: All containers renamed to `<type>-<language>-<framework>-<version>` (e.g. `st-erlang-cowboy-27`, `dy-elixir-pure-1-16`, `ws-erlang-yaws-27`) so graphs and CSVs are self-explanatory.
- **Minimal-base Dockerfiles**: All Erlang and Elixir Dockerfiles follow the same two-stage pattern (builder → debian:bookworm-slim runtime); same apt list and structure. See [MINIMAL_BASES_AND_UNIFICATION.md](MINIMAL_BASES_AND_UNIFICATION.md).
- **Documentation**: BENCHMARKS_AUDIT, FAIRNESS_ASSESSMENT, RESULTS, UNIFIED_BENCHMARKS_DESIGN, and scripts updated for new names. Run script examples use unified image names.

## v2.1

- **CSV Result Grouping**: Results grouped by container name for easier analysis
- **WebSocket-Specific Metrics**: Enhanced WebSocket CSV format with latency and throughput data
- **Super Quick Testing**: New `run-super-quick` option for fastest validation
- **Enhanced Port Management**: All containers now use port 80 internally for consistency

## v2.0

- **Simplified Port Management**: Fixed host port with automatic container port detection
- **Enhanced Health Checks**: Comprehensive validation with HTTP and WebSocket testing
- **Auto-Discovery**: Intelligent container detection from directory structure
- **Improved Automation**: Streamlined Makefile commands and script integration
- **Better Error Handling**: Robust error detection and reporting
- **Configurable Ports**: Environment variable support for custom ports

### Key Changes

- Removed complex port arrays and manual configuration
- Added real HTTP response validation in health checks
- Implemented WebSocket handshake testing
- Enhanced container discovery and management
- Improved repository cleaning and maintenance tools
- Added comprehensive logging and error reporting
