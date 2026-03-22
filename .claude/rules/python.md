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
3. `soft_delete()` — никогда `db.delete()`
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
- `ilike(f"%{input}%")` без экранирования
- Сервис > 400 строк без разбиения
- Функция > 50 строк
- Вложенность > 4 уровней
- Мутация без `invalidate_cache()`

## Порядок создания модуля
Model → Alembic migration → Schema → Service → Router → Test
