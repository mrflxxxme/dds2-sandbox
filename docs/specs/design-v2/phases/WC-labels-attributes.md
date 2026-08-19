---
phase: WC · Цветные метки + справочник реквизитов · status: planned
tier: 3
depends_on: [WB]
executors: [lead, tm-frontend]
reviewers: [database-reviewer, security-reviewer, api-designer, frontend-reviewer]
donors:
  - backend/models/design.py                    # паттерн составного FK (id, project_id) + partial-индексы
  - migrations/versions/dsn01_design_tasks.py    # образец аддитивной миграции модуля
  - backend/services/design/crud.py              # запись событий на «не-переходы»
  - backend/services/design/queries.py           # to_list_items — куда добавить метки/реквизиты
  - frontend-react/src/app/(main)/p/[slug]/design-tasks/components/BoardFilters.tsx  # строка фильтров из волны A
prd_refs: [CHARTER-V2 Р19, Р20, Р21-добор]
---
<!-- HEAD-SUMMARY: два project-scoped справочника, которые ведёт lead: цветные метки (несколько на задачу, круги на карточке + раскрытие названий) и универсальные реквизиты (поле-список «Кабинет ВБ»/«Бренд»/… со своими значениями, выбор в заявке). Полный слой: модели → миграция → схемы → сервис → роутер → UI → фильтры доски. -->

## Goal

Команда сама ведёт свои бренды/кабинеты и цветовую разметку задач, без правки кода.

## In scope

4 новые таблицы + 2 связи · `services/design/refs.py` (новый сервис справочников) ·
ручки CRUD справочников и назначения на задачу · экран «Настройки модуля» ·
селекты в форме заявки и деталке · круги-метки на карточке доски и в списке ·
фильтры доски по меткам и реквизитам (добор Р21) · амендмент CONTRACT.md.

## Out of scope

Глобальный (кросс-проектный) справочник → потом (Р19) · счётчик «сколько кругов было» историей →
не строим (Р20/§5 v2: раскрытие показывает текущие метки; журнал событий уже пишется) ·
обязательность реквизитов при создании заявки → потом (все поля опциональны в v2) ·
импорт справочников из WB API → потом.

## Работы

### Модель (tripwire — только lead)

Все таблицы `project_id`-scoped; изоляция детей — **составным FK**, как в v1
(`UniqueConstraint(id, project_id)` на родителе + FK `(child_id, project_id)`), `ON DELETE CASCADE`.

| Таблица | Поля | Заметки |
|---|---|---|
| `design_labels` | `id`, `project_id`, `name` String(60), `color` String(20), `sort_order` Int, `SoftDeleteMixin`, `TimestampMixin` | `color` — ключ из фикс-палитры (`var(--color-*)`-набор), не произвольный hex: валидация в схеме; partial-unique `(project_id, name) WHERE is_deleted=false` |
| `design_task_labels` | `task_id`, `label_id`, `project_id`, `created_at` | связь M2M, **без** SoftDelete; PK `(task_id, label_id)`; два составных FK — на задачу и на метку |
| `design_attributes` | `id`, `project_id`, `name` String(60) («Кабинет ВБ», «Бренд»), `sort_order`, `SoftDeleteMixin`, `TimestampMixin` | поле-справочник; partial-unique `(project_id, name)` |
| `design_attribute_values` | `id`, `project_id`, `attribute_id`, `value` String(120), `sort_order`, `SoftDeleteMixin` | значения поля; partial-unique `(project_id, attribute_id, value)` |
| `design_task_attribute_values` | `task_id`, `value_id`, `project_id`, `created_at` | выбор задачи; PK `(task_id, value_id)`; ограничение «одно значение на поле в задаче» — гвард в сервисе (в БД дорого) |

Индексы: `(project_id, sort_order)` на обоих справочниках; `(project_id, task_id)` и
`(project_id, label_id)` / `(project_id, value_id)` на связях — фильтры доски ходят по ним.
Зеркалить индексы в `__table_args__` И в миграции (иначе `alembic autogenerate` дрейфует — грабли v1).

Миграция `dsn05_design_labels_attributes.py`, `down_revision` — голова после `dsn04` (волна B).

### Сервис `services/design/refs.py`

CRUD справочников (только lead на запись, чтение — любой с page-ключом):
`list_labels/create_label/update_label/delete_label` (soft, с проверкой «метка используется» —
удаление разрешено, связи чистятся каскадом), аналогично `attributes` и `attribute_values`.

Назначение на задачу: `set_task_labels(task_id, label_ids)` и
`set_task_attribute_values(task_id, value_ids)` — «положить набор» (replace), не add/remove:
проще идемпотентность и один `DesignTaskEvent` на операцию
(`comment = "Метки: Срочный дизайн, Переделка"`). Права: `can_edit` задачи (lead или автор вне терминалов).
Гвард набора реквизитов: не более одного значения на `attribute_id` → `ValueError("Поле «Бренд» уже выбрано")`.
Все выборки — с `project_id` и `is_deleted == False`, с `.limit()` (cap 500 значений на поле).

