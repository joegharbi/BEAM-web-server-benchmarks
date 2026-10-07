-module(hello_handler).
-behaviour(cowboy_handler).

-export([init/2]).

%% GET / returns a page with the current time, POST / returns 204 (like the other dynamic servers).
init(Req0, State) ->
    case {cowboy_req:method(Req0), cowboy_req:path(Req0)} of
        {<<"POST">>, <<"/">>} ->
            Req = cowboy_req:reply(204, Req0),
            {ok, Req, State};
        {_, <<"/">>} ->
            % Get current time for dynamic content
            Now = erlang:localtime(),
            {{Year,Month,Day},{Hour,Min,Sec}} = Now,
            TimeStr = io_lib:format("~4..0w-~2..0w-~2..0w ~2..0w:~2..0w:~2..0w",
                                    [Year, Month, Day, Hour, Min, Sec]),
            Req = cowboy_req:reply(200, #{
                <<"content-type">> => <<"text/html">>
            }, <<"<!DOCTYPE html>
<html>
<head>
    <title>Energy Test</title>
</head>
<body>
    <h1>Hello, Energy Test!</h1>
    <p>Current time: ", (list_to_binary(TimeStr))/binary, "</p>
</body>
</html>">>, Req0),
            {ok, Req, State};
        _ ->
            Req = cowboy_req:reply(404, #{
                <<"content-type">> => <<"text/plain">>
            }, <<"Not Found">>, Req0),
            {ok, Req, State}
    end.