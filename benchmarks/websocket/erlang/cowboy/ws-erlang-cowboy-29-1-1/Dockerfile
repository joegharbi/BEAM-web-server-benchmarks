# Minimal Erlang base: same stages as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on language image
FROM erlang:29.1.1 AS builder

# Set working directory
WORKDIR /app

# Use rebar3 bundled in Erlang image (OTP 29 compatible)
RUN rebar3 --version

# Create source directory and copy source files
RUN mkdir -p src
COPY src/ /app/src/
# rebar.lock pins every dependency (also cowlib, ranch) to the exact version and checksum
COPY rebar.config rebar.lock ./
COPY config/ /app/config/

# Get dependencies and compile
RUN rebar3 get-deps && \
    rebar3 compile

# Build release
RUN rebar3 as prod release

# Stage 2: minimal runtime — trixie-slim + same apt list as all containers
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y \
    libncurses6 \
    libssl3t64 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# The release carries its own Erlang runtime (include_erts), so no second copy is needed

# Set working directory
WORKDIR /app

# Copy only the built release from builder stage
COPY --from=builder /app/_build/prod/rel/websocket_cowboy_app /app/websocket_cowboy_app

# What this server is, for the results index and the graphs (README: server contract)
LABEL wseb.language="erlang" wseb.language_version="29.1.1" \
      wseb.runtime="beam" wseb.runtime_version="OTP 29.1.1" \
      wseb.kind="framework" wseb.framework="cowboy" wseb.framework_version="2.19.0"

EXPOSE 8001

# Start the application
COPY start.sh /start.sh
RUN chmod +x /start.sh
CMD ["/start.sh"]
