-module(simple_cowboy_app).
-behaviour(application).

-export([start/2, stop/1]).

start(_StartType, _StartArgs) ->
    Dispatch = cowboy_router:compile([
        {'_', [
            {"/", hello_handler, []}
        ]}
    ]),
    % Server contract (framework README): listen on PORT, 8001 when it is unset
    Port = case os:getenv("PORT") of
        false -> 8001;
        Value -> list_to_integer(Value)
    end,
    io:format("Starting Cowboy on port: ~p~n", [Port]), % Debug log to verify the port value
    % Canonical transport opts: num_acceptors=8, max_connections=100000 (see docs/CONFIGURATION_PARITY.md)
    {ok, _} = cowboy:start_clear(http_listener,
        #{
            num_acceptors => 8,
            max_connections => 100000,
            socket_opts => [{port, Port}]
        },
        maps:merge(#{env => #{dispatch => Dispatch}}, limits())
    ),
    simple_cowboy_sup:start_link().

%% Experiment experiments/erlang-variants: the only change to the framework's st-erlang-cowboy server.
%% Cowboy's own limits on a connection, each kept at Cowboy's default when its variable is unset:
%%   COWBOY_REQUEST_TIMEOUT  ms without a request before Cowboy closes the connection (default 5000)
%%   COWBOY_MAX_KEEPALIVE    requests on one connection before Cowboy closes it (default 1000)
%%   COWBOY_IDLE_TIMEOUT     ms without data while a request is being handled (default 60000)
%% A number, or "infinity" for no limit.
limits() ->
    Set = [{Key, limit(Value)} || {Key, Var} <- [{request_timeout, "COWBOY_REQUEST_TIMEOUT"},
                                                 {max_keepalive, "COWBOY_MAX_KEEPALIVE"},
                                                 {idle_timeout, "COWBOY_IDLE_TIMEOUT"}],
                                  (Value = os:getenv(Var)) =/= false, Value =/= ""],
    io:format("cowboy limits set: ~p~n", [Set]),
    maps:from_list(Set).

limit("infinity") -> infinity;
limit(Value) -> list_to_integer(Value).

stop(_State) ->
    cowboy:stop_listener(http_listener),
    ok.