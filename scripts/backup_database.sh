#!/usr/bin/env bash
# backup_database.sh — SQLite online backup
# Uses Python's sqlite3.Connection.backup() which is safe while the app runs.
# Usage: bash backup_database.sh
#        KEEP_DAYS=14 bash backup_database.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/home/zaid/financial-news-ai-bridge}"
DB_PATH="${APP_DIR}/data/news.db"
BACKUP_DIR="${APP_DIR%/*}/financial-news-ai-bridge-backups"
KEEP_DAYS="${KEEP_DAYS:-7}"

if [[ ! -f "${DB_PATH}" ]]; then
    echo "ERROR: Database not found at ${DB_PATH}"
    exit 1
fi

mkdir -p "${BACKUP_DIR}"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/news_${TIMESTAMP}.db"

echo "Backing up ${DB_PATH} → ${BACKUP_FILE}..."

# Python's sqlite3 backup API is safe with concurrent writers
"${APP_DIR}/.venv/bin/python3" - <<PYEOF
import sqlite3, sys

src = "${DB_PATH}"
dst = "${BACKUP_FILE}"

try:
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    count = sqlite3.connect(dst).execute("SELECT COUNT(*) FROM news").fetchone()[0]
    print(f"Backup complete. News records: {count}")
except Exception as e:
    print(f"Backup failed: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF

# Remove backups older than KEEP_DAYS
find "${BACKUP_DIR}" -name "news_*.db" -mtime "+${KEEP_DAYS}" -delete 2>/dev/null || true

REMAINING=$(find "${BACKUP_DIR}" -name "news_*.db" | wc -l)
echo "Retained backups: ${REMAINING}  |  Location: ${BACKUP_FILE}"
