#!/usr/bin/env bash
# Kodni GitHub'dan yangilab, botni qayta ishga tushiradi. .env ga tegmaydi.
# Ishlatish: sudo bash deploy/update.sh
set -euo pipefail
APP_DIR=/opt/site-guardian
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
git -C "$SRC_DIR" pull --ff-only
cp "$SRC_DIR"/*.py "$SRC_DIR"/requirements.txt "$APP_DIR"/
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
# Eski .env da CLAUDE_MODELS bo'lmasa — qo'shib qo'yamiz
grep -q '^CLAUDE_MODELS=' "$APP_DIR/.env" || \
  printf '\nCLAUDE_MODELS=claude-opus-5-5,claude-sonnet-5-5,claude-haiku-5-5\nMODEL_COOLDOWN=300\n' >> "$APP_DIR/.env"
systemctl restart site-guardian
echo "Yangilandi. Loglar: journalctl -u site-guardian -f"