### Чтение

`queries.to_list_items` и `get_task` дополняются:
`labels: [{id, name, color}]` и `attributes: [{attribute_id, attribute_name, value_id, value}]`.
Загрузка — батчем (`selectinload` / один `IN`-запрос на страницу), **не** per-task (N+1 запрещён инвариантом v1 §6.10).

### API (аддитивно к FROZEN-контракту)

```
GET/POST                /api/v1/design-tasks/refs/labels
PUT/DELETE              /api/v1/design-tasks/refs/labels/{id}
GET/POST                /api/v1/design-tasks/refs/attributes
PUT/DELETE              /api/v1/design-tasks/refs/attributes/{id}
GET/POST                /api/v1/design-tasks/refs/attributes/{id}/values
PUT/DELETE              /api/v1/design-tasks/refs/values/{id}
PUT                     /api/v1/design-tasks/{id}/labels      { label_ids: [] }
PUT                     /api/v1/design-tasks/{id}/attributes  { value_ids: [] }
```

**Статические пути `refs/*` объявить ДО `/{task_id}`** — FastAPI матчит по порядку
(в v1 это закреплено регресс-тестом openapi, он же поймает ошибку).
Все write — `Depends(rate_limit_write)` + `require_role("editor", page="design-tasks")`;
тонкая проверка lead — в сервисе. Фильтры `GET /design-tasks` и `GET /board` получают
опциональные `label_ids`, `value_ids` (CSV) — серверная фильтрация, чтобы доска не тянула лишнее.

### Фронт

- **Экран «Настройки модуля»** (`design-tasks/settings/page.tsx`, виден только lead —
  но кнопку прячем по `permissions`, не по роли: права считает бэк, §6.9 v1):
  две секции — «Цветные метки» (название + выбор цвета из палитры, drag-порядок, удаление)
  и «Реквизиты» (поля и их значения; «+ Добавить бренд» прямо в строке поля).
- **Форма заявки и `EditTaskModal`**: селект с поиском на каждое поле-реквизит (референс-скрины
  «Предмет/Бренд/Артикул») + мультиселект меток.
- **Карточка доски (`TaskCard`) и строка списка**: цветные круги меток; клик/наведение
  раскрывает список названий (Р20). Круги не должны ломать высоту карточки — максимум 5 видимых + «+N».
- **`BoardFilters` (из волны A)**: добавить селекты «Метки» и по каждому полю-реквизиту.

## AC

- **AC-1:** `alembic upgrade head && downgrade -1 && upgrade head` — чисто, одна голова.
- **AC-2:** Изоляция проектов: попытка привязать к задаче метку/значение чужого проекта — 404/400, не тихое проглатывание; тест на составной FK (подмена `project_id` в связи даёт `IntegrityError`).
- **AC-3:** lead создаёт поле «Бренд» с 3 значениями, ставит одно в заявку — видно в списке, деталке и фильтре; editor-не-lead получает 403 на CRUD справочника, но 200 на выбор значения в своей задаче.
- **AC-4:** Две метки на задаче → два круга на карточке; раскрытие показывает оба названия; снятие метки пишет событие в журнал.
- **AC-5:** Гвард «одно значение на поле»: попытка положить два значения одного `attribute_id` → 400 с текстом поля.
- **AC-6:** Удаление метки, привязанной к 3 задачам: метка исчезает с карточек, задачи целы, связи вычищены.
- **AC-7:** N+1 нет: список из 50 задач с метками — фиксированное число запросов (тест на счётчик запросов или `performance-optimizer`-проверка).
- **AC-8:** Фильтр доски по метке сужает все колонки; комбинация «метка + исполнитель» работает.
- **AC-9:** `mypy`, `check_conventions.sh`, `tsc`, `vitest` — чисто.

## Exit-gate

| Критерий | Порог | Evidence |
|---|---|---|
| Миграция | AC-1 | транскрипт alembic |
| Бэк-тесты | AC-2..AC-7 зелёные | транскрипт pytest |
| Фронт | AC-8, AC-9 | tsc/vitest + webapp-testing |
| Ревью | database + security + api + frontend без BLOCK | 4 вердикта |
| Подпись | tripwire (models/migrations/schemas) — подпись архитектора ДО мержа | STATUS-строка |

## Hints

- Порядок работ строго: Model → Migration → Schema → Service → Router → Test → UI (канон CLAUDE.md). Фан-аут на `tm-frontend` — **только после** мержа хребта (бэк+контракт), иначе types-first ломается.
- Палитра цветов — существующие `var(--color-*)`, hex не вводить (`.claude/rules/design.md`).
- Связи-M2M без `SoftDeleteMixin` — как `DesignMaterial` в v1: удаление жёсткое, каскад из БД.
- `@cached` не добавлять даже на справочники (Р10) — иначе всплывает регистрация префикса и инвалидация на каждой правке метки.
- Cap на выборки: справочники ≤500 строк, `.scalars().all()` без `.limit()` запрещён.
- Тексты ошибок — часть контракта: фиксировать в амендменте CONTRACT.md вместе с ручками.
