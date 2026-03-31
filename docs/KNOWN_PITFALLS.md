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
**ПРАВИЛЬНО:** `.ilike(f"%{escape_like(search)}%", escape="\\")` + `from backend.utils.helpers import escape_like`

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
- Агенты: `services/ai/agents/{role}.py` (8 файлов: analyst, financier, marketer, advertiser, supply_manager, logistics, logistician + base)
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
