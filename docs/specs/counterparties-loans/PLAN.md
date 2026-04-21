# Counterparties + Loans — Implementation PLAN

**Spec:** [SPEC.md](./SPEC.md) · **Tests:** [TESTS.md](./TESTS.md)
**Оценка:** 7–10 рабочих дней (с параллелизмом)
**Координация:** backend и frontend работают параллельно в isolated worktrees. Shared зона (models, migrations, schemas) — только lead.

## Phase 0: Constitution check (lead, 15 мин)

Проверить перед стартом:
- `alembic heads` → должен быть ровно один head. Если нет — `alembic merge heads`.
- `git status` чистый, мы на ветке `dev`.
- `docker compose ps` — все сервисы healthy.
- Backup БД прода (разово, перед prod rollout — не для dev).

## Phase 1: Foundation — lead agent, sequential (~4–5 ч)

**Владелец файлов:** lead (Shared zone). Backend и frontend НЕ стартуют до завершения.

### 1.1 Models (0 конфликтов, т.к. lead один)

| Файл | Действие |
|---|---|
| `backend/models/counterparty.py` | **NEW**: `Counterparty` + `CounterpartyDocument` + `CounterpartyType` enum + `DocType` enum |
| `backend/models/loan.py` | **NEW**: `Loan` + `LoanPayment` + `LoanDirection` + `LoanStatus` + `LoanPaymentType` enums |
| `backend/models/transactions.py` | +`counterparty_id`, `contract_number`, `unk_number`, `loan_id`, `loan_payment_id`, `loan_payment_type`; EventType2 +3 значения |
| `backend/models/supply_chain.py` (Supplier) | +`inn`, `contract_number`, `counterparty_id` |
| `backend/models/warehouse.py` | +`counterparty_id` |
| `backend/models/refs.py` (CounterpartyCategory) | +`counterparty_id` FK |
| `backend/models/customs.py` (CustomsTopup) | +`counterparty_id` FK |
| `backend/models/__init__.py` | +register new models |

### 1.2 Alembic migration (одна!)

```
docker compose exec backend alembic revision -m "counterparties loans faktura parser"
# руками написать upgrade/downgrade (autogenerate не умеет partial indexes + CONCURRENTLY)
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade -1
docker compose exec backend alembic upgrade head
```

Требования:
- `CREATE INDEX CONCURRENTLY` для partial indexes (прецедент learnings `5cb4d11`).
- `op.execute("ALTER TYPE event_type2 ADD VALUE IF NOT EXISTS ...")` — отдельными SQL statements, не через batch.
- Downgrade не удаляет enum values (PostgreSQL limitation).

### 1.3 Pydantic schemas

| Файл | Действие |
|---|---|
| `backend/schemas/counterparty.py` | **NEW**: all create/update/list/detail + filters + DocumentCreate/Response |
| `backend/schemas/loan.py` | **NEW**: all CRUD + LoanPaymentMatch + LoanDetail |
| `backend/schemas/reports.py` | +CounterpartyTurnoversResponse |

### 1.4 Конвенции

- Добавить `Counterparty`, `CounterpartyDocument`, `Loan` в `SOFT_MODELS` (`scripts/check_conventions.sh`).
- Добавить cache prefixes в `backend/cache.py::invalidate_project_reports()`.

### 1.5 Commit

```
git add backend/models backend/schemas migrations/versions scripts/check_conventions.sh
git commit -m "feat(counterparties): models + schemas + migration (Phase 1)"
```

**Artifact:** single commit в `dev`, основа для параллельной Phase 2.

## Phase 2: Parallel Implementation (2 teammates, isolation: worktree, ~10–14 ч)

**Координация:** ОБА teammate стартуют ОДНОВРЕМЕННО после Phase 1 commit. 0 пересечений файлов.

### Backend teammate (sonnet, isolation: worktree)

**Scope:** services, routers, ETL, backfill, tests.

**Constraints (передаются в промпте):**
1. **Relative paths only** — worktree, не main. Никаких `/Users/a1/Desktop/dds_app/...` в Read/Write/Edit.
2. **Tests-first** — начать с `tests/test_*.py`, потом implementation.
3. **Git-status-check в конце** — `git status && git diff --stat && git log -1 --oneline`.

