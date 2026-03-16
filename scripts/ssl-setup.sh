#!/bin/bash
# ssl-setup.sh — Первичная настройка SSL для production
#
# Использование:
#   ssh root@130.49.150.69
#   cd /opt/dds_app
#   bash scripts/ssl-setup.sh
#
# Предусловия:
#   - Домен app.vyatkin-wb.ru указывает на IP сервера
#   - docker compose запущен (nginx слушает порт 80)
#   - email для Let's Encrypt уведомлений

set -e

DOMAIN="app.vyatkin-wb.ru"
EMAIL="admin@vyatkin-wb.ru"

echo "═══════════════════════════════════════════════════════"
echo "  🔒 SSL Setup for ${DOMAIN}"
echo "═══════════════════════════════════════════════════════"

# ─── Step 1: Проверка DNS ─────────────────────────────────────────────────────
echo ""
echo "1️⃣  Проверка DNS..."
RESOLVED_IP=$(dig +short ${DOMAIN} 2>/dev/null || echo "")
SERVER_IP=$(curl -sf ifconfig.me 2>/dev/null || echo "unknown")

if [ -z "$RESOLVED_IP" ]; then
    echo "❌ DNS не разрешился для ${DOMAIN}"
    echo "   Проверьте A-запись в DNS"
    exit 1
fi

echo "   DNS: ${DOMAIN} → ${RESOLVED_IP}"
echo "   Server IP: ${SERVER_IP}"

if [ "$RESOLVED_IP" != "$SERVER_IP" ]; then
    echo "⚠️  IP не совпадает! DNS указывает на ${RESOLVED_IP}, сервер: ${SERVER_IP}"
    read -p "   Продолжить? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
fi
echo "   ✅ DNS OK"

# ─── Step 2: Проверка HTTP ────────────────────────────────────────────────────
echo ""
echo "2️⃣  Проверка HTTP доступности..."
if curl -sf http://${DOMAIN}/health > /dev/null 2>&1; then
    echo "   ✅ HTTP работает"
else
    echo "   ⚠️  HTTP не отвечает на /health, но продолжаем..."
fi

# ─── Step 3: Получение сертификата ────────────────────────────────────────────
echo ""
echo "3️⃣  Получение SSL-сертификата через certbot..."
docker compose run --rm certbot certonly \
    --webroot \
    -w /var/www/certbot \
    -d ${DOMAIN} \
    --email ${EMAIL} \
    --agree-tos \
    --no-eff-email \
    --force-renewal

echo "   ✅ Сертификат получен!"

# ─── Step 4: Проверка сертификата ─────────────────────────────────────────────
echo ""
echo "4️⃣  Проверка сертификата..."
docker compose run --rm certbot certificates

# ─── Step 5: Переключение nginx на SSL ────────────────────────────────────────
echo ""
echo "5️⃣  Переключение nginx на SSL-конфиг..."

# Бэкап текущего конфига
cp nginx/nginx.conf nginx/nginx-http-backup.conf

# Замена конфига (docker-compose монтирует nginx.conf → default.conf)
cp nginx/nginx-ssl.conf nginx/nginx.conf

echo "   ✅ nginx.conf заменён на SSL-версию"

# ─── Step 6: Перезапуск nginx ─────────────────────────────────────────────────
echo ""
echo "6️⃣  Перезапуск nginx..."
docker compose restart nginx

echo "   ⏳ Ожидание 5 сек..."
sleep 5

# ─── Step 7: Проверка HTTPS ──────────────────────────────────────────────────
echo ""
echo "7️⃣  Проверка HTTPS..."
if curl -sf https://${DOMAIN}/health > /dev/null 2>&1; then
    echo "   ✅ HTTPS работает!"
else
    echo "   ⚠️  HTTPS ещё не отвечает (может понадобиться пару секунд)"
    sleep 5
    if curl -sf https://${DOMAIN}/health > /dev/null 2>&1; then
        echo "   ✅ HTTPS работает (после повторной проверки)!"
    else
        echo "   ❌ HTTPS не работает. Проверьте логи: docker compose logs nginx"
        echo "   🔄 Откат на HTTP..."
        cp nginx/nginx-http-backup.conf nginx/nginx.conf
        docker compose restart nginx
        echo "   ✅ Откачено на HTTP"
        exit 1
    fi
fi

# ─── Step 8: Проверка редиректа ──────────────────────────────────────────────
echo ""
echo "8️⃣  Проверка HTTP → HTTPS редиректа..."
HTTP_CODE=$(curl -so /dev/null -w '%{http_code}' http://${DOMAIN}/ 2>/dev/null || echo "000")
if [ "$HTTP_CODE" == "301" ]; then
    echo "   ✅ HTTP → HTTPS редирект (301)"
else
    echo "   ⚠️  HTTP вернул ${HTTP_CODE} (ожидали 301)"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✅ SSL настроен для ${DOMAIN}!"
echo ""
echo "  🔗 https://${DOMAIN}"
echo "  📋 Авто-обновление: certbot контейнер (каждые 12ч)"
echo "  📋 Ручное обновление: make ssl-renew"
echo "  📋 Статус сертификата: make ssl-status"
echo "═══════════════════════════════════════════════════════"
