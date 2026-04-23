defmodule PhoenixStaticWeb.Endpoint do
  use Phoenix.Endpoint, otp_app: :phoenix_static
  plug PhoenixStaticWeb.Router
end
