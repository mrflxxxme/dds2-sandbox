# Counterparties + Loans + Faktura parser — SPEC

**Дата:** 2026-04-21
**Handoff:** `.claude/handoff/spec-counterparties-loans.md`
**Статус:** готово к ревью

## 1. User Stories

- **Финменеджер:** вижу справочник контрагентов с ИНН, категорией и статистикой за период → быстро нахожу платежи и документы по партнёру.
- **Владелец:** вижу займы (INCOMING / OUTGOING / AFFILIATED) с графиком платежей → контролирую кредитную нагрузку.
- **Бухгалтер:** импортирую выписку WB Банка в формате Faktura.ru (Excel 2003 XML) → платежи появляются без ручного ввода + автосоздаются Counterparty.
- **Аналитик:** открываю отчёт «Обороты по контрагентам» (pivot месяц × контрагент, RUB+CNY раздельно) → вижу концентрацию оборота.
- **Оператор склада:** привязываю склад к контрагенту (фулфилмент) → в карточке контрагента видны связанные склады.

## 2. Success Criteria (измеримые)

| Метрика | Цель |
|---|---|
| Backfill покрытие ИНН | ≥117 из WB Банка + 11 из VTB автоматически создаются как Counterparty |
| `GET /counterparties` p95 | < 200 ms при 500 контрагентах |
| `GET /counterparties/{id}` (stats за 3 мес) p95 | < 300 ms |
| `GET /reports/counterparty-turnovers` (12 мес × 200 КА) p95 | < 1 s (c cache) |
| Faktura parser | 242М RUB оборота из WB выписки парсится без ошибок, 117 уникальных ИНН извлекаются |
| Regex pipeline | распознаёт `CONTRACT`, `УНК`, 3 типа депозитов, 3 типа займов (покрытие в тестах 100%) |
| Regression | 0 падений в существующих 1184+ pytest + vitest |
| Coverage нового кода | backend ≥ 80%, frontend ≥ 70% |
| Backfill script | идемпотентен (2-й запуск не создаёт дублей), < 60s на 10К транзакций |

## 3. Out of Scope (отложено, отдельные спеки)

- FactoryOrder ↔ Transaction связь оплат и заказов.
- Уведомления / алерты по крупным платежам.
- Обогащение из ЕГРЮЛ / ФНС API.
- Множественные счета у одного контрагента.
- Прогноз регулярных платежей (аренда, подписки).
- Fine-grained permissions для справочников (пока все могут всё).
- Конвертация CNY↔RUB в отчёте (валюты раздельно).

## 4. Constraints

- **Iron rules DDS:** `project_id` в каждом запросе, `is_deleted=False`, `soft_delete()`, `Numeric(18,2)` для денег, timezone-aware datetime (`backend.utils.time.utcnow`), параметризованный SQL.
- **API:** остаёмся на `/api/v1` — нет breaking changes существующих endpoints.
- **Alembic:** одна миграция для всей фичи, только последовательно (lead agent, Phase 1).
- **PgBouncer:** `prepared_statement_cache_size=0` (уже в коде).
- **MinIO:** документы контрагента в бакете `counterparty-docs`, signed URL для download.
- **Мультивалюта:** храним `currency` на Transaction/Loan/LoanPayment, не конвертируем в отчётах.
- **Мультироль контрагента:** `primary_type` + `secondary_types[]`, но **категория платежа определяется по regex в `Transaction.purpose`**, а не по типу контрагента.
- **Idempotency:** UPSERT автосоздания по `(project_id, inn)` — обновляем `name` только если новое длиннее.

## 5. Data Model

### 5.1 Новые таблицы

