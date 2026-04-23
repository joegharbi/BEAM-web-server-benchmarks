defmodule WsElixirCowboy.StaticHandler do
  @behaviour :cowboy_handler

  @impl true
  def init(req, state) do
    body = """
    <!DOCTYPE html>
    <html>
    <head><title>WebSocket Elixir Cowboy</title></head>
    <body>
    <h1>WebSocket Elixir Cowboy Server</h1>
    <p>Connect to /ws for WebSocket echo.</p>
    </body>
    </html>
    """
    reply = :cowboy_req.reply(200, %{"content-type" => "text/html"}, body, req)
    {:ok, reply, state}
  end
end
