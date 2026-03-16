#!/bin/bash
# ─── SSL Setup for app.vyatkin-wb.ru ─────────────────────────────────────────
# Run on production server: bash scripts/setup-ssl.sh
set -e

DOMAIN="app.vyatkin-wb.ru"
EMAIL="admin@vyatkin-wb.ru"
SSL_DIR="./nginx/ssl"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT_DIR"

echo "🔒 Setting up SSL for $DOMAIN"
echo ""

# ── Step 1: Check DNS ────────────────────────────────────────────────────────
echo "1️⃣  Checking DNS..."
IP=$(dig +short "$DOMAIN" 2>/dev/null || host "$DOMAIN" 2>/dev/null | awk '{print $NF}')
if [ -z "$IP" ] || [ "$IP" = "NXDOMAIN" ]; then
    echo "❌ DNS for $DOMAIN not resolved yet. Wait 15-30 min and retry."
    exit 1
fi
echo "   ✅ $DOMAIN → $IP"
echo ""

# ── Step 2: Install certbot ─────────────────────────────────────────────────
echo "2️⃣  Installing certbot..."
if ! command -v certbot &>/dev/null; then
    apt-get update -qq && apt-get install -y -qq certbot
fi
echo "   ✅ certbot installed"
echo ""

# ── Step 3: Temporarily switch nginx to HTTP-only for ACME challenge ─────────
echo "3️⃣  Preparing nginx for ACME challenge..."

# Create temp nginx config that serves ACME challenge on port 80
cat > /tmp/nginx-acme.conf << 'NGINX'
server {
    listen 80;
    server_name app.vyatkin-wb.ru;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 200 'OK';
        add_header Content-Type text/plain;
    }
}
NGINX

# Stop current nginx, start temp one
docker compose stop nginx 2>/dev/null || true

# Create certbot webroot
mkdir -p /var/www/certbot

# Run temp nginx with certbot support
docker run -d --name nginx-certbot \
    -p 80:80 \
    -v /tmp/nginx-acme.conf:/etc/nginx/conf.d/default.conf:ro \
    -v /var/www/certbot:/var/www/certbot \
    nginx:alpine

sleep 2
echo "   ✅ Temp nginx running for ACME challenge"
echo ""

# ── Step 4: Get certificate ──────────────────────────────────────────────────
echo "4️⃣  Getting SSL certificate from Let's Encrypt..."
certbot certonly \
    --webroot \
    -w /var/www/certbot \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --non-interactive

echo "   ✅ Certificate obtained!"
echo ""

# ── Step 5: Stop temp nginx ─────────────────────────────────────────────────
echo "5️⃣  Cleaning up temp nginx..."
docker stop nginx-certbot && docker rm nginx-certbot
echo "   ✅ Cleaned up"
echo ""

# ── Step 6: Copy certs to project ───────────────────────────────────────────
echo "6️⃣  Copying certificates..."
mkdir -p "$SSL_DIR"
cp /etc/letsencrypt/live/"$DOMAIN"/fullchain.pem "$SSL_DIR"/fullchain.pem
cp /etc/letsencrypt/live/"$DOMAIN"/privkey.pem "$SSL_DIR"/privkey.pem
chmod 600 "$SSL_DIR"/privkey.pem
echo "   ✅ Certificates copied to $SSL_DIR/"
echo ""

# ── Step 7: Restart with SSL ────────────────────────────────────────────────
echo "7️⃣  Starting services with SSL..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build nginx
sleep 5

# ── Step 8: Verify ──────────────────────────────────────────────────────────
echo "8️⃣  Verifying..."
if curl -sI "https://$DOMAIN/health" 2>/dev/null | head -1 | grep -q "200"; then
    echo ""
    echo "🎉 SSL READY!"
    echo "   https://$DOMAIN → ✅ works!"
else
    echo ""
    echo "⚠️  HTTPS check failed. Checking HTTP redirect..."
    curl -sI "http://$DOMAIN" 2>/dev/null | head -3
    echo ""
    echo "Check: docker compose logs nginx --tail=20"
fi

echo ""
echo "📋 Next steps:"
echo "   - Add crontab for auto-renewal: 0 3 * * 1 certbot renew --quiet && cp /etc/letsencrypt/live/$DOMAIN/*.pem $SSL_DIR/ && docker compose restart nginx"
