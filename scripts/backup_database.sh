#!/usr/bin/env bash
# backup_database.sh — SQLite-safe database backup
# Uses the SQLite .backup command to avoid copying a partially written database.
# Safe to run while the application is running.
set -euo pipefail

APP_DIR="/opt/financial-news-ai-bridge"
DB_PATH="${APP_DIR}/data/news.db"
BACKUP_DIR="/opt/financial-news-ai-bridge-backups"
KEEP_DAYS="${KEEP_DAYS:-7}"

if [[ ! -f "${DB_PATH}" ]]; then
    echo "ERROR: Database not found at ${DB_PATH}"
    exit 1
fi

mkdir -p "${BACKUP_DIR}"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/news_${TIMESTAMP}.db"

echo "Backing up database to ${BACKUP_FILE}..."

# SQLite online backup — safe even while the app is running
sqlite3 "${DB_PATH}" ".backup '${BACKUP_FILE}'"

# Verify backup is readable
RECORD_COUNT=$(sqlite3 "${BACKUP_FILE}" "SELECT COUNT(*) FROM news;" 2>/dev/null || echo "0")
echo "Backup complete. News records in backup: ${RECORD_COUNT}"

# Remove backups older than KEEP_DAYS
find "${BACKUP_DIR}" -name "news_*.db" -mtime "+${KEEP_DAYS}" -delete 2>/dev/null || true

REMAINING=$(find "${BACKUP_DIR}" -name "news_*.db" | wc -l)
echo "Backup files retained: ${REMAINING}"
echo "Backup location: ${BACKUP_FILE}"
