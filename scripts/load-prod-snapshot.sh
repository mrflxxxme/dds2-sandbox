#!/usr/bin/env bash
# ─── Залить прод-снимок в локальную БД с маскировкой sensitive полей ────────
#
# Использование:
#   scripts/load-prod-snapshot.sh                    # последний снимок из backups/prod/
#   scripts/load-prod-snapshot.sh FILE.sql.gz        # конкретный файл
#   FORCE=1 scripts/load-prod-snapshot.sh            # без интерактивного confirm
#
# Что делает:
#   1. Останавливает backend/worker (чтобы не было write-conflict + не дёрнули WB API)
#   2. DROP + CREATE локальной БД
#   3. gunzip | psql → залить дамп
#   4. Применить scripts/sql/mask-sensitive.sql
#   5. Прогнать alembic upgrade head (если локальный код впереди прод-снимка)
#   6. Поднять backend/worker обратно
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

LOCAL_SNAPSHOT_DIR="backups/prod"
MASK_SQL="scripts/sql/mask-sensitive.sql"

# ─── Защита: убедиться, что мы НЕ на прод-машине ─────────────────────────────
if [[ -f /opt/dds_data/backups ]] || [[ -d /opt/dds_data/backups ]]; then
  echo "❌ Этот путь существует только на прод-data-сервере. Скрипт работает только локально."
  exit 1
fi
if [[ "${DATABASE_URL:-}" == *"194.67.100.57"* ]] || [[ "${DATABASE_URL:-}" == *"130.49.150.69"* ]]; then
  echo "❌ DATABASE_URL указывает на прод. Прерываю."
  exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔄 Load prod snapshot → local"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ─── Выбор файла ─────────────────────────────────────────────────────────────
SNAPSHOT_FILE="${1:-}"
if [[ -z "${SNAPSHOT_FILE}" ]]; then
  SNAPSHOT_FILE=$(ls -1t "${LOCAL_SNAPSHOT_DIR}"/*.sql.gz 2>/dev/null | head -1 || true)
fi
if [[ -z "${SNAPSHOT_FILE}" ]] || [[ ! -f "${SNAPSHOT_FILE}" ]]; then
  echo "❌ Снимок не найден. Сначала: make pull-prod"
  exit 1
fi

SNAPSHOT_BASENAME=$(basename "${SNAPSHOT_FILE}")
SNAPSHOT_SIZE_MB=$(( $(stat -f %z "${SNAPSHOT_FILE}" 2>/dev/null || stat -c %s "${SNAPSHOT_FILE}") / 1024 / 1024 ))

echo "📦 Снимок:    ${SNAPSHOT_FILE} (${SNAPSHOT_SIZE_MB}MB)"
echo "🎯 Цель:      локальный контейнер 'db' (dds_db)"
echo "🔐 Маскирую:  integration_keys.encrypted_key, users.password_hash, project_invites.invite_token"
echo ""

# ─── Confirm ─────────────────────────────────────────────────────────────────
if [[ "${FORCE:-0}" != "1" ]]; then
  echo "⚠️  Это ПОЛНОСТЬЮ перезапишет локальную БД. Введи 'YES' для подтверждения:"
  read -r CONFIRM
  if [[ "${CONFIRM}" != "YES" ]]; then
    echo "❌ Отменено."
    exit 0
  fi
fi

# ─── 1. Стопаем consumer'ов БД (pgbouncer тоже — иначе DROP не пройдёт) ────
echo ""
echo "🛑 Останавливаю backend + worker + pgbouncer..."
docker compose stop backend worker pgbouncer >/dev/null

# Копируем снимок в локальную backups/ — она примаунчена в db-backup:/backups
LOCAL_COPY_NAME=".prod-snapshot-loading.sql.gz"
cp "${SNAPSHOT_FILE}" "backups/${LOCAL_COPY_NAME}"

# ─── 2-3. Переиспользуем штатный restore.sh (он DROP+CREATE+gunzip|psql) ───
echo "🔁 Restore (через scripts/restore.sh)..."
echo "YES" | docker compose exec -T db-backup /scripts/restore.sh "${LOCAL_COPY_NAME}" \
  | grep -vE "^\s*$" | tail -20

rm -f "backups/${LOCAL_COPY_NAME}"

# Поднимаем pgbouncer обратно — нужен для backend, миграций и маски-SQL
echo "🔌 Поднимаю pgbouncer..."
docker compose up -d pgbouncer >/dev/null
# Wait until healthy
until [[ "$(docker inspect -f '{{.State.Health.Status}}' dds_app-pgbouncer-1 2>/dev/null)" == "healthy" ]]; do
  sleep 1
done
echo "✅ Дамп залит."

# ─── 4. Маскировка sensitive полей ──────────────────────────────────────────
echo "🔐 Маскирую sensitive поля..."
docker compose exec -T db psql -U dds -d dds_db -v ON_ERROR_STOP=1 < "${MASK_SQL}"

# ─── 5. Прогнать alembic upgrade на случай если локальный код впереди ───────
echo "⬆️  alembic upgrade head (на случай новых миграций после снимка)..."
docker compose run --rm backend alembic upgrade head 2>&1 | tail -5

# ─── 6. Поднимаем backend/worker ────────────────────────────────────────────
echo "🚀 Поднимаю backend + worker..."
docker compose up -d backend worker >/dev/null

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Готово. Локальная БД — копия прода на момент:"
echo "   ${SNAPSHOT_BASENAME}"
echo ""
echo "🔑 Логин на локалке: любой прод-email, пароль 'localdev'"
echo "🚫 WB / Telegram интеграции отключены (encrypted_key замаскированы)."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
