#!/bin/bash
# WireGuard setup for APP server (10.0.0.2)
#
# Usage: bash setup-app.sh <DATA_PUBLIC_IP> <MONITORING_PUBLIC_IP>

set -e

DATA_IP="${1:?Usage: $0 <DATA_PUBLIC_IP> <MONITORING_PUBLIC_IP>}"
MON_IP="${2:?Usage: $0 <DATA_PUBLIC_IP> <MONITORING_PUBLIC_IP>}"

echo "📦 Installing WireGuard..."
apt-get update && apt-get install -y wireguard

echo "🔑 Generating keys..."
cd /etc/wireguard
umask 077
wg genkey | tee app_private.key | wg pubkey > app_public.key

PRIVATE_KEY=$(cat app_private.key)
PUBLIC_KEY=$(cat app_public.key)

cat > /etc/wireguard/wg0.conf << EOF
# APP server — WireGuard config
[Interface]
PrivateKey = ${PRIVATE_KEY}
Address = 10.0.0.2/24
ListenPort = 51820

# Data server
[Peer]
PublicKey = REPLACE_WITH_DATA_PUBLIC_KEY
AllowedIPs = 10.0.0.1/32
Endpoint = ${DATA_IP}:51820
PersistentKeepalive = 25

# Monitoring server
[Peer]
PublicKey = REPLACE_WITH_MON_PUBLIC_KEY
AllowedIPs = 10.0.0.3/32
Endpoint = ${MON_IP}:51820
PersistentKeepalive = 25
EOF

echo "✅ WireGuard config created at /etc/wireguard/wg0.conf"
echo ""
echo "📋 APP server public key (copy to other servers):"
echo "   ${PUBLIC_KEY}"
echo ""
echo "⚠️  Next steps:"
echo "   1. Replace REPLACE_WITH_DATA_PUBLIC_KEY and REPLACE_WITH_MON_PUBLIC_KEY"
echo "   2. Then: systemctl enable --now wg-quick@wg0"
echo ""
echo "🔥 Opening firewall port..."
ufw allow 51820/udp 2>/dev/null || iptables -A INPUT -p udp --dport 51820 -j ACCEPT
