defmodule PhoenixStatic.MixProject do
  use Mix.Project

  def project do
    [
      app: :phoenix_static,
      version: "0.1.0",
      elixir: "~> 1.20",
      start_permanent: Mix.env() == :prod,
      # Lean release (framework README, server contract): its own Erlang runtime, no node name
      releases: [phoenix_static: [include_executables_for: [:unix]]],
      deps: deps()
    ]
  end

  def application do
    [
      extra_applications: [:logger],
      mod: {PhoenixStatic.Application, []}
    ]
  end

  defp deps do
    [
      {:phoenix, "~> 1.8.15"},
      {:plug_cowboy, "~> 2.9"},
      {:jason, "~> 1.4"}
    ]
  end
end
