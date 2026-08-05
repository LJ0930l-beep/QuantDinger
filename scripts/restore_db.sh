#!/bin/bash
# restore_db.sh — PostgreSQL restore for QuantDinger
# Usage: bash scripts/restore_db.sh <backup_file.sql.gz> [DATABASE_URL]
# WARNING: This will DROP and recreate the target database.

set -euo pipefail

BACKUP_FILE="${1:-}"
DB_URL="${2:-${DATABASE_URL:-}}"

if [ -z "$BACKUP_FILE" ]; then
  echo "Usage: bash scripts/restore_db.sh <backup_file.sql.gz> [DATABASE_URL]"
  exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
  echo "ERROR: Backup file not found: $BACKUP_FILE"
  exit 1
fi

if [ -z "$DB_URL" ]; then
  echo "ERROR: DATABASE_URL not set."
  exit 1
fi

echo "WARNING: This will overwrite the target database."
echo "Backup: $BACKUP_FILE"
echo "Target: $DB_URL"
echo ""
read -rp "Type 'yes' to confirm: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "Aborted."
  exit 0
fi

echo "Restoring..."
if command -v psql &>/dev/null; then
  gunzip -c "$BACKUP_FILE" | psql "$DB_URL" -v ON_ERROR_STOP=1
else
  echo "ERROR: psql not found. Install PostgreSQL client tools."
  exit 1
fi

echo "Restore complete."
