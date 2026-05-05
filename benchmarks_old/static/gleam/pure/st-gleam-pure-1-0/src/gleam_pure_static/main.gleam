import gleam/bytes_tree
import gleam/erlang/process
import gleam/http
import gleam/http/request.{type Request}
import gleam/http/response.{type Response}
import mist.{type Connection, type ResponseData}

const static_html =
  "<!DOCTYPE html><html><head><title>Energy Test</title></head><body><h1>Hello, Energy Test!</h1></body></html>"

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

  let assert Ok(_) =
    handler
    |> mist.new
    |> mist.port(80)
    |> mist.bind("0.0.0.0")
    |> mist.start

  process.sleep_forever()
}
