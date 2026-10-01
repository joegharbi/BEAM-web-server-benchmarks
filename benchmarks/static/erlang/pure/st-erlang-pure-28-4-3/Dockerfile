# Minimal Erlang base: same stages as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build on language image
FROM erlang:28.4.2 AS builder

# Set working directory
WORKDIR /app

# Create a simple Erlang HTTP server with HTML embedded in code (pure variant)
RUN echo '-module(hello).' > /app/hello.erl && \
    echo '-export([main/1]).' >> /app/hello.erl && \
    echo 'main(_Args) ->' >> /app/hello.erl && \
    echo '    {ok, Sock} = gen_tcp:listen(80, [binary, {packet, raw}, {active, false}, {reuseaddr, true}, {backlog, 1024}]),' >> /app/hello.erl && \
    echo '    accept_loop(Sock).' >> /app/hello.erl && \
    echo 'accept_loop(Sock) ->' >> /app/hello.erl && \
    echo '    case gen_tcp:accept(Sock) of' >> /app/hello.erl && \
    echo '        {ok, Conn} ->' >> /app/hello.erl && \
    echo '            Pid = spawn(fun() -> handle(Conn) end),' >> /app/hello.erl && \
    echo '            gen_tcp:controlling_process(Conn, Pid),' >> /app/hello.erl && \
    echo '            accept_loop(Sock);' >> /app/hello.erl && \
    echo '        Error ->' >> /app/hello.erl && \
    echo '            io:format("Accept error: ~p~n", [Error]),' >> /app/hello.erl && \
    echo '            timer:sleep(1000),' >> /app/hello.erl && \
    echo '            accept_loop(Sock)' >> /app/hello.erl && \
    echo '    end.' >> /app/hello.erl && \
    echo '%% Keep-alive: answer requests on the same connection until the client closes it,' >> /app/hello.erl && \
    echo '%% asks to close it, or sends a body (not read by this server). 60 s idle timeout.' >> /app/hello.erl && \
    echo 'handle(Conn) -> serve(Conn, <<>>).' >> /app/hello.erl && \
    echo 'serve(Conn, Buf) ->' >> /app/hello.erl && \
    echo '    case read_request(Conn, Buf) of' >> /app/hello.erl && \
    echo '        {ok, Head, Rest} ->' >> /app/hello.erl && \
    echo '            Keep = keep_alive(Head),' >> /app/hello.erl && \
    echo '            respond(Conn, Keep),' >> /app/hello.erl && \
    echo '            case Keep of' >> /app/hello.erl && \
    echo '                true -> serve(Conn, Rest);' >> /app/hello.erl && \
    echo '                false -> gen_tcp:close(Conn)' >> /app/hello.erl && \
    echo '            end;' >> /app/hello.erl && \
    echo '        error -> gen_tcp:close(Conn)' >> /app/hello.erl && \
    echo '    end.' >> /app/hello.erl && \
    echo 'read_request(Conn, Acc) ->' >> /app/hello.erl && \
    echo '    case binary:split(Acc, <<"\r\n\r\n">>) of' >> /app/hello.erl && \
    echo '        [Head, Rest] -> {ok, Head, Rest};' >> /app/hello.erl && \
    echo '        [_] ->' >> /app/hello.erl && \
    echo '            case gen_tcp:recv(Conn, 0, 60000) of' >> /app/hello.erl && \
    echo '                {ok, Data} -> read_request(Conn, <<Acc/binary, Data/binary>>);' >> /app/hello.erl && \
    echo '                {error, _} -> error' >> /app/hello.erl && \
    echo '            end' >> /app/hello.erl && \
    echo '    end.' >> /app/hello.erl && \
    echo 'keep_alive(Head) ->' >> /app/hello.erl && \
    echo '    [Line | _] = binary:split(Head, <<"\r\n">>),' >> /app/hello.erl && \
    echo '    binary:longest_common_suffix([Line, <<"HTTP/1.1">>]) =:= 8 andalso' >> /app/hello.erl && \
    echo '        binary:match(string:lowercase(Head), [<<"connection: close">>, <<"content-length:">>, <<"transfer-encoding:">>]) =:= nomatch.' >> /app/hello.erl && \
    echo 'connection(true) -> <<"keep-alive">>;' >> /app/hello.erl && \
    echo 'connection(false) -> <<"close">>.' >> /app/hello.erl && \
    echo 'respond(Conn, Keep) ->' >> /app/hello.erl && \
    echo '    Html = <<"<!DOCTYPE html><html><head><title>Energy Test</title></head><body><h1>Hello, Energy Test!</h1></body></html>">>,' >> /app/hello.erl && \
    echo '    Response = [<<"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: ">>, integer_to_binary(byte_size(Html)), <<"\r\nConnection: ">>, connection(Keep), <<"\r\n\r\n">>, Html],' >> /app/hello.erl && \
    echo '    gen_tcp:send(Conn, Response).' >> /app/hello.erl

# Compile the Erlang app
RUN erlc -o /app /app/hello.erl

# Stage 2: minimal runtime — trixie-slim + same apt list as all containers
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y \
    libncurses6 \
    libssl3t64 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Minimal Erlang = Erlang runtime only (no Elixir, no build tools)
COPY --from=builder /usr/local/lib/erlang /usr/local/lib/erlang

# Set up environment
ENV PATH="/usr/local/lib/erlang/bin:${PATH}"
ENV ERLANG_HOME="/usr/local/lib/erlang"

# Set working directory
WORKDIR /app

# Copy the compiled beam file from builder stage
COPY --from=builder /app/hello.beam /app/hello.beam

# Expose port 8080
EXPOSE 80

# Start the server
COPY start.sh /start.sh
RUN chmod +x /start.sh
CMD ["/start.sh"]
