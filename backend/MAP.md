# Backend Navigation Map (для AI-агентов)

Читай этот файл ОДИН РАЗ при старте задачи — он покажет где что лежит.

## Типовые паттерны

### Добавить API endpoint
1. Schema: `schemas/{domain}.py` — Pydantic модели
2. Service: `services/{domain}_service.py` — бизнес-логика
3. Router: `routers/{domain}.py` — HTTP handlers
4. Test: `tests/test_{domain}_service.py` или `tests/test_api_{domain}.py`

### Добавить модель
1. Model: `models/{domain}.py` + добавить в `models/__init__.py` (`__all__`)
2. Migration: `alembic revision --autogenerate -m "..."` (через `DATABASE_URL_SYNC`, файл появится в `migrations/versions/`)
3. Schema: `schemas/{domain}.py` + добавить в `schemas/__init__.py`
4. Service → Router → Test

### Транзакции
- Импорт/ETL: `etl/` (парсеры VTB, WB)
- Поиск, категоризация, INBOX: `services/transactions_service.py`
- Курсы валют: `services/fx_service.py` — извлечение CNY/RUB из назначений платежей VTB, backfill, lookup (FxRate)

### Справочники
- CRUD (Account, Override, CounterpartyCategory, OpeningBalance): `services/refs_service.py`

### Изменить отчёт
- ДДС: `services/reports/dds.py`
- БДР: `services/wb_bdr_service.py` (+ `bdr_loaders.py`, `bdr_enrichment.py`, `wb_bdr_helpers.py`)
- ОПИУ: `services/opiu_service.py` (+ `opiu_helpers.py`)
- Dashboard: `services/reports/dashboard.py`
- Balance: `services/reports/balance.py`
- ВСЕГДА: `invalidate_cache()` после изменений

### WB интеграция
- HTTP клиент: `integrations/wb_api.py`
- Resilience (per-project circuit breaker, retry): `integrations/resilience.py`
- Воронка/реклама: `services/funnel/` (sync.py, unified_sync.py, wb_funnel_api.py, wb_advertising_api.py, ad_campaigns_service.py)
- Финансы WB: `wb_finance_sync.py` (в корне `services/`, не в funnel/)
- WB акции/остатки: `scheduler/jobs/wb_stocks.py`
- Scheduler jobs: `scheduler/jobs/`
- Rate limiting: asyncio.Semaphore в wb_api.py
- Интеграционные ключи: `services/integrations_service.py` — CRUD ключей (шифрование/маскирование), синхронизации, payouts

### Себестоимость
- Парсеры: `etl/cost_parsers.py` + `etl/cost_parser_helpers.py`
- Вспомогательные парсеры: `etl/parsers/` (vtb.py, wb.py, order_city_parser.py)
- Расчёт: `services/cost/` (duty.py, helpers.py, items.py, nomenclature.py, orders.py, plan_gen.py)
- История себестоимости: `services/cost_history_service.py`
- Тарифы WB: `services/tariff_service.py` — CRUD комиссий WB, загрузка из xlsx, карта среднего выкупа
- Налоги: `services/tax_service.py` — CRUD помесячных налоговых ставок проекта (TaxRate)

### AI агенты
- Точка входа: `services/ai/orchestrator.py`
- Агенты: `services/ai/agents/` (analyst, financier, marketer, advertiser, supply_manager, logistics, logistician)
- Базовый класс: `services/ai/agents/base.py`
- Tools: `services/ai/tools/` (finance.py, marketing.py, logistics.py, shipping.py, supply.py, funnel.py, warehouse.py, health.py, planning.py, products.py, reports.py, common.py)
- Промпты: `services/ai/prompts/`
- Память (BrandNote): `services/ai/memory.py`
- LLM клиент: `services/ai/llm_client.py`
- Синтезатор ответов: `services/ai/synthesizer.py`

### Склад
- CRUD: `services/warehouse_crud.py`
- Сервис: `services/warehouse_service.py`, `warehouse_stock_service.py`, `warehouse_stock_engine.py`
- Входящие/исходящие: `services/warehouse_inbound.py`, `services/warehouse_outbound.py`
- Расчёт потребности: `services/warehouse_need_service.py`
- Прогноз остатков: `services/stock_forecast_service.py` — прогноз выбытия по трендам продаж (wb / wb_rf / wb_rf_transit), светофор
- FBO поставки WB: `services/fbo_supply_service.py`
- Гео данные складов: `services/warehouse_geo.py`, `services/warehouse_geo_data.py`

### Планирование
- CRUD: `services/planning/crud.py`
- Кэшфлоу: `services/planning/cashflow.py`
- Таможня: `services/planning/customs.py`
- WB выплаты: `services/planning/wb.py`
- Связи платежей с фактом: `services/planning/fact_links.py`

