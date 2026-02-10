-module(hello_handler).
-export([init/2]).

init(Req0, State) ->
    Req = cowboy_req:reply(200,
        #{<<"content-type">> => <<"text/html">>},
        <<"<!DOCTYPE html>
<html>
<head>
    <title>Energy Test</title>
</head>
<body>
    <h1>Hello, Energy Test!</h1>
</body>
</html>">>,
        Req0),
    {ok, Req, State}.