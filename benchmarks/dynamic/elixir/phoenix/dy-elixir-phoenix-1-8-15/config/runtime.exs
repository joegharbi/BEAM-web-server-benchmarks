import Config

# Server contract (framework README): listen on PORT, 8001 when it is unset. Read when the release
# starts (config.exs is read when it is built).
config :phoenix_dynamic, PhoenixDynamicWeb.Endpoint,
  http: [port: String.to_integer(System.get_env("PORT", "8001"))]
