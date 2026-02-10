defmodule ElixirStatic.Application do
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Task, fn -> ElixirStatic.Server.start(80) end}
    ]

    opts = [strategy: :one_for_one, name: ElixirStatic.Supervisor]
    Supervisor.start_link(children, opts)
  end
end

