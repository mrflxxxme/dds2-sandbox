#!/bin/sh
# Entry point for Claude Code MCP. Resolves its own directory so the
# .mcp.json command can be relative.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/.venv/bin/python" "$DIR/server.py"
