defmodule WsElixirCowboy.Application do
  @moduledoc false
  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {WsElixirCowboy.Server, []}
    ]
    Supervisor.start_link(children, strategy: :one_for_one, name: WsElixirCowboy.Supervisor)
  end
end
