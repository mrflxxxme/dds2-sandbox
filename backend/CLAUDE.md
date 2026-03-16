# Backend Context

## Before ANY backend change
1. Determine domain → read the corresponding DOMAIN_*.md file
2. Follow architecture: Router → Service → Model (NEVER business logic in routers)

## Quick Reference
- Datetime: `from backend.utils.time import utcnow`
- Money: `Numeric(18, 2)` (NEVER Float)
- SQL: parameterized `:param` binding (NEVER f-strings)
- Queries: MUST filter by `project_id` AND `is_deleted == False`
- Cache: `@cached(ttl=300)` for reads, `invalidate_cache()` after mutations
- Tests: `pytest tests/ -x --tb=short` before commit
- Conventions: `bash scripts/check_conventions.sh`

## Domain files in this directory
- `DOMAIN_TRANSACTIONS.md` — import, ETL, categorization
- `DOMAIN_REPORTS.md` — DDS, BDR, OPIU, dashboard, stock/warehouse analytics
- `DOMAIN_PLANNING.md` — orders, payments, customs
- `DOMAIN_COST.md` — cost, nomenclature, duties
- `DOMAIN_WB.md` — WB API, funnel, sync

## Undomain files (shared/infra)
- `services/refs_service.py` — CRUD для справочников (Account, Override, CounterpartyCategory)
- `services/settings_service.py` — key-value настройки проекта (ProjectSetting)
- `routers/refs.py`, `routers/auth.py`, `routers/projects.py` — базовые endpoints
