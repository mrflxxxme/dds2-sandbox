---
name: verify
description: "Верификация DDS2 перед коммитом: тесты, конвенции, безопасность, frontend-проверка, ревью-фан-аут. Запускай ПРОАКТИВНО, когда закончен блок изменений кода в backend/ или frontend-react/ и дело идёт к коммиту/шипу, либо когда пользователь спрашивает «готово ли к коммиту / всё ли ок». Аргумент quick — быстрый срез (~30 сек)."
argument-hint: "[quick]"
---

# /verify — проверка перед коммитом

## Контекст (снят автоматически при вызове)
Изменения в дереве:
!`git status --porcelain | head -30`
Diff-стата:
!`git diff --stat HEAD | tail -15`

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
1. **Backend-тесты:** делегируй агенту `test-runner` (haiku, изолирует простыню логов от контекста) либо `docker compose exec -T backend pytest tests/ -x --tb=short -q`. НЕ запускай два pytest конкурентно — общая БД даёт ложные падения.
2. **Конвенции:** `bash scripts/check_conventions.sh`
3. **Безопасность** — grep по `backend/`:
   ```bash
   grep -rn 'text(f' --include="*.py" backend/ || echo OK     # SQL-инъекции
   grep -rn 'datetime.utcnow\|datetime.now(' --include="*.py" backend/ || echo OK
   grep -rn 'db.delete\|session.delete' --include="*.py" backend/ || echo OK
   grep -rn 'Float' --include="*.py" backend/models/ || echo OK
   ```
4. **Frontend-проверка** — если в diff есть `frontend-react/` (node на хосте НЕТ — только контейнер): `docker run --rm --entrypoint sh -v "$PWD/frontend-react":/app -v dds2_frontend_node_modules:/app/node_modules -w /app <FRONTEND_IMAGE_ID> -c 'npx tsc --noEmit'` (образ по ID: `docker images | grep frontend`; имя dds2-frontend-react ложно блокируется хуком). Либо делегируй `test-runner`. UI юзер проверяет вживую — браузерный прогон агенту запрещён.
5. **Ревью-фан-аут** — если менялся код: запусти `/review` (профильные субагенты на Opus 4.8 по матрице diff→агент, единый вердикт APPROVE/WARNING/BLOCK). BLOCK блокирует коммит. Для тяжёлого прогона — `Workflow({name:'review-deep'})`.

Тяжёлые шаги можно гонять параллельно фоновыми агентами.

## Отчёт
```
ВЕРИФИКАЦИЯ
  Тесты:        OK / FAIL (X/Y)
  Конвенции:    OK / FAIL
  Безопасность: OK / N проблем
  Сборка:       OK / FAIL / SKIP
  Ревью:        APPROVE / WARNING / BLOCK (C/H/M)
  → готов к коммиту: ДА / НЕТ
```

Если backend-код менялся — после проверок прогони `/learn` для синхронизации документации.
