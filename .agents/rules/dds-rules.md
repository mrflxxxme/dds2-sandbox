# DDS Development Rules

## 1. Git & CI/CD Workflow
- After every code modification: add a clear commit message **in Russian**.
- Execute `git commit` and `git push` to GitHub (branch: `dev`).
- The `dev` branch triggers deployment to the **Staging** environment.
- Do **NOT** deploy directly to `main` without testing on Staging.

## 2. Documentation Discipline
- Every structural or logic change must update documentation.
- Maintain `BUSINESS_RULES.md` for business logic and `README.md` for tech structure.
- Keep API documented (OpenAPI / Swagger aligned).

## 3. Architecture Principle (Domain Modules)
- Design features as scalable domain modules (e.g., `backend/modules/finance`).
- Avoid monolithic components. Keep strict separation of concerns (API / Services / DB / UI).
- Ensure AI-agent-friendly code (clear naming, docstrings, comments).

## 4. Database & Multi-tenancy (RLS) — CRITICAL
- All schema changes must use **Alembic migrations**.
- PostgreSQL RLS is **ENABLED** (FORCE RLS will be activated after creating a separate `dds_app` DB role).
- Every DB session **MUST** set the tenant context: `SET LOCAL app.project_id = %s`.
  - For **HTTP requests**: use the DB middleware/dependency (`get_db_with_rls`).
  - For **Background Tasks** (Schedulers/Workers): **ALWAYS** wrap DB sessions in a context manager to set `app.project_id`.
- Always check that columns/tables exist before querying.
- Use UPSERT (`ON CONFLICT`) for idempotent data imports.

## 5. Tech Stack (do not change without approval)
- **Backend**: Python 3.11, FastAPI, SQLAlchemy 2.0 (async), PostgreSQL 15, Redis.
- **Frontend**: Next.js (App Router), React, TypeScript.
- **Infrastructure**: Docker Compose (with `docker-compose.staging.yml` for tests).
- **External APIs**: Wildberries (Analytics, Adv, Content).

## 6. WB API & Synchronization Performance
- Maximize speed using `asyncio.Semaphore` for concurrency control. Target: Producer-Consumer pattern (`asyncio.Queue`) for high-volume sync.
- Use **separate semaphores** for Stats and Adv APIs (different rate limits).
- **Rate Limits**: Treat 429 errors as normal limits — use custom `RateLimitError` and respect `Retry-After` header. Do **NOT** trip the Circuit Breaker on 429s.
- **Circuit Breaker**: Use strictly for 500-504 server errors to avoid cascading failures (`recovery_timeout=120s`).
- **Fail-safes**: Wrap all background sync tasks in `asyncio.wait_for(timeout)` and `try/finally` to guarantee `sync_log` status updates. No dead RUNNING tasks.

## 7. Cryptography
- API keys are stored encrypted in `integration_keys` using SHA-256 Fernet key derivation.
- Never change crypto logic without an accompanying Alembic data-migration script.

## 8. Observability & Error Handling
- Backend: All errors must be sent to **Sentry** (conditional on `SENTRY_DSN`).
- Use `structlog` for structured JSON logging with bound context (`project_id`, `trace_id`).
- Frontend: Always show user-friendly error messages, never raw stack traces.
- On sync errors: still reload data (partial data may have been saved).

## 9. Hot-reload vs Docker Rebuild
- Backend `.py` files: hot-reload is automatic (`uvicorn --reload`), **do NOT rebuild Docker**.
- Frontend `.tsx/.ts/.css` files: hot-reload is automatic (Next.js), **do NOT rebuild Docker**.
- Rebuild Docker **ONLY** when changing: `Dockerfile`, `docker-compose.yml`, `package.json`, `requirements-backend.txt`.

## 10. Skills and Workflows
- New API endpoint → use skill `new-api-endpoint`.
- New page → use skill `new-page`.
- DB Schema change → use skill `db-migration`.
- Any code change → follow `/dev` workflow.