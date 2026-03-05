#!/bin/sh
# Entrypoint: run migrations then start the application
set -e

echo "🔄 Running alembic migrations..."
alembic upgrade head || echo "⚠️ Alembic migration skipped (may need manual intervention)"

echo "🚀 Starting uvicorn..."
exec "$@"