#### `counterparty`
```
id                  BigInteger PK
project_id          BigInteger FK → project.id NOT NULL  (INDEXED)
inn                 VARCHAR(12)     — UNIQUE в scope (project_id, inn) WHERE is_deleted=false
name                VARCHAR(500)    NOT NULL
primary_type        Enum(CounterpartyType, 13 значений)  default='OTHER'
secondary_types     ARRAY(Enum(CounterpartyType))         default=[]
kpp                 VARCHAR(9)      NULL
contract_number     VARCHAR(100)    NULL   — для китайских поставщиков без ИНН (INDEXED WHERE contract_number IS NOT NULL)
notes               TEXT            NULL
contacts            JSONB           NULL   — {phone, email, tg, contact_person}
created_by_import   BOOLEAN         default=false
is_deleted          BOOLEAN         default=false   (SoftDeleteMixin)
created_at, updated_at, deleted_at  (TimestampMixin)

CONSTRAINTS:
  UNIQUE (project_id, inn) WHERE is_deleted=false AND inn IS NOT NULL
  UNIQUE (project_id, contract_number) WHERE is_deleted=false AND contract_number IS NOT NULL

INDEXES:
  ix_counterparty_project_type  (project_id, primary_type) WHERE is_deleted=false (partial)
  ix_counterparty_project_name_trgm  GIN (project_id, name gin_trgm_ops)  — для поиска по имени
```

**Enum `CounterpartyType`** (13 значений):
`SUPPLIER, FULFILLMENT, CARRIER, CUSTOMS_BROKER, DESIGNER, LEGAL, LANDLORD, IT_SERVICE, MARKETPLACE, BANK, GOVERNMENT, AFFILIATED, OTHER`

#### `loan`
```
id                  BigInteger PK
project_id          BigInteger FK NOT NULL  (INDEXED)
counterparty_id     BigInteger FK → counterparty.id NOT NULL  (INDEXED)
direction           Enum(LoanDirection: INCOMING|OUTGOING|AFFILIATED)
principal           Numeric(18, 2) NOT NULL
currency            VARCHAR(3)     default='RUB'
rate                Numeric(6, 4)  NULL   — годовая ставка (например 0.1850 = 18.50%)
contract_number     VARCHAR(100)   NOT NULL
contract_date       DATE           NOT NULL
start_date          DATE           NOT NULL
maturity_date       DATE           NULL
status              Enum(LoanStatus: ACTIVE|CLOSED|DEFAULTED)  default='ACTIVE'
notes               TEXT           NULL
is_deleted, timestamps

INDEXES:
  ix_loan_project_status  (project_id, status) WHERE is_deleted=false (partial)
  ix_loan_counterparty    (counterparty_id)
```

#### `loan_payment`
```
id                  BigInteger PK
loan_id             BigInteger FK → loan.id NOT NULL (INDEXED)
transaction_id      BigInteger FK → transaction.id NULL (INDEXED)
payment_type        Enum(LoanPaymentType: DISBURSEMENT|PRINCIPAL_REPAY|INTEREST_PAY|PENALTY)
amount              Numeric(18, 2) NOT NULL
currency            VARCHAR(3)     NOT NULL
paid_at             DATE           NOT NULL
created_at, updated_at

CONSTRAINTS:
  UNIQUE (transaction_id) WHERE transaction_id IS NOT NULL  — одна транзакция = один LoanPayment
INDEXES:
  ix_loan_payment_loan_date  (loan_id, paid_at DESC)
```

#### `counterparty_document`
```
id                  BigInteger PK
counterparty_id     BigInteger FK NOT NULL (INDEXED)
minio_path          VARCHAR(500)   NOT NULL
doc_type            Enum(DocType: CONTRACT|CERTIFICATE|INVOICE|OTHER)
original_filename   VARCHAR(500)
file_size           Integer
mime_type           VARCHAR(100)
uploaded_by_user_id BigInteger FK → user.id NULL
uploaded_at         TIMESTAMP WITH TIME ZONE  default=utcnow
is_deleted, deleted_at
```

### 5.2 Расширения существующих таблиц

