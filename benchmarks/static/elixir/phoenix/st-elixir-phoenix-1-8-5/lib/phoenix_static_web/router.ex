defmodule PhoenixStaticWeb.Router do
  use Phoenix.Router

  get "/", PhoenixStaticWeb.PageController, :index
  post "/", PhoenixStaticWeb.PageController, :post_index
end
