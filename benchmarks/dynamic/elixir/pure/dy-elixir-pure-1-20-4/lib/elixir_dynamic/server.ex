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
        [:binary, packet: :raw, active: false, reuseaddr: true, backlog: 1024]
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

  # Keep-alive: answer requests on the same connection until the client closes it,
  # asks to close it, or sends a body (not read by this server). 60 s idle timeout.
  defp handle_client(socket), do: serve(socket, "")

  defp serve(socket, buffer) do
    case read_request(socket, buffer) do
      {:ok, request, rest} ->
        keep_alive = keep_alive?(request)
        send_response(socket, parse_method(request), keep_alive)
        if keep_alive, do: serve(socket, rest), else: :gen_tcp.close(socket)

      {:error, _} ->
        :gen_tcp.close(socket)
    end
  end

  defp read_request(socket, acc) do
    case :binary.split(acc, "\r\n\r\n") do
      [request, rest] ->
        {:ok, request, rest}

      [_] ->
        case :gen_tcp.recv(socket, 0, 60_000) do
          {:ok, data} ->
            read_request(socket, acc <> data)

          {:error, reason} ->
            Logger.debug("recv error: #{inspect(reason)}")
            {:error, reason}
        end
    end
  end

  defp keep_alive?(request) do
    [line | _] = String.split(request, "\r\n", parts: 2)

    String.ends_with?(line, "HTTP/1.1") and
      not String.contains?(String.downcase(request), ["connection: close", "content-length:", "transfer-encoding:"])
  end

  defp connection(true), do: "keep-alive"
  defp connection(false), do: "close"

  defp parse_method(request) do
    case String.split(request, " ", parts: 2) do
      [method, _] -> method
      _ -> "GET"
    end
  end

  defp send_response(socket, "POST", keep_alive) do
    response = """
    HTTP/1.1 204 No Content\r
    Content-Length: 0\r
    Connection: #{connection(keep_alive)}\r
\r
    """

    :gen_tcp.send(socket, response)
  end

  defp send_response(socket, _method, keep_alive) do
    body = dynamic_body()
    length = byte_size(body)

    header = """
    HTTP/1.1 200 OK\r
    Content-Type: text/html; charset=utf-8\r
    Content-Length: #{length}\r
    Connection: #{connection(keep_alive)}\r
\r
    """

    :gen_tcp.send(socket, header <> body)
  end

  defp dynamic_body do
    {{year, month, day}, {hour, min, sec}} = :calendar.local_time()

    time =
      :io_lib.format("~4..0w-~2..0w-~2..0w ~2..0w:~2..0w:~2..0w", [year, month, day, hour, min, sec])
      |> :erlang.iolist_to_binary()

    # The same page, byte for byte, as the Erlang, Gleam and Java pure servers
    "<!DOCTYPE html><html><head><title>Energy Test</title></head><body><h1>Hello, Energy Test!</h1>" <>
      "<p>Current time: #{time}</p></body></html>"
  end
end

