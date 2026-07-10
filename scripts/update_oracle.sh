#!/usr/bin/env bash
# update_oracle.sh — Pull latest code and restart the service on Oracle Cloud VM
# Safe to run — preserves .env and SQLite database.
# Usage: bash /opt/financial-news-ai-bridge/scripts/update_oracle.sh
set -euo pipefail

APP_DIR="/opt/financial-news-ai-bridge"

if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "ERROR: ${APP_DIR} is not a git repository."
    exit 1
fi

if [[ ! -f "${APP_DIR}/.env" ]]; then
    echo "ERROR: ${APP_DIR}/.env is missing. Aborting."
    exit 1
fi

echo "=== Updating Financial News AI Bridge on Oracle Cloud ==="

echo "[1/3] Pulling latest code from GitHub..."
git -C "${APP_DIR}" fetch origin
git -C "${APP_DIR}" reset --hard origin/main

echo "[2/3] Rebuilding and restarting container..."
docker compose -f "${APP_DIR}/docker-compose.yml" up -d --build

echo "[3/3] Verifying health..."
sleep 10
curl -sf http://localhost:8000/health && echo "" || echo "WARNING: Health check failed"

echo "=== Update complete ==="
echo "Logs: docker compose -f ${APP_DIR}/docker-compose.yml logs -f ai-bridge"
