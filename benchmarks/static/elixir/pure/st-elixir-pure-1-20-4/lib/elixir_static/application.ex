defmodule ElixirStatic.Application do
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      {Task, fn -> ElixirStatic.Server.start(port()) end}
    ]

    opts = [strategy: :one_for_one, name: ElixirStatic.Supervisor]
    Supervisor.start_link(children, opts)
  end

  # Server contract (framework README): listen on PORT, 8001 when it is unset
  defp port, do: String.to_integer(System.get_env("PORT", "8001"))
end
