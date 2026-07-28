# Backend Navigation Map

Где что лежит и как добавить типовое. Читать при старте задачи. Бизнес-логику доменов смотри в `DOMAIN_*.md` (каталог — `DOMAIN_INDEX.md`).

## Добавить API endpoint
1. Schema: `schemas/{domain}.py` — Pydantic-модели.
2. Service: `services/{domain}_service.py` — бизнес-логика.
3. Router: `routers/{domain}.py` — HTTP-хендлеры; `Depends(rate_limit_write)` на write-методах.
4. Test: `tests/test_{domain}_service.py` или `tests/test_api_{domain}.py`.

## Добавить модель
1. Model: `models/{domain}.py` + регистрация в `models/__init__.py` (`__all__`).
2. Migration: `alembic revision --autogenerate -m "..."` (через `DATABASE_URL_SYNC`).
3. Schema → Service → Router → Test.

## Типовые импорты
```python
from backend.models import User, Transaction, Project, WbApiKey
from backend.schemas import TransactionSchema, ProjectSchema
from backend.utils.time import utcnow
from backend.cache import cached, invalidate_cache
from backend.database import get_db, AsyncSession
from backend.models.mixins import SoftDeleteMixin
```

## Где искать
| Что | Где |
|-----|-----|
| Модели таблиц | `models/{domain}.py` + `models/__init__.py` |
| API endpoints | `routers/` |
| Бизнес-логика | `services/` (`assembly/`, `fbo_supply/`, `supply_chain/`, `planning/`, `cost/`, `funnel/`, `ai/` — пакеты) |
| Pydantic schemas | `schemas/` |
| Импорт файлов / ETL | `etl/` (парсеры VTB, WB) |
| WB HTTP-клиент | `integrations/wb_api.py` (+ `resilience.py`) |
| Фулфилмент (skladbot.ru, wmscelicom, migfull) | `integrations/skladbot_client.py`, `integrations/wmscelicom_client.py`, `integrations/migfull_client.py`, `services/fulfillment_service.py`, `routers/fulfillment.py`, job `scheduler/jobs/fulfillment_sync.py` |
| FF billing (тарифы, хранение, счета) | `models/ff_billing.py`, `schemas/ff_billing.py`, `services/ff_billing/`, `routers/ff_billing.py`, `etl/sync_ff_invoices.py`, job `scheduler/jobs/ff_storage_snapshot.py` |
| WB FBS (склад продавца: остатки по `chrtId`, задания, поставки) | `models/wb_fbs.py`, `schemas/wb_fbs.py`, `integrations/wb_fbs_api.py`, `services/wb_fbs/` (`client_factory`, `warehouse_service`, `stock_service`, `orders_service`, `supplies_service`), `routers/wb_fbs.py`, job `scheduler/jobs/wb_fbs.py` |
| Ручное количество FBS по товару (0 = не отдавать, N = потолок) | `services/wb_fbs/stock_service.py:set_overrides` (хранение) + `_apply_override` / `_group_override_limit` (применение); ручка `POST /fbs/stock/override` |
| FBO-гейт FBS («отдаём только то, чего нет на складах WB») | `services/wb_fbs/stock_service.py:_load_fbo` / `_fbo_allowed_names` / `_fbo_blocks`; порог — `WbFbsWarehouse.fbo_max_qty` |
| Режим склада продавца (`observe` — не писать в WB / `translate`) | `models/wb_fbs.py:FbsWarehouseMode`, гейт — `services/wb_fbs/stock_service.py:_push_stocks_locked` |
| Обратный гейт FBS → сборка | `services/warehouse_stock_engine.py:get_open_fbs_reserved` → `services/assembly/crud.py:_validate_available_for_assembly` |
| Telegram-бот | `integrations/telegram_bot.py` |
| Фоновые задачи | `scheduler/jobs/` |
| Кэш | `cache.py` |
| Утилиты | `utils/` (time, crypto, file_validation, queries) |
| Тесты | `tests/` (корень проекта) |
| Миграции | `migrations/versions/` + `alembic.ini` (корень) |
| Займы (частные, ВКЛ, аннуитет) | `models/loan.py`, `schemas/loan.py`, `routers/loans.py`, `services/loan_service.py` (CRUD, линия), `loan_interest.py` (движок: проценты по дням, комиссии, график), `loan_schedule.py` (график платежей + разовые комиссии + план погашения долга `is_debt_plan` — строки, которые НЕ подменяют начисление), `loan_daily.py` (начисление по дням: займ и портфель, «сколько стоит день»), `loan_mirror.py` (займ между своими проектами: две книги одного договора), `loan_analytics.py` (дашборд по КАЛЕНДАРНЫМ месяцам, свод по заёмщикам, прогноз, `lent_summary` — сколько должны нам), `loan_import.py`; лендер-портал — `lender_portal_service.py` |
| Зарплата (команды × бренды, тарифная лестница, ведомость) | `models/payroll.py`, `schemas/payroll.py`, `services/payroll_service.py` (недели месяца по четвергу, ступень лестницы, скоупы бренд/категория/бренд×категория — композит вытесняет общий, членство в команде с valid_from/valid_to по месяцам, оклад-периоды `PayrollSalaryPeriod` — оклад месяца = период с max(valid_from) ≤ месяц, `resolve_salary`; ведомость `payroll:sheet`, помесячный ФОТ с разбивкой «Менеджеры»/по должностям `payroll:accruals2` для подстрок ОПиУ, БДР зовётся с `include_cost_tax=False`), `routers/payroll.py` (page-гейт `salary`; тариф — только admin: лестница глобальная, без `project_id`); «Агентство» (консалтинг) — `services/payroll_agency_service.py`: клиенты с форматом оплаты ПЕРИОДАМИ (`PayrollClientBillingPeriod`: fixed/percent/profit_share, формат месяца = период с max(valid_from) ≤ месяц), fee от БДР кабинета клиента (кросс-проектно, осознанно) или ручных сумм `PayrollClientEntry`, ведомость `payroll:agency_sheet`, manager-доля уходит команде строкой «· (агентство)» в ведомость ЗП; исключение выплат сотрудников из opex — `services/opiu_helpers.py:build_opex_by_type_sql` (скоуп: процентщики — вся история, окладники — с min(valid_from), без начислений — не исключаем) |
| Справочники | `services/refs_service.py` (Account, Override, CounterpartyCategory) |
| Курсы валют | `services/fx_service.py` (`FxRate`) |
| Мониторинг и health | `services/monitoring_service.py`, `health_check_service.py` |
| География заказов | `services/order_geography_service.py` |
| Вайбкодинг (телеметрия репозитория) | `services/vibe_service.py`, `routers/vibe.py`, `models/vibe.py` — БЕЗ `project_id` намеренно; доступ по строке в `vibe_authors`, ингест зовёт CI по SSH (не HTTP) |

