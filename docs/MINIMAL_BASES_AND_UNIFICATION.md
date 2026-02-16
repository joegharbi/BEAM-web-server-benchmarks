# Minimal Bases and Unification

All benchmark containers use **three minimal base types**, built with the **same stages and pattern**, so image size and footprint are comparable.

---

## 1. Minimal bases (Bookworm-slim / Alpine)

Every runtime image is **debian:bookworm-slim** plus only what the app needs. No full Erlang/Elixir image as runtime.

| Base type | Final image contents | Used by |
|-----------|----------------------|--------|
| **Minimal Erlang** | bookworm-slim + same apt deps + `/usr/local/lib/erlang` only | Erlang apps (cowboy, pure, index) |
| **Minimal Yaws** | bookworm-slim + same apt deps + yaws (two-stage: builder prepares config/content) | Yaws (static, dynamic, websocket) |
| **Minimal Elixir** | bookworm-slim + same apt deps + Erlang + Elixir + Hex (`.mix`) | Elixir apps (cowboy, phoenix, pure, index) |
| **Minimal Gleam** | Alpine + same apk deps + Erlang + Gleam binary (no compiler, no build) | Gleam apps (mist, index) |

So:
- **One common OS** for Erlang/Elixir/Yaws: `debian:bookworm-slim`. **Gleam**: `alpine` (no Debian Gleam image yet).
- **Same apt list** (Debian): `libncurses5`, `libssl3`, `ca-certificates` (then clean). **Same apk list** (Gleam): `openssl`, `ncurses-libs`, `ca-certificates`, `libgcc`, `libstdc++`.
- **Only the runtime layer differs**: Erlang only, or Erlang+Elixir, or system packages, or Erlang+Gleam binary.

That gives comparable, minimal images and similar footprint.

---

## 2. Unified build pattern (same stages)

Every Dockerfile follows this structure:

```
Stage 1 (builder)
  FROM <language-image> AS builder
  WORKDIR /app
  ... install build tools, copy source, compile ...

Stage 2 (runtime) — same for all
  FROM debian:bookworm-slim
  RUN apt-get update && apt-get install -y \
      libncurses5 \
      libssl3 \
      ca-certificates \
      && apt-get clean && rm -rf /var/lib/apt/lists/*
  COPY --from=builder <only what's needed for runtime>
  ENV PATH=...
  WORKDIR /app
  COPY --from=builder /app /app   # or app-specific path
  EXPOSE 80
  CMD ...
```

Rules:
- **Single final stage**: always `FROM debian:bookworm-slim`.
- **Same apt packages** in that stage (no extra packages unless strictly required).
- **No build tools** in the final image; copy only runtime (Erlang beams, Elixir libs, or app release).
- **Same order**: apt → copy runtime → env → workdir → app → expose → cmd.

**Yaws** uses the same two-stage pattern: Stage 1 (builder) prepares config and content (and compiles the WebSocket handler for ws); Stage 2 is bookworm-slim + same apt list + yaws, COPY config/content from builder. **Gleam** uses the same two-stage pattern but Stage 2 is Alpine with the same apk deps and COPY-only runtime (Erlang + Gleam binary + app).

---

## 3. Naming (folder = container name)

Folder name under `benchmarks/` is the Docker image name and the name used in results/graphs. Names should make the **base type** and **app** clear.

Proposed pattern:

```
<type>-<language>-<framework>-<version>
```

- **type**: `st` | `dy` | `ws`
- **language**: `erlang` | `elixir` | `gleam` (matches minimal base: Erlang vs Elixir vs Gleam)
- **framework**: `cowboy` | `phoenix` | `pure` | `index` | `yaws` | `mist`
- **version**: e.g. `27`, `1-16`, `1-8`, `1-0`

Examples (all minimal, bookworm-slim except Gleam):

| Container name | Base type | Meaning |
|----------------|-----------|--------|
| st-erlang-cowboy-27 | Minimal Erlang | Static, Erlang, Cowboy, OTP 27 |
| dy-erlang-pure-27 | Minimal Erlang | Dynamic, Erlang, pure |
| st-elixir-cowboy-1-16 | Minimal Elixir | Static, Elixir, Cowboy, 1.16 |
| dy-elixir-phoenix-1-8 | Minimal Elixir | Dynamic, Elixir, Phoenix, 1.8 |
| st-erlang-yaws-27 | Minimal Yaws (two-stage, same pattern) | Static, Erlang stack, Yaws |
| st-gleam-mist-1-0 | Minimal Gleam (Alpine; two-stage, same pattern) | Static, Gleam, Mist |

