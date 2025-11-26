#!/bin/bash

echo "Running Beanie migrations..."
beanie migrate -uri "${MONGO_URI:-$mongo_uri}" -db mediafusion -p migrations/

echo "Starting FastAPI server with optimized settings for seedbox..."
# Use fewer workers and threads to reduce memory consumption
# GUNICORN_WORKERS and GUNICORN_THREADS can be set via environment variables
WORKERS="${GUNICORN_WORKERS:-1}"
THREADS="${GUNICORN_THREADS:-2}"

echo "Using $WORKERS workers with $THREADS threads each"
gunicorn api.main:app \
  -w "$WORKERS" \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 180 \
  --max-requests 200 \
  --max-requests-jitter 50 \
  --worker-tmp-dir /dev/shm \
  --graceful-timeout 30 \
  --keep-alive 5
