%% TCP echo server in Erlang: one accept loop, a process per connection; every byte received is sent back
%% until the client closes. The same behaviour as the C and Java echo servers (benchmarks/echo/).
-module(echo).
-export([main/0]).

main() ->
    {ok, Sock} = gen_tcp:listen(port(), [binary, {packet, raw}, {active, false}, {reuseaddr, true}, {backlog, 1024}]),
    accept_loop(Sock).

%% Server contract (framework README): listen on PORT, 8001 when it is unset
port() ->
    case os:getenv("PORT") of
        false -> 8001;
        Value -> list_to_integer(Value)
    end.

accept_loop(Sock) ->
    case gen_tcp:accept(Sock) of
        {ok, Conn} ->
            Pid = spawn(fun() -> serve(Conn) end),
            gen_tcp:controlling_process(Conn, Pid),
            accept_loop(Sock);
        Error ->
            io:format("Accept error: ~p~n", [Error]),
            timer:sleep(1000),
            accept_loop(Sock)
    end.

serve(Conn) ->
    case gen_tcp:recv(Conn, 0) of
        {ok, Data} ->
            case gen_tcp:send(Conn, Data) of
                ok -> serve(Conn);
                {error, _} -> gen_tcp:close(Conn)
            end;
        {error, _} ->
            gen_tcp:close(Conn)
    end.
