# Backend — ориентир

## Перед изменением
1. Определи домен → [DOMAIN_INDEX.md](DOMAIN_INDEX.md) → читай нужный `DOMAIN_*.md`.
2. Архитектура: `routers/` → `services/` → `models/`. Логика — в сервисе, не в роутере.
3. Навигация по коду — [MAP.md](MAP.md).

## Правила
Iron rules — в корневом `CLAUDE.md`. Backend-детали (типы PG, кэш, multi-tenancy, тесты) — в `.claude/rules/backend.md` (грузится при правке `backend/`).

## Shared / infra (вне доменов)
- `services/refs_service.py` — CRUD справочников (Account, Override, CounterpartyCategory).
- `services/settings_service.py` — key-value настройки проекта (`ProjectSetting`).
- `services/project_settings_service.py` — мутации настроек (tax_rate, vat_rate) с инвалидацией кэша.
- `routers/refs.py`, `routers/auth.py`, `routers/projects.py` — базовые endpoints.
