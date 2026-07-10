#!/usr/bin/env bash
# deploy_vps.sh — Initial VPS deployment for Financial News AI Bridge
# Safe to re-run. Does NOT overwrite .env or production database.
set -euo pipefail

APP_DIR="/opt/financial-news-ai-bridge"
APP_USER="finbridge"
REPO_URL="https://github.com/zaidadaqqa/financial-news-ai-bridge.git"
PYTHON_VERSION="python3.12"
COMPOSE_BIN="docker compose"

echo "=== Financial News AI Bridge — VPS Deployment ==="

# ── 1. Check prerequisites ────────────────────────────────────────────────────
if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: Run this script as root: sudo bash deploy_vps.sh"
    exit 1
fi

# ── 2. Install system packages ────────────────────────────────────────────────
echo "[1/8] Updating system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    git curl ca-certificates gnupg lsb-release ufw sqlite3

# ── 3. Install Docker ─────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "[2/8] Installing Docker..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable docker
    systemctl start docker
else
    echo "[2/8] Docker already installed."
fi

# ── 4. Create app user ────────────────────────────────────────────────────────
echo "[3/8] Setting up application user..."
if ! id "${APP_USER}" &>/dev/null; then
    useradd --system --no-create-home --shell /bin/false "${APP_USER}"
fi
usermod -aG docker "${APP_USER}" 2>/dev/null || true

# ── 5. Clone or update repository ────────────────────────────────────────────
echo "[4/8] Deploying application code..."
if [[ -d "${APP_DIR}/.git" ]]; then
    echo "  Updating existing repository..."
    git -C "${APP_DIR}" fetch origin
    git -C "${APP_DIR}" reset --hard origin/main
else
    echo "  Cloning repository..."
    git clone "${REPO_URL}" "${APP_DIR}"
fi

# ── 6. Protect .env and database ─────────────────────────────────────────────
echo "[5/8] Checking environment configuration..."
if [[ ! -f "${APP_DIR}/.env" ]]; then
    echo "ERROR: ${APP_DIR}/.env does not exist."
    echo "       Copy your .env file to the server before running this script:"
    echo "       scp .env user@your-server:${APP_DIR}/.env"
    exit 1
fi
chmod 600 "${APP_DIR}/.env"
chown root:root "${APP_DIR}/.env"

# ── 7. Create persistent data directory ──────────────────────────────────────
echo "[6/8] Setting up data directory..."
mkdir -p "${APP_DIR}/data"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/data"
chmod 750 "${APP_DIR}/data"

# ── 8. Build and start with Docker Compose ────────────────────────────────────
echo "[7/8] Building and starting service..."
cd "${APP_DIR}"
${COMPOSE_BIN} pull 2>/dev/null || true
${COMPOSE_BIN} up -d --build

# ── 9. Configure firewall ────────────────────────────────────────────────────
echo "[8/8] Configuring firewall..."
ufw allow OpenSSH 2>/dev/null || true
# Only allow port 8000 if you need the health API externally
# ufw allow 8000/tcp
ufw --force enable 2>/dev/null || true

echo ""
echo "=== Deployment Complete ==="
echo "  Logs:    docker compose -C ${APP_DIR} logs -f ai-bridge"
echo "  Status:  docker compose -C ${APP_DIR} ps"
echo "  Health:  curl http://localhost:8000/health"
echo "  Stop:    docker compose -C ${APP_DIR} down"
