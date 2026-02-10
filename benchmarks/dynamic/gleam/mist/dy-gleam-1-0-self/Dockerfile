FROM ghcr.io/gleam-lang/gleam:v1.14.0-erlang-alpine

WORKDIR /app

COPY gleam.toml ./
COPY src ./src

# Pre-download deps so container start is fast
RUN gleam deps download

COPY start.sh ./
RUN chmod +x start.sh

RUN addgroup -S webapp && adduser -S webapp -G webapp
RUN chown -R webapp:webapp /app
USER webapp

EXPOSE 80

# Use gleam run (shipment has OTP module naming issues in this image)
ENTRYPOINT ["/app/start.sh"]
