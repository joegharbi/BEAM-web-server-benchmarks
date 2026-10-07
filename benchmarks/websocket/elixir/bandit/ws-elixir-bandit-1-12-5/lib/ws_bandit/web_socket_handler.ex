defmodule WsBandit.WebSocketHandler do
  @behaviour WebSock

  @impl WebSock
  def init(state), do: {:ok, state}

  @impl WebSock
  def handle_in({msg, [opcode: :text]}, state), do: {:push, {:text, msg}, state}

  @impl WebSock
  def handle_in({msg, [opcode: :binary]}, state), do: {:push, {:binary, msg}, state}

  @impl WebSock
  def handle_in(_frame, state), do: {:ok, state}

  @impl WebSock
  def handle_info(_msg, state), do: {:ok, state}

  @impl WebSock
  def terminate(_reason, _state), do: :ok
end
