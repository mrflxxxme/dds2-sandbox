---
phase: WC · Цветные метки + справочник реквизитов · status: planned
tier: 3
depends_on: [WB]
executors: [lead, tm-frontend]
reviewers: [database-reviewer, security-reviewer, api-designer, frontend-reviewer]
donors:
  - backend/models/design.py                     # паттерн UniqueConstraint(id, project_id) + составной FK + partial-индексы
  - migrations/versions/dsn01_design_tasks.py     # образец аддитивной миграции модуля
  - backend/services/design/crud.py               # запись событий на «не-переходы» (смена исполнителя)
  - backend/services/design/queries.py            # to_list_items — куда добавляются метки/реквизиты
  - frontend-react/src/app/(main)/p/[slug]/design-tasks/components/SearchSelect.tsx  # из волны A
prd_refs: [CHARTER-V2 Р19, Р20, Р26–Р31, Р33; CONTRACT-V2 §3]
---
<!-- HEAD-SUMMARY: два project-scoped справочника, которые ведёт lead: цветные метки (несколько на задачу, круги + раскрытие со счётчиком истории) и универсальные реквизиты (поля-списки с флагом is_multi). Плюс вкладка «Настройки», массовое проставление и палитра --color-label-*. Полный слой: модели → миграция → схемы → сервис → роутер → UI. -->

## Goal

Команда сама ведёт свои бренды/кабинеты и цветовую разметку задач, без правки кода.

## In scope

5 новых таблиц · 10 токенов палитры в `globals.css` · `services/design/refs.py` ·
ручки CONTRACT-V2 §3 · вкладка «Настройки» · селекты в заявке и деталке ·
круги меток на карточке и в списке · фильтры доски по меткам и реквизитам (клиентские) ·
массовое проставление · сид.

## Out of scope

Глобальный кросс-проектный справочник → потом · серверная фильтрация доски → никогда в v2 (Р21) ·
обязательность реквизитов при создании заявки → потом (все поля опциональны) ·
импорт справочников из WB API → потом · произвольный hex цвета → никогда (Р26).

## Работы

### 1. Модель (tripwire — только lead)

Все таблицы `project_id`-scoped; изоляция детей — составным FK, как в v1.

> **Про `SoftDeleteMixin` в справочниках.** Он есть у трёх новых таблиц справочников ради
> инварианта v1 §6.2 и единообразия выборок, но в v2 `soft_delete()` по ним **не вызывается
> никогда**: пользовательское «Удалить» — это архивирование (Р30). Поле `is_deleted` остаётся
> зарезервированным на будущее жёсткое удаление и всегда `false`. Именно поэтому все
> partial-индексы условны по **обоим** флагам. Запросы всё равно фильтруют `is_deleted == False`
> (правило 2 не отключается).

**`design_labels`** — `SoftDeleteMixin` + `TimestampMixin`
`id` · `project_id` · `name String(60)` · `color String(20)` · `sort_order Integer default 0` · `is_archived Boolean default false`
`UniqueConstraint(id, project_id)` (родитель для связей) ·
partial-unique `(project_id, name) WHERE is_deleted = false AND is_archived = false` ·
индекс `(project_id, sort_order) WHERE is_deleted = false`.
`color` — одно из 10 значений палитры (Р26), валидация в схеме, не в БД (String, не pg-enum — правило v1).

> **Почему в partial-unique входит `is_archived`.** Архивирование (Р30) не ставит `is_deleted`,
> поэтому индекс только по `is_deleted = false` держал бы имя занятым навсегда: заводя метку
> с именем архивной, пользователь получал бы `IntegrityError` (500) вместо обещанного контрактом
> 400. С условием по обоим флагам имя архивной метки свободно для повторного использования,
> а гвард уникальности сервиса проверяет ровно тот же набор — **активные** записи
> (`is_deleted = false AND is_archived = false`). Индекс и гвард обязаны совпадать по условию;
> расхождение = 500 вместо 400. То же правило для полей и значений реквизитов ниже.

**`design_task_labels`** — связь с историей (**без** SoftDelete)
`id` · `project_id` · `task_id` · `label_id` · `attached_at` · `attached_by String(100)` ·
`removed_at DateTime NULL` · `removed_by String(100) NULL`
Два составных FK: `(task_id, project_id) → design_tasks` и `(label_id, project_id) → design_labels`, оба `ON DELETE CASCADE`.
partial-unique `(task_id, label_id) WHERE removed_at IS NULL` — метка не задваивается.
Индексы: `(project_id, task_id) WHERE removed_at IS NULL`, `(project_id, label_id)`.

> **Почему `removed_at`, а не удаление строки.** Это и есть счётчик Р20: текущие метки —
> `removed_at IS NULL`, «была с меткой N раз» — `COUNT(*)` по `label_id`. Парсить историю из
> `DesignTaskEvent.comment` (свободный текст) нельзя: сломается при переименовании метки.

