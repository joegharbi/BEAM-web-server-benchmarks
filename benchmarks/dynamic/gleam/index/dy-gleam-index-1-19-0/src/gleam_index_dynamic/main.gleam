// Index variant of the plain Gleam HTTP server, no framework: raw sockets (gen_tcp through server_ffi),
// one accept loop, a process per connection; reads the template priv/index.html on every request
// and fills in the current time.
// The same logic as the Erlang and Elixir index servers.
import gleam/bit_array
import gleam/erlang/process.{type Pid}
import gleam/int
import gleam/string

pub type Socket

pub fn main() {
  let listener = listen(port())
  accept_loop(listener)
}

fn accept_loop(listener: Socket) -> Nil {
  case accept(listener) {
    Ok(conn) -> {
      let pid = process.spawn_unlinked(fn() { serve(conn, <<>>) })
      give_away(conn, pid)
    }
    Error(_) -> process.sleep(1000)
  }
  accept_loop(listener)
}

// Keep-alive: answer requests on the same connection until the client closes it,
// asks to close it, or sends a body (not read by this server). 60 s idle timeout.
fn serve(conn: Socket, buffer: BitArray) -> Nil {
  case read_request(conn, buffer) {
    Ok(#(head, rest)) -> {
      let head = bit_array.to_string(head) |> result_or("")
      let keep = keep_alive(head)
      send(conn, bit_array.from_string(response(method(head), keep)))
      case keep {
        True -> serve(conn, rest)
        False -> close(conn)
      }
    }
    Error(_) -> close(conn)
  }
}

fn read_request(conn: Socket, acc: BitArray) -> Result(#(BitArray, BitArray), Nil) {
  case split_head(acc) {
    Ok(parts) -> Ok(parts)
    Error(_) ->
      case recv(conn, 60_000) {
        Ok(data) -> read_request(conn, bit_array.append(acc, data))
        Error(_) -> Error(Nil)
      }
  }
}

fn keep_alive(head: String) -> Bool {
  let line = case string.split_once(head, "\r\n") {
    Ok(#(first, _)) -> first
    Error(_) -> head
  }
  let lower = string.lowercase(head)
  string.ends_with(line, "HTTP/1.1")
  && !string.contains(lower, "connection: close")
  && !string.contains(lower, "content-length:")
  && !string.contains(lower, "transfer-encoding:")
}

fn method(head: String) -> String {
  case string.split_once(head, " ") {
    Ok(#(m, _)) -> m
    Error(_) -> "GET"
  }
}

fn connection(keep: Bool) -> String {
  case keep {
    True -> "keep-alive"
    False -> "close"
  }
}

fn response(method: String, keep: Bool) -> String {
  case method {
    "POST" ->
      "HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: " <> connection(keep) <> "\r\n\r\n"
    _ ->
      case page() {
        Ok(html) -> ok(html, keep)
        Error(_) ->
          "HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
      }
  }
}

fn ok(html: String, keep: Bool) -> String {
  "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
  <> int.to_string(string.byte_size(html))
  <> "\r\nConnection: "
  <> connection(keep)
  <> "\r\n\r\n"
  <> html
}

// The template ships with the program (priv/index.html of the shipment), read on every request
fn page() -> Result(String, Nil) {
  case read_file(index_path()) {
    Ok(bytes) ->
      case bit_array.to_string(bytes) {
        Ok(template) -> Ok(string.replace(template, "{{time}}", time_string()))
        Error(_) -> Error(Nil)
      }
    Error(_) -> Error(Nil)
  }
}

fn time_string() -> String {
  let #(#(y, mo, d), #(h, mi, s)) = local_time()
  let pad = fn(n, width) { string.pad_start(int.to_string(n), width, "0") }
  pad(y, 4) <> "-" <> pad(mo, 2) <> "-" <> pad(d, 2) <> " " <> pad(h, 2) <> ":" <> pad(mi, 2) <> ":" <> pad(s, 2)
}

@external(erlang, "server_ffi", "local_time")
fn local_time() -> #(#(Int, Int, Int), #(Int, Int, Int))

fn result_or(r: Result(a, e), default: a) -> a {
  case r {
    Ok(v) -> v
    Error(_) -> default
  }
}

@external(erlang, "server_ffi", "port")
fn port() -> Int

@external(erlang, "server_ffi", "listen")
fn listen(port: Int) -> Socket

@external(erlang, "server_ffi", "accept")
fn accept(listener: Socket) -> Result(Socket, Nil)

@external(erlang, "server_ffi", "recv")
fn recv(conn: Socket, timeout: Int) -> Result(BitArray, Nil)

@external(erlang, "server_ffi", "send")
fn send(conn: Socket, data: BitArray) -> Nil

@external(erlang, "server_ffi", "close")
fn close(conn: Socket) -> Nil

@external(erlang, "server_ffi", "give_away")
fn give_away(conn: Socket, pid: Pid) -> Nil

@external(erlang, "server_ffi", "split_head")
fn split_head(data: BitArray) -> Result(#(BitArray, BitArray), Nil)

@external(erlang, "server_ffi", "index_path")
fn index_path() -> String

@external(erlang, "server_ffi", "read_file")
fn read_file(path: String) -> Result(BitArray, Nil)
