#!/bin/sh
# Entrypoint for WORKER container (scheduler + background tasks)
#
# Differences from backend entrypoint:
# - Does NOT run migrations (backend handles that)
# - Runs single-process uvicorn (scheduler needs single instance)
set -e

echo "🔧 Starting DDS worker (scheduler + background tasks)..."
exec "$@"