**`design_attributes`** — `SoftDeleteMixin` + `TimestampMixin`
`id` · `project_id` · `name String(60)` · `is_multi Boolean default false` · `sort_order` · `is_archived`
`UniqueConstraint(id, project_id)` · partial-unique `(project_id, name) WHERE is_deleted = false AND is_archived = false`.

**`design_attribute_values`** — `SoftDeleteMixin`
`id` · `project_id` · `attribute_id` · `value String(120)` · `sort_order` · `is_archived`
`UniqueConstraint(id, project_id)` · составной FK на `design_attributes` ·
partial-unique `(project_id, attribute_id, value) WHERE is_deleted = false AND is_archived = false`.

**`design_task_attribute_values`** — связь (**без** SoftDelete, без истории: Р20 про метки, не про реквизиты)
`task_id` · `value_id` · `project_id` · `created_at` · `created_by String(100)`
PK `(task_id, value_id)`; два составных FK; индексы `(project_id, task_id)`, `(project_id, value_id)`.

Индексы зеркалить **и** в `__table_args__`, **и** в миграции — иначе `alembic autogenerate` дрейфует (грабли v1).
Миграция `dsn05_design_labels_attributes.py`, `down_revision` = `dsn04` (волна B).

### 2. Сид (Р33)

В той же миграции (data-migration в `upgrade`, по каждому существующему проекту, где есть задачи дизайна):
- поле «Кабинет ВБ» (`is_multi = false`), без значений;
- поле «Бренд» (`is_multi = false`) со значениями: `АРТСПЕЙС`, `Меллори`, `НУ-НУ`, `СамПоклей`, `Уютопия`, `Redmi`;
- меток не создаём — цвета команда заводит под себя.
`downgrade` удаляет только строки сида (по именам), чужие не трогает.

### 3. Палитра (Р26, tripwire — только lead)

В `frontend-react/src/app/globals.css` — 10 токенов для светлой и тёмной темы:
`--color-label-red · -orange · -amber · -green · -teal · -blue · -violet · -pink · -brown · -slate`.
Контраст к фону карточки (`var(--color-bg-card)`) ≥ 3:1 в обеих темах — проверяется AC-12.
Карта «ключ → var» — в `src/lib/design.ts` (`LABEL_COLORS`), фронт нигде не пишет hex.

### 4. Сервис `services/design/refs.py`

CRUD справочников — запись только `is_lead`, чтение любому с page-ключом.
`DELETE` = архивирование (`is_archived = true`), не `soft_delete()` и не жёсткое удаление (Р30):
запись пропадает из выбора для новых задач, но остаётся видимой на старых и в аналитике.
`usage_count` считается одним GROUP BY на весь список, не per-row.

Назначение на задачу — `set_task_labels(task_id, label_ids)` и `set_task_attribute_values(task_id, value_ids)`,
семантика **replace** (CONTRACT-V2 §3). Реализация меток:
- добавить → новая строка `design_task_labels`;
- снять → `removed_at = utcnow()`, `removed_by = actor_name` (строку **не** удалять);
- набор не изменился → ничего не пишем, событие не создаём (идемпотентность, AC-8).

Событие `DesignTaskEvent` на изменение набора: `old_status == new_status`,
`comment = "Метки: A, B"` / `comment = "Реквизиты: Бренд — Меллори"` (человекочитаемый след для журнала;
машинный счётчик берётся из `removed_at`, не отсюда).

Гварды и тексты — **дословно CONTRACT-V2 §3**, включая `is_multi`, архив и терминальные статусы (Р31).
Все выборки — с `project_id`, `is_deleted == False` и `.limit()`.

Массовое проставление (`bulk_set_labels` / `bulk_set_attribute_values`, режимы `add`/`remove`):
права проверяются по каждой задаче, задача без прав → в `skipped`, а не 403 на весь вызов; cap 500.

### 5. Чтение

`queries.to_list_items` и `get_task` дополняются `labels` и `attributes`;
`get_task` — ещё и `label_history` (CONTRACT-V2 §3).
Загрузка — **батчем**: один `IN`-запрос на страницу для меток и один для реквизитов.
Per-task-запросы запрещены (инвариант v1 §6.10), проверяется AC-7.

### 6. Права

`compute_permissions` получает (Р29, Р31):
`can_set_labels = is_lead or is_author or is_assignee` (терминалы разрешены);
`can_set_attributes = (is_lead or is_author or is_assignee) and status not in (ACCEPTED, CANCELLED)`;
`can_manage_refs = is_lead` (показывает вкладку «Настройки»).
Все три зеркалятся в `DesignTaskPermissions`. `can_manage_refs` дополнительно отдаётся
в `GET /board` (`DesignBoardPermissions`) — вкладка рисуется до открытия задачи.

### 7. Фронт

