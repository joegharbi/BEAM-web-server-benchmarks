// Index variant: serves index.html from disk
import gleam/bytes_tree
import gleam/erlang/file
import gleam/erlang/process
import gleam/http
import gleam/http/request.{type Request}
import gleam/http/response.{type Response}
import mist.{type Connection, type ResponseData}

const index_path = "/var/www/html/index.html"

pub fn main() {
  let handler = fn(req: Request(Connection)) -> Response(ResponseData) {
    case req.method {
      http.Post ->
        response.new(204)
        |> response.set_body(mist.Bytes(bytes_tree.new()))
      _ ->
        case file.read(index_path) {
          Ok(html) ->
            response.new(200)
            |> response.set_header("content-type", "text/html; charset=utf-8")
            |> response.set_body(mist.Bytes(bytes_tree.from_string(html)))
          Error(_) ->
            response.new(500)
            |> response.set_body(mist.Bytes(bytes_tree.from_string("Internal error")))
        }
    }
  }

  let builder =
    handler
    |> mist.new
    |> mist.port(80)
    |> mist.bind("0.0.0.0")

  let assert Ok(_) = mist.start(builder)

  process.sleep_forever()
}
