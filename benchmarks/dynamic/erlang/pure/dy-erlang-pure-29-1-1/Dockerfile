# Minimal Erlang base: same stages as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on language image
FROM erlang:29.1.1 AS builder

# Set working directory
WORKDIR /app

# Plain Erlang HTTP server (page with the current time), built as a lean release (kernel, stdlib, the app)
COPY src/ /app/src/
# rebar.lock pins every dependency (also cowlib, ranch) to the exact version and checksum
COPY rebar.config rebar.lock ./
COPY config/ /app/config/

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
COPY --from=builder /app/_build/prod/rel/hello /app/hello

# What this server is, for the results index and the graphs (README: server contract)
LABEL wseb.language="erlang" wseb.language_version="29.1.1" \
      wseb.runtime="beam" wseb.runtime_version="OTP 29.1.1" \
      wseb.kind="pure" wseb.framework="none" wseb.framework_version=""

EXPOSE 8001

# Start the application
COPY start.sh /start.sh
RUN chmod +x /start.sh
CMD ["/start.sh"]
