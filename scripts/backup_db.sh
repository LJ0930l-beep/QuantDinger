#!/bin/bash
# backup_db.sh — PostgreSQL backup for QuantDinger
# Usage: bash scripts/backup_db.sh [DATABASE_URL]
# Output: backup_YYYYMMDD_HHMMSS.sql.gz

set -euo pipefail

DB_URL="${1:-${DATABASE_URL:-}}"
if [ -z "$DB_URL" ]; then
  echo "ERROR: DATABASE_URL not set."
  exit 1
fi

BACKUP_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/backup_${TIMESTAMP}.sql.gz"

echo "Backing up to: $BACKUP_FILE"

# Extract connection parts from DATABASE_URL
# Format: postgresql://user:password@host:port/dbname
if command -v pg_dump &>/dev/null; then
  pg_dump "$DB_URL" | gzip > "$BACKUP_FILE"
else
  echo "ERROR: pg_dump not found. Install PostgreSQL client tools."
  exit 1
fi

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "Backup complete: $BACKUP_FILE ($SIZE)"

# Keep only last 7 backups
ls -t "$BACKUP_DIR"/backup_*.sql.gz 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null || true
echo "Retained last 7 backups."
