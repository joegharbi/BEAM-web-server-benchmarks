package bench;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Locale;

/**
 * Plain Java HTTP server (page with the current time), no framework: the JDK's own sockets, one accept loop, a virtual
 * thread per connection (Java's closest match to a BEAM process per connection); the same logic as
 * the Erlang, Elixir and Gleam pure servers. GET answers the page, POST answers 204.
 */
public final class Server {
    private static final DateTimeFormatter TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    public static void main(String[] args) throws IOException {
        // Server contract (framework README): listen on PORT, 8001 when it is unset
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8001"));
        try (ServerSocket listener = new ServerSocket()) {
            listener.setReuseAddress(true);
            listener.bind(new InetSocketAddress(port), 1024);
            while (true) {
                try {
                    Socket conn = listener.accept();
                    Thread.ofVirtual().start(() -> serve(conn));
                } catch (IOException e) {
                    System.err.println("Accept error: " + e);
                    sleep(1000);
                }
            }
        }
    }

    // Keep-alive: answer requests on the same connection until the client closes it,
    // asks to close it, or sends a body (not read by this server). 60 s idle timeout.
    static void serve(Socket conn) {
        try (conn) {
            conn.setSoTimeout(60_000);
            InputStream in = new BufferedInputStream(conn.getInputStream());
            OutputStream out = conn.getOutputStream();
            String head;
            while ((head = readHead(in)) != null) {
                boolean keep = keepAlive(head);
                out.write(response(method(head), keep));
                out.flush();
                if (!keep) {
                    return;
                }
            }
        } catch (IOException e) {
            // the client went away or was idle too long: the connection is closed
        }
    }

    // The request head, up to the blank line (without it); null when the connection ends first.
    // What follows stays in the buffer for the next request.
    static String readHead(InputStream in) throws IOException {
        ByteArrayOutputStream head = new ByteArrayOutputStream(512);
        byte[] end = {'\r', '\n', '\r', '\n'};
        int matched = 0;
        int b;
        while ((b = in.read()) != -1) {
            head.write(b);
            matched = b == end[matched] ? matched + 1 : (b == '\r' ? 1 : 0);
            if (matched == 4) {
                byte[] bytes = head.toByteArray();
                return new String(bytes, 0, bytes.length - 4, StandardCharsets.ISO_8859_1);
            }
        }
        return null;
    }

    static boolean keepAlive(String head) {
        int eol = head.indexOf("\r\n");
        String line = eol < 0 ? head : head.substring(0, eol);
        String lower = head.toLowerCase(Locale.ROOT);
        return line.endsWith("HTTP/1.1")
                && !lower.contains("connection: close")
                && !lower.contains("content-length:")
                && !lower.contains("transfer-encoding:");
    }

    static String method(String head) {
        int space = head.indexOf(' ');
        return space < 0 ? "GET" : head.substring(0, space);
    }

    static String connection(boolean keep) {
        return keep ? "keep-alive" : "close";
    }

    static byte[] response(String method, boolean keep) {
        if (method.equals("POST")) {
            return ("HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: " + connection(keep) + "\r\n\r\n")
                    .getBytes(StandardCharsets.ISO_8859_1);
        }
        return ok(page(), keep);
    }

    static byte[] ok(byte[] html, boolean keep) {
        byte[] head = ("HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: " + html.length
                + "\r\nConnection: " + connection(keep) + "\r\n\r\n").getBytes(StandardCharsets.ISO_8859_1);
        byte[] all = new byte[head.length + html.length];
        System.arraycopy(head, 0, all, 0, head.length);
        System.arraycopy(html, 0, all, head.length, html.length);
        return all;
    }

    static byte[] page() {
        return ("<!DOCTYPE html><html><head><title>Energy Test</title></head><body><h1>Hello, Energy Test!</h1>"
                + "<p>Current time: " + LocalDateTime.now().format(TIME) + "</p></body></html>")
                .getBytes(StandardCharsets.UTF_8);
    }

    static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
