# Benchmarks Audit

This document lists the **current benchmark set** in `benchmarks/`: Erlang, Elixir and Gleam on the BEAM VM,
and Java (Netty) as a reference outside the BEAM. The framework is general; see [EXTENDING.md](EXTENDING.md)
and the server contract in the [README](../README.md#adding-a-server) for adding other servers.

## Naming convention

- **Path**: `benchmarks/<type>/<language>/<framework-or-variant>/<container-dir>/`
- **Container dir** = directory containing `Dockerfile` = **Docker image name**
- **Pattern**: `<type>-<language>-<framework>-<version>`, the real version with dashes
  (e.g. `st-erlang-cowboy-29-1-1`, `dy-elixir-phoenix-1-8-15`)
- **Type**: `st-` (static), `dy-` (dynamic), `ws-` (WebSocket)
- **Port**: every server listens on `PORT` (default 8001) and has `EXPOSE 8001`

See [MINIMAL_BASES_AND_UNIFICATION.md](MINIMAL_BASES_AND_UNIFICATION.md) for how the images are built.

## Current layout (30 servers)

Versions: Erlang/OTP 29.1.1 (Cowboy 2.19.0, Yaws 2.3.1); Elixir 1.20.4 on OTP 29.1.1 (Phoenix 1.8.15,
Bandit 1.12.5, Plug.Cowboy 2.9.0); Gleam 1.19.0 on OTP 29.1.1 (mist 6.0.3); Java: Netty 4.2.19.Final on JDK 27.

### Static (12)

| Language | Framework/Variant | Container | Notes |
|----------|-------------------|-----------|-------|
| Erlang | cowboy  | `st-erlang-cowboy-29-1-1` | |
| Erlang | index   | `st-erlang-index-29-1-1` | Raw sockets; reads the index HTML file per request |
| Erlang | pure    | `st-erlang-pure-29-1-1` | Raw sockets; HTML in code |
| Erlang | yaws    | `st-erlang-yaws-29-1-1` | Yaws embedded in a release |
| Elixir | cowboy  | `st-elixir-cowboy-1-20-4` | Plug.Cowboy |
| Elixir | index   | `st-elixir-index-1-20-4` | Raw sockets; reads the index HTML file per request |
| Elixir | phoenix | `st-elixir-phoenix-1-8-15` | |
| Elixir | pure    | `st-elixir-pure-1-20-4` | Raw sockets; HTML in code |
| Gleam  | index   | `st-gleam-index-1-19-0` | Raw sockets (gen_tcp via a small Erlang helper); reads the index HTML file per request |
| Gleam  | mist    | `st-gleam-mist-1-19-0` | |
| Gleam  | pure    | `st-gleam-pure-1-19-0` | Raw sockets (gen_tcp via a small Erlang helper); HTML in code |
| Java   | netty   | `st-java-netty-4-2-19` | Reference; no busy-waiting variant (does not read `ERL_FLAGS`) |

### Dynamic (12)

The same servers as static. `GET /` returns a page with the current time (index servers: the index
template with the time filled in), so every answer is built anew.

| Language | Containers |
|----------|------------|
| Erlang | `dy-erlang-cowboy-29-1-1`, `dy-erlang-index-29-1-1`, `dy-erlang-pure-29-1-1`, `dy-erlang-yaws-29-1-1` |
| Elixir | `dy-elixir-cowboy-1-20-4`, `dy-elixir-index-1-20-4`, `dy-elixir-phoenix-1-8-15`, `dy-elixir-pure-1-20-4` |
| Gleam  | `dy-gleam-index-1-19-0`, `dy-gleam-mist-1-19-0`, `dy-gleam-pure-1-19-0` |
| Java   | `dy-java-netty-4-2-19` (reference) |

### WebSocket (6)

Every server echoes each message on `/ws`.

| Language | Framework | Container |
|----------|-----------|-----------|
| Erlang | cowboy | `ws-erlang-cowboy-29-1-1` |
| Erlang | yaws   | `ws-erlang-yaws-29-1-1` |
| Elixir | cowboy | `ws-elixir-cowboy-1-20-4` |
| Elixir | bandit | `ws-elixir-bandit-1-12-5` |
| Gleam  | mist   | `ws-gleam-mist-1-19-0` |
| Java   | netty  | `ws-java-netty-4-2-19` (reference) |

### Older servers

Older versions (OTP 23-27, Elixir 1.16, Phoenix 1.8, Gleam 1.0) are kept in `benchmarks_old/` for earlier
results. They follow an older layout (port 80, a separate Erlang/Elixir install, node names) and are not
discovered by `make build` or `make run`.

## Consistency check (2026-10-07)

Every one of the 30 servers, in Docker and natively (`DEPLOY=native`):
- answers `GET /` with 200 (WebSocket: the upgrade on `/ws` with 101);
- dynamic servers return a different page one second later, and answer `POST /` with 204;
- runs only the server (`beam.smp` and `erl_child_setup`, or `java`): no `epmd`, no build tool;
- is built from its current folder (the `wseb.recipe` fingerprint matches).

## Results and graph naming

Results use **container names only**.

- **Folder**: `results/<timestamp>/` with subdirs `static/`, `dynamic/`, `websocket/`.
- **File names**: one CSV per server, named by image name; variants and native runs get their own file
  (`st-erlang-cowboy-29-1-1-nobw.csv`, `st-erlang-cowboy-29-1-1-native.csv`). See [RESULTS.md](RESULTS.md).
- **Graphs**: the GUI uses the **"Container Name"** column as the series label.

## Before a long run

```bash
make setup          # once: Python environment in srv/
make build          # builds only what changed (fingerprint)
make check-health   # every server starts and answers
bash tests/config_check.sh   # the machine settings are applied and restored
```

Then a short check config from `campaigns/check/`, then a campaign (`campaigns/README.md`).
