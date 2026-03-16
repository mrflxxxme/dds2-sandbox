#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# DDS — Migration script: single server → 3 servers
#
# Prerequisites:
#   1. All 3 servers have Docker + Docker Compose installed
#   2. WireGuard VPN is configured and running (ping 10.0.0.x works)
#   3. SSH access from this machine to all 3 servers
#   4. .env files configured on each server
#
# Usage: bash migrate-to-3-servers.sh
# ═══════════════════════════════════════════════════════════════════════════════

set -e

# ─── Configuration ────────────────────────────────────────────────────────────
# Текущий production (всё на одном сервере)
CURRENT_HOST="130.49.150.69"
CURRENT_USER="root"
CURRENT_PATH="/opt/dds_app"

# Новые серверы
DATA_HOST=""      # ← ЗАПОЛНИТЬ: публичный IP Data-сервера
DATA_USER="root"
DATA_PATH="/opt/dds_data"

APP_HOST="130.49.150.69"   # Текущий production → become App server
APP_USER="root"
APP_PATH="/opt/dds_app"

MON_HOST=""       # ← ЗАПОЛНИТЬ: публичный IP Monitoring-сервера
MON_USER="root"
MON_PATH="/opt/dds_monitoring"

# ─── Checks ──────────────────────────────────────────────────────────────────

if [ -z "$DATA_HOST" ] || [ -z "$MON_HOST" ]; then
    echo "❌ Заполни DATA_HOST и MON_HOST в скрипте!"
    exit 1
fi

echo "═══════════════════════════════════════════════════════════════"
echo "  DDS Migration: 1 server → 3 servers"
echo "═══════════════════════════════════════════════════════════════"
echo "  Current prod:  ${CURRENT_HOST}"
echo "  Data server:   ${DATA_HOST} (10.0.0.1)"
echo "  App server:    ${APP_HOST} (10.0.0.2)"
echo "  Mon server:    ${MON_HOST} (10.0.0.3)"
echo "═══════════════════════════════════════════════════════════════"
echo ""
read -p "Продолжить? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Отменено."
    exit 0
fi

# ─── Step 1: Backup current DB ──────────────────────────────────────────────

echo ""
echo "📦 Step 1: Backup текущей БД..."
BACKUP_FILE="pre-migration-$(date +%Y%m%d_%H%M%S).sql.gz"
ssh ${CURRENT_USER}@${CURRENT_HOST} \
    "cd ${CURRENT_PATH} && docker compose exec -T db pg_dump -U dds dds_db | gzip > /tmp/${BACKUP_FILE}"
echo "✅ Backup создан: /tmp/${BACKUP_FILE}"

# ─── Step 2: Copy project files to Data server ──────────────────────────────

echo ""
echo "📂 Step 2: Копирование файлов на Data-сервер..."
ssh ${DATA_USER}@${DATA_HOST} "mkdir -p ${DATA_PATH}/{backups,scripts}"
scp -r infra/data/docker-compose.yml ${DATA_USER}@${DATA_HOST}:${DATA_PATH}/docker-compose.yml
scp -r infra/data/postgresql.conf ${DATA_USER}@${DATA_HOST}:${DATA_PATH}/postgresql.conf
scp -r infra/data/.env.example ${DATA_USER}@${DATA_HOST}:${DATA_PATH}/.env.example
scp -r scripts/backup.sh ${DATA_USER}@${DATA_HOST}:${DATA_PATH}/scripts/backup.sh
echo "✅ Файлы скопированы"
echo "⚠️  Не забудь заполнить ${DATA_PATH}/.env на Data-сервере!"

# ─── Step 3: Start Data services ────────────────────────────────────────────

echo ""
echo "🚀 Step 3: Запуск Data-сервисов..."
ssh ${DATA_USER}@${DATA_HOST} "cd ${DATA_PATH} && docker compose up -d"
echo "⏳ Ждём запуска PostgreSQL (30 сек)..."
sleep 30

# ─── Step 4: Restore backup to new Data server ─────────────────────────────

