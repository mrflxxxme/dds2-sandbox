# DOMAIN_<NAME> — <короткое описание>

> Шаблон для нового доменного doc'а. Заменить `<NAME>` и `<...>` плейсхолдеры; удалить эту строку.

## Ownership
Владелец домена (lead-agent / конкретная сабкоманда). Кто отвечает за изменения.

## Tables
Модели БД (имена таблиц), их назначение и unique constraints.

| Модель | Назначение | Unique / Key |
|--------|------------|--------------|
| `Foo` | ... | `(project_id, code)` |

## Endpoints
HTTP routers, методы, краткое описание поведения.

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/<domain>/...` | ... |

## Business Rules
Ключевые инварианты, которые НЕ должны нарушаться при изменениях. Особенно — то, что Claude не может derive из кода.

- Правило 1
- Правило 2

## Dependencies
Связи с другими доменами (внешние API, shared services, models из других доменов).

- DOMAIN_X — для ...
- WB API — для ...

## Known Issues / Pitfalls
Грабли и нюансы, чтобы не наступить второй раз. Прецеденты со ссылками на коммиты.

- **Pitfall A** — описание + commit hash
- **Pitfall B** — описание + commit hash

## Файлы модуля
Список ключевых файлов с однострочным описанием каждого.

- `backend/services/<domain>_service.py` — основная логика
- `backend/models/<domain>.py` — ORM модели
- `backend/schemas/<domain>.py` — Pydantic схемы
- `backend/routers/<domain>.py` — HTTP endpoints
