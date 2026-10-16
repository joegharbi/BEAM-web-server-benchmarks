# C echo server: same two-stage pattern as the other servers
# Stage 1: build with gcc 14 (Debian 13, like the runtime and the host)
FROM gcc:14.2 AS builder

WORKDIR /build
COPY src/ ./src/
RUN gcc -O2 -Wall -Wextra -pthread -o server src/server.c

# Stage 2: minimal runtime — trixie-slim + same apt list as all containers
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y \
    libncurses6 \
    libssl3t64 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Server contract (framework README): the program under /app
COPY --from=builder /build/server /app/server

# No runtime options: ERL_FLAGS variants (busy-waiting) do not apply here
LABEL wseb.options="none"

# What this server is, for the results index and the graphs (README: server contract)
LABEL wseb.language="c" wseb.language_version="C17 (gcc 14.2)" \
      wseb.runtime="native" wseb.runtime_version="glibc" \
      wseb.kind="pure" wseb.framework="none" wseb.framework_version=""

EXPOSE 8001

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
