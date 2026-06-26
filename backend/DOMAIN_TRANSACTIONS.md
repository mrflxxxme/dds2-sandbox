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
- **Pipeline импорта:** источник → нормализация (NORM_COLS) → **`persist_df`** (`master_logic` enrich → upsert контрагентов → дедуп по `txn_id` → bulk insert → `sync_payments` → `sync_wb_payouts` → `sync_payment_requests` → `sync_shipment_payments` → обогащение контрагентов). `persist_df` — общее ядро для ручного файлового импорта и авто-синка. Дедуп по `txn_id` — И против существующих строк БД, И **внутри батча** (одна выписка может содержать две идентичные строки, напр. две комиссии 15₽ с тем же назначением → один `txn_id`; без within-batch-дедупа второй INSERT падает на `transactions_txn_id_key` и откатывает весь импорт). `sync_payment_requests` авто-матчит «заявки на оплату» (DRAFT_CREATED→PAID), `sync_shipment_payments` — прямую связку заборов с дебетом (без заявки) — см. [DOMAIN_PAYMENT_REQUEST.md](DOMAIN_PAYMENT_REQUEST.md).
- **ETL использует SYNC engine** (`SyncSessionLocal`), обёрнут в `run_in_executor`.
- **Авто-синк выписки ВБ Банка (Faktura.ru API):** `services/faktura_service.py` тянет выписку по всем счетам (JSON API, `business.faktura.ru/erpws/2.0`, JWT-auth) 4×/день (06/12/18/23 МСК, scheduler) и кормит `persist_df` — та же категоризация/дедуп/межсчётные перемещения, что и ручной импорт. Креды — в `IntegrationKey(service="faktura")`. **Ручной запуск:** карточка «🏦 Faktura — выписка ВБ Банка» на `/import` (кнопка «Обновить») → `POST /integrations/faktura/sync`; статус (`GET .../status`: configured/login/last_sync_at/last_run). Контракт API и риски — в памяти `faktura-bank-api-integration`.
- **Межсчётные перемещения:** определяются в `master_logic` — если `counterparty_account` ∈ `our_accounts` (оба расчётных счёта = `Account.is_our_account=True`) → `is_internal`, `event_type2=INTERNAL_TRANSFER`, `is_cashflow2=0` (исключены из кэшфлоу). Работает одинаково для файла и API.

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
- `etl/service.py` — ETL-оркестратор (`import_statement` для файла; `persist_df` — общее ядро).
- `etl/parsers/vtb.py`, `etl/parsers/wb.py` — парсеры ВТБ и WB.
- `etl/parsers/faktura.py` — парсер ручной .xls-выписки ВБ Банка; `etl/parsers/faktura_api.py` — конвертер JSON API-выписки в NORM_COLS (паритет `txn_id` с .xls).
- `integrations/faktura_api.py` — async-клиент Faktura API (auth/accounts/motions).
- `services/faktura_service.py` — оркестрация авто-синка; `scheduler/jobs/faktura_statement_sync.py` — джоб 4×/день; `routers/integrations_faktura.py` — ручной триггер `/integrations/faktura/sync` + `/status`.
- `scripts/setup_faktura_account.py` — заливка кредов Faktura (Fernet).
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
