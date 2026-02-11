defmodule ElixirCowboyStatic.Application do
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Plug.Cowboy, scheme: :http, plug: ElixirCowboyStatic.Router, options: [port: 80]}
    ]

    opts = [strategy: :one_for_one, name: ElixirCowboyStatic.Supervisor]
    Supervisor.start_link(children, opts)
  end
end

