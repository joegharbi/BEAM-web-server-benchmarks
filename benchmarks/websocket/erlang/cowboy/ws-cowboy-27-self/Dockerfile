# Multi-stage build for optimized Cowboy WebSocket server
# Builder stage
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
COPY config/vm.args config/vm.args
COPY config/sys.config config/sys.config

# Get dependencies and compile
RUN rebar3 get-deps && \
    rebar3 compile

# Build release
RUN rebar3 as prod release

# Final stage - minimal runtime image
FROM debian:bookworm-slim

# Install only the minimal runtime dependencies
RUN apt-get update && apt-get install -y \
    libncurses5 \
    libssl3 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy only the Erlang runtime from builder stage
COPY --from=builder /usr/local/lib/erlang /usr/local/lib/erlang

# Set up environment
ENV PATH="/usr/local/lib/erlang/bin:${PATH}"
ENV ERLANG_HOME="/usr/local/lib/erlang"

# Set working directory
WORKDIR /app

# Copy only the built release from builder stage
COPY --from=builder /app/_build/prod/rel/websocket_cowboy_app /app/websocket_cowboy_app

# Expose the default Cowboy port
EXPOSE 80

# Run the Cowboy WebSocket server
CMD ["/app/websocket_cowboy_app/bin/websocket_cowboy_app", "foreground"]
