import gleam/bytes_tree
import gleam/erlang/process
import gleam/http
import gleam/http/request.{type Request}
import gleam/http/response.{type Response}
import gleam/int
import gleam/string
import mist.{type Connection, type ResponseData}

pub fn main() {
  let handler = fn(req: Request(Connection)) -> Response(ResponseData) {
    case req.method {
      http.Post ->
        response.new(204)
        |> response.set_body(mist.Bytes(bytes_tree.new()))
      _ -> {
        let time_str = time_string()
        let html =
          "<!DOCTYPE html><html><head><title>Energy Test</title></head><body><h1>Hello, Energy Test!</h1><p>Current time: "
          <> time_str
          <> "</p></body></html>"
        response.new(200)
        |> response.set_header("content-type", "text/html; charset=utf-8")
        |> response.set_body(mist.Bytes(bytes_tree.from_string(html)))
      }
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