echo ""
echo "🔄 Step 4: Перенос данных на новый Data-сервер..."
# Copy backup file
scp ${CURRENT_USER}@${CURRENT_HOST}:/tmp/${BACKUP_FILE} /tmp/${BACKUP_FILE}
scp /tmp/${BACKUP_FILE} ${DATA_USER}@${DATA_HOST}:/tmp/${BACKUP_FILE}
# Restore
ssh ${DATA_USER}@${DATA_HOST} "cd ${DATA_PATH} && gunzip -c /tmp/${BACKUP_FILE} | docker compose exec -T db psql -U dds dds_db"
echo "✅ Данные восстановлены"

# ─── Step 5: Verify VPN connectivity ───────────────────────────────────────

echo ""
echo "🔗 Step 5: Проверка VPN..."
ssh ${APP_USER}@${APP_HOST} "ping -c 3 10.0.0.1 && echo '✅ App → Data: OK' || echo '❌ App → Data: FAIL'"
ssh ${MON_USER}@${MON_HOST} "ping -c 3 10.0.0.1 && echo '✅ Mon → Data: OK' || echo '❌ Mon → Data: FAIL'"
ssh ${MON_USER}@${MON_HOST} "ping -c 3 10.0.0.2 && echo '✅ Mon → App: OK' || echo '❌ Mon → App: FAIL'"

# ─── Step 6: Switch App server to use Data server ──────────────────────────

echo ""
echo "🔄 Step 6: Переключение App-сервера на Data-сервер..."
ssh ${APP_USER}@${APP_HOST} "cd ${APP_PATH} && docker compose down"
# Copy App compose
scp docker-compose.app.yml ${APP_USER}@${APP_HOST}:${APP_PATH}/docker-compose.app.yml
ssh ${APP_USER}@${APP_HOST} "cd ${APP_PATH} && docker compose -f docker-compose.app.yml up -d --build"
echo "⏳ Ждём запуска App-сервисов (30 сек)..."
sleep 30

# ─── Step 7: Health check ───────────────────────────────────────────────────

echo ""
echo "🏥 Step 7: Health check..."
ssh ${APP_USER}@${APP_HOST} "curl -sf http://localhost/health" && echo "✅ Health OK" || echo "❌ Health FAILED"

# ─── Step 8: Start Monitoring ──────────────────────────────────────────────

echo ""
echo "📊 Step 8: Запуск Monitoring-сервисов..."
ssh ${MON_USER}@${MON_HOST} "mkdir -p ${MON_PATH}/grafana/{provisioning,dashboards}"
scp -r infra/monitoring/docker-compose.yml ${MON_USER}@${MON_HOST}:${MON_PATH}/docker-compose.yml
scp -r infra/monitoring/prometheus.yml ${MON_USER}@${MON_HOST}:${MON_PATH}/prometheus.yml
scp -r monitoring/alert_rules.yml ${MON_USER}@${MON_HOST}:${MON_PATH}/alert_rules.yml
scp -r monitoring/alertmanager.yml ${MON_USER}@${MON_HOST}:${MON_PATH}/alertmanager.yml
scp -r monitoring/grafana/provisioning/* ${MON_USER}@${MON_HOST}:${MON_PATH}/grafana/provisioning/
scp -r monitoring/grafana/dashboards/* ${MON_USER}@${MON_HOST}:${MON_PATH}/grafana/dashboards/ 2>/dev/null || true
ssh ${MON_USER}@${MON_HOST} "cd ${MON_PATH} && docker compose up -d"
echo "✅ Monitoring запущен"

# ─── Done ────────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ Миграция завершена!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Проверь:"
echo "    App:        http://${APP_HOST}/health"
echo "    Grafana:    http://${APP_HOST}/grafana/"
echo "    Data VPN:   ssh ${DATA_USER}@${DATA_HOST} 'docker stats --no-stream'"
echo "    Mon VPN:    ssh ${MON_USER}@${MON_HOST} 'docker stats --no-stream'"
echo ""
echo "  ⚠️  Следующие шаги:"
echo "    1. Обновить CI/CD (DEPLOY_HOST, DEPLOY_PATH)"
echo "    2. Обновить GitHub Secrets"
echo "    3. Удалить старые контейнеры data/monitoring с App-сервера"
echo "    4. Настроить SSL (make ssl-init)"
