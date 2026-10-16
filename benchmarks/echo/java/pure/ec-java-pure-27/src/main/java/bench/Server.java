package bench;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;

/**
 * TCP echo server in Java, no framework: the JDK's own sockets, one accept loop, a virtual thread per
 * connection (Java's closest match to a BEAM process per connection). Every byte received is sent back
 * until the client closes: the same behaviour as the C and Erlang echo servers (benchmarks/echo/).
 */
public final class Server {
    private static final int BUF_SIZE = 65536;

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
                    try {
                        Thread.sleep(1000);
                    } catch (InterruptedException ie) {
                        return;
                    }
                }
            }
        }
    }

    static void serve(Socket conn) {
        try (conn) {
            InputStream in = conn.getInputStream();
            OutputStream out = conn.getOutputStream();
            byte[] buf = new byte[BUF_SIZE];
            int n;
            while ((n = in.read(buf)) != -1) {
                out.write(buf, 0, n);
                out.flush();
            }
        } catch (IOException e) {
            // the client went away: the connection is closed
        }
    }
}
