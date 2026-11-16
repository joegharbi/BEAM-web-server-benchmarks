%% Plain Erlang HTTP server (static page embedded in the code) in five variants, chosen by HELLO_VARIANT.
%% Experiment experiments/erlang-variants: what does each change to the framework's server cost or save?
%%   a  the framework's st-erlang-pure server, unchanged: a process per connection, blocking recv,
%%      the request head lowercased to look for three headers, the response built for every request
%%   b  a with lean request code: compiled search patterns instead of lowercasing (the usual spellings of the three
%%      headers only), and a ready-made response
%%   c  b, but the socket delivers its data as messages ({active, once}); still a process per connection
%%   d  b as an event loop: one worker process per scheduler, each owns many connections ({active, 100}) and
%%      answers every request waiting in its mailbox when it wakes up (no 60 s idle timeout)
%%   e  d on the newer socket backend ({inet_backend, socket})
-module(hello).
-export([main/1]).

-define(PAGE, "<!DOCTYPE html><html><head><title>Energy Test</title></head><body><h1>Hello, Energy Test!</h1></body></html>").

main(_Args) ->
    Variant = os:getenv("HELLO_VARIANT", "a"),
    persistent_term:put(hello_end, binary:compile_pattern(<<"\r\n\r\n">>)),
    persistent_term:put(hello_line, binary:compile_pattern(<<"\r\n">>)),
    persistent_term:put(hello_special, binary:compile_pattern(
        [<<"onnection: close">>, <<"ontent-length:">>, <<"ontent-Length:">>,
         <<"ransfer-encoding:">>, <<"ransfer-Encoding:">>])),
    Backend = case Variant of "e" -> [{inet_backend, socket}]; _ -> [] end,
    {ok, Sock} = gen_tcp:listen(port(), Backend ++ [binary, {packet, raw}, {active, false}, {reuseaddr, true},
                                                    {backlog, 1024}]),
    io:format("hello variant ~s~n", [Variant]),
    case Variant of
        "a" -> accept_loop(Sock, fun handle/1);
        "b" -> accept_loop(Sock, fun(Conn) -> serve_b(Conn, <<>>) end);
        "c" -> accept_loop(Sock, fun(Conn) -> serve_c(Conn, <<>>) end);
        _ ->
            Workers = list_to_tuple([spawn_link(fun() -> worker(#{}) end)
                                     || _ <- lists:seq(1, erlang:system_info(schedulers_online))]),
            accept_workers(Sock, Workers, 0)
    end.

%% Server contract (framework README): listen on PORT, 8001 when it is unset
port() ->
    case os:getenv("PORT") of
        false -> 8001;
        Value -> list_to_integer(Value)
    end.

accept_loop(Sock, Handle) ->
    case gen_tcp:accept(Sock) of
        {ok, Conn} ->
            Pid = spawn(fun() -> receive go -> Handle(Conn) end end),
            gen_tcp:controlling_process(Conn, Pid),
            Pid ! go,
            accept_loop(Sock, Handle);
        Error ->
            io:format("Accept error: ~p~n", [Error]),
            timer:sleep(1000),
            accept_loop(Sock, Handle)
    end.

%% ---- a: the framework's code -------------------------------------------------------------------------------------
%% Keep-alive: answer requests on the same connection until the client closes it,
%% asks to close it, or sends a body (not read by this server). 60 s idle timeout.
handle(Conn) -> serve(Conn, <<>>).
serve(Conn, Buf) ->
    case read_request(Conn, Buf) of
        {ok, Head, Rest} ->
            Keep = keep_alive(Head),
            respond(Conn, Keep),
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
respond(Conn, Keep) ->
    Html = <<?PAGE>>,
    Response = [<<"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: ">>, integer_to_binary(byte_size(Html)), <<"\r\nConnection: ">>, connection(Keep), <<"\r\n\r\n">>, Html],
    gen_tcp:send(Conn, Response).

%% ---- b: lean request code ----------------------------------------------------------------------------------------
serve_b(Conn, Buf) ->
    case answer(Conn, Buf) of
        {more, Rest} ->
            case gen_tcp:recv(Conn, 0, 60000) of
                {ok, Data} -> serve_b(Conn, <<Rest/binary, Data/binary>>);
                {error, _} -> gen_tcp:close(Conn)
            end;
        closed -> ok
    end.

%% Answer every complete request in the buffer; {more, Rest} when the connection stays open.
answer(Conn, Buf) ->
    case binary:split(Buf, persistent_term:get(hello_end)) of
        [Head, Rest] ->
            case keep_alive_b(Head) of
                true ->
                    gen_tcp:send(Conn, <<"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: 108\r\nConnection: keep-alive\r\n\r\n", ?PAGE>>),
                    answer(Conn, Rest);
                false ->
                    gen_tcp:send(Conn, <<"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: 108\r\nConnection: close\r\n\r\n", ?PAGE>>),
                    gen_tcp:close(Conn),
                    closed
            end;
        [_] -> {more, Buf}
    end.

keep_alive_b(Head) ->
    End = case binary:match(Head, persistent_term:get(hello_line)) of
              {Pos, _} -> Pos;
              nomatch -> byte_size(Head)
          end,
    End >= 8 andalso binary:part(Head, End - 8, 8) =:= <<"HTTP/1.1">> andalso
        binary:match(Head, persistent_term:get(hello_special)) =:= nomatch.

%% ---- c: data as messages, a process per connection ---------------------------------------------------------------
serve_c(Conn, Buf) ->
    inet:setopts(Conn, [{active, once}]),
    receive
        {tcp, Conn, Data} ->
            case answer(Conn, <<Buf/binary, Data/binary>>) of
                {more, Rest} -> serve_c(Conn, Rest);
                closed -> ok
            end;
        {tcp_closed, Conn} -> ok;
        {tcp_error, Conn, _} -> gen_tcp:close(Conn)
    after 60000 -> gen_tcp:close(Conn)
    end.

%% ---- d, e: event loop ---------------------------------------------------------------------------------------------
accept_workers(Sock, Workers, N) ->
    case gen_tcp:accept(Sock) of
        {ok, Conn} ->
            Worker = element(1 + N rem tuple_size(Workers), Workers),
            gen_tcp:controlling_process(Conn, Worker),
            Worker ! {add, Conn},
            accept_workers(Sock, Workers, N + 1);
        Error ->
            io:format("Accept error: ~p~n", [Error]),
            timer:sleep(1000),
            accept_workers(Sock, Workers, N)
    end.

%% Bufs: the unfinished request bytes of the connections that have some
worker(Bufs) ->
    receive
        {tcp, Conn, Data} ->
            Buf = case Bufs of
                      #{Conn := Old} -> <<Old/binary, Data/binary>>;
                      _ -> Data
                  end,
            case answer(Conn, Buf) of
                {more, <<>>} -> worker(maps:remove(Conn, Bufs));
                {more, Rest} -> worker(Bufs#{Conn => Rest});
                closed -> worker(maps:remove(Conn, Bufs))
            end;
        {tcp_passive, Conn} ->
            inet:setopts(Conn, [{active, 100}]),
            worker(Bufs);
        {add, Conn} ->
            inet:setopts(Conn, [{active, 100}]),
            worker(Bufs);
        {tcp_closed, Conn} -> worker(maps:remove(Conn, Bufs));
        {tcp_error, Conn, _} ->
            gen_tcp:close(Conn),
            worker(maps:remove(Conn, Bufs));
        _ -> worker(Bufs)
    end.
