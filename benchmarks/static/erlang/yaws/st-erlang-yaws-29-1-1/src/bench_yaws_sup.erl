-module(bench_yaws_sup).
-behaviour(supervisor).
-export([start_link/0, init/1]).

%% Top supervisor of the application; Yaws runs under its own supervisor (started embedded).
start_link() ->
    supervisor:start_link({local, ?MODULE}, ?MODULE, []).

init([]) ->
    {ok, {#{strategy => one_for_one}, []}}.
