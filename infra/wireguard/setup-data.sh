#!/bin/bash
# WireGuard setup for DATA server (10.0.0.1)
#
# Usage: bash setup-data.sh <APP_PUBLIC_IP> <MONITORING_PUBLIC_IP>
#
# Run this FIRST — it generates keys and outputs the public key
# for the other servers.

set -e

APP_IP="${1:?Usage: $0 <APP_PUBLIC_IP> <MONITORING_PUBLIC_IP>}"
MON_IP="${2:?Usage: $0 <APP_PUBLIC_IP> <MONITORING_PUBLIC_IP>}"

echo "📦 Installing WireGuard..."
apt-get update && apt-get install -y wireguard

echo "🔑 Generating keys..."
cd /etc/wireguard
umask 077
wg genkey | tee data_private.key | wg pubkey > data_public.key

PRIVATE_KEY=$(cat data_private.key)
PUBLIC_KEY=$(cat data_public.key)

cat > /etc/wireguard/wg0.conf << EOF
# DATA server — WireGuard config
[Interface]
PrivateKey = ${PRIVATE_KEY}
Address = 10.0.0.1/24
ListenPort = 51820

# App server
[Peer]
PublicKey = REPLACE_WITH_APP_PUBLIC_KEY
AllowedIPs = 10.0.0.2/32
Endpoint = ${APP_IP}:51820
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
echo "📋 DATA server public key (copy to other servers):"
echo "   ${PUBLIC_KEY}"
echo ""
echo "⚠️  Next steps:"
echo "   1. Run setup-app.sh on App server"
echo "   2. Run setup-monitoring.sh on Monitoring server"
echo "   3. Replace REPLACE_WITH_APP_PUBLIC_KEY and REPLACE_WITH_MON_PUBLIC_KEY in this config"
echo "   4. Then: systemctl enable --now wg-quick@wg0"
echo ""
echo "🔥 Opening firewall port..."
ufw allow 51820/udp 2>/dev/null || iptables -A INPUT -p udp --dport 51820 -j ACCEPT
