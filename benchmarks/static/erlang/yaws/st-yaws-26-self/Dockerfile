# Multi-stage build for optimized Yaws static server
# Builder stage (minimal - just for any custom compilation if needed)
FROM erlang:26 AS builder

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

# Embed a simple index.html file directly in the image
RUN echo "<!DOCTYPE html>" > /var/www/html/index.html && \
    echo "<html>" >> /var/www/html/index.html && \
    echo "  <head>" >> /var/www/html/index.html && \
    echo "    <title>Energy Test</title>" >> /var/www/html/index.html && \
    echo "  </head>" >> /var/www/html/index.html && \
    echo "  <body>" >> /var/www/html/index.html && \
    echo "    <h1>Hello, Energy Test!</h1>" >> /var/www/html/index.html && \
    echo "  </body>" >> /var/www/html/index.html && \
    echo "</html>" >> /var/www/html/index.html

# Copy the custom Yaws configuration file into the image
COPY yaws.conf /usr/local/etc/yaws/yaws.conf

# Expose port 80 for web traffic
EXPOSE 80

# Start Yaws as a daemon with the custom configuration file
CMD ["/bin/sh", "-c", "ulimit -n 100000 && yaws --daemon --conf /usr/local/etc/yaws/yaws.conf && sleep infinity"]
