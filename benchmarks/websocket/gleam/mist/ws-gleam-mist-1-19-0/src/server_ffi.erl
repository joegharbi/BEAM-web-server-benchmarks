-module(server_ffi).
-export([port/0]).

%% Server contract (framework README): listen on PORT, 8001 when it is unset
port() ->
    case os:getenv("PORT") of
        false -> 8001;
        Value -> list_to_integer(Value)
    end.
