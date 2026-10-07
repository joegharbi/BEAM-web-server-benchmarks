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
COPY priv ./priv

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

COPY --from=builder /app/_build/prod/rel/elixir_index_static /app/elixir_index_static

EXPOSE 8001

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
