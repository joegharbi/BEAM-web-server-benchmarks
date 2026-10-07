-module(server_ffi).
-export([port/0, index_path/0]).

%% Server contract (framework README): listen on PORT, 8001 when it is unset
port() ->
    case os:getenv("PORT") of
        false -> 8001;
        Value -> list_to_integer(Value)
    end.

%% The page ships with the program (priv/, copied into the Erlang shipment)
index_path() ->
    list_to_binary(filename:join(code:priv_dir(gleam_index_dynamic), "index.html")).
