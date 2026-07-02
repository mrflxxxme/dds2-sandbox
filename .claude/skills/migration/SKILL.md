---
name: migration
description: "Alembic-миграция DDS2: проверка heads, генерация, тест upgrade/downgrade, обновление docs."
---

# /migration — Alembic-миграция

## Правила
- Только sequential, только lead. Не делегировать teammate и не запускать параллельно (race на `alembic_version`).
- Большие таблицы — отдельно продумать блокировки и batched-backfill.
- На прод миграции уезжают через CI, не вручную.

## Параметры (спросить, если не дано)
Имя (snake_case), что меняется (колонка / таблица / индекс / data migration), затронутые модели, нужен ли backfill.

## Процесс
1. **Состояние:** `alembic current` + `alembic heads`. Несколько heads → сначала `alembic merge heads -m "merge"` → `upgrade head`.
2. **Модель:** правка в `backend/models/{domain}.py` — `Numeric(18,2)` для денег, `DateTime(timezone=True)` для дат, `BigInteger` для ID. Новые SoftDelete-модели энфорсеры обнаруживают автоматически (AST по `backend/models/`) — список вручную вести не нужно.
3. **Генерация:** `alembic revision --autogenerate -m "имя"`. Проверить файл: корректный `upgrade()`, непустой `downgrade()`, имена индексов `ix_table_column`, partial-индекс `WHERE is_deleted = false` для soft-delete.
4. **Backfill:** ALTER + NOT NULL → `server_default` ИЛИ batch UPDATE до ALTER. Большие таблицы — batched в отдельном скрипте, не в `upgrade()`.
5. **Тест:** `alembic upgrade head && downgrade -1 && upgrade head` — без ошибок, данные не теряются.
6. **Тесты:** `make test-changed`; новая модель/поле → тест на multi-tenancy и soft-delete.
7. **Docs:** упомянуть модель/поле в `backend/DOMAIN_{DOMAIN}.md`; `bash scripts/check_docs.sh`.

## Антипаттерны
Пустой `downgrade()`; ALTER NOT NULL без backfill на большой таблице; data-migration в `upgrade()` для большой таблицы; drop колонки одновременно с использующим её кодом (нужно два деплоя).
