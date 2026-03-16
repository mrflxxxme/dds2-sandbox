#!/bin/bash
# WireGuard setup for MONITORING server (10.0.0.3)
#
# Usage: bash setup-monitoring.sh <DATA_PUBLIC_IP> <APP_PUBLIC_IP>

set -e

DATA_IP="${1:?Usage: $0 <DATA_PUBLIC_IP> <APP_PUBLIC_IP>}"
APP_IP="${2:?Usage: $0 <DATA_PUBLIC_IP> <APP_PUBLIC_IP>}"

echo "📦 Installing WireGuard..."
apt-get update && apt-get install -y wireguard

echo "🔑 Generating keys..."
cd /etc/wireguard
umask 077
wg genkey | tee mon_private.key | wg pubkey > mon_public.key

PRIVATE_KEY=$(cat mon_private.key)
PUBLIC_KEY=$(cat mon_public.key)

cat > /etc/wireguard/wg0.conf << EOF
# MONITORING server — WireGuard config
[Interface]
PrivateKey = ${PRIVATE_KEY}
Address = 10.0.0.3/24
ListenPort = 51820

# Data server
[Peer]
PublicKey = REPLACE_WITH_DATA_PUBLIC_KEY
AllowedIPs = 10.0.0.1/32
Endpoint = ${DATA_IP}:51820
PersistentKeepalive = 25

# App server
[Peer]
PublicKey = REPLACE_WITH_APP_PUBLIC_KEY
AllowedIPs = 10.0.0.2/32
Endpoint = ${APP_IP}:51820
PersistentKeepalive = 25
EOF

echo "✅ WireGuard config created at /etc/wireguard/wg0.conf"
echo ""
echo "📋 MONITORING server public key (copy to other servers):"
echo "   ${PUBLIC_KEY}"
echo ""
echo "⚠️  Next steps:"
echo "   1. Replace REPLACE_WITH_DATA_PUBLIC_KEY and REPLACE_WITH_APP_PUBLIC_KEY"
echo "   2. Then: systemctl enable --now wg-quick@wg0"
echo ""
echo "🔥 Opening firewall port..."
ufw allow 51820/udp 2>/dev/null || iptables -A INPUT -p udp --dport 51820 -j ACCEPT
