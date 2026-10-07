defmodule WsElixirCowboy.MixProject do
  use Mix.Project

  def project do
    [
      app: :ws_elixir_cowboy,
      version: "0.1.0",
      elixir: "~> 1.20",
      start_permanent: Mix.env() == :prod,
      # Lean release (framework README, server contract): its own Erlang runtime, no node name
      releases: [ws_elixir_cowboy: [include_executables_for: [:unix]]],
      deps: deps()
    ]
  end

  def application do
    [
      extra_applications: [:logger],
      mod: {WsElixirCowboy.Application, []}
    ]
  end

  defp deps do
    [
      {:plug_cowboy, "~> 2.9"}
    ]
  end
end
