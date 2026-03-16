# Domain: Transactions & Import

## Ownership
Файлы этого домена:
- `etl/service.py` — ETL-оркестратор (импорт выписок)
- `etl/parsers/vtb.py` — парсер ВТБ
- `etl/parsers/wb.py` — парсер WB
- `etl/parsers/helpers.py` — общие утилиты парсеров
- `etl/parsers/order_city_parser.py` — парсер городов из WB заказов
- `etl/master_logic.py` — обогащение транзакций (категоризация, event_type, txn_id)
- `etl/sync_payments.py` — синхронизация плановых платежей с фактом
- `etl/sync_wb_payouts.py` — синхронизация WB-выплат
- `services/transactions_service.py` — CRUD + поиск транзакций
- `services/auto_categorize.py` — правила автокатегоризации
- `routers/import_txn.py` — HTTP endpoints
- `models/transactions.py` — Transaction, CategoryChangeLog, ImportLog
- `schemas/transactions.py`, `schemas/imports.py`
- `tests/test_parsers.py`, `tests/test_master_logic.py`, `tests/test_api_transactions.py`

## Tables
- `transactions` — главная таблица (txn_id уникален, project_id обязателен)
- `category_change_log` — аудит ручной категоризации
- `import_log` — история импортов

## Business Rules
1. **Дедупликация:** ON CONFLICT по txn_id (хэш от даты+суммы+назначения)
2. **Приоритет категорий:** overrides (txn_id) → counterparty_categories (cp_key) → UNASSIGNED
3. **Типы транзакций:**
   - REGULAR — влияет на cash flow (is_cashflow2=1)
   - FX — конвертация валют (is_cashflow2=0)
   - INTERNAL — внутренние переводы (is_cashflow2=0)
4. **Импорт:** файл → parse → master_logic enrich → bulk insert → sync_payments → sync_wb_payouts
5. **ETL использует SYNC engine** (SyncSessionLocal), обёрнут в run_in_executor

## Dependencies (этот домен зависит от)
- `models/refs.py` — Account, CounterpartyCategory, Override
- `services/fx_service.py` — конвертация CNY→RUB для отображения
- `cache.py` — инвалидация `reports:*` после импорта

## Dependencies (от этого домена зависят)
- Все отчёты (reports/) читают transactions
- Planning (sync_payments) линкует факт с планом
- WB BDR/OPIU берут данные из transactions

## Known Issues & Gotchas
- ETL sync — НЕ атомарный (несколько flush/commit). При сбое — partial data
- `routers/import_txn.py:126-171` — FX-конвертация в роутере (должна быть в сервисе)
- `create_auto_rule` принимает `dict` вместо Pydantic-схемы
- При изменении master_logic → ОБЯЗАТЕЛЬНО обновить test_master_logic.py (300 строк тестов)

## Cache Invalidation
После импорта: `await invalidate_cache("reports")` — сбрасывает ВСЕ отчёты
После категоризации: `await invalidate_cache("reports:balance")`, `await invalidate_cache("reports:dashboard")`, `await invalidate_cache("reports:dds_month")`
