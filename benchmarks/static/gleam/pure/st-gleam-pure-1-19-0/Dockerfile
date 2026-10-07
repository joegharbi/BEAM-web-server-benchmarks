# Minimal Gleam base: same two-stage pattern as Erlang/Elixir (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on the Erlang/OTP 29.1.1 image (Debian 13, like the runtime and the host) + Gleam 1.19.0
FROM erlang:29.1.1 AS builder

RUN curl -fsSL -o /tmp/gleam.tar.gz \
      "https://github.com/gleam-lang/gleam/releases/download/v1.19.0/gleam-v1.19.0-x86_64-unknown-linux-musl.tar.gz" && \
    tar -xzf /tmp/gleam.tar.gz -C /usr/local/bin gleam && \
    rm -f /tmp/gleam.tar.gz

WORKDIR /app

# Dependencies at the versions of manifest.toml
COPY gleam.toml manifest.toml ./
COPY src ./src

# Erlang shipment: the compiled program and its dependencies, run with erl (no gleam tool at run time)
RUN gleam export erlang-shipment

# Stage 2: minimal runtime — trixie-slim + same apt list as all containers
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y \
    libncurses6 \
    libssl3t64 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Server contract (framework README): the program and its runtime under /app
COPY --from=builder /usr/local/lib/erlang /app/erlang
COPY --from=builder /app/build/erlang-shipment /app/shipment

# What this server is, for the results index and the graphs (README: server contract)
LABEL wseb.language="gleam" wseb.language_version="1.19.0" \
      wseb.runtime="beam" wseb.runtime_version="OTP 29.1.1" \
      wseb.kind="pure" wseb.framework="none" wseb.framework_version=""

EXPOSE 8001

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
