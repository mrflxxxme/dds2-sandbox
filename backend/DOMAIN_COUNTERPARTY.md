# Domain: Counterparties + Loans (Phase 2)

## Overview

Справочник контрагентов и займы, связанные с банковскими транзакциями. Заменяет
старый `CounterpartyCategory.cp_key` на полноценную сущность с типом, мультиролью,
документами и статистикой по периодам (мультивалюта RUB/CNY раздельно).

## Models

| Модель | Файл | Ключевые поля |
|--------|------|---------------|
| `Counterparty` | `backend/models/counterparty.py` | `project_id`, `inn`, `name`, `primary_type`, `secondary_types[]`, `contract_number`, `created_by_import`, SoftDeleteMixin |
| `CounterpartyDocument` | `backend/models/counterparty.py` | `minio_path`, `doc_type` (CONTRACT/CERTIFICATE/INVOICE/OTHER), `file_size`, `mime_type`, SoftDeleteMixin |
| `Loan` | `backend/models/loan.py` | `counterparty_id`, `direction` (INCOMING/OUTGOING/AFFILIATED), `principal`, `currency`, `rate`, `contract_number`, `status`, SoftDeleteMixin |
| `LoanPayment` | `backend/models/loan.py` | `loan_id`, `transaction_id` (UNIQUE partial), `payment_type` (DISBURSEMENT/PRINCIPAL_REPAY/INTEREST_PAY/PENALTY), `amount`, `currency`, `paid_at` |

### Multi-role
`Counterparty` имеет `primary_type` (главная роль) + `secondary_types: list[str]`
(дополнительные). Пример: ИП Кузнецов — `primary="FULFILLMENT"`,
`secondary=["CARRIER"]`.

### Categories (13 values for primary_type)
`SUPPLIER, FULFILLMENT, CARRIER, CUSTOMS_BROKER, DESIGNER, LEGAL, LANDLORD,
IT_SERVICE, MARKETPLACE, BANK, GOVERNMENT, AFFILIATED, OTHER`.

## Services

| Сервис | Файл | Главные методы |
|--------|------|----------------|
| `CounterpartyService` | `backend/services/counterparty_service.py` | `upsert_by_inn`, `upsert_by_contract`, `list`, `get`, `stats`, `create`, `update`, `soft_delete`, `upload_document`, `list_documents`, `delete_document` |
| `LoanService` | `backend/services/loan_service.py` | `create`, `list`, `get`, `get_detail`, `update`, `match_transaction`, `auto_link_from_etl` |
| `counterparty_turnovers` | `backend/services/reports/counterparty_turnovers.py` | `get_counterparty_turnovers` — pivot отчёт мультивалюта |

### Errors (custom exceptions)
- `CounterpartyConflictError` — дубликат INN или contract_number в проекте
- `CounterpartyNotFoundError`
- `LoanNotFoundError`
- `LoanPaymentAlreadyExistsError` — транзакция уже связана с LoanPayment
- `ProjectMismatchError` — tx.project_id ≠ loan.project_id

## Routers

| Router | Файл | Endpoints |
|--------|------|-----------|
| `/counterparties` | `backend/routers/counterparty.py` | GET list/detail, POST create, PATCH update, DELETE (soft), POST/GET/DELETE /documents |
| `/loans` | `backend/routers/loans.py` | GET list/detail, POST create, PATCH update, POST /{id}/payments/match |
| `/reports/counterparty-turnovers` | `backend/routers/reports.py` (extension) | GET pivot report |

### Register
Роутеры подключены в `backend/main.py` под `prefix="/api/v1"` + `Depends(get_current_user)`.

## ETL integration (master_logic.py)

`enrich_purpose(purpose: str) -> dict` применяет regex к `Transaction.purpose`:

| Regex | Поле / поведение |
|-------|-----------------|
| `RE_CONTRACT` | `contract_number` (для китайских поставщиков, min 6 digits) |
| `RE_UNK` | `unk_number` (валютный контроль) |
| `RE_MT103` | `mt103_ref` |
| `RE_DEPOSIT_PLACE` | `event_type2=DEPOSIT_PLACE`, `is_cashflow2=0` |
| `RE_DEPOSIT_RETURN` | `event_type2=DEPOSIT_RETURN`, `is_cashflow2=0` |
| `RE_DEPOSIT_INTEREST` | `event_type2=DEPOSIT_INTEREST`, `is_cashflow2=1` |
| `RE_LOAN_DISBURSEMENT` | `loan_payment_type=DISBURSEMENT` |
| `RE_LOAN_REPAY` | `loan_payment_type=PRINCIPAL_REPAY` |
| `RE_LOAN_INTEREST` | `loan_payment_type=INTEREST_PAY` |

