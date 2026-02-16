defmodule PhoenixDynamicWeb.Router do
  use Phoenix.Router

  get "/", PhoenixDynamicWeb.PageController, :index
  post "/", PhoenixDynamicWeb.PageController, :post_index
end
