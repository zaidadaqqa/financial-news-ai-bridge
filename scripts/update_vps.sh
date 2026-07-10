#!/usr/bin/env bash
# update_vps.sh — Pull latest code from GitHub and restart the service
# Safe to run — preserves .env and the SQLite database.
# Usage: bash scripts/update_vps.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/home/zaid/financial-news-ai-bridge}"
SERVICE="financial-news-ai-bridge"

echo "=== Updating Financial News AI Bridge ==="

if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "ERROR: ${APP_DIR} is not a git repository."
    exit 1
fi

if [[ ! -f "${APP_DIR}/.env" ]]; then
    echo "ERROR: ${APP_DIR}/.env is missing. Aborting to protect production."
    exit 1
fi

echo "[1/4] Backing up database before update..."
bash "${APP_DIR}/scripts/backup_database.sh"

echo "[2/4] Pulling latest code..."
git -C "${APP_DIR}" fetch origin
git -C "${APP_DIR}" reset --hard origin/main

echo "[3/4] Installing any new dependencies..."
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

echo "[4/4] Restarting service..."
systemctl --user restart "${SERVICE}"
sleep 5
systemctl --user status "${SERVICE}" --no-pager | head -15

echo "=== Update complete ==="
echo "Logs: journalctl --user -u ${SERVICE} -f"
