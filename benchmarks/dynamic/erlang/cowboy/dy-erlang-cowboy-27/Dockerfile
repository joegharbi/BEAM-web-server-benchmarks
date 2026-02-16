# Minimal Erlang base: same stages as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on language image
FROM erlang:27 AS builder

# Set working directory
WORKDIR /app

# Install Rebar3 (Erlang build tool)
RUN wget https://github.com/erlang/rebar3/releases/download/3.22.0/rebar3 && \
    chmod +x rebar3 && \
    mv rebar3 /usr/local/bin/

# Create source directory and copy source files
RUN mkdir -p src
COPY src/ /app/src/
COPY rebar.config .

# Get dependencies and compile
RUN rebar3 get-deps && \
    rebar3 compile

# Build release
RUN rebar3 as prod release

# Stage 2: minimal runtime — bookworm-slim + same apt list as all containers
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    libncurses5 \
    libssl3 \
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