**Файлы:**

```
backend/services/counterparty_service.py          NEW
backend/services/loan_service.py                  NEW
backend/services/reports/counterparty_turnovers.py NEW
backend/routers/counterparties.py                 NEW
backend/routers/loans.py                          NEW
backend/routers/reports.py                        MODIFY (+counterparty_turnovers)
backend/routers/__init__.py                       MODIFY (register new routers)
backend/etl/parsers/faktura.py                    NEW
backend/etl/parsers/__init__.py                   MODIFY (+FAKTURA_WB_BANK в SOURCE_PARSERS)
backend/etl/master_logic.py                       MODIFY (+regex block + upsert hook)
backend/etl/service.py                            MODIFY (+вызов upsert_by_inn)
scripts/backfill_counterparties.py                NEW
tests/test_counterparty_service.py                NEW
tests/test_loan_service.py                        NEW
tests/test_counterparties_api.py                  NEW
tests/test_loans_api.py                           NEW
tests/test_faktura_parser.py                      NEW
tests/test_master_logic_regex.py                  NEW
tests/test_counterparty_turnovers_report.py       NEW
tests/test_backfill_script.py                     NEW
tests/fixtures/faktura_wb_bank_sample.xml         NEW (анонимизированный sample 10 строк)
backend/DOMAIN_COUNTERPARTIES.md                  NEW (краткий domain doc)
```

**Workflow:**
1. Pull latest `dev` в worktree.
2. TDD: сначала тесты (RED), затем services (GREEN), затем routers.
3. После каждого модуля: `docker compose exec backend pytest tests/test_<name>.py -x`.
4. `bash scripts/check_conventions.sh` — 0 errors.
5. `git commit -m "feat(counterparties-backend): services, routers, ETL, backfill"`.

### Frontend teammate (sonnet, isolation: worktree)

**Scope:** types, API clients, pages, components, tests.

**Constraints (в промпте):**
1. **Relative paths only**.
2. **Types-first** — `types/api.ts` → `lib/api/*` → тесты → компоненты/страницы.
3. **Git-status-check в конце**.
4. **URL через URLSearchParams** (не template literals).
5. **`?? null` для API body** (не `|| null`).
6. **`process.env` читать на module level**.
7. **formatNumber/formatDate** — не inline форматирование.
8. **Loading/error/empty — обязательно** на каждой странице.

**Файлы:**

```
frontend-react/src/types/api.ts                                        MODIFY (+все новые типы)
frontend-react/src/lib/api/counterparty.ts                             NEW
frontend-react/src/lib/api/loans.ts                                    NEW
frontend-react/src/lib/api/reports.ts                                  MODIFY (+getCounterpartyTurnovers)
frontend-react/src/app/(main)/p/[slug]/layout.tsx                      MODIFY (+sidebar section)
frontend-react/src/app/(main)/p/[slug]/refs/counterparty/page.tsx      NEW
frontend-react/src/app/(main)/p/[slug]/refs/counterparty/[id]/page.tsx NEW
frontend-react/src/app/(main)/p/[slug]/refs/counterparty/loading.tsx   NEW
frontend-react/src/app/(main)/p/[slug]/refs/counterparty/error.tsx     NEW
frontend-react/src/app/(main)/p/[slug]/refs/loans/page.tsx             NEW
frontend-react/src/app/(main)/p/[slug]/refs/loans/[id]/page.tsx        NEW
frontend-react/src/app/(main)/p/[slug]/refs/loans/loading.tsx          NEW
frontend-react/src/app/(main)/p/[slug]/import/page.tsx                 MODIFY (+FAKTURA_WB_BANK)
frontend-react/src/app/(main)/p/[slug]/warehouse/page.tsx              MODIFY (+Combobox контрагент)
frontend-react/src/app/(main)/p/[slug]/supply-chain/                   MODIFY (+ИНН+contract для Supplier)
frontend-react/src/app/(main)/p/[slug]/reports/page.tsx                MODIFY (+Tab «Обороты по КА»)
frontend-react/src/components/CounterpartyTypeBadge.tsx                NEW
frontend-react/src/components/CounterpartyCard.tsx                     NEW
frontend-react/src/components/LoanScheduleChart.tsx                    NEW
frontend-react/src/components/DocumentUploader.tsx                     NEW
frontend-react/src/components/CurrencySplitStats.tsx                   NEW
frontend-react/tests/counterparty.test.tsx                             NEW
frontend-react/tests/loans.test.tsx                                    NEW
frontend-react/tests/components/CounterpartyTypeBadge.test.tsx         NEW
frontend-react/tests/components/LoanScheduleChart.test.tsx             NEW
```

