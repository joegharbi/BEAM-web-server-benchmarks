import Config

config :phoenix_static, PhoenixStaticWeb.Endpoint,
  http: [ip: {0, 0, 0, 0}, port: 80],
  server: true

config :logger, level: :info
