#!/usr/bin/env bash
# update_gcp_vm.sh — Pull latest code and restart service on GCP VM
set -euo pipefail

APP_DIR="${APP_DIR:-/home/${USER}/financial-news-ai-bridge}"
SERVICE="financial-news-ai-bridge"

echo "=== Updating Financial News AI Bridge ==="

[[ -d "${APP_DIR}/.git" ]] || { echo "ERROR: ${APP_DIR} is not a git repository."; exit 1; }
[[ -f "${APP_DIR}/.env" ]] || { echo "ERROR: .env missing — aborting to protect production."; exit 1; }

echo "[1/4] Backing up database..."
bash "${APP_DIR}/scripts/backup_database.sh"

echo "[2/4] Pulling latest code..."
git -C "${APP_DIR}" fetch origin
git -C "${APP_DIR}" reset --hard origin/main

echo "[3/4] Installing new dependencies..."
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

echo "[4/4] Restarting service..."
sudo systemctl restart "${SERVICE}"
sleep 5
sudo systemctl status "${SERVICE}" --no-pager | head -15

echo "=== Update complete ==="
echo "Logs: sudo journalctl -u ${SERVICE} -f"
