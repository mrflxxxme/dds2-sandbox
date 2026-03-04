#!/bin/bash
# ─── DDS PostgreSQL Backup Script ─────────────────────────────────────────────
# Создаёт pg_dump бэкап, сжимает gzip, удаляет старые (ротация).
#
# Использование:
#   Автоматически — через контейнер db-backup (каждые 6 часов)
#   Вручную       — docker compose exec db-backup /scripts/backup.sh
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# Настройки (берутся из переменных окружения или дефолты)
PGHOST="${PGHOST:-db}"
PGUSER="${POSTGRES_USER:-dds}"
PGDATABASE="${POSTGRES_DB:-dds_db}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

# Имя файла: dds_db_20260304_170800.sql.gz
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="${PGDATABASE}_${TIMESTAMP}.sql.gz"
FILEPATH="${BACKUP_DIR}/${FILENAME}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📦 DDS Backup"
echo "   Database : ${PGDATABASE}"
echo "   Host     : ${PGHOST}"
echo "   Time     : $(date '+%Y-%m-%d %H:%M:%S')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Создаём директорию если нет
mkdir -p "${BACKUP_DIR}"

# pg_dump → gzip
echo "⏳ Делаю дамп базы..."
pg_dump -h "${PGHOST}" -U "${PGUSER}" -d "${PGDATABASE}" \
  --no-owner --no-privileges --clean --if-exists \
  | gzip > "${FILEPATH}"

# Проверяем что файл не пустой
FILESIZE=$(stat -c%s "${FILEPATH}" 2>/dev/null || stat -f%z "${FILEPATH}" 2>/dev/null || echo 0)
if [ "${FILESIZE}" -lt 100 ]; then
  echo "❌ Бэкап слишком маленький (${FILESIZE} байт), что-то пошло не так!"
  rm -f "${FILEPATH}"
  exit 1
fi

FILESIZE_MB=$(echo "scale=2; ${FILESIZE}/1048576" | bc 2>/dev/null || echo "${FILESIZE} bytes")
echo "✅ Бэкап создан: ${FILENAME} (${FILESIZE_MB} MB)"

# Ротация: удаляем файлы старше N дней
DELETED=$(find "${BACKUP_DIR}" -name "${PGDATABASE}_*.sql.gz" -mtime +${RETENTION_DAYS} -delete -print | wc -l)
if [ "${DELETED}" -gt 0 ]; then
  echo "🗑️  Удалено старых бэкапов: ${DELETED}"
fi

# Список текущих бэкапов
TOTAL=$(find "${BACKUP_DIR}" -name "${PGDATABASE}_*.sql.gz" | wc -l)
echo "📁 Всего бэкапов: ${TOTAL} (хранение ${RETENTION_DAYS} дней)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
