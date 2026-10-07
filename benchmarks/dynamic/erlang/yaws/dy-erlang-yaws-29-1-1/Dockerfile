# Minimal Erlang base: same stages as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on language image
FROM erlang:29.1.1 AS builder

# Yaws builds two small C parts (epam needs the PAM headers; not used by this server)
RUN apt-get update && apt-get install -y libpam0g-dev && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Yaws embedded in a lean release (page with the current time, index.yaws)
COPY src/ /app/src/
COPY priv/ /app/priv/
COPY rebar.config .
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
COPY --from=builder /app/_build/prod/rel/bench_yaws /app/bench_yaws

EXPOSE 8001

# Start the application
COPY start.sh /start.sh
RUN chmod +x /start.sh
CMD ["/start.sh"]
