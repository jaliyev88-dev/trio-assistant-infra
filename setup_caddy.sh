#!/bin/bash
set -e
cp /root/trio-assistant-infra/Caddyfile /var/snap/caddy/common/Caddyfile
/snap/bin/caddy adapt --config /var/snap/caddy/common/Caddyfile --adapter caddyfile > /var/snap/caddy/common/caddy.json
snap restart caddy
echo "Caddy JSON regenerated and service restarted."
