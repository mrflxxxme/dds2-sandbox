-- Read-only user for MCP agent access (used by mcp-servers/dds-mcp).
-- Auto-applied at PostgreSQL first init via /docker-entrypoint-initdb.d/.
-- For an already-initialized DB run manually:
--   docker compose exec db psql -U dds -d dds_db -f /docker-entrypoint-initdb.d/init-readonly-user.sql

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'readonly_agent') THEN
        CREATE USER readonly_agent WITH PASSWORD 'readonly_dds_2026';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE dds_db TO readonly_agent;
GRANT USAGE ON SCHEMA public TO readonly_agent;

-- Existing tables (idempotent — re-run safe)
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_agent;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO readonly_agent;

-- Future tables created by Alembic migrations (which run as `dds`).
-- IMPORTANT: must be FOR ROLE dds, otherwise default privileges apply only to
-- tables created by the role running this script (postgres at init time),
-- and Alembic-created tables won't get SELECT for readonly_agent.
ALTER DEFAULT PRIVILEGES FOR ROLE dds IN SCHEMA public GRANT SELECT ON TABLES TO readonly_agent;
ALTER DEFAULT PRIVILEGES FOR ROLE dds IN SCHEMA public GRANT SELECT ON SEQUENCES TO readonly_agent;
