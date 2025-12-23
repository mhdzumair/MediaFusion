#!/bin/bash

echo "Running Alembic PostgreSQL migrations..."
alembic upgrade head

echo "Starting FastAPI server..."
gunicorn api.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 120 --max-requests 500 --max-requests-jitter 200
