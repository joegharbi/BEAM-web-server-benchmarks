defmodule WsBandit.MixProject do
  use Mix.Project

  def project do
    [
      app: :ws_bandit,
      version: "0.1.0",
      elixir: "~> 1.20",
      start_permanent: Mix.env() == :prod,
      # Lean release (framework README, server contract): its own Erlang runtime, no node name
      releases: [ws_bandit: [include_executables_for: [:unix]]],
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
      {:bandit, "~> 1.12"},
      {:plug, "~> 1.20"},
      {:websock, "~> 0.5.3"},
      {:websock_adapter, "~> 0.6.0"}
    ]
  end
end
