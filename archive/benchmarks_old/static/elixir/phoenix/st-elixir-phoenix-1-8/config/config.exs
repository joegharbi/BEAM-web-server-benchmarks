import Config

config :phoenix_static, PhoenixStaticWeb.Endpoint,
  http: [
    ip: {0, 0, 0, 0},
    port: 80,
    transport_options: [num_acceptors: 8, max_connections: 100_000]
  ],
  server: true

config :logger, level: :info
