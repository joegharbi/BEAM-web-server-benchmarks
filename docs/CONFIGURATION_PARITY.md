# Configuration Parity for Comparable Benchmarks

For fair, comparable energy measurements, **every configurable property** must be aligned across all benchmark containers. This document defines the canonical values and where they apply.

---

## Canonical Values

| Property | Canonical Value | Rationale |
|----------|-----------------|-----------|
| **Base image (runtime)** | `debian:bookworm-slim` | Same OS, libs, kernel surface for Erlang/Elixir. Gleam uses Alpine (different distro) until a Debian-based image exists. |
| **num_acceptors / acceptor_pool_size** | **8** | Same number of acceptor processes across all pools. Yaws uses 8; Ranch defaults vary (10 or 100). We standardize on 8. |
| **max_connections** | **100_000** | Matches `ulimit -n 100000`. No server should throttle connections before the FD limit. Ranch default 1024 would bias results. |
| **ulimit nofile** | **100_000** | Already consistent: run scripts, Docker `--ulimit`, and container `start.sh`. |
| **Port** | **80** (container), **8001** (host) | All containers EXPOSE 80; host maps 8001:80. |
| **Docker run flags** | `--cgroupns=host`, `--ulimit nofile=100000:100000` | Required for Scaphandre and FD limit. |

---

## Where Each Property Is Set

### Cowboy / Ranch (Erlang, Elixir, Plug, Phoenix)

- **Transport options**: `num_acceptors: 8`, `max_connections: 100_000`
- **Erlang** (`cowboy:start_clear/3`): Second argument = transport opts, e.g. `[{port, 80}, {num_acceptors, 8}, {max_connections, 100000}]`
- **Plug.Cowboy** (Elixir): `options: [port: 80, transport_options: [num_acceptors: 8, max_connections: 100_000]]`
- **Phoenix**: `config :app, Endpoint, http: [..., transport_options: [num_acceptors: 8, max_connections: 100_000]]`

### Yaws

- **acceptor_pool_size** = 8 ✓ (already set)
- **max_connections** = 100000 (set explicitly for parity; was `nolimit`)

### Mist (Gleam)

- Mist does not expose acceptor-pool or max-connections options in its public API.
- Documented as “same logical behavior” where applicable; no config changes possible without forking Mist.

### Pure gen_tcp (Erlang, Elixir)

- No acceptor pool; one listener, one process per connection.
- Effective limit: `ulimit` (100_000). No explicit `max_connections`; documented for clarity.

---

## Checklist for New Benchmarks

When adding a new container:

1. **Base image**: Use `debian:bookworm-slim` for Erlang/Elixir runtimes.
2. **Acceptors**: Set `num_acceptors` / `acceptor_pool_size` to **8** if the server supports it.
3. **max_connections**: Set to **100_000** if the server supports it.
4. **ulimit**: `ulimit -n 100000` in `start.sh`; Docker already passes `--ulimit nofile=100000:100000`.
5. **Port**: EXPOSE 80 in Dockerfile.

---

## Summary Table

| Stack | num_acceptors | max_connections | base (runtime) |
|-------|---------------|-----------------|----------------|
| Erlang Cowboy | 8 | 100_000 | debian:bookworm-slim |
| Elixir Cowboy/Plug | 8 | 100_000 | debian:bookworm-slim |
| Phoenix | 8 | 100_000 | debian:bookworm-slim |
| Yaws | 8 (acceptor_pool_size) | 100_000 | debian:bookworm-slim |
| Mist (Gleam) | N/A | N/A | Alpine (no parity option) |
| Pure gen_tcp | N/A (1 listener) | ulimit | debian:bookworm-slim |
