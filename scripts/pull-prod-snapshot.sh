#!/usr/bin/env bash
# ─── Скачать свежий прод-дамп в локальный backups/prod/ ─────────────────────
#
# Использование:
#   scripts/pull-prod-snapshot.sh           # последний существующий дамп
#   scripts/pull-prod-snapshot.sh --fresh   # сначала создать новый на проде
#
# Прод-бэкапы лежат в /opt/dds_data/backups/ (контейнер dds_data-db-backup-1
# складывает их туда каждые 6 часов автоматически).
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SSH_HOST="${DDS_DATA_SSH:-dds-data}"
REMOTE_BACKUP_DIR="/opt/dds_data/backups"
LOCAL_SNAPSHOT_DIR="backups/prod"
KEEP_LOCAL=3  # сколько последних снимков держать локально

FRESH=0
if [[ "${1:-}" == "--fresh" ]]; then
  FRESH=1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📥 Pull prod snapshot from ${SSH_HOST}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Проверка SSH
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "${SSH_HOST}" 'echo ok' >/dev/null 2>&1; then
  echo "❌ SSH ${SSH_HOST} недоступен. Проверь ~/.ssh/config."
  exit 1
fi

# Опционально — создать свежий дамп на проде
if [[ "${FRESH}" -eq 1 ]]; then
  echo "🔄 Создаю свежий дамп на проде (это ~30-60 сек)..."
  ssh "${SSH_HOST}" 'cd /opt/dds_data && docker compose exec -T db-backup /scripts/backup.sh' \
    | tail -20
fi

# Находим самый свежий .sql.gz
REMOTE_FILE=$(ssh "${SSH_HOST}" "ls -1t ${REMOTE_BACKUP_DIR}/*.sql.gz 2>/dev/null | head -1")
if [[ -z "${REMOTE_FILE}" ]]; then
  echo "❌ На проде нет .sql.gz файлов в ${REMOTE_BACKUP_DIR}/"
  exit 1
fi

REMOTE_NAME=$(basename "${REMOTE_FILE}")
REMOTE_SIZE=$(ssh "${SSH_HOST}" "stat -c %s '${REMOTE_FILE}'")
REMOTE_SIZE_MB=$(( REMOTE_SIZE / 1024 / 1024 ))

mkdir -p "${LOCAL_SNAPSHOT_DIR}"
LOCAL_FILE="${LOCAL_SNAPSHOT_DIR}/${REMOTE_NAME}"

if [[ -f "${LOCAL_FILE}" ]]; then
  LOCAL_SIZE=$(stat -f %z "${LOCAL_FILE}" 2>/dev/null || stat -c %s "${LOCAL_FILE}")
  if [[ "${LOCAL_SIZE}" -eq "${REMOTE_SIZE}" ]]; then
    echo "✅ Снимок ${REMOTE_NAME} (${REMOTE_SIZE_MB}MB) уже скачан."
    echo "   Путь: ${LOCAL_FILE}"
    exit 0
  fi
fi

echo "⬇️  Скачиваю ${REMOTE_NAME} (${REMOTE_SIZE_MB}MB)..."
scp -q "${SSH_HOST}:${REMOTE_FILE}" "${LOCAL_FILE}"

# Sanity-check: размер совпал
LOCAL_SIZE=$(stat -f %z "${LOCAL_FILE}" 2>/dev/null || stat -c %s "${LOCAL_FILE}")
if [[ "${LOCAL_SIZE}" -ne "${REMOTE_SIZE}" ]]; then
  echo "❌ Размер не совпал (remote=${REMOTE_SIZE}, local=${LOCAL_SIZE}). Удаляю битый файл."
  rm -f "${LOCAL_FILE}"
  exit 1
fi

# Ротация: оставить только последние N
KEEP_COUNT=$(ls -1t "${LOCAL_SNAPSHOT_DIR}"/*.sql.gz 2>/dev/null | wc -l | tr -d ' ')
if [[ "${KEEP_COUNT}" -gt "${KEEP_LOCAL}" ]]; then
  ls -1t "${LOCAL_SNAPSHOT_DIR}"/*.sql.gz | tail -n +$((KEEP_LOCAL + 1)) | xargs rm -f
  echo "🧹 Удалены старые снимки (оставлено последних ${KEEP_LOCAL})."
fi

echo ""
echo "✅ Готово: ${LOCAL_FILE} (${REMOTE_SIZE_MB}MB)"
echo "   Далее: make load-prod"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
