#!/bin/bash
set -e
/snap/bin/caddy adapt --config /root/trio-assistant-infra/Caddyfile --adapter caddyfile > /var/snap/caddy/common/caddy.json
snap restart caddy
echo "Caddy JSON regenerated and service restarted."
