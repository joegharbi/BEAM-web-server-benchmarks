import gleam/bytes_tree
import gleam/erlang/process
import gleam/http/request
import gleam/http/response
import gleam/option.{None}
import mist.{type Connection, type ResponseData}

const index_html =
  "<!DOCTYPE html><html><head><title>WebSocket Gleam Mist</title></head><body><h1>WebSocket Gleam Mist Server</h1><p>Connect to /ws for WebSocket echo.</p></body></html>"

pub fn main() {
  let handler = fn(req: request.Request(Connection)) -> response.Response(ResponseData) {
    case request.path_segments(req) {
      ["ws"] ->
        mist.websocket(
          request: req,
          on_init: fn(_conn) { #(Nil, None) },
          on_close: fn(_state) { Nil },
          handler: handle_ws_message,
        )
      _ ->
        response.new(200)
        |> response.set_header("content-type", "text/html; charset=utf-8")
        |> response.set_body(mist.Bytes(bytes_tree.from_string(index_html)))
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

fn handle_ws_message(state, message, conn) {
  case message {
    mist.Text(msg) -> {
      let assert Ok(_) = mist.send_text_frame(conn, msg)
      mist.continue(state)
    }
    mist.Binary(msg) -> {
      let assert Ok(_) = mist.send_binary_frame(conn, msg)
      mist.continue(state)
    }
    mist.Closed | mist.Shutdown -> mist.stop()
    mist.Custom(_) -> mist.continue(state)
  }
}
