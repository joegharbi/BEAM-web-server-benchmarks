# Minimal Elixir base: same stages as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on language image
FROM elixir:1.19.5-otp-28 AS builder

ENV MIX_ENV=prod

WORKDIR /app

RUN mix local.hex --force && \
    mix local.rebar --force

COPY mix.exs ./
COPY lib ./lib

RUN mix deps.get --only prod && \
    mix compile

# Stage 2: minimal runtime — bookworm-slim + same apt list as all containers
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y \
    libncurses6 \
    libssl3t64 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

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
