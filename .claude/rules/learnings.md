---
paths:
  - "**/*"
---

# DDS Learnings (накопленные решения)

## Ошибки и решения
- Alembic "multiple heads": `alembic merge heads -m "merge"` затем `alembic upgrade head`
- FastAPI 422: отсутствует Pydantic валидатор, проверь типы полей схемы
- Next.js hydration mismatch: убедись что сервер и клиент рендерят одинаковое начальное состояние
- Docker "port already in use": `docker compose down` затем `lsof -i :8000`
- PgBouncer prepared statements: `prepared_statement_cache_size=0`, используй `DATABASE_URL_SYNC` для Alembic
- Redis ConnectionError: проверь `REDIS_URL` в .env и что redis контейнер запущен
- ilike injection (P4): ВСЕГДА экранируй `%` и `_` в пользовательском вводе для ILIKE
- Case-sensitive JOINs (P26): коды WB могут отличаться регистром — нормализуй перед JOIN

## Паттерны которые работают
- Новый API endpoint: schema → service → router → test (в этом порядке)
- Новая страница: скопируй ближайшую существующую, модифицируй
- DB миграция: тестируй `upgrade head && downgrade -1 && upgrade head`
- Кэш: после мутации вызывай `invalidate_cache(prefix)` — суффикс `:*` добавляется автоматически
- Отчёты с датами (P25): привязывай rolling period к дате запроса, не к "сегодня"
- **Pending-файл паттерн** (`.claude/.pending-learn.log`): post-hook пишет, отдельный потребитель читает/чистит. Развязывает синхронные хуки от асинхронной обработки, не порождает рекурсию параллельных claude-процессов
- **`git blame --porcelain -L line,line -- file | awk '/^author-time/ {print $2; exit}'`** — для получения timestamp когда строка добавлена. Быстрее и стабильнее в CI чем `git log -L`
- **Anthropic prompt caching** — `system` как `[{"type":"text","text":...,"cache_control":{"type":"ephemeral"}}]` + `cache_control` на последнем tool. ~90% экономия токенов на повторных вызовах в 5-мин TTL. Backward-compat через kwarg `enable_cache=True` default
- **Idempotent GH issue management** в sentinels — `listForRepo` с лейблом → если есть открытый issue с тем же префиксом title → `update` вместо `create`. Auto-close с комментом «passing again» при восстановлении
- **Opt-in feature через env флаг** (`DDS_PREWARM_ENABLED=1`) — позволяет постепенный rollout инфраструктурных фич, безопасно тестить на одном окружении
- **Anti-recursion через child env** (`DDS_X_ACTIVE=1`) — родительский процесс ставит флаг для child, child проверяет в начале и выходит, чтобы не self-trigger
- **Conditional `claude_args` через GitHub Actions expression** — `${{ contains(labels.*.name, 'security') && '--model opus' || '--model sonnet' }}` для динамического model routing по лейблам PR

## Антипаттерны (не повторять)
- `SELECT *` в продакшн запросах — всегда указывай колонки
- Пропуск type annotations на возвращаемых значениях сервисов
- Создание миграции без проверки `alembic heads`
- `.scalars().all()` без `.limit()` на больших таблицах
- `except Exception` без `asyncio.CancelledError` в scheduler jobs
- **Ранний `exit 0` в shell hook на основе одного условия блокирует все последующие блоки.** При добавлении нового блока в существующий hook — убедись что предыдущие условия не вылетают раньше. Решение: использовать пустые placeholder-значения вместо exit
- **`grep -c | ... || echo 0`** — на пустом stdin даёт многострочный результат («0\n0»), ломает арифметику в `[ "$x" -gt N ]`. Решение: `grep ... | wc -l | tr -d ' \n'` + `: "${var:=0}"` для дефолта
- **JS template literals в YAML literal block** (`script: |` в actions/github-script) — backtick-строка с `${var}` ломает YAML parser, потому что многострочный текст без отступов нарушает literal block. Решение: использовать `array.join('\n')` вместо template literal, или одинарные кавычки + конкатенация
