-module(server_ffi).
-export([port/0, listen/1, accept/1, recv/2, send/2, close/1, give_away/2, split_head/1, local_time/0,
         index_path/0, read_file/1]).

%% Raw sockets for the index server (no framework): the gen_tcp calls the Gleam code uses,
%% and the file read of the page.

%% Server contract (framework README): listen on PORT, 8001 when it is unset
port() ->
    case os:getenv("PORT") of
        false -> 8001;
        Value -> list_to_integer(Value)
    end.

listen(Port) ->
    {ok, Sock} = gen_tcp:listen(Port, [binary, {packet, raw}, {active, false}, {reuseaddr, true}, {backlog, 1024}]),
    Sock.

accept(Listener) ->
    case gen_tcp:accept(Listener) of
        {ok, Conn} -> {ok, Conn};
        {error, _} -> {error, nil}
    end.

recv(Conn, Timeout) ->
    case gen_tcp:recv(Conn, 0, Timeout) of
        {ok, Data} -> {ok, Data};
        {error, _} -> {error, nil}
    end.

send(Conn, Data) ->
    gen_tcp:send(Conn, Data),
    nil.

close(Conn) ->
    gen_tcp:close(Conn),
    nil.

give_away(Conn, Pid) ->
    gen_tcp:controlling_process(Conn, Pid),
    nil.

%% The request head (up to the blank line) and what follows it
split_head(Bin) ->
    case binary:split(Bin, <<"\r\n\r\n">>) of
        [Head, Rest] -> {ok, {Head, Rest}};
        [_] -> {error, nil}
    end.

local_time() ->
    calendar:local_time().

%% The page ships with the program (priv/, copied into the Erlang shipment)
index_path() ->
    list_to_binary(filename:join(code:priv_dir(gleam_index_dynamic), "index.html")).

read_file(Path) ->
    case file:read_file(Path) of
        {ok, Bin} -> {ok, Bin};
        {error, _} -> {error, nil}
    end.
