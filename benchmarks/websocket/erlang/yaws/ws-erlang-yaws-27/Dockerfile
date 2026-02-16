# Minimal Yaws base: same two-stage pattern as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build — compile WebSocket handler and prepare config/content
FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y \
    yaws \
    libncurses5 \
    libssl3 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN mkdir -p app etc/yaws www/html

# WebSocket handler (Yaws defaults to 16 MB max; benchmark uses 64 MB messages)
RUN echo '-module(ws_handler).\n\
-include("/usr/lib/yaws-2.1.1/include/yaws_api.hrl").\n\
-export([out/1, handle_message/1, terminate/2]).\n\
out(Arg) ->\n\
    Opts = [{max_frame_size, 64*1024*1024}, {max_message_size, 64*1024*1024}],\n\
    {websocket, ws_handler, Opts}.\n\
handle_message({text, Data}) ->\n\
    {reply, {text, Data}};\n\
handle_message({binary, Data}) ->\n\
    {reply, {binary, Data}};\n\
handle_message({close, Status, Reason}) ->\n\
    {close, Status, Reason}.\n\
terminate(_Reason, _State) ->\n\
    ok.' > /build/app/ws_handler.erl
RUN erlc -I /usr/lib/yaws-2.1.1/include -o /build/app /build/app/ws_handler.erl

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

# Stage 2: minimal runtime — bookworm-slim + same apt list + yaws (no build-only deps in final)
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    yaws \
    libncurses5 \
    libssl3 \
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
