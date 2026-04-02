---
paths:
  - "**/*.py"
---
# Python правила DDS2

## Стандарты
- PEP 8
- Type annotations на всех public функциях
- async/await для всех DB операций

## Железные правила (нарушение = баг)
1. `project_id` — КАЖДЫЙ запрос к БД
2. `is_deleted == False` — для SoftDeleteMixin моделей
3. `soft_delete()` для моделей с SoftDeleteMixin — никогда `db.delete()` на них
4. `from backend.utils.time import utcnow` — никогда `datetime.utcnow()`
5. `Numeric(18, 2)` для денег — никогда Float
6. Параметризованный `:param` SQL — никогда f-string в `text()`
7. `invalidate_cache(prefix)` после мутаций
8. Бизнес-логика в `services/` — никогда в `routers/`

## Архитектура слоёв
```
routers/ (HTTP only, валидация, auth)
  ↓
services/ (бизнес-логика, внешние вызовы, кэш)
  ↓
models/ (ORM, без логики)
```

## Кэширование (Redis)
- `@cached(prefix="...", ttl=300)` для отчётов
- `invalidate_cache(prefix)` — сам добавляет `:*`
- При ошибке Redis → graceful degradation
- Кэш-ключ MUST содержать project_id

## PgBouncer
- `prepared_statement_cache_size=0` обязателен
- `DATABASE_URL_SYNC` для Alembic/ETL
- Statement timeout через event listener

## Анти-паттерны (НЕ ДЕЛАТЬ)
- `.scalars().all()` без `.limit()`
- `ilike(f"%{input}%")` без экранирования и без `escape="\\"`
- Сервис > 500 строк без разбиения
- Функция > 50 строк
- Вложенность > 4 уровней
- Мутация без `invalidate_cache()`
- `float()` в финансовых расчётах — только `Decimal(str(value))`
- `except Exception` в scheduler без `except asyncio.CancelledError: raise` перед ним
- Upload endpoint без проверки `MAX_UPLOAD_SIZE_MB`
- Прямая мутация `project.*` в роутере — выноси в сервис
- Запрос к дочерней сущности (без project_id в модели) без проверки parent.project_id

## Порядок создания модуля
Model → Alembic migration → Schema → Service → Router → Test

## Документация — ОБЯЗАТЕЛЬНО обновить при изменениях
При создании/изменении backend файлов — обнови соответствующие docs:
- Новая модель → DOMAIN_*.md + SOFT_MODELS в check_conventions.sh + models/__init__.py
- Новый сервис/роутер → DOMAIN_*.md + backend/MAP.md
- Новый кэш → invalidate_project_reports() в cache.py
- Новый антипаттерн найден → docs/KNOWN_PITFALLS.md + check_conventions.sh
- Изменение бизнес-логики → DOMAIN_*.md (описание поведения)