So from the name you see: **language = which minimal base** (erlang → minimal Erlang, elixir → minimal Elixir, yaws → bookworm-slim + system Yaws).

---

## 4. Same approximate footprint

- **Same base OS**: bookworm-slim (Erlang/Elixir/Yaws); Alpine (Gleam).
- **Same apt/apk set**: one line, same packages, then clean.
- **Runtime**: only BEAM runtime + app, no compilers or build deps.

So:
- Erlang-only images: bookworm-slim + erlang lib + app (smallest).
- Elixir images: bookworm-slim + erlang + elixir + .mix + app (larger but still minimal).
- Yaws: bookworm-slim + yaws (two-stage: builder prepares config/content; runtime copies from builder).
- Gleam: Alpine + erlang + gleam binary + app (two-stage: build on Gleam image, copy to minimal Alpine).

Image sizes will sit in a narrow band per base type; we can document expected ranges and check in CI later.

---

## 5. Summary

- **Four minimal bases**: Minimal Erlang, Minimal Yaws, Minimal Elixir (all bookworm-slim, same two-stage pattern); Minimal Gleam (Alpine, same two-stage pattern).
- **Unification**: Same stages (builder → minimal runtime), same apt/apk list per base, copy-only runtime, no build tools in final image.
- **Naming**: Folder = container name; pattern `st-erlang-cowboy-27` / `dy-elixir-cowboy-1-16` so the name encodes type, language (base), framework, and version.

Implementing this means: (1) every Dockerfile follows the same two-stage pattern (builder → minimal runtime), (2) container folders use the unified naming scheme, (3) Gleam uses Alpine for runtime (no Debian Gleam image yet) but the same Stage 1 / Stage 2 structure.

---

## 6. Unified Dockerfile template (Minimal Elixir example)

Every **Elixir** container (minimal Elixir base) should follow this pattern:

```dockerfile
# Stage 1: build (language image)
FROM elixir:1.16 AS builder
ENV MIX_ENV=prod
WORKDIR /app
RUN mix local.hex --force && mix local.rebar --force
COPY mix.exs ./
COPY lib ./lib
RUN mix deps.get --only prod && mix compile

# Stage 2: minimal runtime (same for all — bookworm-slim + same apt)
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y \
    libncurses5 \
    libssl3 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Minimal Elixir base: Erlang + Elixir + Hex only (no build tools)
COPY --from=builder /usr/local/lib/erlang /usr/local/lib/erlang
COPY --from=builder /usr/local/lib/elixir /usr/local/lib/elixir
COPY --from=builder /root/.mix /root/.mix

ENV PATH="/usr/local/lib/erlang/bin:/usr/local/lib/elixir/bin:${PATH}"
ENV LANG=C.UTF-8
ENV MIX_ENV=prod
ENV HEX_OFFLINE=1

WORKDIR /app
COPY --from=builder /app /app

EXPOSE 80
COPY start.sh /start.sh
RUN chmod +x /start.sh
CMD ["/start.sh"]
```

**Minimal Erlang** containers use the same Stage 2 base (bookworm-slim + same apt), then only `COPY --from=builder /usr/local/lib/erlang` and app beams/release — no Elixir.

**Minimal Gleam** containers use the same two-stage pattern but Stage 2 is Alpine:

```dockerfile
# Stage 1: build (Gleam language image)
FROM ghcr.io/gleam-lang/gleam:v1.14.0-erlang-alpine AS builder
WORKDIR /app
COPY gleam.toml ./
COPY src ./src
RUN gleam deps download && gleam build

# Stage 2: minimal runtime (Alpine + same apk deps; no build tools)
FROM alpine:3.20
RUN apk add --no-cache openssl ncurses-libs ca-certificates libgcc libstdc++ && rm -rf /var/cache/apk/*
COPY --from=builder /usr/local/lib/erlang /usr/local/lib/erlang
COPY --from=builder /bin/gleam /usr/local/bin/gleam
COPY --from=builder /app /app
ENV PATH="/usr/local/lib/erlang/bin:/usr/local/bin:${PATH}"
WORKDIR /app
COPY start.sh /start.sh
RUN chmod +x /start.sh
EXPOSE 80
CMD ["/start.sh"]
```
