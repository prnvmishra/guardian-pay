#!/usr/bin/env bash
# One-time setup on an IBM LinuxONE Community Cloud Ubuntu server (s390x).
# Run from inside the project folder:  bash deploy/linuxone_setup.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8000}"

echo "== Guardian Pay setup on $(uname -m) in $APP_DIR"

echo "== Installing Python packages from Ubuntu (numpy has no s390x wheel on PyPI)"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip python3-numpy iptables-persistent

echo "== Installing the web server libraries"
pip3 install --user --break-system-packages "fastapi>=0.110" "uvicorn>=0.29" 2>/dev/null \
  || pip3 install --user "fastapi>=0.110" "uvicorn>=0.29"

echo "== Opening port $PORT (LinuxONE Community Cloud only opens SSH by default)"
if ! sudo iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT -p tcp --dport "$PORT" -j ACCEPT
fi
sudo mkdir -p /etc/iptables
sudo bash -c "iptables-save > /etc/iptables/rules.v4"

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "   Created .env from .env.example; add your watsonx values to it."
fi

echo "== Installing a service so the app keeps running after you log out"
sudo tee /etc/systemd/system/guardian-pay.service >/dev/null <<UNIT
[Unit]
Description=Guardian Pay
After=network-online.target

[Service]
User=$(whoami)
WorkingDirectory=$APP_DIR
EnvironmentFile=-$APP_DIR/.env
ExecStart=/usr/bin/python3 -m uvicorn guardian.server:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now guardian-pay
sudo systemctl restart guardian-pay

IP=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
HTTPS_URL=""
if [ "${HTTPS:-1}" = "1" ]; then
  # Phones only allow the microphone on https pages, so put Caddy in front
  # with a free certificate for <ip>.sslip.io (a name that points at this IP).
  HOST="$(echo "$IP" | tr . -).sslip.io"
  echo "== Setting up https://$HOST (needed for the microphone on phones)"
  if sudo DEBIAN_FRONTEND=noninteractive apt-get install -y caddy >/dev/null 2>&1; then
    for p in 80 443; do
      sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || sudo iptables -I INPUT -p tcp --dport "$p" -j ACCEPT
    done
    sudo bash -c "iptables-save > /etc/iptables/rules.v4"
    sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$HOST {
    reverse_proxy 127.0.0.1:$PORT
}
CADDY
    sudo systemctl restart caddy && HTTPS_URL="https://$HOST"
  else
    echo "   Caddy is not available from apt here; the app still works over http."
  fi
fi

echo "== Checking the app"
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    curl -s "http://127.0.0.1:$PORT/api/health"; echo
    BASE="${HTTPS_URL:-http://$IP:$PORT}"
    echo
    echo "Guardian Pay is running on $(uname -m)."
    echo "Console:        $BASE/"
    echo "Customer app:   $BASE/pay"
    echo "Family screen:  $BASE/guardian"
    [ -n "$HTTPS_URL" ] && echo "(The https certificate can take a minute the first time. Plain http also works: http://$IP:$PORT/)"
    exit 0
  fi
  sleep 1
done
echo "The app did not start. Last log lines:"
sudo journalctl -u guardian-pay -n 40 --no-pager
exit 1
