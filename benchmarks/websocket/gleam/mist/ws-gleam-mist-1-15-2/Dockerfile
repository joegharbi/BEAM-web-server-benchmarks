# Minimal Gleam base: same two-stage pattern as other Gleam benchmarks; websocket echo on /ws
# Stage 1: build on language image
FROM erlang:28-alpine AS builder

WORKDIR /app

RUN apk add --no-cache curl tar && \
    curl -fsSL -o /tmp/gleam.tar.gz \
      "https://github.com/gleam-lang/gleam/releases/download/v1.15.2/gleam-v1.15.2-x86_64-unknown-linux-musl.tar.gz" && \
    tar -xzf /tmp/gleam.tar.gz -C /usr/local/bin gleam && \
    rm -f /tmp/gleam.tar.gz && \
    chmod +x /usr/local/bin/gleam

COPY gleam.toml ./
COPY src ./src

RUN gleam deps download && gleam build

# Stage 2: minimal runtime on matching Alpine major/minor
FROM alpine:3.23

RUN apk add --no-cache \
    openssl \
    ncurses-libs \
    ca-certificates \
    libgcc \
    libstdc++ \
    && rm -rf /var/cache/apk/*

COPY --from=builder /usr/local/lib/erlang /usr/local/lib/erlang
COPY --from=builder /usr/local/bin/gleam /usr/local/bin/gleam
COPY --from=builder /app /app

ENV PATH="/usr/local/lib/erlang/bin:/usr/local/bin:${PATH}"

WORKDIR /app

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 80

CMD ["/start.sh"]