### Автосоздание Counterparty при импорте
- Upsert по (project_id, inn) — всегда: обновляем имя если новое длиннее.
- Китайские поставщики без ИНН — upsert по (project_id, contract_number).
- `primary_type=OTHER`, `created_by_import=True` по умолчанию.
- Не перезаписываем `primary_type` если был выставлен вручную.

### Автопривязка LoanPayment
При ETL срабатывает `LoanService.auto_link_from_etl(transaction, purpose_match, project_id)`:
- Ищет ACTIVE Loan с тем же `contract_number` в проекте.
- Создаёт LoanPayment + проставляет `Transaction.loan_id`, `Transaction.loan_payment_id`.
- Если займ не найден — `loan_payment_type` всё равно проставляется (видимость в UI).

## Parser

`FAKTURA_WB_BANK` — XML Spreadsheet 2003 parser для банковских выписок WB Банка (Faktura.ru):
- `backend/etl/parsers/faktura.py` (`FakturaParser`, `parse_faktura_wb_bank`)
- Namespace: `urn:schemas-microsoft-com:office:spreadsheet`
- Колонки: Плат.пор № | Дата | Корреспондент | ИНН | КПП | Счёт | БИК | Вх.остаток | Дт | Кт | Назначение
- Зарегистрирован в `SOURCE_PARSERS` (`backend/etl/parsers/__init__.py`).

## Backfill

`scripts/backfill_counterparties.py --project-id=X [--dry-run]`:
1. Находит уникальные ИНН во всех `Transaction` проекта.
2. Создаёт `Counterparty` для тех что отсутствуют (или берёт `primary_type` из `KNOWN_INN_TYPES`).
3. Линкует `Transaction.counterparty_id` для строк где он NULL.
4. Идемпотентный: повторный запуск создаёт 0 новых записей.

Тесты: `tests/test_backfill_script.py`.

## Cache

Prefixes (инвалидируются в `invalidate_project_reports(project_id)`):
- `counterparty_list`, `counterparty_detail`
- `reports:counterparty_turnovers`
- `loan_list`

Все мутации (create/update/delete/match) вызывают `invalidate_project_reports(project_id)`.

## Tests

| Файл | Покрытие |
|------|----------|
| `tests/test_master_logic_regex.py` | 100% регексов (положительные + отрицательные) |
| `tests/test_faktura_parser.py` | Парсер на fixture (10 строк), ошибки формата |
| `tests/test_counterparty_service.py` | upsert_by_inn, upsert_by_contract, list, stats, CRUD, soft_delete, multi-tenancy |
| `tests/test_loan_service.py` | create, match_transaction, auto_link_from_etl, get_detail |
| `tests/test_counterparties_api.py` | HTTP endpoints, 404/409, project isolation |
| `tests/test_loans_api.py` | HTTP endpoints /loans |
| `tests/test_counterparty_turnovers_report.py` | Pivot отчёт, мультивалюта |
| `tests/test_backfill_script.py` | Идемпотентность, dry-run, known INN type |

## Migration

`migrations/versions/cp01_counterparties_loans.py` создаёт:
- Таблицы `counterparty`, `counterparty_document`, `loan`, `loan_payment`
- Новые колонки в `transactions`: `counterparty_id`, `contract_number`, `unk_number`, `loan_id`, `loan_payment_id`, `loan_payment_type`
- Partial indexes (через CONCURRENTLY):
  - `uq_counterparty_project_inn` UNIQUE (project_id, inn) WHERE inn IS NOT NULL AND is_deleted=false
  - `uq_counterparty_project_contract` UNIQUE (project_id, contract_number) WHERE contract_number IS NOT NULL AND is_deleted=false
  - `ix_counterparty_active` (project_id, primary_type) WHERE is_deleted=false
  - `uq_loan_payment_transaction` UNIQUE (transaction_id) WHERE transaction_id IS NOT NULL

## File list (быстрая навигация)

```
backend/models/counterparty.py      # Counterparty, CounterpartyDocument
backend/models/loan.py              # Loan, LoanPayment
backend/schemas/counterparty.py     # Pydantic schemas
backend/schemas/loan.py
backend/services/counterparty_service.py
backend/services/loan_service.py
backend/services/reports/counterparty_turnovers.py
backend/routers/counterparty.py
backend/routers/loans.py
backend/etl/parsers/faktura.py      # FAKTURA_WB_BANK parser
backend/etl/master_logic.py         # regex + enrich_purpose
scripts/backfill_counterparties.py  # backfill script
migrations/versions/cp01_counterparties_loans.py
```