| Таблица | Новые колонки |
|---|---|
| `transaction` | `counterparty_id BIGINT FK NULL` (INDEXED), `contract_number VARCHAR(100) NULL`, `unk_number VARCHAR(100) NULL`, `loan_id BIGINT FK NULL`, `loan_payment_id BIGINT FK NULL`, `loan_payment_type Enum NULL` |
| `supplier` (supply_chain) | `inn VARCHAR(12) NULL`, `contract_number VARCHAR(100) NULL`, `counterparty_id BIGINT FK NULL` (+ UNIQUE по `(project_id, contract_number)` partial) |
| `warehouse` | `counterparty_id BIGINT FK NULL` |
| `counterparty_category` (refs.py) | `counterparty_id BIGINT FK NULL` — **полная миграция** `cp_key → counterparty_id`, после проверки колонка `cp_key` становится DEPRECATED (drop в следующем релизе) |
| `customs_topup` | `counterparty_id BIGINT FK NULL` — в backfill проставляется на ФТС (ИНН 7730176610) |

### 5.3 Enum расширения

- **`EventType2`** (`backend/models/transactions.py`) +3 значения:
  - `DEPOSIT_PLACE` — размещение депозита (is_cashflow2=0, не денежный поток)
  - `DEPOSIT_RETURN` — возврат депозита (is_cashflow2=0)
  - `DEPOSIT_INTEREST` — проценты по депозиту (is_cashflow2=1, доход)

### 5.4 Миграция (Alembic upgrade/downgrade)

**Upgrade:**
1. CREATE TYPE counterparty_type, loan_direction, loan_status, loan_payment_type, doc_type (enums).
2. ALTER TYPE event_type2 ADD VALUE 'DEPOSIT_PLACE' / 'DEPOSIT_RETURN' / 'DEPOSIT_INTEREST'.
3. CREATE TABLE counterparty / loan / loan_payment / counterparty_document.
4. ALTER TABLE transaction/supplier/warehouse/counterparty_category/customs_topup — добавить колонки nullable.
5. CREATE INDEX CONCURRENTLY partial indexes (isolation_level=AUTOCOMMIT).

**Downgrade:**
- DROP INDEX (не CONCURRENTLY — быстро на пустых партицях, обратная последовательность).
- ALTER TABLE … DROP COLUMN.
- DROP TABLE (в обратном порядке FK).
- **НЕ** удаляем новые значения enum `EventType2` (PostgreSQL не умеет ALTER TYPE DROP VALUE) → оставляем как есть, downgrade помечает их устаревшими в коде.
- DROP TYPE новых enums.

## 6. API Contract (`/api/v1`)

### 6.1 Counterparties

```
GET    /api/v1/counterparties
  ?project_id=UUID (required)
  &type=FULFILLMENT (optional, CounterpartyType)
  &q=кузнец (optional, поиск по name/inn, ILIKE %q% с escape)
  &active_only=true (optional, default false)
  &limit=50&offset=0
  200 → { items: CounterpartyListItem[], total: int }
  Cache: 60s (prefix counterparty_list:)

GET    /api/v1/counterparties/{id}
  ?project_id=UUID&date_from=2026-01-01&date_to=2026-04-21
  200 → CounterpartyDetail {
    id, inn, name, primary_type, secondary_types, kpp, contract_number, notes, contacts,
    stats_rub: { in_sum, out_sum, net, tx_count },
    stats_cny: { in_sum, out_sum, net, tx_count },
    linked_warehouses: [{id, name}],
    linked_suppliers: [{id, name}],
    active_loans: LoanShort[],
    docs_count: int
  }
  404 → не найден / чужой project_id

POST   /api/v1/counterparties
  Body: CounterpartyCreate { inn?, name, primary_type, secondary_types?, kpp?, contract_number?, notes?, contacts? }
  Depends(rate_limit_write)
  201 → CounterpartyDetail
  409 → { code: "inn_conflict", message: "ИНН уже используется" }
  422 → валидация (ИНН 10/12 цифр, тип из enum)

PATCH  /api/v1/counterparties/{id}
  Body: CounterpartyUpdate (partial)
  Depends(rate_limit_write)
  200 → CounterpartyDetail

DELETE /api/v1/counterparties/{id}?project_id=X
  Depends(rate_limit_write)
  204 (soft delete)
  → invalidate_project_reports()

POST   /api/v1/counterparties/{id}/documents  (multipart)
  file: binary, doc_type: CONTRACT|CERTIFICATE|INVOICE|OTHER
  Depends(rate_limit_write)
  max size 20MB, MIME allowlist: pdf/docx/xlsx/jpg/png
  201 → CounterpartyDocument { id, minio_path_signed_url, doc_type, original_filename, file_size, uploaded_at }

GET    /api/v1/counterparties/{id}/documents
  200 → CounterpartyDocument[] (signed_url TTL=300s)

DELETE /api/v1/counterparties/{id}/documents/{doc_id}
  Depends(rate_limit_write)
  204 → MinIO object delete + row soft_delete
```

