# Single-stage build for Yaws WebSocket echo server
FROM debian:bookworm-slim

# Install Yaws and dependencies
RUN apt-get update && apt-get install -y \
    yaws \
    libncurses5 \
    libssl3 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create necessary directories
RUN mkdir -p /etc/yaws /var/log/yaws /var/www/html /app

# Create the WebSocket handler
RUN echo '-module(ws_handler).\n\
-include("/usr/lib/yaws-2.1.1/include/yaws_api.hrl").\n\
-export([out/1, handle_message/1, terminate/2]).\n\
out(Arg) ->\n\
    {websocket, ws_handler, []}.\n\
handle_message({text, Data}) ->\n\
    {reply, {text, Data}};\n\
handle_message({binary, Data}) ->\n\
    {reply, {binary, Data}};\n\
handle_message({close, Status, Reason}) ->\n\
    {close, Status, Reason}.\n\
terminate(_Reason, _State) ->\n\
    ok.' > /app/ws_handler.erl

# Compile the WebSocket handler
RUN erlc -I /usr/lib/yaws-2.1.1/include -o /app /app/ws_handler.erl

# Create static content
RUN echo '<!DOCTYPE html>\n\
<html>\n\
<head><title>Yaws WebSocket Server</title></head>\n\
<body><h1>Yaws WebSocket Server</h1></body>\n\
</html>' > /var/www/html/index.html

# Create Yaws configuration
RUN echo 'logdir = /var/log/yaws\n\
    ebin_dir = /app\n\
    <server localhost>\n\
        port = 80\n\
        listen = 0.0.0.0\n\
    docroot = /var/www/html\n\
        appmods = </ws, ws_handler>\n\
</server>' > /etc/yaws/yaws.conf

# Create startup script
RUN echo '#!/bin/bash\n\
set -e\n\
echo "Starting Yaws WebSocket server..."\n\
echo "Configuration:"\n\
cat /etc/yaws/yaws.conf\n\
echo "WebSocket handler:"\n\
ls -la /app/\n\
echo "Testing Yaws configuration..."\n\
yaws --configtest /etc/yaws/yaws.conf\n\
echo "Starting Yaws in daemon mode..."\n\
yaws --daemon --conf /etc/yaws/yaws.conf\n\
echo "Yaws started in daemon mode. Waiting for it to be ready..."\n\
sleep 3\n\
echo "Checking Yaws status..."\n\
yaws --status\n\
echo "Yaws is running. Keeping container alive..."\n\
while true; do\n\
    sleep 10\n\
    if ! yaws --status > /dev/null 2>&1; then\n\
        echo "Yaws has stopped. Exiting..."\n\
        exit 1\n\
    fi\n\
done' > /start.sh && chmod +x /start.sh

EXPOSE 80

# Use the startup script
CMD ["/start.sh"]