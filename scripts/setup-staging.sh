#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# Скрипт настройки нового Staging-сервера для DDS
# Запускать на ЧИСТОМ VPS (Ubuntu 22.04+): curl ... | bash
# ──────────────────────────────────────────────────────────────────────────────
set -e

echo "🚀 Настройка Staging-сервера для DDS..."

# ─── 1. Обновить систему ─────────────────────────────────────────────────────
echo "📦 Обновление пакетов..."
apt-get update -qq && apt-get upgrade -y -qq

# ─── 2. Установить Docker ────────────────────────────────────────────────────
if ! command -v docker &> /dev/null; then
    echo "🐳 Установка Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
else
    echo "✅ Docker уже установлен"
fi

# ─── 3. Установить Docker Compose (v2) ──────────────────────────────────────
if ! docker compose version &> /dev/null; then
    echo "🐳 Установка Docker Compose plugin..."
    apt-get install -y docker-compose-plugin
else
    echo "✅ Docker Compose уже установлен"
fi

# ─── 4. Создать директорию проекта ───────────────────────────────────────────
PROJECT_DIR="/opt/dds_staging"
if [ ! -d "$PROJECT_DIR" ]; then
    echo "📁 Создание $PROJECT_DIR..."
    mkdir -p "$PROJECT_DIR"
else
    echo "✅ $PROJECT_DIR уже существует"
fi

# ─── 5. Настроить firewall ───────────────────────────────────────────────────
echo "🔒 Настройка firewall..."
apt-get install -y -qq ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP (staging)
ufw allow 443/tcp   # HTTPS (будущее)
ufw --force enable

# ─── 6. Создать swap (если мало RAM) ─────────────────────────────────────────
if [ ! -f /swapfile ]; then
    echo "💾 Создание 2GB swap..."
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
    echo "✅ Swap уже существует"
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "✅ Сервер готов!"
echo ""
echo "Следующие шаги:"
echo "  1. Клонируйте репозиторий:"
echo "     cd $PROJECT_DIR && git clone https://github.com/vladbydaev37/dds2.git ."
echo ""
echo "  2. Создайте .env из шаблона:"
echo "     cp .env.staging .env"
echo ""
echo "  3. Запустите staging:"
echo "     docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d --build"
echo ""
echo "  4. Добавьте SSH ключ GitHub Actions:"
echo "     В GitHub repo → Settings → Secrets → добавьте:"
echo "     - STAGING_HOST = $(hostname -I | awk '{print $1}')"
echo "     - STAGING_USER = root"
echo "     - STAGING_SSH_KEY = (ваш ssh private key)"
echo "     - STAGING_PATH = $PROJECT_DIR"
echo "════════════════════════════════════════════════════════════════"
