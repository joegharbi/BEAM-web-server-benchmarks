-module(echo_app).
-behaviour(application).
-export([start/2, stop/1]).

%% The server is one accept loop (echo:main/0); the application runs it in a linked process.
start(_StartType, _StartArgs) ->
    {ok, spawn_link(fun echo:main/0)}.

stop(_State) ->
    ok.
