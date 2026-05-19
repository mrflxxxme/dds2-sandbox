# DOMAIN_TRANSACTIONS — Транзакции и импорт выписок

## Таблицы
| Модель | Назначение | Ключ / constraint |
|--------|------------|-------------------|
| `Transaction` | Главная таблица ДДС | `txn_id` уникален, `project_id` обязателен |
| `CategoryChangeLog` | Аудит ручной категоризации | — |
| `ImportLog` | История импортов | — |

## Бизнес-правила
- **Дедупликация:** `ON CONFLICT` по `txn_id` (хэш от даты + суммы + назначения).
- **Приоритет категорий:** overrides (по `txn_id`) → `counterparty_categories` (по `cp_key`) → `UNASSIGNED`.
- **Типы транзакций:**
  - `REGULAR` — влияет на cash flow (`is_cashflow2 = 1`).
  - `FX` — конвертация валют (`is_cashflow2 = 0`).
  - `INTERNAL` — внутренние переводы (`is_cashflow2 = 0`).
- **Pipeline импорта:** файл → parse → `master_logic` enrich → bulk insert → `sync_payments` → `sync_wb_payouts`.
- **ETL использует SYNC engine** (`SyncSessionLocal`), обёрнут в `run_in_executor`.

## Зависимости
- `models/refs.py` — `Account`, `CounterpartyCategory`, `Override`.
- `services/fx_service.py` — конвертация CNY→RUB для отображения.
- Все отчёты (`reports/`) читают `transactions`.
- Planning (`sync_payments`) линкует факт с планом; WB BDR/OPIU берут данные из `transactions`.

## Грабли
- ETL sync **не атомарный** (несколько flush/commit) — при сбое возможны partial data.
- FX-конвертация выполняется в роутере `import_txn.py` — должна быть в сервисе.
- `create_auto_rule` принимает `dict` вместо Pydantic-схемы.
- При изменении `master_logic` — обязательно обновить `test_master_logic.py`.

## Файлы
- `etl/service.py` — ETL-оркестратор (импорт выписок).
- `etl/parsers/vtb.py`, `etl/parsers/wb.py` — парсеры ВТБ и WB.
- `etl/parsers/helpers.py` — общие утилиты парсеров.
- `etl/parsers/order_city_parser.py` — парсер городов из WB заказов.
- `etl/master_logic.py` — обогащение транзакций (категоризация, event_type, txn_id).
- `etl/sync_payments.py` — синхронизация плановых платежей с фактом.
- `etl/sync_wb_payouts.py` — синхронизация WB-выплат.
- `services/transactions_service.py` — CRUD + поиск транзакций.
- `services/auto_categorize.py` — правила автокатегоризации.
- `routers/import_txn.py` — HTTP endpoints.
- `models/transactions.py` — `Transaction`, `CategoryChangeLog`, `ImportLog`.
- `schemas/transactions.py`, `schemas/imports.py`.

## Кэш
- После импорта: `invalidate_cache("reports")` — сбрасывает все отчёты.
- После категоризации: `invalidate_cache("reports:balance")`, `reports:dashboard`, `reports:dds_month`.
