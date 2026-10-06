-module(bench_yaws_app).
-behaviour(application).
-export([start/2, stop/1]).

%% Yaws embedded in this release (page with the current time, index.yaws), with the settings of the former yaws.conf:
%% 8 acceptors, at most 100000 connections, no access log; the pages are under priv/www.
start(_StartType, _StartArgs) ->
    DocRoot = filename:join(code:priv_dir(bench_yaws), "www"),
    ServerConf = [{servername, "localhost"},
          {port, port()},
          {listen, {0, 0, 0, 0}},
          {flags, [{access_log, false}]}],
    GlobalConf = [{acceptor_pool_size, 8},
          {max_connections, 100000}],
    ok = yaws:start_embedded(DocRoot, ServerConf, GlobalConf, "bench"),
    bench_yaws_sup:start_link().

stop(_State) ->
    ok.

%% Server contract (framework README): listen on PORT, 8001 when it is unset
port() ->
    case os:getenv("PORT") of
        false -> 8001;
        Value -> list_to_integer(Value)
    end.
