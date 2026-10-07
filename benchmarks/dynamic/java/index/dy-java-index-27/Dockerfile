# Java reference server: same two-stage pattern as the BEAM servers
# Stage 1: build with Maven on Eclipse Temurin JDK 27, then a trimmed Java runtime (jlink) for the jar
FROM maven:3.10.0-eclipse-temurin-27 AS builder

WORKDIR /build
COPY pom.xml .
RUN mvn -q -B dependency:go-offline
COPY src ./src
RUN mvn -q -B package -DskipTests

# Only the Java modules the jar uses (jdeps)
RUN modules="$(jdeps --ignore-missing-deps --multi-release 27 --print-module-deps target/app.jar)" && \
    jlink --add-modules "$modules" --strip-debug --no-man-pages --no-header-files --compress=zip-6 \
          --output /build/jre

# Stage 2: minimal runtime — trixie-slim + same apt list as all containers
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y \
    libncurses6 \
    libssl3t64 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Server contract (framework README): the program and its runtime under /app
COPY --from=builder /build/jre /app/jre
COPY --from=builder /build/target/app.jar /app/app.jar
COPY index.html /app/index.html

# Runtime options come from JAVA_TOOL_OPTIONS; ERL_FLAGS variants (busy-waiting) do not apply here
LABEL wseb.options="JAVA_TOOL_OPTIONS"

EXPOSE 8001

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
