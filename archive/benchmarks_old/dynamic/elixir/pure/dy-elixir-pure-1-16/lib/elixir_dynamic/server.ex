defmodule ElixirDynamic.Server do
  @moduledoc """
  Minimal dynamic HTTP server using :gen_tcp in pure Elixir.

  - GET / returns HTML with current local time embedded
  - POST / returns 204 No Content
  """

  require Logger

  def start(port) when is_integer(port) do
    {:ok, socket} =
      :gen_tcp.listen(port,
        [:binary, packet: :raw, active: false, reuseaddr: true]
      )

    Logger.info("ElixirDynamic.Server listening on port #{port}")
    accept_loop(socket)
  end

  defp accept_loop(socket) do
    case :gen_tcp.accept(socket) do
      {:ok, client} ->
        spawn(fn -> handle_client(client) end)
        accept_loop(socket)

      {:error, reason} ->
        Logger.error("Accept error: #{inspect(reason)}")
        :timer.sleep(1000)
        accept_loop(socket)
    end
  end

  defp handle_client(socket) do
    case read_request(socket, "") do
      {:ok, request} ->
        method = parse_method(request)
        send_response(socket, method)

      {:error, _} ->
        :ok
    end

    :gen_tcp.close(socket)
  end

  defp read_request(socket, acc) do
    case :gen_tcp.recv(socket, 0, 5000) do
      {:ok, data} ->
        new_acc = acc <> data

        if String.contains?(new_acc, "\r\n\r\n") do
          {:ok, new_acc}
        else
          read_request(socket, new_acc)
        end

      {:error, reason} ->
        Logger.debug("recv error: #{inspect(reason)}")
        {:error, reason}
    end
  end

  defp parse_method(request) do
    case String.split(request, " ", parts: 2) do
      [method, _] -> method
      _ -> "GET"
    end
  end

  defp send_response(socket, "POST") do
    response = """
    HTTP/1.1 204 No Content\r
    Content-Length: 0\r
\r
    """

    :gen_tcp.send(socket, response)
  end

  defp send_response(socket, _method) do
    body = dynamic_body()
    length = byte_size(body)

    header = """
    HTTP/1.1 200 OK\r
    Content-Type: text/html; charset=utf-8\r
    Content-Length: #{length}\r
\r
    """

    :gen_tcp.send(socket, header <> body)
  end

  defp dynamic_body do
    {{year, month, day}, {hour, min, sec}} = :calendar.local_time()

    time =
      :io_lib.format("~4..0w-~2..0w-~2..0w ~2..0w:~2..0w:~2..0w", [year, month, day, hour, min, sec])
      |> :erlang.iolist_to_binary()

    """
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
  end
end

