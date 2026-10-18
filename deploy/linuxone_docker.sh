#!/usr/bin/env bash
# Guardian Pay on IBM LinuxONE Community Cloud, using IBM's own AI image
# (registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x). That image
# brings scikit-learn, PyTorch with the Telum NNPA plugin, and IBM Granite on
# llama.cpp built with zDNN, so the models run on the mainframe's AI chip.
#
# Put this project at ~/shared/guardian-pay, then run:
#     bash ~/shared/guardian-pay/deploy/linuxone_docker.sh
set -euo pipefail

IMAGE="registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest"
SHARED="$HOME/shared"
APP="$SHARED/guardian-pay"
PORT="${PORT:-8000}"

if [ ! -d "$APP/guardian" ]; then
  echo "Put the project at $APP first (unzip guardian-pay.zip inside $SHARED)."
  exit 1
fi
if [ ! -f "$APP/.env" ]; then
  cp "$APP/.env.example" "$APP/.env"
  echo "Created $APP/.env. Put your watsonx values in it (nano $APP/.env), then run this again."
  echo "Without them the app still runs, using Granite inside LinuxONE and the built-in checks."
fi
chmod -R a+rwX "$SHARED"

if ! command -v docker >/dev/null 2>&1; then
  echo "== Installing Docker"
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sudo sh /tmp/get-docker.sh
fi
sudo systemctl enable --now docker

echo "== Pulling IBM's LinuxONE AI image (first time takes a few minutes)"
sudo docker pull "$IMAGE"

echo "== Opening ports: $PORT (app), 38888 (Jupyter), 80/443 (https)"
for p in "$PORT" 38888 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || sudo iptables -I INPUT -p tcp --dport "$p" -j ACCEPT
done
sudo mkdir -p /etc/iptables && sudo bash -c "iptables-save > /etc/iptables/rules.v4"

echo "== Starting Jupyter Lab (for the notebook and screenshots)"
if ! sudo docker ps -a --format '{{.Names}}' | grep -qx notebook; then
  sudo docker run --network host --name notebook --restart unless-stopped \
    -v "$SHARED":/home/ibm-user/shared -d "$IMAGE"
else
  sudo docker start notebook >/dev/null
fi

echo "== Starting Guardian Pay inside the same IBM image"
sudo docker rm -f guardian-pay >/dev/null 2>&1 || true
sudo docker run -d --name guardian-pay --network host --restart unless-stopped \
  -v "$SHARED":/home/ibm-user/shared --entrypoint bash "$IMAGE" -lc '
    set -e
    PY=$(command -v /opt/venv/bin/python || command -v python3)
    LIB=/home/ibm-user/shared/.pylib
    if [ ! -d "$LIB/fastapi" ]; then
      "$PY" -m pip install -q --target "$LIB" "fastapi>=0.110" "uvicorn>=0.29"
    fi
    export PYTHONPATH="$LIB"
    cd /home/ibm-user/shared/guardian-pay
    exec "$PY" -m uvicorn guardian.server:app --host 0.0.0.0 --port '"$PORT"'
  '

IP=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
HTTPS_URL=""
if [ "${HTTPS:-1}" = "1" ]; then
  HOST="$(echo "$IP" | tr . -).sslip.io"
  echo "== Free https at https://$HOST (phones only allow the microphone on https)"
  if sudo DEBIAN_FRONTEND=noninteractive apt-get install -y caddy >/dev/null 2>&1; then
    sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$HOST {
    reverse_proxy 127.0.0.1:$PORT
}
CADDY
    sudo systemctl restart caddy && HTTPS_URL="https://$HOST"
  else
    echo "   Caddy isn't available from apt here; the app still works over http."
  fi
fi

echo "== Waiting for the app"
for i in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    curl -s "http://127.0.0.1:$PORT/api/health"; echo; echo
    BASE="${HTTPS_URL:-http://$IP:$PORT}"
    echo "Guardian Pay is running on $(uname -m)."
    echo "  Console:        $BASE/"
    echo "  Customer app:   $BASE/pay"
    echo "  Family screen:  $BASE/guardian"
    echo "  Jupyter Lab:    http://$IP:38888  (token: Your_Token1)"
    [ -n "$HTTPS_URL" ] && echo "  (https can take a minute the first time; http://$IP:$PORT/ works too)"
    exit 0
  fi
  sleep 2
done
echo "The app did not start. Last log lines:"
sudo docker logs --tail 60 guardian-pay
exit 1
