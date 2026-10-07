defmodule WsBandit.Application do
  @moduledoc false
  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Bandit, plug: WsBandit.Router, scheme: :http, port: port()}
    ]

    Supervisor.start_link(children, strategy: :one_for_one, name: WsBandit.Supervisor)
  end

  # Server contract (framework README): listen on PORT, 8001 when it is unset
  defp port, do: String.to_integer(System.get_env("PORT", "8001"))
end