- **Вкладка «Настройки»** (`design-tasks/settings/page.tsx`, **последняя** в `DesignTabs` —
  пятая на момент волны C, шестая после того, как волна D добавит «Аналитику»; видна по
  `can_manage_refs` от бэка, не по роли): секция «Цветные метки» (название + выбор цвета из 10,
  drag-порядок, «Удалить» с подтверждением «Используется в N задачах») и секция «Реквизиты»
  (поля с флагом «несколько значений», их значения, «+ Добавить значение» прямо в строке поля).
- **Заявка и `EditTaskModal`**: `SearchSelect` (из волны A) на каждое активное поле-реквизит —
  одиночный или мультивыбор по `is_multi`; мультиселект меток с цветными кружками.
- **`TaskCard` и строка списка**: до 5 кругов + «+N»; клик/наведение раскрывает список названий,
  в деталке — со счётчиком «была N раз» (`label_history`). Круги не меняют высоту карточки.
- **`BoardFilters`** (из волны A): добавляются селекты «Метки» и по каждому активному полю-реквизиту.
  Фильтрация — **клиентская**, как и остальные фильтры доски (Р21).
- **Массовое проставление**: в `ListView` — чекбоксы строк, панель «Выбрано N» с действиями
  «Проставить метку / бренд» и «Снять».

## AC

- **AC-1:** `alembic upgrade head && downgrade -1 && upgrade head` — чисто; `alembic heads` = одна голова; сид создал два поля и 6 брендов.
- **AC-2:** Изоляция: попытка привязать метку/значение чужого проекта → 404 текстом контракта; прямая подмена `project_id` в строке связи падает `IntegrityError` (тест на составной FK).
- **AC-3:** lead создаёт поле «Бренд» → ставит значение в заявку → видно в списке, деталке и фильтре доски; editor-не-lead получает 403 на CRUD справочника, но 200 на выбор значения в своей задаче.
- **AC-4:** Две метки на задаче → два круга; раскрытие показывает оба названия; снятие метки и повторное назначение дают `times = 2` в `label_history`.
- **AC-5:** `is_multi = false` + два значения одного поля → 400 «Поле «Бренд» допускает одно значение»; при `is_multi = true` оба сохраняются.
- **AC-6:** Архивирование метки, стоящей на 3 задачах: метка исчезает из выбора новых задач, но на этих 3 остаётся видимой и попадает в аналитику; `usage_count` в справочнике показывает 3.
- **AC-7:** N+1 нет: список 50 задач с метками и реквизитами даёт фиксированное число запросов независимо от числа задач (тест-счётчик запросов).
- **AC-8:** Идемпотентность: повторный `PUT /{id}/labels` с тем же набором не создаёт ни строк, ни событий.
- **AC-9:** Терминалы (Р31): в ACCEPTED метки меняются (200), реквизиты — 400 «Реквизиты закрытой задачи не меняются».
- **AC-10:** Массовое проставление 3 задачам: `updated = 3`; задача без прав уходит в `skipped`, вызов остаётся 200.
- **AC-11:** Фильтр доски по метке сужает все колонки; комбинация «метка + исполнитель» работает; сетевых запросов при смене фильтра нет.
- **AC-12:** Контраст каждого из 10 цветов метки к фону карточки (`var(--color-bg-card)`) ≥ 3:1 в светлой и тёмной теме — замер контраст-чекером, таблица 10×2 в транскрипте; `mypy`, `check_conventions.sh`, `tsc`, `vitest` — чисто.
- **AC-13:** Вкладок в модуле пять: Доска · Список · Календарь · Загрузка · Настройки; «Настройки» не видны пользователю без `can_manage_refs`.

## Exit-gate

| Критерий | Порог | Evidence |
|---|---|---|
| Миграция и сид | AC-1 | транскрипт alembic |
| Бэк-тесты | AC-2..AC-10 зелёные | транскрипт pytest |
| Фронт | AC-11, AC-12 | tsc/vitest + webapp-testing |
| Ревью | database + security + api + frontend без BLOCK | 4 вердикта |
| Подпись | tripwire (`models/`, `migrations/`, `schemas/`, `globals.css`) — подпись архитектора ДО мержа | строка в STATUS |

## Hints

- Порядок строго: Model → Migration → Schema → Service → Router → Test → UI. Фан-аут на `tm-frontend` — только после мержа бэк-хребта волны.
- Связи-M2M без `SoftDeleteMixin`: у `design_task_labels` роль «удаления» играет `removed_at`, у `design_task_attribute_values` — жёсткий DELETE строки (истории по реквизитам не ведём).
- `@cached` не добавлять даже на справочники (Р10 v1) — иначе всплывёт регистрация префикса и инвалидация на каждой правке метки.
- Cap: 200 полей, 500 значений на поле, 100 меток; `.scalars().all()` без `.limit()` запрещён.
- Статические пути `refs/*` и `bulk/*` объявлять ДО `/{task_id}` — иначе `refs` уедет в `task_id` (ловит регресс-тест openapi).
- Три новых «не найдено»-текста добавить в `_NOT_FOUND_TEXTS` роутера, иначе они станут 400 вместо 404.
