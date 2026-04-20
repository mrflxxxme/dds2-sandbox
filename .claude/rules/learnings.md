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
- **Wait-on-workflow через GH API polling** — `gh api "repos/X/actions/runs?head_sha=$SHA" --jq '...workflow_runs[] | select(.name=="Y") | .[0].status + ":" + .conclusion'` в loop с timeout. Для post-merge workflow который должен дождаться cd-production. Лучше hardcoded `sleep N`
- **User input через ENV вместо string interpolation в shell** — `PROMPT_TEMPLATE="...\"$USER_INPUT\"..."` уязвим к `$(cmd)` substitution. Вместо: `MY_ENV_VAR="$USER_INPUT" command "$STATIC_TEMPLATE"` (template без подстановки, агент читает из ENV). Прецедент: prewarm-spawn.sh CRITICAL fix
- **`--allowed-tools` без Write/Edit + spec через shell redirect** — для безопасного запуска LLM-помощника: tools = только Read/Glob/Grep, результат идёт в stdout, hook сам пишет файл. Альтернатива `--dangerously-skip-permissions + Write` опасна (LLM пишет произвольные пути)

## Антипаттерны (не повторять)
- `SELECT *` в продакшн запросах — всегда указывай колонки
- Пропуск type annotations на возвращаемых значениях сервисов
- Создание миграции без проверки `alembic heads`
- `.scalars().all()` без `.limit()` на больших таблицах
- `except Exception` без `asyncio.CancelledError` в scheduler jobs
- **Ранний `exit 0` в shell hook на основе одного условия блокирует все последующие блоки.** При добавлении нового блока в существующий hook — убедись что предыдущие условия не вылетают раньше. Решение: использовать пустые placeholder-значения вместо exit
- **`grep -c | ... || echo 0`** — на пустом stdin даёт многострочный результат («0\n0»), ломает арифметику в `[ "$x" -gt N ]`. Решение: `grep ... | wc -l | tr -d ' \n'` + `: "${var:=0}"` для дефолта
- **JS template literals в YAML literal block** (`script: |` в actions/github-script) — backtick-строка с `${var}` ломает YAML parser, потому что многострочный текст без отступов нарушает literal block. Решение: использовать `array.join('\n')` вместо template literal, или одинарные кавычки + конкатенация
- **Cron workflow с pytest/runtime без docker compose setup** — ubuntu-latest runner чистый, БД/Redis/MinIO не подняты. Без `docker compose up` (как в test.yml) тесты упадут с connection errors → false positive issue → spam. Либо setup, либо lightweight check (import + ruff). Прецедент: удалён `test-sentinel.yml` 2026-04-20
- **`$$` в bash subshell даёт PID родителя, не subshell** — `( echo $$ > lock ) &` пишет PID родительского процесса, который завершится через секунду → lock невалиден. Решение: `${BASHPID:-$$}` (BASHPID только в bash 4+, на macOS дефолт bash 3.2 → unbound variable с `set -u`). Прецедент: prewarm-spawn.sh lock не работал
- **`auto-merge.yml` блокирует PR при ЛЮБОМ check failure** — `claude` (Claude Code Review) всегда failed на PR с изменением `claude-review.yml` (workflow validation self-modify) → auto-merge скипается навсегда → требуется ручной merge. Решение: whitelist в `.jq select(.name != "claude")`. Прецедент 2026-04-20
- **`timeout-minutes` GH Actions должен покрывать max polling + retries + buffer** — мой post-merge.yml ставил 8 мин при cd-production реальном времени 8-9 мин → workflow убивался ДО завершения healthcheck. Формула: `timeout >= max_wait_polled + retries_time + 5min_buffer`
- **Hardcoded `sleep N` для ожидания workflow** — нестабильно, реальное время варьируется (cd-production: 8-9 мин в моём проекте, не 3). Лучше polling GH API: `gh api ... | jq` в `for` loop с timeout cap. Дожидаемся реального события, не угадываем
- **Claude Code Review failure при self-modification** — `anthropics/claude-code-action@v1` валидирует что claude-review.yml на PR == default branch. Если PR меняет сам workflow → expected failure «Workflow validation failed». Самоисправляется после merge в main. Игнорировать на первом PR с изменением workflow
