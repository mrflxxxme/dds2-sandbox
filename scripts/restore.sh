#!/bin/bash
# ─── DDS PostgreSQL Restore Script ───────────────────────────────────────────
# Восстанавливает базу из бэкапа (.sql.gz или .sql).
#
# Использование:
#   docker compose exec db-backup /scripts/restore.sh [файл_бэкапа]
#
# Если файл не указан — показывает список доступных бэкапов.
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

PGHOST="${PGHOST:-db}"
PGUSER="${POSTGRES_USER:-dds}"
PGDATABASE="${POSTGRES_DB:-dds_db}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔄 DDS Restore"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Если файл не указан — показать список
if [ -z "${1:-}" ]; then
  echo ""
  echo "📁 Доступные бэкапы:"
  echo ""
  ls -lh "${BACKUP_DIR}"/*.sql.gz "${BACKUP_DIR}"/*.sql 2>/dev/null | awk '{print "  " $NF " (" $5 ")"}'
  echo ""
  echo "Использование: restore.sh <имя_файла>"
  echo "Пример:        restore.sh dds_db_20260304_170800.sql.gz"
  exit 0
fi

BACKUP_FILE="${1}"

# Проверяем путь — абсолютный или относительный от BACKUP_DIR
if [ ! -f "${BACKUP_FILE}" ]; then
  BACKUP_FILE="${BACKUP_DIR}/${BACKUP_FILE}"
fi

if [ ! -f "${BACKUP_FILE}" ]; then
  echo "❌ Файл не найден: ${1}"
  exit 1
fi

echo ""
echo "⚠️  ВНИМАНИЕ: это полностью перезапишет базу ${PGDATABASE}!"
echo "   Файл: ${BACKUP_FILE}"
echo ""
echo "   Для подтверждения введите 'YES': "
read -r CONFIRM

if [ "${CONFIRM}" != "YES" ]; then
  echo "❌ Отменено."
  exit 0
fi

echo "⏳ Восстанавливаю из ${BACKUP_FILE}..."

# Закрываем активные подключения
psql -h "${PGHOST}" -U "${PGUSER}" -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${PGDATABASE}' AND pid <> pg_backend_pid();" \
  2>/dev/null || true

# Дропаем и пересоздаём базу
psql -h "${PGHOST}" -U "${PGUSER}" -d postgres -c "DROP DATABASE IF EXISTS ${PGDATABASE};"
psql -h "${PGHOST}" -U "${PGUSER}" -d postgres -c "CREATE DATABASE ${PGDATABASE} OWNER ${PGUSER};"

# Восстанавливаем
if [[ "${BACKUP_FILE}" == *.gz ]]; then
  gunzip -c "${BACKUP_FILE}" | psql -h "${PGHOST}" -U "${PGUSER}" -d "${PGDATABASE}" --quiet
else
  psql -h "${PGHOST}" -U "${PGUSER}" -d "${PGDATABASE}" --quiet < "${BACKUP_FILE}"
fi

echo "✅ База ${PGDATABASE} восстановлена из ${BACKUP_FILE}"
echo ""
echo "⚠️  Перезапустите бэкенд: docker compose restart backend"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
