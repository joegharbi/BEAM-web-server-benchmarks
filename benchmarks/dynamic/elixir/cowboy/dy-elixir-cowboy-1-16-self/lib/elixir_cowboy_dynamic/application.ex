defmodule ElixirCowboyDynamic.Application do
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Plug.Cowboy, scheme: :http, plug: ElixirCowboyDynamic.Router, options: [port: 80]}
    ]

    opts = [strategy: :one_for_one, name: ElixirCowboyDynamic.Supervisor]
    Supervisor.start_link(children, opts)
  end
end

