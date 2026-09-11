#!/bin/bash
set -e

TENANT="$1"
PORT="$2"

if [ -z "$TENANT" ] || [ -z "$PORT" ]; then
  echo "Usage: create_tenant.sh <tenant_name> <port>"
  exit 1
fi

REPO_DIR="/root/trio-assistant-infra"
SRV_DIR="/srv/$TENANT"

echo "Creating user $TENANT..."
id -u "$TENANT" >/dev/null 2>&1 || useradd -r -m -d "$SRV_DIR" -s /usr/sbin/nologin "$TENANT"

echo "Creating directories..."
mkdir -p "$SRV_DIR/app/tools" "$SRV_DIR/data" "$SRV_DIR/db" "$SRV_DIR/logs"

echo "Copying template app..."
cp "$REPO_DIR/template_app/server.py" "$SRV_DIR/app/server.py"
cp "$REPO_DIR/template_app/requirements.txt" "$SRV_DIR/app/requirements.txt"

echo "Creating venv..."
python3 -m venv "$SRV_DIR/venv"
"$SRV_DIR/venv/bin/pip" install --upgrade pip -q
"$SRV_DIR/venv/bin/pip" install -r "$SRV_DIR/app/requirements.txt" -q

echo "Generating token and .env..."
TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(24))")
cat > "$SRV_DIR/.env" <<EOF
TENANT_NAME=$TENANT
MCP_PORT=$PORT
MCP_TOKEN=$TOKEN
DATA_ROOT=$SRV_DIR/data
EOF
chmod 600 "$SRV_DIR/.env"

echo "Installing systemd service..."
sed "s/__TENANT__/$TENANT/g" "$REPO_DIR/tenant.service.template" > "/etc/systemd/system/${TENANT}-mcp.service"

echo "Setting ownership..."
chown -R "$TENANT:$TENANT" "$SRV_DIR"
chmod 700 "$SRV_DIR"

echo "Starting service..."
systemctl daemon-reload
systemctl enable "${TENANT}-mcp.service"
systemctl restart "${TENANT}-mcp.service"

sleep 1
systemctl status "${TENANT}-mcp.service" --no-pager || true

echo ""
echo "=== DONE ==="
echo "Tenant: $TENANT"
echo "Port: $PORT"
echo "Token: $TOKEN"
