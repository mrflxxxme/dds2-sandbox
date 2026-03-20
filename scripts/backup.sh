#!/bin/bash
# ─── DDS PostgreSQL Backup Script ─────────────────────────────────────────────
# Создаёт pg_dump бэкап, сжимает gzip, удаляет старые (ротация).
# При ошибке — шлёт alert в Telegram.
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
MIN_SIZE=10240  # 10KB minimum — реальный дамп всегда больше
MIN_BACKUP_COUNT=4  # минимум бэкапов (6h интервал × 4 = 24h покрытие)

# Имя файла: dds_db_20260304_170800.sql.gz
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="${PGDATABASE}_${TIMESTAMP}.sql.gz"
FILEPATH="${BACKUP_DIR}/${FILENAME}"

# ─── Telegram alert ──────────────────────────────────────────────────────────
alert_telegram() {
  local msg="⚠️ DDS Backup FAILED on $(hostname) at $(date): $1"
  if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_ALERT_CHAT_ID:-}" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d chat_id="${TELEGRAM_ALERT_CHAT_ID}" \
      -d text="$msg" || true
    echo "📨 Telegram alert sent"
  else
    echo "⚠️  TELEGRAM_BOT_TOKEN or TELEGRAM_ALERT_CHAT_ID not set, skipping alert"
  fi
}

# ─── Cleanup on failure ─────────────────────────────────────────────────────
fail() {
  echo "❌ $1"
  alert_telegram "$1"
  rm -f "${FILEPATH}"
  exit 1
}

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
  | gzip > "${FILEPATH}" \
  || fail "pg_dump failed for ${PGDATABASE}"

# Проверяем минимальный размер (10KB)
FILESIZE=$(stat -c%s "${FILEPATH}" 2>/dev/null || stat -f%z "${FILEPATH}" 2>/dev/null || echo 0)
if [ "${FILESIZE}" -lt "${MIN_SIZE}" ]; then
  fail "Backup too small (${FILESIZE} bytes, minimum ${MIN_SIZE}). Dump is likely empty or corrupted."
fi

# Верификация: проверяем что gzip-файл валиден
echo "🔍 Верифицирую бэкап..."
gunzip -t "${FILEPATH}" || fail "Backup file is corrupted (gunzip -t failed)"

FILESIZE_MB=$(echo "scale=2; ${FILESIZE}/1048576" | bc 2>/dev/null || echo "${FILESIZE} bytes")
echo "✅ Бэкап создан и верифицирован: ${FILENAME} (${FILESIZE_MB} MB)"

# Ротация: удаляем файлы старше N дней
DELETED=$(find "${BACKUP_DIR}" -name "${PGDATABASE}_*.sql.gz" -mtime +${RETENTION_DAYS} -delete -print | wc -l)
if [ "${DELETED}" -gt 0 ]; then
  echo "🗑️  Удалено старых бэкапов: ${DELETED}"
fi

# Проверка количества бэкапов
TOTAL=$(find "${BACKUP_DIR}" -name "${PGDATABASE}_*.sql.gz" | wc -l)
if [ "${TOTAL}" -lt "${MIN_BACKUP_COUNT}" ]; then
  echo "⚠️  Мало бэкапов: ${TOTAL} (минимум ${MIN_BACKUP_COUNT}). Возможно, пропущены запуски."
  alert_telegram "Low backup count: ${TOTAL} (minimum ${MIN_BACKUP_COUNT}). Backups may be missing for last 24h."
fi

echo "📁 Всего бэкапов: ${TOTAL} (хранение ${RETENTION_DAYS} дней)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
