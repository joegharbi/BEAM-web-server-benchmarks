# Minimal Elixir base: same stages as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on language image (Elixir 1.20.4 on Erlang/OTP 29.1.1)
FROM elixir:1.20.4-otp-29 AS builder

ENV MIX_ENV=prod

WORKDIR /app

RUN mix local.hex --force && \
    mix local.rebar --force

# Dependencies at the versions of mix.lock
COPY mix.exs mix.lock* ./
COPY rel ./rel
COPY lib ./lib

# Lean release (framework README, server contract): the app, its dependencies and its own Erlang runtime
RUN mix deps.get --only prod && \
    mix release

# Stage 2: minimal runtime — trixie-slim + same apt list as all containers; only the release
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y \
    libncurses6 \
    libssl3t64 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Elixir expects a UTF-8 locale
ENV LANG=C.UTF-8

WORKDIR /app

COPY --from=builder /app/_build/prod/rel/ws_elixir_cowboy /app/ws_elixir_cowboy

# What this server is, for the results index and the graphs (README: server contract)
LABEL wseb.language="elixir" wseb.language_version="1.20.4" \
      wseb.runtime="beam" wseb.runtime_version="OTP 29.1.1" \
      wseb.kind="framework" wseb.framework="cowboy" wseb.framework_version="2.19.0"

EXPOSE 8001

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
