#!/bin/sh
ulimit -n 100000
# The jar on its own trimmed Java runtime (jlink) under APP_DIR; options come from JAVA_TOOL_OPTIONS.
# APP_DIR: /app in the container; native mode (tools/native_server.py) runs this same script on a copy.
APP_DIR="${APP_DIR:-/app}"
exec "$APP_DIR/jre/bin/java" -jar "$APP_DIR/app.jar"
