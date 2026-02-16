# Minimal Yaws base: same two-stage pattern as all BEAM benchmarks (see docs/MINIMAL_BASES_AND_UNIFICATION.md)
# Stage 1: build — prepare config and content
FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y \
    yaws \
    libncurses5 \
    libssl3 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN mkdir -p etc/yaws www/html
COPY yaws.conf etc/yaws/yaws.conf
COPY index.yaws www/html/index.yaws

# Stage 2: minimal runtime — bookworm-slim + same apt list + yaws (no build-only deps in final)
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    yaws \
    libncurses5 \
    libssl3 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/www/html && chown -R www-data:www-data /var/www/html
COPY --from=builder /build/etc/yaws/yaws.conf /usr/local/etc/yaws/yaws.conf
COPY --from=builder /build/www/html/index.yaws /var/www/html/index.yaws

EXPOSE 80
CMD ["/bin/sh", "-c", "yaws --daemon --conf /usr/local/etc/yaws/yaws.conf && sleep infinity"]
