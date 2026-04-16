---
description: "Быстрая проверка статуса проекта — git, docker, backend, миграции, тесты."
---

# Project Status Check

Запусти эти команды и покажи результат как дашборд:

1. **Git**: `git status --short && git log --oneline -3`
2. **Docker**: `docker compose ps --format 'table {{.Name}}\t{{.Status}}'`
3. **Backend health**: `curl -s http://localhost:8000/health | python3 -m json.tool`
4. **Миграции**: `docker compose exec -T backend alembic current`
5. **Тесты**: `docker compose exec -T backend pytest --co -q 2>/dev/null | tail -3`

Покажи результат в виде компактной таблицы с статусами OK/WARN/FAIL.
