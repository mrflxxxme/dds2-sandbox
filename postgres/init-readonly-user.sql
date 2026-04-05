-- Read-only user for MCP agent access
-- Runs via: docker compose exec db psql -U dds -d dds_db -f /etc/postgresql/init-readonly-user.sql

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'readonly_agent') THEN
        CREATE USER readonly_agent WITH PASSWORD 'readonly_dds_2026';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE dds_db TO readonly_agent;
GRANT USAGE ON SCHEMA public TO readonly_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO readonly_agent;
