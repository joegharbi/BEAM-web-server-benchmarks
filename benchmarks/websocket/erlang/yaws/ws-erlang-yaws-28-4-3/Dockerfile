# Minimal Yaws base: same two-stage pattern as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build — compile WebSocket handler and prepare config/content
FROM debian:trixie-slim AS builder

RUN apt-get update && apt-get install -y \
    yaws \
    libncurses6 \
    libssl3t64 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN mkdir -p app etc/yaws www/html

# WebSocket handler (Yaws defaults to 16 MB max; benchmark uses 64 MB messages).
# Resolve yaws_api.hrl at build time (Debian trixie may ship a different yaws version than bookworm).
RUN set -eux; \
    YAWS_HRL="$(find /usr/lib /usr/share -name yaws_api.hrl 2>/dev/null | head -n1)"; \
    test -n "$YAWS_HRL"; \
    YAWS_INC="$(dirname "$YAWS_HRL")"; \
    { \
      echo '-module(ws_handler).'; \
      printf '%s\n' "-include(\"$YAWS_HRL\")."; \
      echo '-export([out/1, handle_message/1, terminate/2]).'; \
      echo 'out(_Arg) ->'; \
      echo '    Opts = [{max_frame_size, 64*1024*1024}, {max_message_size, 64*1024*1024}],'; \
      echo '    {websocket, ws_handler, Opts}.'; \
      echo 'handle_message({text, Data}) ->'; \
      echo '    {reply, {text, Data}};'; \
      echo 'handle_message({binary, Data}) ->'; \
      echo '    {reply, {binary, Data}};'; \
      echo 'handle_message({close, Status, Reason}) ->'; \
      echo '    {close, Status, Reason}.'; \
      echo 'terminate(_Reason, _State) ->'; \
      echo '    ok.'; \
    } > /build/app/ws_handler.erl; \
    erlc -I "$YAWS_INC" -o /build/app /build/app/ws_handler.erl

# Yaws config (acceptor_pool_size=8, max_connections=100000)
RUN echo 'logdir = /var/log/yaws\n\
ebin_dir = /app\n\
acceptor_pool_size = 8\n\
max_connections = 100000\n\
<server localhost>\n\
    port = 80\n\
    listen = 0.0.0.0\n\
    docroot = /var/www/html\n\
    appmods = </ws, ws_handler>\n\
</server>' > /build/etc/yaws/yaws.conf

RUN echo '<!DOCTYPE html><html><head><title>Yaws WebSocket</title></head><body><h1>Yaws WebSocket Server</h1></body></html>' > /build/www/html/index.html

RUN echo '#!/bin/sh\n\
set -e\n\
yaws --configtest /etc/yaws/yaws.conf\n\
yaws --daemon --conf /etc/yaws/yaws.conf\n\
sleep 3\n\
while true; do\n\
  sleep 10\n\
  yaws --status > /dev/null 2>&1 || exit 1\n\
done' > /build/start.sh

# Stage 2: minimal runtime — trixie-slim + same apt list + yaws (no build-only deps in final)
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y \
    yaws \
    libncurses6 \
    libssl3t64 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/yaws /var/log/yaws /var/www/html /app
COPY --from=builder /build/app/ws_handler.beam /app/
COPY --from=builder /build/etc/yaws/yaws.conf /etc/yaws/yaws.conf
COPY --from=builder /build/www/html/index.html /var/www/html/
COPY --from=builder /build/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 80
CMD ["/start.sh"]
