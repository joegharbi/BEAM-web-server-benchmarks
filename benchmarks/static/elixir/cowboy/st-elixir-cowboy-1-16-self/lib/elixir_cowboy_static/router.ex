defmodule ElixirCowboyStatic.Router do
  @moduledoc """
  Minimal Plug router on Cowboy for static HTTP:
  - GET / -> 200 with static HTML
  - POST / -> 204 No Content
  """

  use Plug.Router

  plug :match
  plug :dispatch

  get "/" do
    body = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>Energy Test</title>
      </head>
      <body>
        <h1>Hello, Energy Test!</h1>
      </body>
    </html>
    """

    conn
    |> Plug.Conn.put_resp_content_type("text/html; charset=utf-8")
    |> send_resp(200, body)
  end

  post "/" do
    send_resp(conn, 204, "")
  end

  match _ do
    send_resp(conn, 404, "Not found")
  end
end

