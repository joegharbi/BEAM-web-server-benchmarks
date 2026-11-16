package bench;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Plain Java HTTP server (static page) as an event loop, no framework: one accept loop and as many worker
 * threads as the server has CPUs; each worker owns many connections and answers every request that is ready
 * when it wakes up (java.nio selector). The request logic is that of the framework's st-java-pure server
 * (a virtual thread per connection); only the way connections are served differs. No idle timeout.
 * Experiment experiments/erlang-variants.
 */
public final class Server {
    private static final byte[] PAGE = ("<!DOCTYPE html><html><head><title>Energy Test</title></head>"
            + "<body><h1>Hello, Energy Test!</h1></body></html>").getBytes(StandardCharsets.UTF_8);

    /** The number of workers, from WORKERS: "cpus" (the default: the CPUs the server may use), "cpus+N", "cpus-N",
     *  "cpus*N" or a plain number; at least 1. */
    static int workerCount() {
        int cpus = Runtime.getRuntime().availableProcessors();
        String w = System.getenv().getOrDefault("WORKERS", "cpus").trim();
        int n = !w.startsWith("cpus") ? Integer.parseInt(w) : w.length() == 4 ? cpus
                : w.charAt(4) == '+' ? cpus + Integer.parseInt(w.substring(5))
                : w.charAt(4) == '-' ? cpus - Integer.parseInt(w.substring(5))
                : cpus * Integer.parseInt(w.substring(5));
        return Math.max(1, n);
    }

    public static void main(String[] args) throws IOException {
        // Server contract (framework README): listen on PORT, 8001 when it is unset
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8001"));
        Worker[] workers = new Worker[workerCount()];
        for (int i = 0; i < workers.length; i++) {
            workers[i] = new Worker();
            new Thread(workers[i], "worker-" + i).start();
        }
        System.out.println("java event loop, " + workers.length + " workers");
        try (ServerSocketChannel listener = ServerSocketChannel.open()) {
            listener.socket().setReuseAddress(true);
            listener.bind(new InetSocketAddress(port), 1024);
            for (long n = 0; ; n++) {
                try {
                    SocketChannel conn = listener.accept();
                    conn.configureBlocking(false);
                    workers[(int) (n % workers.length)].add(conn);
                } catch (IOException e) {
                    System.err.println("Accept error: " + e);
                }
            }
        }
    }

    static final class Worker implements Runnable {
        private final Selector selector;
        private final ConcurrentLinkedQueue<SocketChannel> added = new ConcurrentLinkedQueue<>();
        private final ByteBuffer in = ByteBuffer.allocate(64 * 1024);

        Worker() throws IOException {
            selector = Selector.open();
        }

        void add(SocketChannel conn) {
            added.add(conn);
            selector.wakeup();
        }

        @Override
        public void run() {
            while (true) {
                try {
                    selector.select();
                    SocketChannel conn;
                    while ((conn = added.poll()) != null) {
                        conn.register(selector, SelectionKey.OP_READ, new StringBuilder());
                    }
                    for (SelectionKey key : selector.selectedKeys()) {
                        if (key.isValid() && key.isReadable()) {
                            read(key);
                        }
                    }
                    selector.selectedKeys().clear();
                } catch (IOException e) {
                    System.err.println("Worker error: " + e);
                }
            }
        }

        // Read what is there, then answer every complete request; the rest stays in the connection's buffer.
        private void read(SelectionKey key) {
            SocketChannel conn = (SocketChannel) key.channel();
            StringBuilder buf = (StringBuilder) key.attachment();
            try {
                in.clear();
                int n = conn.read(in);
                if (n < 0) {
                    close(key);
                    return;
                }
                buf.append(new String(in.array(), 0, n, StandardCharsets.ISO_8859_1));
                int end;
                while ((end = buf.indexOf("\r\n\r\n")) >= 0) {
                    String head = buf.substring(0, end);
                    buf.delete(0, end + 4);
                    boolean keep = keepAlive(head);
                    ByteBuffer out = ByteBuffer.wrap(response(method(head), keep));
                    while (out.hasRemaining()) {
                        conn.write(out);
                    }
                    if (!keep) {
                        close(key);
                        return;
                    }
                }
            } catch (IOException e) {
                close(key);          // the client went away
            }
        }

        private static void close(SelectionKey key) {
            key.cancel();
            try {
                key.channel().close();
            } catch (IOException e) {
                // already closed
            }
        }
    }

    // ---- the request logic of st-java-pure, unchanged ----

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
        return ok(PAGE, keep);
    }

    static byte[] ok(byte[] html, boolean keep) {
        byte[] head = ("HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: " + html.length
                + "\r\nConnection: " + connection(keep) + "\r\n\r\n").getBytes(StandardCharsets.ISO_8859_1);
        byte[] all = new byte[head.length + html.length];
        System.arraycopy(head, 0, all, 0, head.length);
        System.arraycopy(html, 0, all, head.length, html.length);
        return all;
    }
}
