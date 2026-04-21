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
- **`a || null` vs `a ?? null` для API body**: `|| null` теряет `[]`/`0`/`''` (они falsy), тело запроса превращается в `undefined` из-за `body ? JSON.stringify(body) : undefined` в client.ts. Использовать `?? null` если хотим различать «не передали» и «передали пустое». Прецедент: `warehouse.acceptReceipt` (коммит 600b133)
- **URL-билдинг в API-модуле**: ВСЕГДА `URLSearchParams`, НИКОГДА template literals. `brand = "H&M"` ломает query если через `${brand}` — `&` интерпретируется как разделитель. Учти: URLSearchParams кодирует пробел как `+`, не `%20` (тесты писать под `+`). Прецедент: `reports.getOpiu/getWbBdr/getOrderGeography` (коммит 45dda1d)
- **`process.env` вне функций** — читать env на module level (`const API_URL = process.env.X || ''`), не внутри каждого вызова: тестируемость (mock env один раз) + консистентность с `client.ts`. Прецедент: `supply-chain.downloadVehicleDocument` (коммит 8eaf627)
- **DOMPurify вместо regex-allowlist** для санитизации HTML из AI: ручной regex НЕ ловит `<img onerror>`, `javascript:` в href, `<svg onload>`. Использовать `sanitizeAIHtml()` из `@/lib/sanitize`, hook `afterSanitizeAttributes` для force `target=_blank rel=noopener`. Прецедент: ai-chat + tma/chat (коммит 8c1d167)

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
- **Относительный порог `< N% от reference`** вместо `> 0` для sanity check числовых полей — когда в контексте есть эталон (цена, retail, объём). Ловит мусор от bulk-upload (1.01 при цене 2000 — потерянный множитель в Excel) который абсолютный `> 0` пропускает тихо. В ответе API возвращать `current_value` + `is_suspicious` чтобы UI показал проблему (P29, commit 433b433)
- **`.claude/agents/*.md` frontmatter `model:` подхватывается live** — замена `model: sonnet → opus` в subagent definition не требует рестарта Claude Code, срабатывает при следующем Agent(subagent_type=...). Для reviewers (code-reviewer, security-reviewer, planner) opus даёт +13% на coding benchmark при цене: на Max plan — бесплатно, на pay-as-you-go ~$0.05-0.10/вызов. Рекомендация: opus ТОЛЬКО для reviewers/planner; sonnet default для remaining; haiku для validation/docs. Прецедент: commit b52e8d1
- **Override на дочернем уровне → пересчёт производных полей**. Когда поле может быть переопределено (`box_size_override`, `pcs_per_box_override`, `per-vehicle overrides`) — все зависимые расчёты (`volume_m3`, распределение `delivery_rub`, стоимость логистики) должны использовать `effective_value = override ?? source` И пересчитываться при любом изменении override. Не хранить «снимок» от момента создания. Прецедент: supply-chain коммиты 8c565f0, a72a205 — volume_m3 не пересчитывался при смене override
- **Auto-detect колонок при Excel-paste по характеристикам**, не по фиксированному порядку. Определять тип колонки через эвристики: разделитель `*x× х` для box-size, наличие `.` или `,` для decimal, диапазон int, формат даты. Нормализовать строки ДО сравнения (`*` / `x` / `×` / `х` в box-size — все варианты). Любой порядок Excel-файла должен работать. Прецедент: supply-chain paste коммиты 7254bd8, 002f5c2
- **`alembic upgrade || echo skipped` в entrypoint = silent schema drift → 500 в проде**. Всегда fail-fast: миграция падает → контейнер не стартует → CD откатывает. Плюс env variables через `POSTGRES_PASSWORD:?error_msg` вместо fallback на default — запуск без `.env` громко падает, не с обфусцированной ошибкой позже. Прецедент: коммит e8b0c70 (audit 3 CRITICAL)
- **Secure-default + lifespan-guard для открывающих prod-флагов** (`REGISTER_ENABLED=true`). Default `False` в коде + runtime assert в lifespan если `ENV=production and REGISTER_ENABLED=True` (или whitelist только через explicit env). Defense-in-depth. Сопутствующий паттерн: `# no-soft-delete-check: <reason>` — точечный whitelist для ужесточения конвенции с backward-compat (commit fe5d74f превратил warn в error)
- **Partial index `WHERE is_deleted = false` через `CREATE INDEX CONCURRENTLY`** — обязательный паттерн для таблиц с SoftDeleteMixin где 90%+ запросов фильтруют по этому флагу. Без CONCURRENTLY — lock всей таблицы на минуты, с CONCURRENTLY — online. Важно: в Alembic revision убрать `with op.batch_alter_table(...)` и использовать raw SQL или `op.execute("CREATE INDEX CONCURRENTLY ...")` + `op.get_bind().execution_options(isolation_level="AUTOCOMMIT")`. Прецедент: коммит 5cb4d11 (partial indexes + worker memory 512→768M)
- **Scheduler cron для external-API sync = окно публикации поставщика, не рабочее время**. WB публикует финотчёт за прошлый день 03-05 MSK — ранний слот `05:00 MSK` + страхующий `08:00` + добор `14:00`, не «один прогон в 10:00 когда мы пришли на работу». Добавлять ранний слот если данные нужны к утреннему дайджесту/отчётам. Прецедент: commit 5fd7291 — добавили `wb_finance_daily_05` к существующим 08/14
- **Security-fix с блокирующим поведением (`raise`, lifespan-guard, pre-commit block) ОБЯЗАТЕЛЬНО сверяй с `memory/feedback_*.md`**. Generic OWASP-паттерн может быть намеренной feature владельца. Прецедент 2026-04-21: security-агент применил `raise RuntimeError("REGISTER_ENABLED=true in production is forbidden")` при старте FastAPI без чтения `feedback_register_enabled_prod.md` (где явно написано «открытая регистрация намеренна»). После merge прод крашился в бесконечном autoheal-цикле, пока не откатили через SSH + hotfix (RuntimeError → warning log). Правило зафиксировано в `.claude/agents/security-reviewer.md` секция «ОБЯЗАТЕЛЬНЫЙ первый шаг — context check». Альтернатива блокирующему фиксу: warning log + запись в learnings.md

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
- **Pre-commit `trim-trailing-whitespace` модифицирует файл → коммит падает** — hook detected trailing space, сам исправил и вернул exit 1 → «files were modified by this hook». Фикс: `git add <modified-file>` повторно + `git commit` заново с тем же message. НЕ `--amend` (коммит не создавался, amend модифицирует ПРЕДЫДУЩИЙ коммит). Частая жертва: markdown с code-blocks где пробелы после ``` или в pseudo-code. Прецедент: commit b52e8d1 — codemod.md
- **`pytest-xdist -n auto` в Docker с RAM limit → OOM (exit 137)** — `auto` спавнит по кол-ву CPU (8 на моей машине) × test-worker overhead ≈ >2GB → SIGKILL от Docker memory limit. Фикс: `-n 2` (2 воркера, ~7-8 сек на DDS2 test suite vs 30-60 сек без xdist). Tradeoff: 2× ускорение вместо 4×, но стабильно. Если backend container raise memory limit > 4GB → можно `-n 4`. Прецедент: commit 4587fea → откат в следующем коммите
