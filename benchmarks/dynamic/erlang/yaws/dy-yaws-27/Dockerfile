# Multi-stage build for optimized Yaws dynamic server
# Builder stage (minimal - just for any custom compilation if needed)
FROM erlang:27 AS builder

# Final stage - minimal runtime image
FROM debian:bookworm-slim

# Install Yaws and minimal runtime dependencies
RUN apt-get update && apt-get install -y \
    yaws \
    libncurses5 \
    libssl3 \
    ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Create a unified document root for serving web content
RUN mkdir -p /var/www/html && \
    chown -R www-data:www-data /var/www/html

# Add a dynamic .yaws file instead of static HTML
COPY index.yaws /var/www/html/index.yaws

# Copy the custom Yaws configuration file into the image
COPY yaws.conf /usr/local/etc/yaws/yaws.conf

# Expose port 80 for web traffic
EXPOSE 80

# Start Yaws as a daemon with the custom configuration file
CMD ["/bin/sh", "-c", "yaws --daemon --conf /usr/local/etc/yaws/yaws.conf && sleep infinity"]
