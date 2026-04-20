---
description: "Создать Alembic миграцию для DDS2: проверка heads, генерация, тест upgrade/downgrade, обновление docs."
---

# /migration — Alembic миграция DDS2

## Параметры (спрашивай у пользователя)
1. **Имя миграции** (snake_case, например `add_assembly_status_index`)
2. **Что меняется** (новая колонка / новая таблица / индекс / удаление / data migration)
3. **Модели затронуты** (список)
4. **Backfill нужен?** (да/нет — если ALTER TABLE NOT NULL → почти всегда да)

## ⚠️ Правила
- **Только последовательно** — НИКОГДА не запускай миграции параллельными агентами
- **Только lead agent** — не делегируй teammate'у
- **Большие таблицы** — отдельный обзор: блокировки, online schema change, batched updates
- **Production** — миграции деплоятся через CI (cd-production.yml), не вручную

## Процесс

### 1. Проверь состояние Alembic
```bash
docker compose exec backend alembic current
docker compose exec backend alembic heads
```
- Если `multiple heads` → НЕ создавай новую миграцию, сначала: `alembic merge heads -m "merge"` → `alembic upgrade head`

### 2. Обнови модель (если нужно)
- Файл: `backend/models/{domain}.py`
- Поля с типами: `Mapped[type] = mapped_column(...)`
- Money → `Numeric(18, 2)`
- Datetime → `timestamptz` (`DateTime(timezone=True)`)
- ID → `BigInteger`
- Если новая модель с `SoftDeleteMixin` → добавь в `SOFT_MODELS` в `scripts/check_conventions.sh`

### 3. Сгенерируй миграцию
```bash
docker compose exec backend alembic revision --autogenerate -m "имя_миграции"
```
- Проверь сгенерированный файл `migrations/versions/XXX_имя.py`:
  - `upgrade()` — корректные ops
  - `downgrade()` — обратные ops (НЕ оставляй пустым)
  - Имена индексов соответствуют конвенции `ix_table_column`
  - Партишн-индексы для soft-delete: `WHERE is_deleted = false`

### 4. Backfill / data migration (если нужно)
- Если ALTER + NOT NULL → нужен default value ИЛИ batch UPDATE до ALTER
- Big tables → batched в Python в скрипте, не в миграции
- Lock-free: NEW колонка с DEFAULT, потом backfill batches, потом ALTER NOT NULL

### 5. Тест upgrade/downgrade
```bash
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade -1
docker compose exec backend alembic upgrade head
```
- Должно проходить без ошибок
- Данные не должны теряться при downgrade-upgrade

### 6. Тесты
- `make test-changed` — старые тесты не сломались
- Если новая модель/поле → тест на multi-tenancy + soft-delete

### 7. Документация
- `backend/DOMAIN_{DOMAIN}.md` — упомяни новую модель/поле
- `backend/MAP.md` — если новая модель
- Запусти `bash scripts/check_docs.sh` — passed

## Антипаттерны
- Миграция без `downgrade()`
- ALTER TABLE NOT NULL без backfill на больших таблицах
- Параллельная работа двух агентов с миграциями (race на `alembic_version`)
- Миграция данных в `upgrade()` функции вместо отдельного скрипта (для большой таблицы)
- Удаление колонки в одной миграции с использующим её кодом (нужно: 1) deploy stop using → 2) deploy migration drop)

## Отчёт
```
| Шаг | Результат |
|-----|-----------|
| alembic heads | single head OK |
| Модель | services/X.py — добавлено поле Y |
| Миграция | migrations/versions/abc_add_y.py |
| upgrade/downgrade | passed |
| Тесты | 42 passed, 0 failed |
| DOMAIN_X.md | updated |
```
