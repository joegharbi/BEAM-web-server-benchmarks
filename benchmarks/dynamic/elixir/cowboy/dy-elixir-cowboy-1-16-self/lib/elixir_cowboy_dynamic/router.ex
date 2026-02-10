defmodule ElixirCowboyDynamic.Router do
  @moduledoc """
  Minimal Plug router on Cowboy for dynamic HTTP:
  - GET / -> 200 with HTML including current time
  - POST / -> 204 No Content
  """

  use Plug.Router

  plug :match
  plug :dispatch

  get "/" do
    {{year, month, day}, {hour, min, sec}} = :calendar.local_time()

    time =
      :io_lib.format("~4..0w-~2..0w-~2..0w ~2..0w:~2..0w:~2..0w", [year, month, day, hour, min, sec])
      |> :erlang.iolist_to_binary()

    body = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>Energy Test</title>
      </head>
      <body>
        <h1>Hello, Energy Test!</h1>
        <p>Current time: #{time}</p>
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

