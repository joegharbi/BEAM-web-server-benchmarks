# Minimal Gleam base: same two-stage pattern as other Gleam benchmarks; websocket echo on /ws
# Stage 1: build on language image
FROM ghcr.io/gleam-lang/gleam:v1.14.0-erlang-alpine AS builder

WORKDIR /app

COPY gleam.toml ./
COPY src ./src

RUN gleam deps download && gleam build

# Stage 2: minimal runtime — Alpine + same base deps as other Gleam containers
FROM alpine:3.20

RUN apk add --no-cache \
    openssl \
    ncurses-libs \
    ca-certificates \
    libgcc \
    libstdc++ \
    && rm -rf /var/cache/apk/*

COPY --from=builder /usr/local/lib/erlang /usr/local/lib/erlang
COPY --from=builder /bin/gleam /usr/local/bin/gleam
COPY --from=builder /app /app

ENV PATH="/usr/local/lib/erlang/bin:/usr/local/bin:${PATH}"

WORKDIR /app

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 80

CMD ["/start.sh"]
