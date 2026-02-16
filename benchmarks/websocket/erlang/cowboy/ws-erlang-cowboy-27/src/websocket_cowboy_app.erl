-module(websocket_cowboy_app).
-behaviour(application).

-export([start/2, stop/1]).

start(_StartType, _StartArgs) ->
    websocket_cowboy_sup:start_link(),
    cowboy:start_clear(http,
        [{port, 80}],
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