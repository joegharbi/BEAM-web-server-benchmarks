defmodule WsElixirCowboy.WebSocketHandler do
  @behaviour :cowboy_websocket

  @impl true
  def init(req, state) do
    opts = %{idle_timeout: 60_000, max_frame_size: 64 * 1024 * 1024}
    {:cowboy_websocket, req, state, opts}
  end

  @impl true
  def websocket_init(state) do
    {:ok, state}
  end

  @impl true
  def websocket_handle({:text, msg}, state) do
    {:reply, {:text, msg}, state}
  end

  @impl true
  def websocket_handle({:binary, msg}, state) do
    {:reply, {:binary, msg}, state}
  end

  @impl true
  def websocket_handle(_frame, state) do
    {:ok, state}
  end

  @impl true
  def websocket_info(_info, state) do
    {:ok, state}
  end

  @impl true
  def terminate(_reason, _req, _state) do
    :ok
  end
end
