defmodule PhoenixDynamic.Application do
  @moduledoc false
  use Application

  @impl true
  def start(_type, _args) do
    children = [
      PhoenixDynamicWeb.Endpoint
    ]
    opts = [strategy: :one_for_one, name: PhoenixDynamic.Supervisor]
    Supervisor.start_link(children, opts)
  end
end
