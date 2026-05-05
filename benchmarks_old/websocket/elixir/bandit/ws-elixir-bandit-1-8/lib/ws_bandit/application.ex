defmodule WsBandit.Application do
  @moduledoc false
  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Bandit, plug: WsBandit.Router, scheme: :http, port: 80}
    ]

    Supervisor.start_link(children, strategy: :one_for_one, name: WsBandit.Supervisor)
  end
end
