#!/usr/bin/env bash
# setup_oracle_vm.sh — One-time setup on Oracle Cloud Always Free ARM VM
#
# Run as the default 'ubuntu' user (not root).
# Prerequisites:
#   1. Oracle Cloud Always Free account created
#   2. ARM VM provisioned (Ampere A1, Ubuntu 22.04)
#   3. SSH access working: ssh ubuntu@<your-vm-ip>
#   4. .env file ready on your local machine
#
# Usage:
#   bash scripts/setup_oracle_vm.sh
#
# After this script completes:
#   - Copy your .env to the VM: scp .env ubuntu@<ip>:/opt/financial-news-ai-bridge/.env
#   - Start service: cd /opt/financial-news-ai-bridge && docker compose up -d
#   - Verify: curl http://localhost:8000/health
set -euo pipefail

APP_DIR="/opt/financial-news-ai-bridge"
REPO_URL="https://github.com/zaidadaqqa/financial-news-ai-bridge.git"

echo "=== Financial News AI Bridge — Oracle Cloud ARM Setup ==="
echo "Platform: $(uname -m) | $(lsb_release -d 2>/dev/null | cut -f2 || echo 'Linux')"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/6] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    git curl ca-certificates gnupg lsb-release ufw

# ── 2. Docker (official ARM-compatible package) ───────────────────────────────
if command -v docker &>/dev/null; then
    echo "[2/6] Docker already installed: $(docker --version)"
else
    echo "[2/6] Installing Docker..."
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo systemctl enable docker
    sudo systemctl start docker
    sudo usermod -aG docker "$USER"
    echo "  Docker installed. Group membership effective after re-login."
    echo "  Log out and log back in, then re-run this script to continue."
    exit 0
fi

# ── 3. Clone repository ───────────────────────────────────────────────────────
echo "[3/6] Cloning repository to ${APP_DIR}..."
if [[ -d "${APP_DIR}/.git" ]]; then
    echo "  Repository already exists — pulling latest code..."
    sudo git -C "${APP_DIR}" fetch origin
    sudo git -C "${APP_DIR}" reset --hard origin/main
else
    sudo git clone "${REPO_URL}" "${APP_DIR}"
fi
sudo chown -R "$USER:$USER" "${APP_DIR}"

# ── 4. Create persistent data directory ──────────────────────────────────────
echo "[4/6] Creating data directory..."
mkdir -p "${APP_DIR}/data"
chmod 750 "${APP_DIR}/data"

# ── 5. Firewall ───────────────────────────────────────────────────────────────
echo "[5/6] Configuring firewall..."
# Oracle Cloud also requires an ingress rule in the VCN Security List for port 22.
# Port 8000 is NOT exposed externally — health check via localhost only.
sudo ufw allow OpenSSH 2>/dev/null || true
sudo ufw --force enable 2>/dev/null || true

# ── 6. Final instructions ─────────────────────────────────────────────────────
echo ""
echo "[6/6] Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Copy your .env file to the VM:"
echo "     scp .env ubuntu@<your-vm-ip>:${APP_DIR}/.env"
echo ""
echo "  2. On the VM, start the service:"
echo "     cd ${APP_DIR} && docker compose up -d --build"
echo ""
echo "  3. Verify the service is running:"
echo "     curl http://localhost:8000/health"
echo ""
echo "  4. View logs:"
echo "     docker compose -f ${APP_DIR}/docker-compose.yml logs -f ai-bridge"
echo ""
echo "  5. Configure automatic updates (optional):"
echo "     Add to crontab: 0 3 * * * bash ${APP_DIR}/scripts/update_oracle.sh"