### Цепочка поставок (Supply Chain)
- Пакет: `services/supply_chain/` (factory_orders.py, vehicle_delivery.py)
- Заказы на производство: `services/supply_chain/factory_orders.py` — CRUD FactoryOrder, split_to_vehicles
- Доставка (Vehicle/CostOrder): `services/supply_chain/vehicle_delivery.py` — CRUD транспортов, статусы (VehicleStatus), обзор цепочки

### Мониторинг и здоровье
- Метрики синхронизаций: `services/monitoring_service.py` — sync health, статус scheduler, обзор за 24ч
- Ежедневная проверка: `services/health_check_service.py` — дефицит на складах WB, просроченные сборки, здоровье Category-A, неликвид

### География заказов
- Агрегация по городам: `services/order_geography_service.py` — WB заказы по городам/регионам, график по дням, фильтры (бренд, категория, артикул)

### Telegram
- Бот-сервис: `services/telegram_service.py` — deep link auth, привязка чатов, BrandNote, TMA авторизация
- Бот (polling): `integrations/telegram_bot.py`

## Типовые импорты
```python
from backend.models import User, Transaction, Project, WbApiKey
from backend.models import IntegrationKey, SyncLog, WbFunnelDaily
from backend.schemas import TransactionSchema, ProjectSchema
from backend.utils.time import utcnow
from backend.cache import cached, invalidate_cache
from backend.database import get_db, AsyncSession
from backend.models.mixins import SoftDeleteMixin
```

## Где искать
| Что ищу | Где |
|---------|-----|
| Модель таблицы | `models/{domain}.py` + `models/__init__.py` |
| API endpoint | `routers/` (assembly, auth, cost, fbo_supplies, funnel, import_txn, integrations, monitoring, planning, planning_customs, planning_wb_payouts, projects, refs, reports, reports_stock, reports_wb, supply_chain, telegram, telegram_miniapp, telegram_webhook, warehouse, ws) |
| Бизнес-логика | `services/` (domain services + `project_settings_service.py` для мутаций настроек проекта). NB: `assembly/`, `fbo_supply/`, `supply_chain/` — пакеты (разбиты из монолитов) |
| Pydantic schemas | `schemas/` (anomaly, assembly, auth, capital, common, cost, imports, integrations, monitoring, planning, refs, reports, supply_chain, tariff, tax, telegram, transactions, warehouse, wb_fbo) |
| Импорт файлов / ETL | `etl/` |
| WB HTTP клиент | `integrations/wb_api.py` |
| Telegram бот | `integrations/telegram_bot.py` |
| Фоновые задачи | `scheduler/jobs/` (ai_digest, fbo_supplies, funnel, health_check, prewarm, wb_finance, wb_stocks) |
| Кэш конфигурация | `cache.py` |
| Утилиты | `utils/` (time.py, crypto.py, file_validation.py, queries.py, telegram.py, telegram_auth.py) |
| Тесты | `tests/` (в корне проекта, рядом с `backend/`) |
| Миграции | `migrations/versions/` (в корне проекта, рядом с `alembic.ini`) |
| Алембик конфиг | `alembic.ini` + `migrations/env.py` (корень проекта) |

## Ключевые модели
| Модель | Файл | Назначение |
|--------|------|-----------|
| `User`, `Project`, `ProjectMember` | `models/auth.py` | Аутентификация, мультиарендность |
| `Transaction`, `ImportLog` | `models/transactions.py` | ДДС транзакции |
| `IntegrationKey` (= `WbApiKey`) | `models/integrations.py` | API-ключи WB |
| `SyncLog` | `models/integrations.py` | Статус синхронизаций |
| `WbFunnelDaily`, `WbAdCampaign` | `models/integrations.py` | WB воронка/реклама |
| `WbFinanceRow`, `WbFinanceSyncLog` | `models/wb_finance.py` | WB финансовые отчёты |
| `Order`, `PlannedPayment`, `BrandPlan` | `models/planning.py` | Планирование |
| `Nomenclature`, `CostOrder` | `models/cost.py` | Себестоимость |
| `Warehouse`, `WarehouseStock` | `models/warehouse.py` | Склад |
| `WbFboSupply` | `models/wb_fbo.py` | FBO поставки |
| `BrandNote`, `TelegramBotUser` | `models/telegram.py` | Telegram / AI память |
| `Account`, `CategoryRef` | `models/refs.py` | Справочники |
| `FxRate` | `models/fx_rates.py` | Курсы валют |

## Тесты (make-команды)
```bash
make test            # Все тесты
make test-fast       # Быстрые тесты (без медленных)
make test-changed    # Только тесты затронутых файлов
make test-unit       # Только unit-тесты
make lint            # Линтер
```

## Алиасы
- `WbApiKey` = `IntegrationKey` (обратная совместимость, определён в `models/__init__.py`)
