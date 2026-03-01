---
trigger: always_on
---

DDS Development Rules

1. After every code modification:
   - Add a clear commit message in Russian.
   - Execute git commit and push to GitHub (branch: dev).
   - Always follow the /dev workflow for any code changes.

2. Documentation discipline:
   - Every structural or logic change must update documentation.
   - Maintain BUSINESS_RULES.md for business logic.
   - Maintain README.md for technical structure.
   - Keep API documented (OpenAPI / Swagger aligned).

3. Architecture principle:
   - Always design features as scalable modules.
   - Avoid hardcoded logic.
   - Keep separation of concerns (API / Services / DB / UI).
   - Ensure AI-agent-friendly code (clear naming, docstrings, comments).

4. Long-term scalability:
   - Prefer modular structure.
   - Prepare system for multi-project, multi-user future.
   - Maintain clean folder structure.
   - Avoid monolithic components.

5. Tech stack (do not change without approval):
   - Backend: Python 3.11, FastAPI, SQLAlchemy, PostgreSQL, Redis.
   - Frontend: Next.js (App Router), React, TypeScript.
   - Infrastructure: Docker Compose.
   - External APIs: Wildberries (Analytics, Adv, Content).

6. After backend/frontend changes:
   - Rebuild Docker containers: docker compose up -d --build backend frontend-react.
   - Verify in browser that the feature works.
   - Check docker compose logs for errors.

7. WB API integration:
   - Always handle 429 rate limits with retry + exponential backoff.
   - Commit partial data (per-day) to avoid losing progress on timeout.
   - API keys are stored encrypted in wb_api_keys table.

8. Database:
   - All schema changes via SQL migrations or alembic.
   - Always check that columns/tables exist before querying.
   - Use UPSERT (ON CONFLICT) for idempotent data imports.

9. Error handling:
   - Backend: log errors with structured logging, return meaningful HTTP status codes.
   - Frontend: always show user-friendly error messages, never raw stack traces.
   - On sync errors: still reload data (partial data may have been saved).

10. Hot-reload vs Docker rebuild:
   - Backend .py files: hot-reload автоматический (uvicorn --reload), НЕ пересобирай Docker.
   - Frontend .tsx/.ts/.css files: hot-reload автоматический (Next.js), НЕ пересобирай Docker.
   - Docker rebuild ТОЛЬКО при изменении: Dockerfile, docker-compose.yml, package.json, requirements-backend.txt.

11. Skills и Workflows:
   - При создании нового API — используй skill `new-api-endpoint`.
   - При создании новой страницы — используй skill `new-page`.
   - При изменении схемы БД — используй skill `db-migration`.