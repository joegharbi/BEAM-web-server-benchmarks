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

    # Canonical transport opts (docs/CONFIGURATION_PARITY.md): num_acceptors=8, max_connections=100000
    trans_opts = %{
      socket_opts: [port: 80],
      num_acceptors: 8,
      max_connections: 100_000
    }
    proto_opts = %{
      env: %{dispatch: dispatch},
      max_frame_size: 64 * 1024 * 1024
    }

    case :cowboy.start_clear(:http, trans_opts, proto_opts) do
      {:ok, _pid} -> {:ok, %{}}
      {:error, _} = err -> err
    end
  end
end
