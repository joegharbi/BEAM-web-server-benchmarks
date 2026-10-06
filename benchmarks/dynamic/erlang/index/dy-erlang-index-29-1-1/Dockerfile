# Minimal Erlang base: same stages as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on language image
FROM erlang:29.1.1 AS builder

# Set working directory
WORKDIR /app

# Plain Erlang HTTP server (dynamic index: template priv/index.html + current time), built as a lean release (kernel, stdlib, the app)
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

# Minimal Erlang = Erlang runtime only (no Elixir, no build tools)
COPY --from=builder /usr/local/lib/erlang /usr/local/lib/erlang

# Set up environment
ENV PATH="/usr/local/lib/erlang/bin:${PATH}"
ENV ERLANG_HOME="/usr/local/lib/erlang"

# Set working directory
WORKDIR /app

# Copy only the built release from builder stage
COPY --from=builder /app/_build/prod/rel/hello /app/hello

EXPOSE 8001

# Start the application
COPY start.sh /start.sh
RUN chmod +x /start.sh
CMD ["/start.sh"]
