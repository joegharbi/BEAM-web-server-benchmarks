defmodule ElixirIndexDynamic.Router do
  @moduledoc """
  Serves index.html from disk (index variant).
  - GET / -> 200 with file content from /var/www/html/index.html
  - POST / -> 204 No Content
  """

  use Plug.Router

  plug :match
  plug :dispatch

  get "/" do
    body = File.read!("/var/www/html/index.html")

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