### 6.2 Loans

```
GET    /api/v1/loans?project_id=X&direction=OUTGOING&status=ACTIVE&counterparty_id=Y
  200 → { items: Loan[], totals_by_direction: { INCOMING: {count, sum_rub, sum_cny}, OUTGOING: {...}, AFFILIATED: {...} } }

GET    /api/v1/loans/{id}
  200 → LoanDetail { loan, counterparty: CounterpartyShort, payments: LoanPayment[], schedule_summary: {principal_paid, interest_paid, remaining} }

POST   /api/v1/loans  (+rate_limit_write)  201 → LoanDetail
PATCH  /api/v1/loans/{id} (+rate_limit_write)  200

POST   /api/v1/loans/{id}/payments/match
  Body: { transaction_id: int, payment_type: LoanPaymentType, amount: Decimal }
  Depends(rate_limit_write)
  201 → LoanPayment
  409 → транзакция уже привязана к другому LoanPayment
  403 → транзакция в другом проекте
```

### 6.3 Reports

```
GET    /api/v1/reports/counterparty-turnovers
  ?project_id=X&date_from=2026-01-01&date_to=2026-04-21&type=FULFILLMENT&currency=RUB&min_turnover=0
  200 → {
    rows: [{
      counterparty_id, inn, name, primary_type,
      months: [{month: "2026-01", in: Decimal, out: Decimal, net: Decimal}],
      total: {in, out, net},
      tx_count
    }],
    period: {from, to},
    currency
  }
  Cache: 300s (prefix counterparty_turnovers:)
```

### 6.4 Import (расширение существующего)

```
POST   /api/v1/import/statement
  form-data: file, source_type=FAKTURA_WB_BANK, project_id
  → вызывает FakturaParser (новый) → master_logic (с upsert Counterparty)
  201 → { imported_count, skipped_dedup, counterparties_created, counterparties_updated }
```

### 6.5 Error format (единый)

```json
{ "detail": "человекочитаемое", "code": "inn_conflict", "fields": {"inn": "..."} }
```

Коды: `inn_conflict`, `loan_tx_already_matched`, `tx_different_project`, `file_too_large`, `mime_not_allowed`, `invalid_enum`.

## 7. Service Layer

### 7.1 `backend/services/counterparty_service.py`

```python
class CounterpartyService:
    async def list(self, project_id, filters: CounterpartyFilter, limit, offset) -> tuple[list, int]
    async def get(self, id, project_id, date_from, date_to) -> CounterpartyDetail
    async def create(self, data: CounterpartyCreate, project_id) -> Counterparty
    async def update(self, id, data: CounterpartyUpdate, project_id) -> Counterparty
    async def soft_delete(self, id, project_id) -> None
    # ETL-хуки (idempotent):
    async def upsert_by_inn(self, inn, name, project_id, defaults: dict | None) -> Counterparty
    async def upsert_by_contract(self, contract_number, project_id, defaults: dict | None) -> Counterparty
    # Stats:
    async def stats(self, id, project_id, date_from, date_to, currency) -> CounterpartyStats
```

**Кэш:** `counterparty_list:<project>:<hash_filters>` TTL 60s. Инвалидация при create/update/delete.

