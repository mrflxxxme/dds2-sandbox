# Фича: «Предзаявка (бронь) на моно» — рабочая спецификация

> Поверх предброни. Решения пользователя зафиксированы (2 вопроса). Anchors — live worktree feat/assembly-need-unified.

## Идея
Целая МОНО-паллета на WB-склад БЕЗ лимита приёмки (⌛, `mono_meta.free+paid=0`) не сдаётся
обычной сборкой — только **предзаявкой (бронью)**. Такие готовые паллеты держим в предброни
(вкладка «Предзаявка»), а не выносим в черновик; по кнопке создаём заявки на сборку сразу с
пометкой **`is_prebooking`**.

## Решения пользователя
1. Готовые ⌛-моно-паллеты — **в предброни, вкладка «Предзаявка»** (перестать авто-выносить в черновик).
2. Пометка — **новый флаг `is_prebooking`** на AssemblyRequest (как `is_pre_distribution`).

## Backend (✅ сделано, гейты: mypy 0 · pytest 21)
- **Модель** `assembly.py`: `is_prebooking` (Boolean, server_default false). Колонка добавлена в dev DB **вручную** (`ALTER TABLE assembly_requests ADD COLUMN IF NOT EXISTS is_prebooking boolean NOT NULL DEFAULT false`); формальная **alembic-миграция — на ребейз** (как pre-dist колонки, sync-prod сотрёт → повторить ALTER).
- **crud** `create_assembly_request(is_prebooking=False)` + `_build_response` отдаёт флаг.
- **Схемы** `AssemblyRequestResponse.is_prebooking`; `PrebookingRow{warehouse_id,barcode,wb_warehouse_name,qty,package_type}` · `PrebookingCreate{rows}` · `PrebookingCreateResult`.
- **Сервис** `services/assembly/prebooking.py::create_prebooking` — группирует по (ФФ-источник, WB, упаковка) → 1 заявка/группа, `create_assembly_request(is_prebooking=True)`, обычная валидация стока (товар реально на ФФ), status IN_PROGRESS. Fail-fast (пусто/qty<=0/ШК-нет). Экспорт в `__init__.py` + фасад `assembly_service.py`.
- **Роутер** `POST /warehouse/assembly/prebooking` (rate_limit_write, до `/{request_id}`).
- **Тест** `tests/test_prebooking.py` (6): пометка+группировка, одна заявка на (ФФ,WB), пусто, неизвестный ШК, превышение стока, изоляция.
- ⌛/whitelist НЕ валидируется на бэке (это делает фронт — там загружена приёмка); бэк только ставит флаг.

## Frontend (✅ сделано, гейты: tsc 0 · eslint 0 · vitest 199)
- **types/api.ts**: `AssemblyRequestResponse.is_prebooking` + `PrebookingRow/Create/CreateResult`. **warehouse.ts**: `createPrebooking`.
- **Приёмка предброни грузится при НАЛИЧИИ предброни** (не только на вкладке) — нужна консолидации. Whitelist: `api.getPreorderAllowedWarehouses()` → `preorderWbs`.
- **`monoNoLimitWbs`** (memo): склады, где моно открыто, но лимита нет (⌛).
- **Консолидация** `consolidatePrebookWholePallets(prebook, ctx, keepInPrebook?)` — новый предикат: целые ⌛-моно-паллеты (`pkg===MONOPALLET && monoNoLimitWbs.has(wb)`) НЕ выносятся в черновик, остаются в предброни. Гейт useEffect: если есть моно и приёмка не загружена → ждём (иначе ⌛-моно уедет в черновик).
- **PrebookView Моно split** по footprint: «🧩 Дозабить» (footprint<1) / «📋 Предзаявка» (footprint≥1). На карточке предзаявки — кнопка «Создать предзаявку» (активна если `preorderWbs.has(wb)`).
- **`handleCreatePrebooking`**: собирает PrebookingRow[] по (ffId→wb) через allocatePairs → `api.createPrebooking` → убирает позиции из предброни (скоуп по ffId). confirm.

## Инварианты (не ломать)
- keepInPrebook только для ⌛-моно; BOX-хвосты и не-⌛ моно консолидируются как раньше.
- Приёмка per-pkg (моно → mono_meta), без priority-схлопа «Потребности».
- Мутация предброни только по (ffId→wb) через allocatePairs; rows не трогаем.

## Осталось
- Live-проверка юзером на стенде.
- Формальная alembic-миграция `is_prebooking` при ребейзе на origin/dev.
- (опц.) бейдж «предзаявка» в списке/деталке сборок; фильтр по is_prebooking.
