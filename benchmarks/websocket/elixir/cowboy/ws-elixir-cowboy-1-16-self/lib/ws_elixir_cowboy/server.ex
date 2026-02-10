defmodule WsElixirCowboy.Server do
  @moduledoc "Starts Cowboy with / and /ws (WebSocket echo)."
  use GenServer

  def start_link(opts \\ []) do
    GenServer.start_link(__MODULE__, opts, name: __MODULE__)
  end

  @impl true
  def init(_opts) do
    dispatch =
      :cowboy_router.compile([
        {:_,
         [
           {"/", WsElixirCowboy.StaticHandler, []},
           {"/ws", WsElixirCowboy.WebSocketHandler, []}
         ]}
      ])

    # Ranch 2.0 map format; max_connections omitted => Ranch default (1024)
    trans_opts = %{socket_opts: [port: 80]}
    proto_opts = %{env: %{dispatch: dispatch}}

    case :cowboy.start_clear(:http, trans_opts, proto_opts) do
      {:ok, _pid} -> {:ok, %{}}
      {:error, _} = err -> err
    end
  end
end
