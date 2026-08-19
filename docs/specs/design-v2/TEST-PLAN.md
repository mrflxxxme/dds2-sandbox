# TEST-PLAN — правки модуля «Дизайн карточек» (v2)

<!-- HEAD-SUMMARY: точные файлы тестов, команды прогона и соответствие «AC → тест» для волн A–D. Нужен, чтобы исполнитель не изобретал структуру тестов и чтобы шаг Verify цикла фазы был машинно проверяемым. -->

**Версия:** 1.0 · **Дата:** 2026-08-19

## Команды (одни и те же во всех волнах)

```bash
# бэк: целевой срез волны
docker compose exec backend pytest tests/test_design_v2_<волна>.py -x

# бэк: регресс модуля целиком
docker compose exec backend pytest tests/ -k design -x

# миграции (волны B, C, D)
docker compose exec backend alembic upgrade head && \
docker compose exec backend alembic downgrade -1 && \
docker compose exec backend alembic upgrade head && \
docker compose exec backend alembic heads

# статика бэка
docker compose exec backend mypy backend/services/design backend/routers/design_tasks.py backend/models/design.py
bash scripts/check_conventions.sh

# фронт (node на хосте НЕТ — только контейнер, образ по ID: docker images | grep frontend)
docker run --rm --entrypoint sh -v "$PWD/frontend-react":/app \
  -v dds2_frontend_node_modules:/app/node_modules -w /app <IMAGE_ID> \
  -c 'npx tsc --noEmit && npx vitest run'
```

Прогон — через субагента `test-runner` (изолирует простыню логов, возвращает только упавшее).
«Зелёное по утверждению» запрещено (v1 §9): в STATUS идёт транскрипт.

## Файлы тестов

Новые файлы, по одному на волну — чтобы срез волны гонялся за секунды:

| Волна | Бэк | Фронт |
|---|---|---|
| A | `tests/test_design_v2_calendar_range.py` | `src/__tests__/design/boardFilters.test.ts`, `src/__tests__/components/periodPicker.test.ts` |
| B | `tests/test_design_v2_number.py` | — (поле проверяется браузерно) |
| C | `tests/test_design_v2_refs.py`, `tests/test_design_v2_task_labels.py` | `src/__tests__/design/labelChips.test.ts` |
| D | `tests/test_design_v2_stats.py`, `tests/test_design_v2_export.py` | `src/__tests__/design/dashboardLayout.test.ts` |

Существующие тесты, которые ОБЯЗАНЫ остаться зелёными (регресс, не переписывать под новое поведение):
`test_permissions_schema_matches_service` и `test_board_permissions_schema_matches_service`
(паритет флагов — упадут, если забыть зеркалить новый флаг в схему) ·
`test_board_not_captured_by_task_id` · существующие тесты `GET /calendar?month=` ·
вся сюита `-k design` волны v1.

⚠️ **Порядок роутов покрыт НЕ полностью.** Существующий тест проверяет только путь `/board`.
Каждая волна, добавляющая статический путь, пишет свой аналогичный тест — иначе ошибка
порядка проявится 422 в рантайме:

| Волна | Новый тест |
|---|---|
| C | `test_refs_not_captured_by_task_id`, `test_bulk_not_captured_by_task_id` |
| D | `test_stats_not_captured_by_task_id`, `test_dashboard_not_captured_by_task_id` |

## Матрица «AC → тест»

### Волна A

| AC | Тип | Где |
|---|---|---|
| AC-1 диапазон дат + 4 ошибки + регресс `month=` | pytest | `test_design_v2_calendar_range.py` |
| AC-2 фильтры доски, пресеты пикера | vitest | `boardFilters.test.ts`, `periodPicker.test.ts` |
| AC-3 нет вкладки «Все бренды» | браузер | webapp-testing |
| AC-4 фильтрация без сетевых запросов | браузер | webapp-testing, вкладка Network |
| AC-5 два месяца, пресет «3 месяца» | браузер | webapp-testing |
| AC-6 регресс ads-manager | браузер | webapp-testing |
| AC-7 InfoTip в обеих темах + тач | браузер | webapp-testing |
| AC-8 иконка | grep | команда из AC |
| AC-9 состояния страниц, статика | mypy/conventions + браузер | транскрипты |

### Волна B

| AC | Тип | Где |
|---|---|---|
| AC-1 миграция up/down/up | alembic | транскрипт |
| AC-2 смена номера + запись в журнал | pytest | `test_design_v2_number.py` |
| AC-3 403 автору и исполнителю | pytest | там же |
| AC-4 пять гвардов дословно | pytest, параметризованный | там же |
| AC-5 гонка двух смен | pytest, две сессии | там же |
| AC-6 автонумерация не сбита | pytest | там же |
| AC-7 номер удалённой задачи переиспользуем | pytest | там же |
| AC-8 терминал → 403 | pytest | там же |
| AC-9 статика | mypy/conventions/tsc/vitest | транскрипты |

