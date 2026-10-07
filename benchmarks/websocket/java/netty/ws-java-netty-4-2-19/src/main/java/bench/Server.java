package bench;

import io.netty.bootstrap.ServerBootstrap;
import io.netty.buffer.Unpooled;
import io.netty.channel.ChannelHandlerContext;
import io.netty.channel.ChannelInitializer;
import io.netty.channel.EventLoopGroup;
import io.netty.channel.MultiThreadIoEventLoopGroup;
import io.netty.channel.SimpleChannelInboundHandler;
import io.netty.channel.nio.NioIoHandler;
import io.netty.channel.socket.SocketChannel;
import io.netty.channel.socket.nio.NioServerSocketChannel;
import io.netty.handler.codec.http.DefaultFullHttpResponse;
import io.netty.handler.codec.http.FullHttpRequest;
import io.netty.handler.codec.http.HttpHeaderNames;
import io.netty.handler.codec.http.HttpObjectAggregator;
import io.netty.handler.codec.http.HttpResponseStatus;
import io.netty.handler.codec.http.HttpServerCodec;
import io.netty.handler.codec.http.HttpServerKeepAliveHandler;
import io.netty.handler.codec.http.HttpVersion;
import io.netty.handler.codec.http.websocketx.BinaryWebSocketFrame;
import io.netty.handler.codec.http.websocketx.TextWebSocketFrame;
import io.netty.handler.codec.http.websocketx.WebSocketFrame;
import io.netty.handler.codec.http.websocketx.WebSocketFrameAggregator;
import io.netty.handler.codec.http.websocketx.WebSocketServerProtocolConfig;
import io.netty.handler.codec.http.websocketx.WebSocketServerProtocolHandler;
import java.nio.charset.StandardCharsets;

/**
 * WebSocket echo server on Netty (the JVM reference): every message on /ws is sent back, up to 128 MB
 * like the BEAM WebSocket servers; GET / answers a small page. Netty as it comes: its default thread
 * pools and the portable NIO transport.
 */
public final class Server {
    private static final int MAX = 128 * 1024 * 1024;
    private static final byte[] PAGE = ("<!DOCTYPE html><html><head><title>WebSocket Netty</title></head>"
            + "<body><h1>WebSocket Netty Server</h1><p>Connect to /ws for WebSocket echo.</p></body></html>")
            .getBytes(StandardCharsets.UTF_8);

    public static void main(String[] args) throws InterruptedException {
        // Server contract (framework README): listen on PORT, 8001 when it is unset
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8001"));
        EventLoopGroup boss = new MultiThreadIoEventLoopGroup(1, NioIoHandler.newFactory());
        EventLoopGroup workers = new MultiThreadIoEventLoopGroup(NioIoHandler.newFactory());
        WebSocketServerProtocolConfig ws = WebSocketServerProtocolConfig.newBuilder()
                .websocketPath("/ws")
                .maxFramePayloadLength(MAX)
                .build();
        try {
            new ServerBootstrap()
                    .group(boss, workers)
                    .channel(NioServerSocketChannel.class)
                    .childHandler(new ChannelInitializer<SocketChannel>() {
                        @Override
                        protected void initChannel(SocketChannel ch) {
                            ch.pipeline()
                                    .addLast(new HttpServerCodec())
                                    .addLast(new HttpServerKeepAliveHandler())
                                    .addLast(new HttpObjectAggregator(1 << 20))
                                    .addLast(new WebSocketServerProtocolHandler(ws))
                                    .addLast(new WebSocketFrameAggregator(MAX))
                                    .addLast(new Echo())
                                    .addLast(new Page());
                        }
                    })
                    .bind(port).sync().channel().closeFuture().sync();
        } finally {
            boss.shutdownGracefully();
            workers.shutdownGracefully();
        }
    }

    /** Text and binary messages back as they came (pings and closes are answered by Netty). */
    static final class Echo extends SimpleChannelInboundHandler<WebSocketFrame> {
        @Override
        protected void channelRead0(ChannelHandlerContext ctx, WebSocketFrame frame) {
            if (frame instanceof TextWebSocketFrame || frame instanceof BinaryWebSocketFrame) {
                ctx.writeAndFlush(frame.retain());
            }
        }
    }

    /** Plain HTTP requests (not on /ws): a small page. */
    static final class Page extends SimpleChannelInboundHandler<FullHttpRequest> {
        @Override
        protected void channelRead0(ChannelHandlerContext ctx, FullHttpRequest request) {
            DefaultFullHttpResponse response = new DefaultFullHttpResponse(
                    HttpVersion.HTTP_1_1, HttpResponseStatus.OK, Unpooled.wrappedBuffer(PAGE));
            response.headers()
                    .set(HttpHeaderNames.CONTENT_TYPE, "text/html; charset=utf-8")
                    .set(HttpHeaderNames.CONTENT_LENGTH, PAGE.length);
            ctx.writeAndFlush(response);
        }
    }
}
