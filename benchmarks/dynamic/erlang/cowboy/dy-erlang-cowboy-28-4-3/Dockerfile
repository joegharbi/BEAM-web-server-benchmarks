# Minimal Erlang base: same stages as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on language image
FROM erlang:28.4.2 AS builder

# Set working directory
WORKDIR /app

# Use rebar3 bundled in Erlang image (OTP 28 compatible)
RUN rebar3 --version

# Create source directory and copy source files
RUN mkdir -p src
COPY src/ /app/src/
COPY rebar.config .

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

# Minimal Erlang = Erlang runtime only (no Elixir, no build tools)
COPY --from=builder /usr/local/lib/erlang /usr/local/lib/erlang

# Set up environment
ENV PATH="/usr/local/lib/erlang/bin:${PATH}"
ENV ERLANG_HOME="/usr/local/lib/erlang"

# Set working directory
WORKDIR /app

# Copy only the built release from builder stage
COPY --from=builder /app/_build/prod/rel/simple_cowboy_app /app/simple_cowboy_app

# Expose port 8080
EXPOSE 80

# Start the application
CMD ["/app/simple_cowboy_app/bin/simple_cowboy_app", "foreground"]
