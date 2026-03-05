#!/bin/sh
# Entrypoint: run migrations then start the application
set -e

echo "🔄 Running alembic migrations..."
alembic upgrade head

echo "🚀 Starting uvicorn..."
exec "$@"
