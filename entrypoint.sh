#!/bin/sh
set -e

if [ "${1:-}" = "consumer" ]; then
  exec python -m app.workers.kafka_consumer
fi

if [ "${1:-}" = "worker" ]; then
  exec python -m app.workers.mail_sender
fi

echo "Running mail database migrations..."
alembic upgrade head

echo "Starting mail service..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
