#!/usr/bin/env sh
set -eu

DATA_DIR="${DATA_DIR:-/data}"

mkdir -p "$DATA_DIR"

# Bootstrap buffers.yml
if [ ! -f "$DATA_DIR/buffers.yml" ] && [ -f "/app/buffers.yml.example" ]; then
  cp /app/buffers.yml.example "$DATA_DIR/buffers.yml"
  echo "[init] Created $DATA_DIR/buffers.yml from example"
fi

# Bootstrap trackers.yml
if [ ! -f "$DATA_DIR/trackers.yml" ] && [ -f "/app/trackers.yml.example" ]; then
  cp /app/trackers.yml.example "$DATA_DIR/trackers.yml"
  echo "[init] Created $DATA_DIR/trackers.yml from example"
fi

exec "$@"
