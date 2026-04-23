defmodule PhoenixDynamic.MixProject do
  use Mix.Project

  def project do
    [
      app: :phoenix_dynamic,
      version: "0.1.0",
      elixir: "~> 1.19",
      start_permanent: Mix.env() == :prod,
      deps: deps()
    ]
  end

  def application do
    [
      extra_applications: [:logger],
      mod: {PhoenixDynamic.Application, []}
    ]
  end

  defp deps do
    [
      {:phoenix, "~> 1.8.5"},
      {:plug_cowboy, "~> 2.7"},
      {:jason, "~> 1.4"}
    ]
  end
end
