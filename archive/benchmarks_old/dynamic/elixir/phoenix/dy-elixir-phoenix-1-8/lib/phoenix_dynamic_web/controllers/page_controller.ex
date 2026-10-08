defmodule PhoenixDynamicWeb.PageController do
  use PhoenixDynamicWeb, :controller

  def index(conn, _params) do
    {{y, mo, d}, {h, mi, s}} = :calendar.local_time()
    time = :io_lib.format("~4..0w-~2..0w-~2..0w ~2..0w:~2..0w:~2..0w", [y, mo, d, h, mi, s]) |> :erlang.iolist_to_binary()
    html(conn, """
    <!DOCTYPE html>
    <html>
      <head><title>Energy Test</title></head>
      <body>
        <h1>Hello, Energy Test!</h1>
        <p>Current time: #{time}</p>
      </body>
    </html>
    """)
  end

  def post_index(conn, _params) do
    send_resp(conn, 204, "")
  end
end
