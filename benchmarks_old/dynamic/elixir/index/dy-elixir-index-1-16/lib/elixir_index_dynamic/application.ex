defmodule ElixirIndexDynamic.Application do
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Task, fn -> ElixirIndexDynamic.Server.start(80) end}
    ]

    opts = [strategy: :one_for_one, name: ElixirIndexDynamic.Supervisor]
    Supervisor.start_link(children, opts)
  end
end
