# Fairness and Comparability Assessment

This document assesses whether the benchmark framework yields **fair, comparable energy consumption results** across containers. For valid comparisons, all containers must be exercised under identical conditions and built consistently.

---

## What Is Already Fair

| Aspect | Status | Notes |
|--------|--------|-------|
| **Energy sampling** | ✓ Consistent | Scaphandre runs same way for all; same JSON output; cgroup fallback when needed |
| **Load parameters** | ✓ Centralized | Request counts, WebSocket clients/sizes/rates come from `run_benchmarks.sh` only |
| **One-at-a-time runs** | ✓ | Single container per measurement; same host port (8001) |
| **ulimit** | ✓ | 100,000 FDs for all containers and host scripts |
| **Docker run flags** | ✓ | Same `--cgroupns=host`, `--ulimit nofile=100000:100000` for all |
| **HTTP test** | ✓ | Same GET request to `/` for all HTTP benchmarks |
| **WebSocket test** | ✓ | Same echo protocol for all WebSocket benchmarks |
| **Port** | ✓ | All expose 80; scripts map 8001:80 |
| **Runtime isolation** | ✓ | One container per run; no interference |

The **measurement pipeline** (start Scaphandre → start container → send load → stop Scaphandre → parse energy) is identical for every container.

---

## Gaps That Affect Comparability

### 1. Base images are inconsistent

| Language / Stack | Current base | Runtime size / distro |
|------------------|--------------|------------------------|
| **Erlang** (cowboy, pure, index) | `erlang:X` → `debian:bookworm-slim` | Minimal: only Erlang copied into slim Debian |
| **Elixir** (cowboy, phoenix, pure) | `elixir:1.16` directly | Full Elixir image (Erlang + Elixir, Debian-based) |
| **Gleam** | `ghcr.io/gleam-lang/gleam:v1.14.0-erlang-alpine` | Alpine-based, different distro |
| **Yaws** | `debian:bookworm-slim` + apt install yaws | System Erlang/Yaws, no OTP version control |

Implication: different base images mean different system libs, kernels, and baseline resource use. Energy differences may reflect base-image choice, not only the server implementation.

Recommendation:

- Use a **common base strategy** for BEAM benchmarks:
  - Erlang: `erlang:27` → `debian:bookworm-slim` (keep current approach).
  - Elixir: build on `erlang:27` (or the chosen Erlang base), then add Elixir in the build stage. Final image: same slim Debian + Erlang + Elixir runtime only.
  - Gleam: consider `erlang:27`-based image instead of Alpine for consistency with Erlang/Elixir.
- Create **shared base images** if desired:
  - `beam-base-erlang27:debian-slim`
  - `beam-base-elixir1.16:erlang27` (Erlang 27 + Elixir runtime only)

### 2. Variant parity: index vs pure

| Language | Index (serves file) | Pure (HTML in code) |
|----------|--------------------|---------------------|
| Erlang   | ✓ `st-erlang-index-*`, `dy-erlang-index-*` | ✓ `st-erlang-pure-*`, `dy-erlang-pure-*` |
| Elixir   | ✓ `st-elixir-index-1-16`, `dy-elixir-index-1-16` | ✓ `st-elixir-pure-*`, `dy-elixir-pure-*` |
| Gleam    | ✓ `st-gleam-index-1-0`, `dy-gleam-index-1-0` | ✓ `st-gleam-mist-*`, `dy-gleam-mist-*` |

All three languages now have both index and pure variants; naming is unified (e.g. `st-erlang-index-27`, `st-elixir-index-1-16`, `st-gleam-index-1-0`).

### 3. Response content

Most servers return similar HTML (`"Hello, Energy Test!"` style), but wording and size vary slightly. For strict comparability, response payloads should be identical in size and structure.

Recommendation: use a fixed response payload across benchmarks for comparability (same size and structure).

### 4. WebSocket: server-specific options

ws-erlang-yaws-27 had to use custom `max_frame_size` / `max_message_size` for 64 MB messages. If other WebSocket servers have different default limits, they may fail or behave differently under the same load.

Recommendation: document and standardize WebSocket limits across all servers (or align on a common max that every server supports).

---

## Summary: Current State and Next Steps

| Criterion | Current | Target |
|-----------|---------|--------|
| Same load (requests, concurrency, payload) | ✓ | ✓ |
| Same measurement pipeline | ✓ | ✓ |
| Same base / runtime strategy | ✗ | Use shared Erlang-based bases |
| Same variant coverage | ✗ | Add index variants for Elixir, Gleam |
| Same response payload | Partial | Fixed payload per benchmark type |
| Same Docker/ulimit/config | ✓ | ✓ |
| Same server config (acceptors, max_connections) | ✗→✓ | See [CONFIGURATION_PARITY.md](CONFIGURATION_PARITY.md) |

The framework is already in good shape for **fair energy comparison** in terms of load and measurement. The main improvements for stronger comparability are:

1. Normalize base images (Erlang-based for Erlang/Elixir, consider for Gleam).
2. Add index variants for Elixir and Gleam.
3. Optionally standardize response payload and WebSocket limits.
4. Optionally introduce named base images (e.g., `beam-base-erlang27`, `beam-base-elixir1.16`) for clarity and consistency.
