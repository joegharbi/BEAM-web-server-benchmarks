defmodule ElixirIndexStatic.Server do
  @moduledoc """
  Minimal static HTTP server using :gen_tcp in pure Elixir.
  """

  require Logger

  @index_path "/var/www/html/index.html"

  def start(port) when is_integer(port) do
    {:ok, socket} = :gen_tcp.listen(port, [:binary, packet: :raw, active: false, reuseaddr: true])
    Logger.info("ElixirIndexStatic.Server listening on port #{port}")
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
      {:ok, request} -> send_response(socket, parse_method(request))
      {:error, _} -> :ok
    end

    :gen_tcp.close(socket)
  end

  defp read_request(socket, acc) do
    case :gen_tcp.recv(socket, 0, 5000) do
      {:ok, data} ->
        new_acc = acc <> data
        if String.contains?(new_acc, "\r\n\r\n"), do: {:ok, new_acc}, else: read_request(socket, new_acc)

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
    :gen_tcp.send(socket, "HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
  end

  defp send_response(socket, _method) do
    body = File.read!(@index_path)
    header = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: #{byte_size(body)}\r\n\r\n"
    :gen_tcp.send(socket, header <> body)
  end
end