### 7.2 `backend/services/loan_service.py`

```python
class LoanService:
    async def list(self, project_id, filters) -> LoanListResponse
    async def get_detail(self, id, project_id) -> LoanDetail
    async def create(self, data: LoanCreate, project_id) -> Loan
    async def update(self, id, data: LoanUpdate, project_id) -> Loan
    async def match_transaction(self, loan_id, tx_id, payment_type, amount, project_id) -> LoanPayment
    # Автопривязка из ETL:
    async def auto_link_from_etl(self, transaction, purpose_match: RegexMatch, project_id) -> LoanPayment | None
```

### 7.3 `backend/services/reports/counterparty_turnovers.py`

```python
class CounterpartyTurnoversReport:
    async def generate(self, project_id, date_from, date_to, type_filter, currency, min_turnover) -> ReportData
```

- SQL: aggregate `Transaction` по `(counterparty_id, date_trunc('month', date), currency)`, JOIN `Counterparty` для name/inn/type.
- Предотвращение N+1: один запрос с GROUP BY, pivot в Python.
- Cache prefix: `counterparty_turnovers:` → добавить в `cache.py::invalidate_project_reports()`.

### 7.4 `backend/etl/parsers/faktura.py`

```python
SOURCE_PARSERS["FAKTURA_WB_BANK"] = FakturaParser

class FakturaParser:
    def parse(self, file_bytes: bytes) -> list[ParsedTransaction]:
        # XML Spreadsheet 2003, namespace urn:schemas-microsoft-com:office:spreadsheet
        # Читаем через lxml (уже в зависимостях для etl/cost)
        # Колонки: Плат.пор. № | дата | корреспондент | ИНН | КПП | счёт | БИК | вх.остаток | Дт | Кт | Назначение
```

Особенности:
- Namespace-aware XPath.
- `amount` = `Кт - Дт` (или знак в зависимости от направления).
- `txn_id` = hash(ИНН + дата + сумма + нац.целей) для дедупа.
- Правильная обработка плательщика/получателя (наш счёт определяется по БИК 044525092 и номеру счёта организации из `Account.is_our_account=true`).

### 7.5 `backend/etl/master_logic.py` (расширение)

**Новые regex (добавляются в `enrich_transaction`):**

```python
RE_CONTRACT = re.compile(r'CONTRACT\s+(?:NO\.)?(\d{6,})', re.I)
RE_UNK      = re.compile(r'УНК\s+([\d/]+)', re.I)

RE_DEPOSIT_PLACE    = re.compile(r'Размещение\s+средств\s+в\s+депозит', re.I)
RE_DEPOSIT_RETURN   = re.compile(r'Возврат\s+депозита', re.I)
RE_DEPOSIT_INTEREST = re.compile(r'Уплата\s+процентов\s+по\s+депозиту', re.I)

RE_LOAN_DISBURSEMENT = re.compile(r'(?:Предоставление|Выдача|Перевод).*договор.*займ', re.I)
RE_LOAN_REPAY        = re.compile(r'(?:Оплата|Возврат).*договор.*займ', re.I)
RE_LOAN_INTEREST     = re.compile(r'Уплата\s+процентов.*займ', re.I)

RE_FULFILLMENT = re.compile(r'упаковк|маркировк|фулфилмент|ФФ-\d|фф\s', re.I)
RE_LOGISTICS   = re.compile(r'доставк|транспортн|перевоз|экспедиц|форвардин|forwarding|freight', re.I)
```

**Logic:**
1. После парсинга Transaction: `contract_number`, `unk_number` заполняются регексами.
2. Depositы → устанавливают `event_type2` + `is_cashflow2`.
3. Если `RE_LOAN_*` сматчился → `LoanService.auto_link_from_etl()` (если Loan с таким `contract_number` существует в проекте — создать LoanPayment; иначе только установить `loan_payment_type` на Transaction и оставить без loan_id — матчинг вручную из UI).
4. UPSERT `Counterparty` по ИНН (через `CounterpartyService.upsert_by_inn`) в конце цикла enrich. Китайские поставщики (ИНН нет) → `upsert_by_contract(contract_number)`.

