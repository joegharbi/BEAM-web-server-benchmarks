-module(static_handler).
-behaviour(cowboy_handler).

-export([init/2]).

init(Req0, State) ->
    Req = cowboy_req:reply(200, #{
        <<"content-type">> => <<"text/html">>
    }, <<"<!DOCTYPE html>
<html>
<head><title>WebSocket Cowboy Server</title></head>
<body>
<h1>WebSocket Cowboy Server</h1>
<p>This is a WebSocket-enabled Cowboy server for energy benchmarking.</p>
<p>Connect to /ws for WebSocket functionality.</p>
</body>
</html>">>, Req0),
    {ok, Req, State}. 