**Workflow:**
1. Pull latest `dev` в worktree.
2. Types → api → тесты (vitest) → компоненты → страницы.
3. После каждой страницы: `cd frontend-react && npx vitest run`.
4. Один smoke E2E: `npx playwright test tests/e2e/smoke.spec.ts` (27 страниц не крашатся).
5. `git commit -m "feat(counterparties-frontend): pages, components, api clients"`.

### Pre-warm haiku (параллельно с Phase 1.3)

Опциональный ранний запуск haiku-агента для генерации карты frontend контекста (`src/app/(main)/p/[slug]/refs/*`, `src/components/*`, `src/lib/api/*`) — ускорит frontend teammate на ~20%. Запустить как только готовы schemas (Phase 1.3). Env `DDS_PREWARM_ENABLED=1` уже поддерживается.

## Phase 3: Verify (parallel haiku agents, ~30 мин)

Запустить параллельно в main worktree (после merge обоих teammate):

| Агент | Команда | Ожидание |
|---|---|---|
| pytest-runner | `docker compose exec backend pytest tests/ --cov -n 2` | 1184+ существующих pass + ~40 новых pass, coverage новых >80% |
| vitest-runner | `cd frontend-react && npx vitest run --coverage` | все pass, coverage >70% |
| conventions | `bash scripts/check_conventions.sh` | 0 errors |
| docs | `bash scripts/check_docs.sh` + обновить DOMAIN_COUNTERPARTIES.md + upsert row в таблице CLAUDE.md | обновлено |
| backfill-smoke | `python -m scripts.backfill_counterparties --project-id 1 --dry-run` локально | idempotent, stats non-zero |
| migration-smoke | `alembic downgrade -1 && alembic upgrade head` | no errors |

## Phase 4: Review (parallel opus agents, ~30–45 мин)

| Агент | Фокус |
|---|---|
| `code-reviewer` | качество + конвенции на всём diff |
| `security-reviewer` | upload (MIME, magic bytes, MinIO), SQL injection в regex, project_id guards, ILIKE escape, audit счётчик uploaded_by |
| `api-designer` | OpenAPI diff: новые 15 endpoints, нет breaking changes к `/api/v1`, consistency error codes |
| `performance-optimizer` | N+1 в list (stats per row), partial indexes используются в plans, bundle size (<100KB новый код), cache TTL sane |
| `database-reviewer` | миграция — reversible, CONCURRENTLY, backfill idempotency, partial indexes правильно сконфигурены |

Все ревью запускаются в **read-only** в main worktree. Каждый пишет report в `.claude/review/<agent>-<timestamp>.md`. Lead агрегирует, фиксит P0/P1.

## Phase 5: Merge + deploy (lead, ~1 ч)

### 5.1 Merge worktrees

Lead мержит backend и frontend worktrees в локальный `dev`:
```bash
# backend worktree → dev
cd <backend-worktree> && git push origin <backend-branch>
cd /Users/a1/Desktop/dds_app && git merge <backend-branch>
# frontend аналогично (нет пересечений, fast-forward)
```

### 5.2 Final local verify

```bash
make test-fast              # pytest с xdist -n 2
cd frontend-react && npx vitest run
bash scripts/check_conventions.sh
docker compose exec backend alembic current    # head совпадает
```

### 5.3 Commit summary

