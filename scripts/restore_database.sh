#!/usr/bin/env bash
# restore_database.sh — Restore database from a backup file
# IMPORTANT: Stop the application before restoring to avoid corruption.
set -euo pipefail

BACKUP_FILE="${1:-}"
APP_DIR="/opt/financial-news-ai-bridge"
DB_PATH="${APP_DIR}/data/news.db"
COMPOSE_BIN="docker compose"

if [[ -z "${BACKUP_FILE}" ]]; then
    echo "Usage: $0 /path/to/backup/news_YYYYMMDD_HHMMSS.db"
    echo ""
    echo "Available backups:"
    ls -lh /opt/financial-news-ai-bridge-backups/news_*.db 2>/dev/null || echo "  None found"
    exit 1
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
    echo "ERROR: Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

echo "=== Database Restore ==="
echo "Backup file: ${BACKUP_FILE}"
echo "Target:      ${DB_PATH}"
echo ""
echo "WARNING: This will replace the current production database."
read -r -p "Type 'yes' to confirm: " CONFIRM

if [[ "${CONFIRM}" != "yes" ]]; then
    echo "Aborted."
    exit 0
fi

# Stop the service
echo "Stopping service..."
cd "${APP_DIR}"
${COMPOSE_BIN} down

# Create pre-restore snapshot
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
SNAPSHOT="/opt/financial-news-ai-bridge-backups/pre_restore_${TIMESTAMP}.db"
if [[ -f "${DB_PATH}" ]]; then
    sqlite3 "${DB_PATH}" ".backup '${SNAPSHOT}'"
    echo "Pre-restore snapshot saved: ${SNAPSHOT}"
fi

# Restore
sqlite3 "${BACKUP_FILE}" ".backup '${DB_PATH}'"

# Verify
RECORD_COUNT=$(sqlite3 "${DB_PATH}" "SELECT COUNT(*) FROM news;" 2>/dev/null || echo "0")
echo "Restore complete. News records: ${RECORD_COUNT}"

# Restart
echo "Restarting service..."
${COMPOSE_BIN} up -d
echo "=== Restore complete ==="
