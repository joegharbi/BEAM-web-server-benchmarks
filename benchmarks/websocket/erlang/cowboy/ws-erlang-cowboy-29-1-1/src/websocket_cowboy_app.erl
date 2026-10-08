-module(websocket_cowboy_app).
-behaviour(application).

-export([start/2, stop/1]).

start(_StartType, _StartArgs) ->
    % Server contract (framework README): listen on PORT, 8001 when it is unset
    Port = case os:getenv("PORT") of
        false -> 8001;
        Value -> list_to_integer(Value)
    end,
    % Canonical transport opts: num_acceptors=8, max_connections=100000 (see docs/CONFIGURATION_PARITY.md)
    {ok, _} = cowboy:start_clear(http,
        #{
            num_acceptors => 8,
            max_connections => 100000,
            socket_opts => [{port, Port}]
        },
        #{
            env => #{dispatch => dispatch()},
            max_frame_size => 64 * 1024 * 1024
        }
    ),
    % An application returns its top supervisor (OTP): with the listener returned instead, the
    % application never finished stopping on SIGTERM and was only killed after the stop timeout
    websocket_cowboy_sup:start_link().

stop(_State) ->
    cowboy:stop_listener(http),
    ok.

dispatch() ->
    cowboy_router:compile([
        {'_', [
            {"/", static_handler, []},
            {"/ws", websocket_handler, []}
        ]}
    ]). 