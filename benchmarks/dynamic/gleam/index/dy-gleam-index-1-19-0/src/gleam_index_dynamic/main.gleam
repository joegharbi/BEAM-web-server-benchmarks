// Dynamic index: the template priv/index.html with the current time filled in on every request
import gleam/bytes_tree
import gleam/erlang/process
import gleam/http
import gleam/http/request.{type Request}
import gleam/http/response.{type Response}
import gleam/int
import gleam/string
import mist.{type Connection, type ResponseData}
import simplifile


pub fn main() {
  let handler = fn(req: Request(Connection)) -> Response(ResponseData) {
    case req.method {
      http.Post ->
        response.new(204)
        |> response.set_body(mist.Bytes(bytes_tree.new()))
      _ ->
        case simplifile.read(from: index_path()) {
          Ok(html) ->
            response.new(200)
            |> response.set_header("content-type", "text/html; charset=utf-8")
            |> response.set_body(
              mist.Bytes(bytes_tree.from_string(string.replace(html, "{{time}}", time_string()))),
            )
          Error(_) ->
            response.new(500)
            |> response.set_body(mist.Bytes(bytes_tree.from_string("Internal error")))
        }
    }
  }

  let builder =
    handler
    |> mist.new
    |> mist.port(port())
    |> mist.bind("0.0.0.0")

  let assert Ok(_) = mist.start(builder)

  process.sleep_forever()
}

// Server contract (framework README): listen on PORT, 8001 when it is unset
@external(erlang, "server_ffi", "port")
fn port() -> Int

// The page ships with the program: priv/index.html of the shipment (was /var/www/html, outside /app)
@external(erlang, "server_ffi", "index_path")
fn index_path() -> String

fn time_string() -> String {
  let #(date, time) = calendar_local_time()
  let #(y, mo, d) = date
  let #(h, mi, s) = time
  let pad2 = fn(n) {
    let s = int.to_string(n)
    case string.length(s) {
      1 -> "0" <> s
      _ -> s
    }
  }
  let pad4 = fn(n) {
    let s = int.to_string(n)
    case string.length(s) {
      1 -> "000" <> s
      2 -> "00" <> s
      3 -> "0" <> s
      _ -> s
    }
  }
  pad4(y) <> "-" <> pad2(mo) <> "-" <> pad2(d) <> " " <> pad2(h) <> ":" <> pad2(mi) <> ":" <> pad2(s)
}

@external(erlang, "calendar", "local_time")
fn calendar_local_time() -> #(#(Int, Int, Int), #(Int, Int, Int)) {
  #(#(0, 0, 0), #(0, 0, 0))
}
