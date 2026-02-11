# Multi-stage build for optimized Erlindex server
# Builder stage
FROM erlang:23 AS builder

# Set working directory
WORKDIR /app

# Create a simple Erlang HTTP server to serve index.html
RUN echo '-module(hello).' > /app/hello.erl && \
    echo '-export([main/1]).' >> /app/hello.erl && \
    echo 'main(_Args) ->' >> /app/hello.erl && \
    echo '    {ok, Sock} = gen_tcp:listen(80, [{reuseaddr, true}]),' >> /app/hello.erl && \
    echo '    accept_loop(Sock).' >> /app/hello.erl && \
    echo 'accept_loop(Sock) ->' >> /app/hello.erl && \
    echo '    case gen_tcp:accept(Sock) of' >> /app/hello.erl && \
    echo '        {ok, Conn} ->' >> /app/hello.erl && \
    echo '            spawn(fun() -> handle(Conn) end),' >> /app/hello.erl && \
    echo '            accept_loop(Sock);' >> /app/hello.erl && \
    echo '        Error ->' >> /app/hello.erl && \
    echo '            io:format("Accept error: ~p~n", [Error]),' >> /app/hello.erl && \
    echo '            timer:sleep(1000),' >> /app/hello.erl && \
    echo '            accept_loop(Sock)' >> /app/hello.erl && \
    echo '    end.' >> /app/hello.erl && \
    echo 'handle(Conn) ->' >> /app/hello.erl && \
    echo '    {ok, Html} = file:read_file("/var/www/html/index.html"),' >> /app/hello.erl && \
    echo '    Response = <<<<"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n">>/binary, Html/binary>>,' >> /app/hello.erl && \
    echo '    gen_tcp:send(Conn, Response),' >> /app/hello.erl && \
    echo '    gen_tcp:close(Conn).' >> /app/hello.erl

# Compile the Erlang app
RUN erlc -o /app /app/hello.erl

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

# Create the document root directory
RUN mkdir -p /var/www/html

# Embed a simple index.html file directly in the image
RUN echo "<!DOCTYPE html>" > /var/www/html/index.html && \
    echo "<html>" >> /var/www/html/index.html && \
    echo "  <head>" >> /var/www/html/index.html && \
    echo "    <title>Energy Test</title>" >> /var/www/html/index.html && \
    echo "  </head>" >> /var/www/html/index.html && \
    echo "  <body>" >> /var/www/html/index.html && \
    echo "    <h1>Hello, Energy Test!</h1>" >> /var/www/html/index.html && \
    echo "  </body>" >> /var/www/html/index.html && \
    echo "</html>" >> /var/www/html/index.html

# Copy the compiled beam file from builder stage
COPY --from=builder /app/hello.beam /app/hello.beam

# Expose port 8080
EXPOSE 80

# Start the server
COPY start.sh /start.sh
RUN chmod +x /start.sh
CMD ["/start.sh"]
