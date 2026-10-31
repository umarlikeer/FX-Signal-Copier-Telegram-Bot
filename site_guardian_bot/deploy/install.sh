#!/usr/bin/env bash
# Site Guardian'ni VPS ga o'rnatish (Ubuntu/Debian). Ishlatish: sudo bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/site-guardian
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "root sifatida ishga tushiring: sudo bash $0"; exit 1; fi

apt-get update -y
apt-get install -y python3 python3-venv python3-pip

mkdir -p "$APP_DIR"
cp "$SRC_DIR"/*.py "$SRC_DIR"/requirements.txt "$APP_DIR"/
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$SRC_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo ">>> $APP_DIR/.env yaratildi — uni to'ldiring: nano $APP_DIR/.env"
fi

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

cp "$SRC_DIR/deploy/site-guardian.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable site-guardian

echo
echo "Tayyor. Keyingi qadamlar:"
echo "  1) nano $APP_DIR/.env          # tokenlar va sozlamalarni kiriting"
echo "  2) systemctl restart site-guardian"
echo "  3) journalctl -u site-guardian -f   # loglarni kuzatish"
