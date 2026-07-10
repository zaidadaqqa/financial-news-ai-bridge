#!/usr/bin/env bash
# setup_gcp_vm.sh — Bootstrap Financial News AI Bridge on a fresh GCP e2-micro VM
#
# Run this script once after SSH-ing into the new VM:
#   bash setup_gcp_vm.sh
#
# Tested on: Debian 12 (Bookworm), Ubuntu 22.04, Ubuntu 24.04
# Requires:  sudo access, outbound internet, GitHub repo set to public or SSH key added
set -euo pipefail

REPO_URL="https://github.com/zaidadaqqa/financial-news-ai-bridge.git"
APP_USER="${USER}"
APP_DIR="/home/${APP_USER}/financial-news-ai-bridge"
SERVICE="financial-news-ai-bridge"

echo "=================================================================="
echo "  Financial News AI Bridge — GCP VM Setup"
echo "  User: ${APP_USER}  |  Dir: ${APP_DIR}"
echo "=================================================================="

# ── 1. System packages ────────────────────────────────────────────────
echo ""
echo "[1/7] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    git curl wget software-properties-common ca-certificates

# Python 3.12 — use deadsnakes PPA on Ubuntu, backports on Debian
if python3.12 --version &>/dev/null 2>&1; then
    echo "       Python 3.12 already installed."
else
    if grep -qi debian /etc/os-release; then
        echo "       Installing Python 3.12 from debian backports..."
        sudo apt-get install -y --no-install-recommends python3.12 python3.12-venv python3.12-dev
    else
        # Ubuntu
        sudo add-apt-repository -y ppa:deadsnakes/ppa
        sudo apt-get update -qq
        sudo apt-get install -y --no-install-recommends python3.12 python3.12-venv python3.12-dev
    fi
fi

# ── 2. Clone repository ───────────────────────────────────────────────
echo ""
echo "[2/7] Cloning repository..."
if [[ -d "${APP_DIR}/.git" ]]; then
    echo "       Repo already cloned — pulling latest..."
    git -C "${APP_DIR}" fetch origin
    git -C "${APP_DIR}" reset --hard origin/main
else
    git clone "${REPO_URL}" "${APP_DIR}"
fi

cd "${APP_DIR}"

# ── 3. Python virtual environment ─────────────────────────────────────
echo ""
echo "[3/7] Creating virtual environment..."
python3.12 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
echo "       Dependencies installed."

# ── 4. Data directory ─────────────────────────────────────────────────
echo ""
echo "[4/7] Creating data directory..."
mkdir -p "${APP_DIR}/data"

# ── 5. Environment file ───────────────────────────────────────────────
echo ""
echo "[5/7] Configuring environment variables..."
if [[ -f "${APP_DIR}/.env" ]]; then
    echo "       .env already exists — skipping (edit manually if needed)."
else
    echo ""
    echo "  Enter your credentials below. Input is hidden for secrets."
    echo ""

    read -r -p "  TELEGRAM_BOT_TOKEN: " -s TELEGRAM_BOT_TOKEN; echo
    read -r -p "  TELEGRAM_CHAT_ID (e.g. @mychannel or -100xxx): " TELEGRAM_CHAT_ID
    read -r -p "  TELEGRAM_THREAD_ID (leave blank if none): " TELEGRAM_THREAD_ID
    read -r -p "  AI_API_KEY (OpenAI key): " -s AI_API_KEY; echo
    read -r -p "  AI_MODEL [gpt-4o-mini]: " AI_MODEL
    AI_MODEL="${AI_MODEL:-gpt-4o-mini}"

    cat > "${APP_DIR}/.env" << ENVEOF
# FinancialJuice RSS
FJ_RSS_URL=https://www.financialjuice.com/feed.ashx?xy=rss
RSS_POLL_INTERVAL=30

# Telegram
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
TELEGRAM_THREAD_ID=${TELEGRAM_THREAD_ID}

# AI
AI_PROVIDER=openai
AI_MODEL=${AI_MODEL}
AI_API_KEY=${AI_API_KEY}
AI_BASE_URL=

# Database — SQLite, persisted to disk
DATABASE_URL=sqlite+aiosqlite:///data/news.db

# Application
APP_ENV=production
LOG_LEVEL=INFO
TIMEZONE=UTC

# Features
ENABLE_TRANSLATION=true
ENABLE_AI_CACHE=true
ENABLE_MARKET_IMPACT=true
ENABLE_DUPLICATE_DETECTION=true
ENVEOF

    chmod 600 "${APP_DIR}/.env"
    echo "       .env written (permissions: 600)."
fi

# ── 6. systemd system service ─────────────────────────────────────────
echo ""
echo "[6/7] Installing systemd service..."

SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
VENV_PYTHON="${APP_DIR}/.venv/bin/python3"

sudo tee "${SERVICE_FILE}" > /dev/null << UNITEOF
[Unit]
Description=Financial News AI Bridge — FinancialJuice RSS to Telegram
Documentation=https://github.com/zaidadaqqa/financial-news-ai-bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_PYTHON} -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info
Restart=always
RestartSec=10
StartLimitIntervalSec=120
StartLimitBurst=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE}
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNITEOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE}"
sudo systemctl start "${SERVICE}"

echo "       Service installed and started."

# ── 7. Verify ─────────────────────────────────────────────────────────
echo ""
echo "[7/7] Verifying deployment..."
sleep 8

if sudo systemctl is-active --quiet "${SERVICE}"; then
    echo "       Service is RUNNING."
else
    echo "ERROR: Service failed to start. Check logs:"
    echo "       sudo journalctl -u ${SERVICE} -n 50 --no-pager"
    exit 1
fi

# Health check
HEALTH=$(curl -sf http://127.0.0.1:8000/health 2>/dev/null || echo "FAILED")
echo "       Health: ${HEALTH}"

echo ""
echo "=================================================================="
echo "  Deployment complete."
echo ""
echo "  Useful commands:"
echo "    Status:  sudo systemctl status ${SERVICE}"
echo "    Logs:    sudo journalctl -u ${SERVICE} -f"
echo "    Restart: sudo systemctl restart ${SERVICE}"
echo "    Stop:    sudo systemctl stop ${SERVICE}"
echo ""
echo "  Update (pull latest code + restart):"
echo "    bash ${APP_DIR}/scripts/update_gcp_vm.sh"
echo "=================================================================="
