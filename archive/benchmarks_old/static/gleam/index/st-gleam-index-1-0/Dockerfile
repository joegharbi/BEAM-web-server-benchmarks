# Minimal Gleam base: same two-stage pattern as Erlang/Elixir; index variant serves index.html from disk (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on language image
FROM ghcr.io/gleam-lang/gleam:v1.14.0-erlang-alpine AS builder

WORKDIR /app

RUN mkdir -p /var/www/html
COPY index.html /var/www/html/index.html

COPY gleam.toml ./
COPY src ./src

RUN gleam deps download && gleam build

# Stage 2: minimal runtime — Alpine + same base deps as other Gleam containers (no build tools)
FROM alpine:3.20

RUN apk add --no-cache \
    openssl \
    ncurses-libs \
    ca-certificates \
    libgcc \
    libstdc++ \
    && rm -rf /var/cache/apk/*

# Minimal Gleam runtime = Erlang + Gleam binary only (no compiler, no build)
COPY --from=builder /usr/local/lib/erlang /usr/local/lib/erlang
COPY --from=builder /bin/gleam /usr/local/bin/gleam
COPY --from=builder /app /app
COPY --from=builder /var/www/html /var/www/html

ENV PATH="/usr/local/lib/erlang/bin:/usr/local/bin:${PATH}"

WORKDIR /app

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 80

CMD ["/start.sh"]