## Ключевые модели
| Модель | Файл | Назначение |
|--------|------|-----------|
| `User`, `Project`, `ProjectMember` | `models/auth.py` | аутентификация, мультиарендность |
| `Transaction`, `ImportLog` | `models/transactions.py` | ДДС-транзакции |
| `IntegrationKey` (= `WbApiKey`), `SyncLog` | `models/integrations.py` | ключи WB, статус синков |
| `Order`, `PlannedPayment`, `BrandPlan` | `models/planning.py` | планирование |
| `Nomenclature`, `CostOrder` | `models/cost.py` | себестоимость |
| `Warehouse`, `WarehouseStock` | `models/warehouse.py` | склад |
| `FulfillmentStock`, `FulfillmentRequest` | `models/fulfillment.py` | зеркало остатков/заявок внешнего ФФ (skladbot, wmscelicom, migfull) |
| `Account`, `CategoryRef` | `models/refs.py` | справочники |
| `WarehouseTariff`, `FfStorageDaily`, `FfInvoice`, `FfInvoiceLine` | `models/ff_billing.py` | тарифы услуг ФФ, хранение, счета |
| `WbFbsWarehouse`, `WbFbsWarehouseLink`, `WbFbsStockOverride`, `WbFbsOrder`, `WbFbsSupply` | `models/wb_fbs.py` | склады продавца WB, привязка к нашим, ручное количество по товару, сборочные задания и поставки FBS |

## Тесты
`make test` · `test-fast` · `test-changed` · `test-unit` · `lint`. Прямо: `docker compose exec backend pytest tests/ -x --tb=short`.

## Алиасы
`WbApiKey` = `IntegrationKey` (обратная совместимость, в `models/__init__.py`).
