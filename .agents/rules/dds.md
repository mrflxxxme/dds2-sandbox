---
trigger: glob
---

Git & CI/CD Workflow:

After every code modification: Add a clear commit message in Russian.

Execute git commit and push to GitHub (branch: dev).

The dev branch triggers deployment to the Staging environment. Do NOT deploy directly to main without testing on Staging.

Documentation discipline:

Every structural or logic change must update documentation.

Maintain BUSINESS_RULES.md for business logic and README.md for tech structure.

Keep API documented (OpenAPI / Swagger aligned).

Architecture principle (Domain Modules):

Design features as scalable domain modules (e.g., backend/modules/finance).

Avoid monolithic components. Keep strict separation of concerns (API / Services / DB / UI).

Database & Multi-tenancy (RLS) - CRITICAL:

All schema changes must use Alembic migrations.

PostgreSQL RLS is enabled. Note: FORCE RLS will be activated after creating a separate dds_app database role.

Every DB session MUST set the tenant context (SET LOCAL app.project_id = %s).

For HTTP requests: use the DB middleware/dependency.

For Background Tasks (Celery/Schedulers): ALWAYS wrap DB sessions in a context manager to set app.project_id.

Tech stack (do not change without approval):

Backend: Python 3.11, FastAPI, SQLAlchemy 2.0 (async), PostgreSQL 15, Redis.

Frontend: Next.js (App Router), React, TypeScript.

Infrastructure: Docker Compose (with docker-compose.staging.yml for tests).

WB API & Synchronization Performance:

Concurrency: Use asyncio.Semaphore and asyncio.gather for concurrency control. Target architecture for high-volume sync: migrate to Producer-Consumer (asyncio.Queue).

Use separate semaphores for Stats and Adv APIs due to different rate limits.

Rate Limits: Treat 429 errors as normal limits (use custom RateLimitError and respect Retry-After). Do NOT trip the Circuit Breaker on 429s.

Circuit Breaker: Use it strictly for 500-504 server errors.

Fail-safes: Wrap all background sync tasks in asyncio.wait_for (timeout) and try/finally to guarantee sync_log status updates and avoid dead RUNNING tasks.

Cryptography:

API keys are stored encrypted in wb_api_keys using SHA-256 Fernet key derivation.

Current logic uses legacy_fallback in crypto.py. Planned: add encryption_version column to the DB model.

Never change crypto logic without an accompanying Alembic data-migration script.

Observability & Error handling:

Backend: All errors must be sent to Sentry.

Use structlog for structured JSON logging with bound context (project_id, trace_id).

Frontend: Always show user-friendly error messages, never raw stack traces.

Hot-reload vs Docker rebuild:

Backend .py files: hot-reload is automatic (uvicorn --reload).

Frontend .tsx/.ts files: hot-reload is automatic (Next.js).

Rebuild Docker ONLY when changing: Dockerfile, docker-compose.yml, package.json, requirements-backend.txt.

Skills and Workflows:

New API endpoint -> use skill new-api-endpoint.

New page -> use skill new-page.

DB Schema change -> use skill db-migration.