defmodule ElixirIndexStatic.Application do
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Plug.Cowboy, scheme: :http, plug: ElixirIndexStatic.Router,
       options: [port: 80, transport_options: [num_acceptors: 8, max_connections: 100_000]]}
    ]

    opts = [strategy: :one_for_one, name: ElixirIndexStatic.Supervisor]
    Supervisor.start_link(children, opts)
  end
end