### 7.6 `scripts/backfill_counterparties.py`

```bash
python -m scripts.backfill_counterparties --project-id 1 [--dry-run]
```

Шаги (идемпотентно):
1. SELECT DISTINCT inn FROM transaction WHERE project_id=X AND inn IS NOT NULL → upsert в counterparty.
2. Применить хардкод-мапу `{inn: (primary_type, name)}` для 20+ известных ИНН (полный список в handoff §«Справочные данные»).
3. Перепривязать `counterparty_category.cp_key` → `counterparty_id` через lookup по inn/name (если не нашли — warning в лог, оставляем cp_key как есть).
4. UPDATE customs_topup SET counterparty_id = (SELECT id FROM counterparty WHERE inn='7730176610') WHERE counterparty_id IS NULL.
5. UPDATE transaction SET counterparty_id = (SELECT id FROM counterparty c WHERE c.inn = transaction.inn AND c.project_id = transaction.project_id) WHERE counterparty_id IS NULL.
6. Stats вывод: created, updated, linked_transactions, unmatched_cp_keys.

## 8. Frontend

### 8.1 Новые страницы (Next.js App Router)

#### `/p/[slug]/refs/counterparty/page.tsx` — список
- Фильтры: тип (Select), период (DateRangePicker), активные/архивные (Toggle), поиск (Input debounced).
- DataTable (TanStackDataTable): ИНН | Имя | Тип (badge) | RUB оборот | CNY оборот | Платежей | Действия.
- Кнопка «Экспорт Excel» (reuse `exportToExcel`).
- Кнопка «Добавить» → модалка `FormModal` с полями.
- Состояния: loading (Skeleton), error (Toast), empty (EmptyState с CTA).

#### `/p/[slug]/refs/counterparty/[id]/page.tsx` — карточка
- Header: name + inn + type badge + кнопки edit/delete.
- DateRangePicker (влияет на stats, default: текущий месяц).
- `CurrencySplitStats` — RUB + CNY колонки (in/out/net/tx_count).
- TabLayout: **Статистика** (графики) | **Платежи** (DataTable транзакций) | **Документы** (список + uploader) | **Займы** (LoanShort cards).
- При удалении → confirm dialog → soft delete → redirect в список.

#### `/p/[slug]/refs/loans/page.tsx` — займы
- Фильтры: direction (Tabs: Получ./Выдан./Аффил./Закрытые), counterparty (Combobox).
- Таблица: контрагент, direction, principal, currency, start, maturity, status (badge), paid/remaining.
- Sum-footer: totals по direction.

#### `/p/[slug]/refs/loans/[id]/page.tsx` — карточка займа
- Header: контрагент, principal, rate, даты, contract_number.
- `LoanScheduleChart` (recharts Bar+Line): месяцы × amount, линия remaining principal.
- Таблица LoanPayment: дата, тип, сумма, связанная транзакция.
- Кнопка «Привязать транзакцию» → модалка поиска Transaction → match.

### 8.2 Модификации существующих

| Страница | Что |
|---|---|
| `/p/[slug]/layout.tsx` | +sidebar секция «СПРАВОЧНИКИ»: Контрагенты, Займы, Склады, Поставщики (расположение см. handoff) |
| `/p/[slug]/import/page.tsx` | +источник `FAKTURA_WB_BANK` в Select с подписью «WB Банк (Faktura.ru)» |
| `/p/[slug]/warehouse/page.tsx` | +колонка «Контрагент» + Combobox поиск по ИНН/имени |
| `/p/[slug]/supply-chain/*` (Supplier форма) | +поля ИНН и Номер контракта |
| `/p/[slug]/reports/page.tsx` | +Tab «Обороты по контрагентам» с pivot-таблицей (DataTable с dynamic columns по месяцам) |

### 8.3 API клиенты (`src/lib/api/`)

