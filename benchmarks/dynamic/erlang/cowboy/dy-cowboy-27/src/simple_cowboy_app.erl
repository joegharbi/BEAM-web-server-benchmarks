-module(simple_cowboy_app).
-behaviour(application).

-export([start/2, stop/1]).

start(_StartType, _StartArgs) ->
    Dispatch = cowboy_router:compile([
        {'_', [
            {"/", hello_handler, []}
        ]}
    ]),
    Port = case application:get_env(simple_cowboy_app, port) of
        undefined -> 8080; % Default to 8080 if not set
        {ok, Value} when is_integer(Value) -> Value;
        _ -> exit({error, invalid_port})
    end,
    io:format("Starting Cowboy on port: ~p~n", [Port]), % Debug log to verify the port value
    {ok, _} = cowboy:start_clear(http_listener,
        [{port, Port}],
        #{env => #{dispatch => Dispatch}}
    ),
    simple_cowboy_sup:start_link().

stop(_State) ->
    cowboy:stop_listener(http_listener),
    ok.