```
git commit -m "feat(counterparties): справочник, займы, Faktura parser, отчёт оборотов

- Counterparty (13 типов) + Loan + LoanPayment + CounterpartyDocument
- Парсер WB Банк (Faktura.ru XML 2003)
- Regex pipeline: contract/УНК/депозиты/займы
- Отчёт 'Обороты по контрагентам' (RUB+CNY раздельно)
- Backfill script (идемпотентный)
- UI: /refs/counterparty, /refs/loans + интеграция в warehouse/supply-chain/import/reports

Closes: handoff spec-counterparties-loans"
```

### 5.4 Push + deploy

```bash
git push origin dev       # → auto-pr.yml создаёт PR → CI → auto-merge → cd-production
```

Мониторить:
- `gh run watch` для CI + cd-production.
- `post-merge.yml` ждёт healthcheck `app.vyatkin-wb.ru`.

### 5.5 Production backfill (разово, после deploy)

```bash
ssh dds-app 'docker compose exec backend python -m scripts.backfill_counterparties --project-id 1'
```

Ожидаемый output:
```
Counterparties created: 128
Counterparties updated: 0
Transactions linked: ~5000
CounterpartyCategory migrated: ~40
CustomsTopup linked: N
Unmatched cp_keys: 0 (warnings в log если есть)
```

### 5.6 Smoke на проде

- Открыть `https://app.vyatkin-wb.ru/p/plus-vibe/refs/counterparty` → 128+ контрагентов.
- Импортировать WB Банк выписку (`statement_21042026.xls`) → 117 ИНН upsert, транзакции созданы.
- Отчёт `reports/counterparty-turnovers` за апрель → данные совпадают с выпиской.

## Phase 6: Learn (~10 мин)

```bash
/learn
```

Фиксируем уроки в `.claude/rules/learnings.md`:
- Что неожиданно оказалось сложнее (например, PostgreSQL `ALTER TYPE ADD VALUE` не в transaction → отдельная миграция? или в CONCURRENTLY mode?).
- Что неожиданно легко (pre-warm + worktree → 0 конфликтов).
- Где teammate нарушил constraints (абсолютные пути, `|| null`, inline форматирование) → усилить промпты.

## Dependencies Graph

```
Phase 0 (15m) Constitution
       ↓
Phase 1 (4-5h) Foundation — lead
       ↓
       ├─────────────────────────┐
       ↓                         ↓
Phase 2 Backend (~10h)   Phase 2 Frontend (~8h)
       ↓                         ↓
       └─────────────┬───────────┘
                     ↓
         Phase 3 Verify (30m, parallel)
                     ↓
         Phase 4 Review (45m, parallel)
                     ↓
         Phase 5 Merge + Deploy (1h)
                     ↓
         Phase 6 Learn (10m)
```

Критический путь: Phase 1 (5h) + Phase 2 backend (10h) + Phase 3–5 (2h) ≈ **17 часов** (~2 рабочих дня при фокусе + dev setup).

## Риски и митигация

| Риск | Вероятность | Митигация |
|---|---|---|
| PostgreSQL enum ALTER TYPE конфликт в миграции | Средняя | Использовать `ADD VALUE IF NOT EXISTS`, выполнять вне transaction (отдельный op.execute) |
| Backfill дубликаты при повторном запуске | Высокая | Strict idempotency: UPSERT через `ON CONFLICT (project_id, inn) DO UPDATE` |
| Перформанс `/counterparties/{id}` с stats за период | Средняя | Один GROUP BY запрос вместо N+1, индекс на (project_id, counterparty_id, date) |
| Faktura parser ломается на corrupt XML | Низкая | Try/except c ParseError → 422 с детальным сообщением |
| MinIO disk fill от документов | Низкая | Max 20MB/файл, нет retention policy (отложено, not in scope) |
| Merge конфликты backend ↔ frontend | **Нулевая** | 0 пересечений файлов (см. File Ownership Rules) |

## Rollback план

Если после deploy обнаружены критические баги:
1. `/rollback` — откат на предыдущий образ (автоматически через GH Actions).
2. Миграция Alembic downgrade: `alembic downgrade -1` (но данные в новых таблицах теряются — принимаем, т.к. это новая фича).
3. Если только регрессия в existing endpoint — hotfix-патч вместо rollback.
