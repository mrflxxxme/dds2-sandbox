#!/bin/sh
# Entrypoint: run migrations then start the application
# ВАЖНО: миграция должна упасть → контейнер не стартует → CD откатит.
# Ранее был `|| echo "skipped"` — это маскировало ошибку, контейнер стартовал
# на старой схеме → 500 ошибки на runtime у клиентов.
set -e

echo "🔄 Running alembic migrations..."
alembic upgrade head

echo "🚀 Starting app server..."
exec "$@"
