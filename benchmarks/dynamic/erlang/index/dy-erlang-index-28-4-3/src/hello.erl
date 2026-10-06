%% Plain Erlang HTTP server (dynamic index): every GET reads the template priv/index.html and puts the
%% current time into it; POST returns 204. One accept loop, a process per connection.
-module(hello).
-export([main/1]).
main(_Args) ->
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
            Pid = spawn(fun() -> handle(Conn) end),
            gen_tcp:controlling_process(Conn, Pid),
            accept_loop(Sock);
        Error ->
            io:format("Accept error: ~p~n", [Error]),
            timer:sleep(1000),
            accept_loop(Sock)
    end.
%% Keep-alive: answer requests on the same connection until the client closes it,
%% asks to close it, or sends a body (not read by this server). 60 s idle timeout.
handle(Conn) -> serve(Conn, <<>>).
serve(Conn, Buf) ->
    case read_request(Conn, Buf) of
        {ok, Head, Rest} ->
            Keep = keep_alive(Head),
            respond(Conn, method(Head), Keep),
            case Keep of
                true -> serve(Conn, Rest);
                false -> gen_tcp:close(Conn)
            end;
        error -> gen_tcp:close(Conn)
    end.
read_request(Conn, Acc) ->
    case binary:split(Acc, <<"\r\n\r\n">>) of
        [Head, Rest] -> {ok, Head, Rest};
        [_] ->
            case gen_tcp:recv(Conn, 0, 60000) of
                {ok, Data} -> read_request(Conn, <<Acc/binary, Data/binary>>);
                {error, _} -> error
            end
    end.
keep_alive(Head) ->
    [Line | _] = binary:split(Head, <<"\r\n">>),
    binary:longest_common_suffix([Line, <<"HTTP/1.1">>]) =:= 8 andalso
        binary:match(string:lowercase(Head), [<<"connection: close">>, <<"content-length:">>, <<"transfer-encoding:">>]) =:= nomatch.
connection(true) -> <<"keep-alive">>;
connection(false) -> <<"close">>.
method(Head) ->
    [Method | _] = binary:split(Head, <<" ">>),
    Method.
respond(Conn, <<"POST">>, Keep) ->
    gen_tcp:send(Conn, [<<"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: ">>, connection(Keep), <<"\r\n\r\n">>]);
respond(Conn, _Method, Keep) ->
    {ok, Template} = file:read_file(filename:join(code:priv_dir(hello), "index.html")),
    {{Y,Mo,D},{H,Mi,S}} = calendar:local_time(),
    TimeBin = iolist_to_binary(io_lib:format("~4..0B-~2..0B-~2..0B ~2..0B:~2..0B:~2..0B", [Y,Mo,D,H,Mi,S])),
    Html = binary:replace(Template, <<"{{time}}">>, TimeBin),
    Response = [<<"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: ">>, integer_to_binary(byte_size(Html)), <<"\r\nConnection: ">>, connection(Keep), <<"\r\n\r\n">>, Html],
    gen_tcp:send(Conn, Response).
