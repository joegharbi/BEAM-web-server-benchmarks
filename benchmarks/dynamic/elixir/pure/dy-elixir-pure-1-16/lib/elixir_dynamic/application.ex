defmodule ElixirDynamic.Application do
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Task, fn -> ElixirDynamic.Server.start(80) end}
    ]

    opts = [strategy: :one_for_one, name: ElixirDynamic.Supervisor]
    Supervisor.start_link(children, opts)
  end
end

