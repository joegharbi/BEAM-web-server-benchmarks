-module(websocket_cowboy_app).
-behaviour(application).

-export([start/2, stop/1]).

start(_StartType, _StartArgs) ->
    websocket_cowboy_sup:start_link(),
    % Canonical transport opts: num_acceptors=8, max_connections=100000 (see docs/CONFIGURATION_PARITY.md)
    cowboy:start_clear(http,
        [{port, 80}, {num_acceptors, 8}, {max_connections, 100000}],
        #{
            env => #{dispatch => dispatch()},
            max_frame_size => 64 * 1024 * 1024
        }
    ).

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