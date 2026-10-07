// Index variant: serves index.html from disk
import gleam/bytes_tree
import gleam/erlang/process
import gleam/http
import gleam/http/request.{type Request}
import gleam/http/response.{type Response}
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
