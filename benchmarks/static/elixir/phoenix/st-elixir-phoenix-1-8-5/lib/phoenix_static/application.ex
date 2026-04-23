defmodule PhoenixStatic.Application do
  @moduledoc false
  use Application

  @impl true
  def start(_type, _args) do
    children = [
      PhoenixStaticWeb.Endpoint
    ]
    opts = [strategy: :one_for_one, name: PhoenixStatic.Supervisor]
    Supervisor.start_link(children, opts)
  end
end
