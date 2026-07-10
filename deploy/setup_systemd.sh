#!/usr/bin/env bash
# setup_systemd.sh — Install and enable the systemd service (Docker-free option)
# Run as root on the VPS.
set -euo pipefail

APP_DIR="/opt/financial-news-ai-bridge"
APP_USER="finbridge"
SERVICE_NAME="financial-news-ai-bridge"
REPO_URL="https://github.com/zaidadaqqa/financial-news-ai-bridge.git"

echo "=== Financial News AI Bridge — systemd Setup ==="

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: Run as root: sudo bash setup_systemd.sh"
    exit 1
fi

# Install Python and dependencies
apt-get update -qq
apt-get install -y --no-install-recommends python3.12 python3.12-venv python3-pip git curl sqlite3

# Create non-root user
if ! id "${APP_USER}" &>/dev/null; then
    useradd --system --home-dir "${APP_DIR}" --shell /bin/false "${APP_USER}"
fi

# Clone or update
if [[ -d "${APP_DIR}/.git" ]]; then
    git -C "${APP_DIR}" fetch origin && git -C "${APP_DIR}" reset --hard origin/main
else
    git clone "${REPO_URL}" "${APP_DIR}"
fi

# Check .env
if [[ ! -f "${APP_DIR}/.env" ]]; then
    echo "ERROR: ${APP_DIR}/.env missing. Create it before proceeding."
    echo "  cp /path/to/your/.env ${APP_DIR}/.env"
    echo "  chmod 600 ${APP_DIR}/.env"
    exit 1
fi
chmod 600 "${APP_DIR}/.env"
chown root:root "${APP_DIR}/.env"

# Create venv
echo "Setting up Python virtual environment..."
python3.12 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip -q
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

# Create data directory
mkdir -p "${APP_DIR}/data"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/data"
chmod 750 "${APP_DIR}/data"

# Run migrations
echo "Running database migrations..."
cd "${APP_DIR}"
"${APP_DIR}/.venv/bin/python" -m alembic upgrade head

# Install and start systemd service
cp "${APP_DIR}/deploy/financial-news-ai-bridge.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

sleep 3
systemctl status "${SERVICE_NAME}" --no-pager || true

echo ""
echo "=== systemd Setup Complete ==="
echo "  Status:   systemctl status ${SERVICE_NAME}"
echo "  Logs:     journalctl -u ${SERVICE_NAME} -f"
echo "  Restart:  systemctl restart ${SERVICE_NAME}"
echo "  Stop:     systemctl stop ${SERVICE_NAME}"
