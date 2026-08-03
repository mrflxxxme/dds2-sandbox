---
phase: F2 · Роутер + фриз контракта · status: planned
tier: 3
depends_on: [F1]
executors: [lead]
reviewers: [api-designer, security-reviewer, code-reviewer]
donors:
  - backend/routers/ab_tests.py       # thin-layer, ValueError→400, rate_limit_write
  - backend/routers/raw_data.py       # require_role("viewer", page=...) :26
  - backend/routers/payment_requests.py  # multipart-загрузка :710 (порядок проверок)
  - tests/test_api_isolation.py       # X-Project-Id, изоляция
  - tests/test_api_auth.py            # invite/pages-хелперы
prd_refs: [PRD v4 §4, §7]
---
<!-- HEAD-SUMMARY: backend/routers/design_tasks.py — все ручки модуля, регистрация в main.py, RBAC на бэке (Р11), API-тесты изоляции/прав/гвардов. На выходе — CONTRACT.md, контракт заморожен, открывается фан-аут Ф3‖Ф4. Tripwire-фаза (main.py): подпись ДО мержа. -->

## Goal

HTTP-слой поверх сервиса Ф1 без бизнес-логики; контракт зафиксирован для параллельной работы фронта и бэка.

## In scope

`backend/routers/design_tasks.py` · регистрация в `backend/main.py` · `tests/test_api_design_tasks.py` · снапшот `docs/specs/design/CONTRACT.md`.

## Out of scope

Фронт (→ Ф3) · уведомления (→ Ф4) · `POST /{id}/ab-test` объявить в контракте, реализация → Ф6 (до Ф6 отвечает 501 с текстом).

## Работы

`router = APIRouter(prefix="/design-tasks")`. Все write — `Depends(rate_limit_write)`. Права — `require_role(min_role, page="design-tasks")` (Р11); тонкая доводка (author/assignee/lead) — по `compute_permissions` в сервисе, роутер только транслирует `PermissionError→403`, `ValueError→400`, нет записи → 404.

**Порядок объявления: статические пути ДО `/{task_id}`** (FastAPI матчит по порядку): `board`, `all-projects`, `workload`, `calendar`, `stats`, `product-suggest` — первыми.

| Метод | Путь | min_role | Назначение |
|---|---|---|---|
| GET | `/board` | viewer | Доска: 6 колонок + counts (Р4) |
| GET | `` | viewer | Список с фильтрами (limit/offset обязательны) |
| POST | `` | editor | Создать заявку |
| GET | `/all-projects` | — (только `get_current_user`) | Сквозной список по членству `ProjectMember` × page-гейт: включаются только проекты, где `"design-tasks" ∈ get_effective_pages(role, pages)` (owner/admin — всегда); **единственная ручка без `get_current_project`** — отдельные тесты изоляции и page-гейта обязательны |
| GET | `/workload` | viewer | Загрузка по исполнителям |
| GET | `/calendar?month=YYYY-MM` | viewer | Задачи месяца по `due_date` (границы видимой сетки ±6 дней) |
| GET | `/stats?date_from&date_to` | viewer | Метрики PRD §10 |
| GET | `/product-suggest?q=` | editor | Автоподсказка товара (Р2) |
| GET | `/{task_id}` | viewer | Деталка + permissions |
| PUT | `/{task_id}` | editor | Правка (правила в сервисе) |
| DELETE | `/{task_id}` | editor | `soft_delete` (author/lead) |
| POST | `/{task_id}/status` | editor | Смена статуса (кнопочный путь) |
| POST | `/{task_id}/move` | editor | Dnd: статус+позиция (Р4) |
| POST | `/{task_id}/assign` | editor | Назначение/снятие (lead-only в сервисе) |
| POST | `/{task_id}/viewed` | editor | Отметка просмотра лидом (Р5, идемпотентно) |
| POST | `/{task_id}/materials` | editor | Материал: ссылка или артикул |
| POST | `/{task_id}/materials/file` | editor | Материал файлом, multipart (донор pr:710 — порядок проверок: allowlist mime/расширения → blocklist → read → validate_file_content → размер 413) |
| GET | `/{task_id}/materials/{mat_id}/file` | viewer | Скачать материал |
| DELETE | `/{task_id}/materials/{mat_id}` | editor | Удалить материал |
| POST | `/{task_id}/submissions` | editor | Сдать версию, multipart (файлы+comment) → сервис создаёт версию и переводит в REVIEW |
| GET | `/{task_id}/submissions/{sub_id}/files/{file_id}` | viewer | Скачать файл версии |
| POST | `/{task_id}/submissions/{sub_id}/verdict` | editor | Принять / вернуть |
| POST | `/{task_id}/comments` | viewer | Комментарий (viewer может писать? — НЕТ: editor; viewer read-only → 403) |
| POST | `/{task_id}/ab-test` | editor | Ф6; до неё — 501 «Появится после Ф6» |

Регистрация в `main.py`: импорт в блок роутеров + `app.include_router(design_tasks.router, prefix="/api/v1", tags=["Design Tasks"], dependencies=[Depends(get_current_user)])`.

`CONTRACT.md`: перечень путей + имена схем запрос/ответ + коды ошибок; помечен «FROZEN <дата> — изменение = эскалация архитектору».

## AC

- **AC-1:** `tests/test_api_design_tasks.py::test_isolation` — задача проекта A недоступна с `X-Project-Id` проекта B (404), список/доска пусты (донор test_api_isolation.py).
- **AC-2:** `test_all_projects_membership` — пользователь видит в `/all-projects` только проекты своего членства; чужой проект не появляется даже при знании id.
- **AC-3:** `test_rbac_page_gate` — editor, приглашённый без `design-tasks` в pages, получает 403 на `GET /design-tasks` (хелперы из test_api_auth.py).
- **AC-4:** `test_guards_over_http` — переходы с нарушением гвардов → 400 с текстами из Ф1; запрещённый переход по словарю → 400.
- **AC-5:** `test_move_permissions` — reorder внутри колонки от editor-автора → 403; от lead → 200 и порядок изменён.
- **AC-6:** `GET /docs` (openapi.json) содержит все пути таблицы; `/{task_id}` не перехватывает `board` (регресс-тест на порядок роутов).
- **AC-7:** мутация без `rate_limit_write` отсутствует (grep-тест по файлу роутера, донор-паттерн из conventions-тестов).

## Exit-gate

| Критерий | Порог | Evidence |
|---|---|---|
| API-тесты | `pytest tests/test_api_design_tasks.py -x` зелёный | транскрипт |
| Изоляция | AC-1, AC-2 зелёные | транскрипт |
| Полный срез | `make test-fast` без регрессий | транскрипт |
| Ревью T3 | api-designer + security + code без BLOCK | вердикт |
| CONTRACT.md | создан, помечен FROZEN | файл в репо |
| **Подпись архитектора** | ДО мержа (tripwire: main.py; фриз контракта) | STATUS.md |

## Hints

- ab_tests.py гейтит только на фронте — здесь НЕ копировать это, Р11 требует `require_role` на каждой ручке.
- `/all-projects`: join через `ProjectMember` c `is_deleted == False`; limit обязателен.
- После подписи Ф2 lead открывает фан-аут: спавн tm-frontend (Ф3) и tm-backend (Ф4) в отдельных worktree одним сообщением (CLAUDE.md «Параллелизм»).
