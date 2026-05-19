---
paths:
  - "backend/**/*.py"
  - "migrations/versions/*.py"
  - "tests/**/*.py"
---

# Backend — детали реализации

Iron rules, архитектура слоёв и анти-паттерны — в корневом `CLAUDE.md`. Здесь только то, чего там нет.

## Типы PostgreSQL
`bigint` для ID, `text` для строк, `timestamptz` для дат, `Numeric(18,2)` для денег, `boolean` для флагов.

## Multi-tenancy
Дочерняя таблица без своего `project_id` → фильтровать через `project_id` родителя (JOIN или подзапрос).

## Запросы
- `.scalars().all()` — всегда с `.limit()`; на больших выборках — пагинация.
- Batch inserts и JOIN вместо N+1.
- `ilike()` — экранировать `%` и `_` в пользовательском вводе.

## Кэш
`@cached(prefix, ttl=300)` для чтения — ключ обязан включать `project_id`.

## Безопасность
- Upload-файлы → проверять `MAX_UPLOAD_SIZE_MB` до обработки.
- Мутации настроек проекта — через `project_settings_service`, не напрямую.
- В scheduler jobs: `except asyncio.CancelledError: raise` ПЕРЕД `except Exception`.

## Тестирование
pytest, `asyncio_mode=auto`. Покрывать: happy path, edge cases, изоляцию по `project_id`, фильтрацию soft-delete.

## Размер
Сервис >500 строк или функция >50 строк — повод разбить.
