package bench;

import io.netty.bootstrap.ServerBootstrap;
import io.netty.buffer.ByteBuf;
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
import io.netty.handler.codec.http.FullHttpResponse;
import io.netty.handler.codec.http.HttpHeaderNames;
import io.netty.handler.codec.http.HttpMethod;
import io.netty.handler.codec.http.HttpObjectAggregator;
import io.netty.handler.codec.http.HttpResponseStatus;
import io.netty.handler.codec.http.HttpServerCodec;
import io.netty.handler.codec.http.HttpServerKeepAliveHandler;
import io.netty.handler.codec.http.HttpVersion;
import java.nio.charset.StandardCharsets;

/**
 * Static HTTP server on Netty (the JVM reference): GET / answers the same page and headers as the
 * pure BEAM servers, POST answers 204; keep-alive. Netty as it comes: its default thread pools and
 * the portable NIO transport.
 */
public final class Server {
    private static final byte[] PAGE = ("<!DOCTYPE html><html><head><title>Energy Test</title></head>"
            + "<body><h1>Hello, Energy Test!</h1></body></html>").getBytes(StandardCharsets.UTF_8);

    public static void main(String[] args) throws InterruptedException {
        // Server contract (framework README): listen on PORT, 8001 when it is unset
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8001"));
        EventLoopGroup boss = new MultiThreadIoEventLoopGroup(1, NioIoHandler.newFactory());
        EventLoopGroup workers = new MultiThreadIoEventLoopGroup(NioIoHandler.newFactory());
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
                                    .addLast(new Page());
                        }
                    })
                    .bind(port).sync().channel().closeFuture().sync();
        } finally {
            boss.shutdownGracefully();
            workers.shutdownGracefully();
        }
    }

    static final class Page extends SimpleChannelInboundHandler<FullHttpRequest> {
        @Override
        protected void channelRead0(ChannelHandlerContext ctx, FullHttpRequest request) {
            FullHttpResponse response;
            if (HttpMethod.POST.equals(request.method())) {
                response = new DefaultFullHttpResponse(HttpVersion.HTTP_1_1, HttpResponseStatus.NO_CONTENT);
                response.headers().set(HttpHeaderNames.CONTENT_LENGTH, 0);
            } else {
                ByteBuf body = Unpooled.wrappedBuffer(PAGE);
                response = new DefaultFullHttpResponse(HttpVersion.HTTP_1_1, HttpResponseStatus.OK, body);
                response.headers()
                        .set(HttpHeaderNames.CONTENT_TYPE, "text/html; charset=utf-8")
                        .set(HttpHeaderNames.CONTENT_LENGTH, PAGE.length);
            }
            ctx.writeAndFlush(response);           // HttpServerKeepAliveHandler sets Connection and closes if asked
        }
    }
}
