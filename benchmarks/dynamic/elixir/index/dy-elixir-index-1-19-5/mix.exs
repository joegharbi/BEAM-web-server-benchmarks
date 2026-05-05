defmodule ElixirIndexDynamic.MixProject do
  use Mix.Project

  def project do
    [
      app: :elixir_index_dynamic,
      version: "0.1.0",
      elixir: "~> 1.19",
      start_permanent: Mix.env() == :prod,
      deps: deps()
    ]
  end

  def application do
    [
      extra_applications: [:logger],
      mod: {ElixirIndexDynamic.Application, []}
    ]
  end

  defp deps, do: []
end
