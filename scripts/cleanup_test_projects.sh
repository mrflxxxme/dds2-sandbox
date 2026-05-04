#!/usr/bin/env bash
# Local-DB cleanup: удалить все проекты кроме явно перечисленных в KEEP_IDS.
# Безопасный idempotent скрипт для локальной разработки.
#
# ИНЦИДЕНТ 2026-05-04: тестовые фикстуры conftest.py создавали projects + users
# и не делали teardown. За 2 месяца накопилось 74k проектов, 524 MB category_ref,
# worker startup висел 5+ мин на seed_default_categories (N×30 INSERT через
# PgBouncer). После cleanup: 1325 MB → 630 MB, startup → 3 сек.
#
# НЕ запускать на production! Скрипт удаляет owner users без других проектов.

set -euo pipefail

KEEP_IDS="${KEEP_IDS:-4,15}"  # 4=Default, 15=вяткин (override через env)

echo "Backup → .local-backup-pre-cleanup-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T db pg_dump -U dds -d dds_db --no-owner --no-acl 2>/dev/null \
    | gzip > ".local-backup-pre-cleanup-$(date +%Y%m%d-%H%M%S).sql.gz"

echo "Cleanup projects WHERE id NOT IN ($KEEP_IDS) ..."
docker compose exec -T db psql -U dds -d dds_db <<SQL
BEGIN;
SET LOCAL session_replication_role = 'replica';
CREATE TEMP TABLE keep_projects (id BIGINT PRIMARY KEY);
INSERT INTO keep_projects VALUES $(echo "$KEEP_IDS" | sed 's/,/),(/g;s/^/(/;s/$/)/');

DO \$\$
DECLARE
    rec RECORD;
    deleted BIGINT;
BEGIN
    FOR rec IN
        SELECT DISTINCT tc.table_name, kcu.column_name
        FROM information_schema.referential_constraints rc
        JOIN information_schema.table_constraints tc ON rc.constraint_name = tc.constraint_name
        JOIN information_schema.key_column_usage kcu ON rc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu ON rc.unique_constraint_name = ccu.constraint_name
        WHERE ccu.table_name = 'projects' AND ccu.column_name = 'id'
    LOOP
        EXECUTE format('DELETE FROM %I WHERE %I NOT IN (SELECT id FROM keep_projects)', rec.table_name, rec.column_name);
        GET DIAGNOSTICS deleted = ROW_COUNT;
        IF deleted > 0 THEN RAISE NOTICE 'Deleted % from %', deleted, rec.table_name; END IF;
    END LOOP;
END \$\$;

DELETE FROM projects WHERE id NOT IN (SELECT id FROM keep_projects);
DELETE FROM users WHERE id NOT IN (SELECT DISTINCT owner_id FROM projects WHERE owner_id IS NOT NULL)
  AND id NOT IN (SELECT DISTINCT user_id FROM project_members WHERE user_id IS NOT NULL);
COMMIT;
SQL

echo "VACUUM FULL ANALYZE (compaction) ..."
docker compose exec -T db psql -U dds -d dds_db -c "VACUUM FULL ANALYZE;"

echo "Final size:"
docker compose exec -T db psql -U dds -d dds_db -c "SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size;"
