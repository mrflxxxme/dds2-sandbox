# CLAUDE.md — DDS Project Entry Point

## Quick Start
```bash
docker compose up -d                              # Start all services
docker compose exec backend pytest tests/ -x      # Run tests
docker compose logs backend --tail=50              # Check logs
bash scripts/check_conventions.sh                  # Convention checks
```

## Project Overview
**DDS** — система управленческого учёта (ДДС) для e-commerce (Wildberries).
Стек: FastAPI + PostgreSQL 15 + PgBouncer + Redis + MinIO + Next.js 15.

## Architecture (read before ANY change)
- Full guide: [AGENTS.md](AGENTS.md)
- Business rules: [BUSINESS_RULES.md](BUSINESS_RULES.md)
- Code conventions: [CONVENTIONS.md](CONVENTIONS.md)
- Deploy playbook: [PLAYBOOK.md](PLAYBOOK.md)

## Domain Context Files
Перед работой с модулем — **ОБЯЗАТЕЛЬНО** прочитай его контекстный файл:

| Домен | Контекст | Ключевые файлы |
|-------|----------|----------------|
| Транзакции & Импорт | `backend/DOMAIN_TRANSACTIONS.md` | etl/, services/transactions_service.py, routers/import_txn.py |
| Отчёты (ДДС, БДР, ОПИУ) | `backend/DOMAIN_REPORTS.md` | services/reports/, services/wb_bdr_service.py, services/opiu_service.py |
| Планирование | `backend/DOMAIN_PLANNING.md` | services/planning/, routers/planning.py |
| Себестоимость | `backend/DOMAIN_COST.md` | services/cost/, routers/cost.py, etl/cost_parsers.py |
| WB Интеграция | `backend/DOMAIN_WB.md` | integrations/, services/funnel/, scheduler/jobs/ |
| Фронтенд | `frontend-react/DOMAIN_FRONTEND.md` | src/app/, src/lib/api.ts, src/types/api.ts |

## Critical Rules (MUST follow)
1. **Multi-tenancy:** EVERY query MUST filter by `project_id`
2. **Datetime:** ONLY `from backend.utils.time import utcnow` (NEVER datetime.utcnow())
3. **Money:** ONLY `Numeric(18, 2)` (NEVER Float)
4. **SQL:** ONLY parameterized `:param` binding (NEVER f-strings in text())
5. **Soft delete:** `model.soft_delete()` (NEVER db.delete())
6. **Cache:** invalidate after mutations, key MUST include project_id
7. **Business logic:** in services/ (NEVER in routers/)
8. **Tests:** MUST pass before commit

## Git Workflow
- Коммиты на русском: `feat:` / `fix:` / `infra:` / `refactor:` / `test:`
- Push в `dev` → staging auto-deploy → verify → merge to `main` → production
- НИКОГДА не деплоить напрямую через SSH

## Before Every Commit
```bash
docker compose exec backend pytest tests/ -x --tb=short
bash scripts/check_conventions.sh
```
