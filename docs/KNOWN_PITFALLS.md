# Known Pitfalls (для AI-агентов)

Проверь перед коммитом — эти ошибки агенты повторяют регулярно.

## P1: CancelledError в scheduler jobs
**НЕПРАВИЛЬНО:** `except Exception` — не ловит CancelledError (это BaseException в Python 3.9+)
**ПРАВИЛЬНО:**
```python
try:
    await long_running_task()
except asyncio.CancelledError:
    logger.warning("Task cancelled")
    raise  # ВСЕГДА re-raise CancelledError
except Exception as e:
    logger.error(f"Task failed: {e}")
```

## P2: invalidate_cache с wildcard
**НЕПРАВИЛЬНО:** `invalidate_cache("reports:*")` — prefix уже добавляет `:*`
**ПРАВИЛЬНО:** `invalidate_cache("reports")`

## P3: PgBouncer + prepared statements
**НЕПРАВИЛЬНО:** `prepared_statement_cache_size=100` — ломает transaction pooling
**ПРАВИЛЬНО:** `prepared_statement_cache_size=0` в DATABASE_URL

## P4: ilike без экранирования
**НЕПРАВИЛЬНО:** `.ilike(f"%{search}%")` — пользователь вводит `%` или `_` → сломанный запрос
**ПРАВИЛЬНО:** Определи helper локально или скопируй из transactions_service.py:
```python
def _escape_like(s: str) -> str:
    return s.replace("%", r"\%").replace("_", r"\_")

query = select(Model).where(Model.name.ilike(f"%{_escape_like(search)}%", escape="\\"))
```

## P5: sync_log остаётся RUNNING
**НЕПРАВИЛЬНО:** обновлять sync_log только в try блоке
**ПРАВИЛЬНО:** ВСЕГДА обновлять в finally:
```python
try:
    sync_log.status = "running"
    await do_sync()
    sync_log.status = "completed"
except Exception:
    sync_log.status = "failed"
finally:
    sync_log.finished_at = utcnow()
    await db.commit()
```

## P6: Float для денежных сумм
**НЕПРАВИЛЬНО:** `Column(Float)` → потеря точности при больших суммах
**ПРАВИЛЬНО:** `Column(Numeric(18, 2))`

## P7: Деструктивные операции без soft_delete
**НЕПРАВИЛЬНО:** `await db.delete(model)` для моделей с SoftDeleteMixin
**ПРАВИЛЬНО:** `model.soft_delete()` + `await db.commit()`

## P8: WB deductions — ad vs loan vs other
- `ad_deduction` → отдельная статья расходов (реклама), НЕ включать в to_pay
- `loan_deduction` → финансовая операция, НЕ включать в операционную прибыль
- `other_deduction` → операционные расходы
- Менял типы? → обнови ОБА: `services/wb_bdr_service.py` И `services/opiu_service.py`

## P9: datetime.utcnow()
**НЕПРАВИЛЬНО:** `datetime.utcnow()` или `datetime.now()` — deprecated, не timezone-aware
**ПРАВИЛЬНО:** `from backend.utils.time import utcnow`

## P10: SQL без project_id
**НЕПРАВИЛЬНО:** `select(Model).where(Model.name == name)`
**ПРАВИЛЬНО:** `select(Model).where(Model.project_id == project_id, Model.name == name)`

## P11: Миграции — неправильный DATABASE_URL
**НЕПРАВИЛЬНО:** запускать `alembic upgrade head` через `DATABASE_URL` (PgBouncer) → ошибки с prepared statements
**ПРАВИЛЬНО:** `DATABASE_URL_SYNC` для всех Alembic операций (прямое подключение к PostgreSQL)
- Alembic конфиг и `migrations/` находятся в **корне проекта**, не в `backend/`

## P12: WbApiKey vs IntegrationKey
**НЕПРАВИЛЬНО:** импортировать `WbApiKey` как отдельную модель, создавать новую таблицу
**ПРАВИЛЬНО:** `WbApiKey = IntegrationKey` — это алиас, определён в `models/__init__.py`
```python
from backend.models import WbApiKey  # это IntegrationKey
```

