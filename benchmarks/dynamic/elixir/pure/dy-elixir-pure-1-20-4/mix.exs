defmodule ElixirDynamic.MixProject do
  use Mix.Project

  def project do
    [
      app: :elixir_dynamic,
      version: "0.1.0",
      elixir: "~> 1.20",
      start_permanent: Mix.env() == :prod,
      # Lean release (framework README, server contract): its own Erlang runtime, no node name
      releases: [elixir_dynamic: [include_executables_for: [:unix]]],
      deps: []
    ]
  end

  def application do
    [
      extra_applications: [:logger],
      mod: {ElixirDynamic.Application, []}
    ]
  end
end

