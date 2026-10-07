import gleam/bytes_tree
import gleam/erlang/process
import gleam/http
import gleam/http/request.{type Request}
import gleam/http/response.{type Response}
import mist.{type Connection, type ResponseData}

const static_html =
  "<!DOCTYPE html>\n<html>\n<head>\n    <title>Energy Test</title>\n</head>\n<body>\n    <h1>Hello, Energy Test!</h1>\n</body>\n</html>"

pub fn main() {
  let handler = fn(req: Request(Connection)) -> Response(ResponseData) {
    case req.method {
      http.Post ->
        response.new(204)
        |> response.set_body(mist.Bytes(bytes_tree.new()))
      _ ->
        response.new(200)
        |> response.set_header("content-type", "text/html; charset=utf-8")
        |> response.set_body(mist.Bytes(bytes_tree.from_string(static_html)))
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