## P13: Бизнес-логика в роутере
**НЕПРАВИЛЬНО:** SQL запросы, вычисления, внешние вызовы в `routers/{domain}.py`
**ПРАВИЛЬНО:** вся логика в `services/{domain}_service.py`, роутер только HTTP (валидация, auth, вызов сервиса)

## P14: Тесты не в `tests/` (корень проекта)
**НЕПРАВИЛЬНО:** класть тесты в `backend/tests/`
**ПРАВИЛЬНО:** тесты живут в `tests/` рядом с `backend/` и `migrations/` (корень проекта)

## P15: Кэш-ключ без project_id
**НЕПРАВИЛЬНО:** `@cached(prefix="reports:dds", ttl=300)` — ключ не содержит project_id → один проект видит данные другого
**ПРАВИЛЬНО:** кэш-ключ ДОЛЖЕН содержать project_id (обычно передаётся через аргументы функции, которые включаются в ключ автоматически)

## P16: services/ai — неверная точка входа
- Оркестратор: `services/ai/orchestrator.py` (классификация интента, маршрутизация)
- Агенты: `services/ai/agents/{role}.py` (7 агентов + base.py (базовый класс): analyst, financier, marketer, advertiser, supply_manager, logistics, logistician)
- Tools: `services/ai/tools/` (finance, marketing, logistics, shipping, supply + all_tools.py)
- Промпты: `services/ai/prompts/` (по одному файлу на агента + orchestrator + synthesizer)
- НЕ путать `services/ai/tools/logistics.py` и `services/ai/agents/logistics.py` — это разные файлы

## P17: WB Finance sync — не в funnel/
**НЕПРАВИЛЬНО:** искать wb_finance_sync.py в `services/funnel/`
**ПРАВИЛЬНО:** `services/wb_finance_sync.py` — в корне services/, не во вложенной директории
- Scheduler job: `scheduler/jobs/wb_finance.py`

## P18: .scalars().all() без .limit()
**НЕПРАВИЛЬНО:** `(await db.execute(select(Model).where(...))).scalars().all()` на больших таблицах
**ПРАВИЛЬНО:** всегда добавлять `.limit(N)` при выборке коллекций

## P19: Cross-tenant через дочерние сущности без project_id
**НЕПРАВИЛЬНО:** `select(CostOrderItem).where(CostOrderItem.order_no == order_no)` — нет project_id у CostOrderItem
**ПРАВИЛЬНО:** Сначала проверить `CostOrder.project_id == project_id`, потом выбирать items
```python
# Проверить что parent принадлежит проекту
order = await db.execute(select(CostOrder).where(
    CostOrder.order_no == order_no,
    CostOrder.project_id == project_id,
    CostOrder.is_deleted == False,
))
if not order.scalar_one_or_none():
    return [], {}
# Только после этого — items
```

## P20: Upload endpoint без проверки размера файла
**НЕПРАВИЛЬНО:** `data = await file.read()` без лимита — OOM при большом файле
**ПРАВИЛЬНО:**
```python
from backend.config import settings as app_settings
data = await file.read()
if len(data) > app_settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
    raise HTTPException(status_code=413, detail=f"File too large (max {app_settings.MAX_UPLOAD_SIZE_MB}MB)")
```

## P21: float() в финансовых расчётах
**НЕПРАВИЛЬНО:** `total = sum(float(x.amount) for x in items)` — потеря точности на копейках
**ПРАВИЛЬНО:** `total = sum((safe_decimal(x.amount) for x in items), Decimal('0'))` — float() только для JSON

## P22: Мутация настроек проекта в роутере
**НЕПРАВИЛЬНО:** `project.tax_rate = value; await db.commit()` в роутере — забудешь инвалидацию кэша
**ПРАВИЛЬНО:** через `project_settings_service.set_tax_rate()` / `set_vat_rate()` — полная инвалидация внутри
