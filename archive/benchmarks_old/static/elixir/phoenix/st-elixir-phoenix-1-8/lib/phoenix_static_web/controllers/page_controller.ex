defmodule PhoenixStaticWeb.PageController do
  use PhoenixStaticWeb, :controller

  def index(conn, _params) do
    html(conn, """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Energy Test</title>
    </head>
    <body>
        <h1>Hello, Energy Test!</h1>
    </body>
    </html>
    """)
  end

  def post_index(conn, _params) do
    send_resp(conn, 204, "")
  end
end