### Волна C

| AC | Тип | Где |
|---|---|---|
| AC-1 миграция + сид | alembic + pytest | транскрипт, `test_design_v2_refs.py` |
| AC-2 изоляция проектов + составной FK | pytest | `test_design_v2_refs.py` |
| AC-3 сценарий бренда, права CRUD | pytest | `test_design_v2_refs.py` |
| AC-4 две метки, счётчик `times = 2` | pytest | `test_design_v2_task_labels.py` |
| AC-5 `is_multi` | pytest | там же |
| AC-6 архивирование при 3 использованиях | pytest | `test_design_v2_refs.py` |
| AC-7 отсутствие N+1 | pytest со счётчиком запросов | `test_design_v2_task_labels.py` |
| AC-8 идемпотентность | pytest | там же |
| AC-9 терминалы: метки да, реквизиты нет | pytest | там же |
| AC-10 массовое проставление, `skipped` | pytest | там же |
| AC-11 фильтр доски по метке | vitest + браузер | `labelChips.test.ts` |
| AC-12 контраст палитры, статика | браузер + транскрипты | webapp-testing |

### Волна D

| AC | Тип | Где |
|---|---|---|
| AC-1 миграция | alembic | транскрипт |
| AC-2 шесть вкладок, виджеты | браузер | webapp-testing |
| AC-3 паритет цифр со `StatsPanel` | pytest | `test_design_v2_stats.py` |
| AC-4 раскладка переживает перезагрузку и браузер, персональность | pytest + браузер | `test_design_v2_stats.py` + webapp-testing |
| AC-5 валидация раскладки | pytest | `test_design_v2_stats.py` |
| AC-6 XLSX открывается и сходится, строка усечения | pytest (openpyxl читает свой же файл) | `test_design_v2_export.py` |
| AC-7 один запрос на виджет | pytest со счётчиком | `test_design_v2_stats.py` |
| AC-8 сходимость с `/workload` | pytest | там же |
| AC-9 падение виджета не роняет страницу | браузер, мок 500 | webapp-testing |
| AC-10 viewer видит аналитику, не видит «Настройки» | pytest + браузер | `test_design_v2_stats.py` |
| AC-11 статика | транскрипты | — |

## Обязательные тест-паттерны модуля

Взяты из v1, повторить в каждой волне, где применимо:

- **Изоляция проекта** — на каждую новую ручку отдельный тест: пользователь проекта A не видит и не меняет данные проекта B; для новых таблиц — прямая проверка составного FK (подмена `project_id` в дочерней строке даёт `IntegrityError`, а не тихую утечку).
- **RBAC** — на каждую write-ручку: `viewer` → 403, `editor`-без-роли → 403 там, где нужен lead.
- **Тексты ошибок** — сверять **дословно** со списком CONTRACT-V2 §6; тест на текст, а не только на код.
- **Гонки** — где есть advisory-lock (волна B), тест с двумя параллельными сессиями.
- **N+1** — где добавляется чтение связей (волны C, D): число запросов фиксировано и не зависит от числа строк.
- **Фикстуры** — `project_id` только из фикстуры проекта, хардкод id в INSERT запрещён (ловит `check_conventions.sh`).

## Границы, которые тесты обязаны закрыть

| Граница | Волна | Ожидание |
|---|---|---|
| Диапазон ровно 400 дней / 401 день | A | 200 / 400 |
| Номер ровно 40 / 41 символ | B | 200 / 400 |
| Номер с пробелом, слешем, эмодзи | B | 400 всем трём |
| 500 / 501 значений в поле-реквизите | C | 201 / 400 |
| 500 / 501 задач в массовом проставлении | C | 200 / 400 |
| Пустой массив в `PUT /{id}/labels` | C | 200, все метки сняты |
| 5000 / 5001 задач в XLSX | D | без строки усечения / со строкой |
| Пустое окно периода (нет данных) | D | `None`-метрики, не нули, страница не падает |
| Окно ровно 400 / 401 день у `/stats/*` | D | 200 / 400 |
| `GET /stats?date_from=X` без `date_to` | D | 200, `date_to` = сегодня (совместимость v1) |
| `GET /stats?date_to=X` без `date_from` | D | 400 «Укажите обе границы диапазона» |
| Раскладка без одного известного виджета | D | 400 «Не хватает виджета: {id}» |
| Имя, занятое **архивной** меткой | C | 201 — имя свободно (индекс учитывает `is_archived`) |
| Повторный `DELETE` архивной записи | C | 204, состояние не меняется |
| 50 / 51 поле-реквизит в XLSX | D | все колонки / строка «Показаны первые 50…» |
