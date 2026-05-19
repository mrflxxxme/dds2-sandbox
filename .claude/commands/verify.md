---
description: "Верификация DDS2 перед коммитом: тесты, конвенции, безопасность. Аргумент quick — быстрый срез."
---

# /verify — проверка перед коммитом

## Режимы
- `/verify quick` — быстрый срез (~30 сек): импорты + pytest + конвенции, последовательно.
- `/verify` (по умолчанию) — полная проверка: тесты, конвенции, безопасность, frontend-сборка.

## quick
```bash
docker compose exec backend python -c "from backend.models import *; print('models OK')"
docker compose exec backend pytest tests/ -x --timeout=60 -q
bash scripts/check_conventions.sh
```

## full
1. **Backend-тесты:** `docker compose exec backend pytest tests/ -x --tb=short -q`
2. **Конвенции:** `bash scripts/check_conventions.sh`
3. **Безопасность** — grep по `backend/`:
   ```bash
   grep -rn 'text(f' --include="*.py" backend/ || echo OK     # SQL-инъекции
   grep -rn 'datetime.utcnow\|datetime.now(' --include="*.py" backend/ || echo OK
   grep -rn 'db.delete\|session.delete' --include="*.py" backend/ || echo OK
   grep -rn 'Float' --include="*.py" backend/models/ || echo OK
   ```
4. **Frontend-сборка** — если в diff есть `frontend-react/`: `cd frontend-react && npm run build`.

Тяжёлые шаги можно гонять параллельно фоновыми агентами.

## Отчёт
```
ВЕРИФИКАЦИЯ
  Тесты:        OK / FAIL (X/Y)
  Конвенции:    OK / FAIL
  Безопасность: OK / N проблем
  Сборка:       OK / FAIL / SKIP
  → готов к коммиту: ДА / НЕТ
```

Если backend-код менялся — после проверок прогони `/learn` для синхронизации документации.
