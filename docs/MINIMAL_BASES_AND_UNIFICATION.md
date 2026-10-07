# Minimal Bases and Unification

All benchmark images are built the **same way**, so image size and footprint are comparable and every
image can also run natively (see the server contract in the [README](../README.md#adding-a-server)).

---

## 1. One base for every runtime image

Every runtime image is **`debian:trixie-slim`** (the host's OS, Debian 13, so native mode can run the same
program) plus one apt line, the same in every image:

```
libncurses6 libssl3t64 ca-certificates
```

The runtime differs only in **what is copied under `/app`**: the program together with its own runtime.

| Language | Built with | Copied to `/app` | Started by `/start.sh` |
|----------|------------|------------------|------------------------|
| Erlang (Cowboy, pure, index, Yaws) | `erlang:29.1.1`, `rebar3 as prod release` | the release, with its own ERTS (`include_erts`) | `bin/<release> -noinput` (no node name) |
| Elixir (Cowboy, Phoenix, Bandit, pure, index) | `elixir:1.20.4-otp-29`, `mix release` | the release, with its own ERTS (the `mix release` default) | `bin/<release> start` (no node name) |
| Gleam (mist, pure, index) | `erlang:29.1.1` + the Gleam 1.19.0 binary, `gleam export erlang-shipment` | the shipment (`/app/shipment`) and OTP (`/app/erlang`) | `shipment/entrypoint.sh run`, with `/app/erlang/bin` first on `PATH` |
| Java (Netty, pure, index; reference) | `maven:3.10.0-eclipse-temurin-27`, `jlink` | the jar and a `jlink` Java runtime (`/app/jre`) | `/app/jre/bin/java -jar /app/app.jar` |

No image contains a build tool (`rebar3`, `mix`, `gleam`, Maven), `epmd`, or a second copy of a runtime.
The exact versions are part of each folder name (e.g. `st-erlang-cowboy-29-1-1`, `st-java-pure-27`);
dependency versions are pinned in `rebar.config` / `mix.lock` / `manifest.toml` / `pom.xml`.

---

## 2. Same stages in every Dockerfile

```
Stage 1 (builder)
  FROM <language image> AS builder
  ... copy source, fetch pinned dependencies, build the release / shipment / jar ...

Stage 2 (runtime) — the same for all
  FROM debian:trixie-slim
  RUN apt-get update && apt-get install -y libncurses6 libssl3t64 ca-certificates \
      && apt-get clean && rm -rf /var/lib/apt/lists/*
  WORKDIR /app
  COPY --from=builder <program + its runtime> /app/...
  EXPOSE 8001
  COPY start.sh /start.sh
  CMD ["/start.sh"]
```

Rules:
- **Single final stage** from `debian:trixie-slim`, with the **same apt line**.
- **Everything the server needs is under `/app`**; `/start.sh` finds it under `${APP_DIR:-/app}`, so
  native mode can run the same script on a copy.
- **Port** from `PORT` (default 8001), `EXPOSE 8001`.
- **Runtime options from the environment** (`ERL_FLAGS`, `JAVA_TOOL_OPTIONS`); an image that does not read
  `ERL_FLAGS` names what it reads with `LABEL wseb.options` (Java: `JAVA_TOOL_OPTIONS`).
- `make build` adds `LABEL wseb.recipe=<fingerprint of the folder>`, so a run notices an image that is
  older than its folder.

---

## 3. Naming (folder = image name)

```
<type>-<language>-<framework>-<version>
```

- **type**: `st` | `dy` | `ws`
- **language**: `erlang` | `elixir` | `gleam` | `java`
- **framework**: `cowboy` | `phoenix` | `bandit` | `pure` | `index` | `yaws` | `mist` | `netty`
- **version**: the real version with dashes: OTP for Erlang (`29-1-1`), Elixir (`1-20-4`) or the framework
  (`phoenix-1-8-15`, `bandit-1-12-5`), Gleam (`1-19-0`), Netty (`4-2-19`), the JDK for Java pure and index (`27`)

The current servers are listed in [BENCHMARKS_AUDIT.md](BENCHMARKS_AUDIT.md). Older servers (OTP 23-27,
Elixir 1.16, Gleam 1.0, Alpine-based Gleam) are kept in `benchmarks_old/` for earlier results; they follow
an older layout (port 80, a separate Erlang/Elixir install) and are not part of the current measurements.
