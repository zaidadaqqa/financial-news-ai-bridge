#!/usr/bin/env bash
# update_vps.sh — Pull latest code and restart the service
# Safe to run without losing data or .env
set -euo pipefail

APP_DIR="/opt/financial-news-ai-bridge"
COMPOSE_BIN="docker compose"

echo "=== Updating Financial News AI Bridge ==="

if [[ ! -d "${APP_DIR}/.git" ]]; then
    echo "ERROR: ${APP_DIR} is not a git repository. Run deploy_vps.sh first."
    exit 1
fi

if [[ ! -f "${APP_DIR}/.env" ]]; then
    echo "ERROR: ${APP_DIR}/.env is missing. Aborting to protect production."
    exit 1
fi

echo "[1/3] Pulling latest code..."
git -C "${APP_DIR}" fetch origin
git -C "${APP_DIR}" reset --hard origin/main

echo "[2/3] Rebuilding and restarting service..."
cd "${APP_DIR}"
${COMPOSE_BIN} up -d --build

echo "[3/3] Showing recent logs..."
sleep 3
${COMPOSE_BIN} logs --tail=30 ai-bridge

echo "=== Update complete ==="
