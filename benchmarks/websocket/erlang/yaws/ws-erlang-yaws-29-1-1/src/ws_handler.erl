-module(ws_handler).
-include_lib("yaws/include/yaws_api.hrl").
-export([out/1, handle_message/1, terminate/2]).

%% WebSocket echo (Yaws defaults to 16 MB messages; the benchmark sends up to 64 MB).
out(_Arg) ->
    Opts = [{max_frame_size, 128*1024*1024}, {max_message_size, 128*1024*1024}],
    {websocket, ws_handler, Opts}.
handle_message({text, Data}) ->
    {reply, {text, Data}};
handle_message({binary, Data}) ->
    {reply, {binary, Data}};
handle_message({close, Status, Reason}) ->
    {close, Status, Reason}.
terminate(_Reason, _State) ->
    ok.
