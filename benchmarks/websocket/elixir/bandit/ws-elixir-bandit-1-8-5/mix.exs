defmodule WsBandit.MixProject do
  use Mix.Project

  def project do
    [
      app: :ws_bandit,
      version: "0.1.0",
      elixir: "~> 1.19",
      start_permanent: Mix.env() == :prod,
      deps: deps()
    ]
  end

  def application do
    [
      extra_applications: [:logger],
      mod: {WsBandit.Application, []}
    ]
  end

  defp deps do
    [
      {:bandit, "~> 1.5"},
      {:plug, "~> 1.17"},
      {:websock, "~> 0.5"},
      {:websock_adapter, "~> 0.6"}
    ]
  end
end
