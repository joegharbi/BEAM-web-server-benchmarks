-module(hello_app).
-behaviour(application).
-export([start/2, stop/1]).

%% The server is one accept loop (hello:main/1); the application runs it in a linked process.
start(_StartType, _StartArgs) ->
    {ok, spawn_link(fun() -> hello:main([]) end)}.

stop(_State) ->
    ok.
