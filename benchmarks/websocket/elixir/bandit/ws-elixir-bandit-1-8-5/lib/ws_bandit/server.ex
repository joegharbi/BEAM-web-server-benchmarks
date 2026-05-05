defmodule WsBandit.Router do
  @moduledoc "Bandit + WebSockAdapter router exposing / and /ws."

  use Plug.Router

  plug :match
  plug :dispatch

  get "/" do
    body = """
    <!DOCTYPE html>
    <html>
    <head><title>WebSocket Elixir Bandit</title></head>
    <body>
    <h1>WebSocket Elixir Bandit Server</h1>
    <p>Connect to /ws for WebSocket echo.</p>
    </body>
    </html>
    """

    conn
    |> Plug.Conn.put_resp_content_type("text/html; charset=utf-8")
    |> Plug.Conn.send_resp(200, body)
  end

  get "/ws" do
    conn
    |> WebSockAdapter.upgrade(WsBandit.WebSocketHandler, %{}, timeout: 60_000)
    |> Plug.Conn.halt()
  end

  match _ do
    Plug.Conn.send_resp(conn, 404, "Not found")
  end
end
