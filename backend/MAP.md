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
| Telegram-бот | `integrations/telegram_bot.py` |
| Фоновые задачи | `scheduler/jobs/` |
| Кэш | `cache.py` |
| Утилиты | `utils/` (time, crypto, file_validation, queries) |
| Тесты | `tests/` (корень проекта) |
| Миграции | `migrations/versions/` + `alembic.ini` (корень) |
| Справочники | `services/refs_service.py` (Account, Override, CounterpartyCategory) |
| Курсы валют | `services/fx_service.py` (`FxRate`) |
| Мониторинг и health | `services/monitoring_service.py`, `health_check_service.py` |
| География заказов | `services/order_geography_service.py` |

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

## Тесты
`make test` · `test-fast` · `test-changed` · `test-unit` · `lint`. Прямо: `docker compose exec backend pytest tests/ -x --tb=short`.

## Алиасы
`WbApiKey` = `IntegrationKey` (обратная совместимость, в `models/__init__.py`).