- `counterparty.ts` — 8 методов: list, getById, create, update, softDelete, listDocuments, uploadDocument, deleteDocument.
- `loans.ts` — 6 методов: list, getById, create, update, matchTransaction.
- `reports.ts` — +`getCounterpartyTurnovers(params)`.

**URL-билдинг:** только `URLSearchParams` (не template literals) — прецедент из `learnings.md` про `brand="H&M"`.

### 8.4 Типы (`src/types/api.ts`)

Новые: `Counterparty`, `CounterpartyListItem`, `CounterpartyDetail`, `CounterpartyStats`, `CounterpartyCreate`, `CounterpartyUpdate`, `CounterpartyType` (union 13 строк), `CounterpartyDocument`, `DocType`, `Loan`, `LoanDetail`, `LoanShort`, `LoanPayment`, `LoanDirection`, `LoanStatus`, `LoanPaymentType`, `CounterpartyTurnoversResponse`.

### 8.5 Компоненты (`src/components/`)

- `CounterpartyTypeBadge.tsx` — цвет+иконка по 13 типам.
- `CounterpartyCard.tsx` — карточка для карточной страницы (reuse в списке).
- `LoanScheduleChart.tsx` — recharts chart.
- `DocumentUploader.tsx` — drag&drop + progress + preview (reuse `react-dropzone`).
- `CurrencySplitStats.tsx` — две колонки RUB/CNY.

### 8.6 State / data fetching

- Всё через React Query (уже используется в проекте) — cache 60s, invalidate ключи `['counterparties', projectId]` / `['loans', projectId]` / `['report-counterparty-turnovers', projectId, period]` после мутаций.

### 8.7 Loading/Error/Empty (обязательные)

Каждая новая страница имеет Skeleton (loading), ErrorBoundary с кнопкой retry (error), EmptyState с CTA «Добавить контрагента / Создать займ / Запустить backfill» (empty).

## 9. Permissions

Пока все авторизованные пользователи в проекте могут всё (RLS по `project_id` уже enforced через middleware). Fine-grained permissions — отдельная спека (out of scope).

## 10. Cache Invalidation

Дополнить `backend/cache.py::invalidate_project_reports()` префиксами:
- `counterparty_list:`
- `counterparty_detail:`
- `counterparty_turnovers:`
- `loan_list:`

Вызывать после мутации любого из: Counterparty, Loan, LoanPayment, Transaction (имеющих counterparty_id).

## 11. Security

- **Upload:** MIME allowlist (`pdf, docx, xlsx, jpg, png`), max 20MB, magic-byte валидация (`python-magic`), filename sanitize.
- **ILIKE поиск по `q`:** escape `%` и `_` в пользовательском вводе (прецедент P4 в learnings).
- **project_id check** на каждом endpoint (`require_project_access(project_id)`).
- **SQL:** параметризованный — regex матчи передаются как bind-param, не f-string.
- **MinIO signed URL TTL = 300s** (короткий) чтобы URL не утекал.
- **counterparty_document soft-delete** с отметкой uploaded_by_user_id для audit.

## 12. Observability

- **Logs:** structured (structlog) с `project_id`, `counterparty_id`, `action`.
- **Metrics:** Prometheus counter `counterparty_upsert_total{via="etl|manual"}`, histogram `counterparty_turnovers_query_seconds`.
- **Scheduler:** нет новых job (backfill — разовый скрипт).

## 13. Rollout Plan

1. Merge PR → deploy на prod через `cd-production.yml`.
2. Миграция накатывается автоматически в entrypoint (Alembic upgrade, fail-fast).
3. После deploy — вручную через SSH:
   ```
   ssh dds-app 'docker compose exec backend python -m scripts.backfill_counterparties --project-id 1'
   ```
4. Smoke: открыть `/refs/counterparty`, проверить что 117+ ИНН из WB загружены.
5. Если issue — rollback через `/rollback` (SoftDeleteMixin позволит обратимо деактивировать созданные Counterparty).
