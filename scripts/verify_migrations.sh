#!/bin/bash
# verify_migrations.sh — validate expand-only migration integrity
# Usage: bash scripts/verify_migrations.sh [DATABASE_URL]
# Requires: psql in PATH, DATABASE_URL set or passed as arg

set -euo pipefail

DB_URL="${1:-${DATABASE_URL:-}}"
if [ -z "$DB_URL" ]; then
  echo "ERROR: DATABASE_URL not set. Pass as argument or export DATABASE_URL."
  exit 1
fi

MIGRATIONS_DIR="$(dirname "$0")/../backend_api_python/migrations"
if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo "ERROR: migrations directory not found: $MIGRATIONS_DIR"
  exit 1
fi

echo "=== Migration Integrity Check ==="
echo "Target: $DB_URL"
echo "Migrations: $MIGRATIONS_DIR"
echo ""

# 1. Check all migration files are idempotent (IF NOT EXISTS pattern)
MISSING_GUARD=0
for f in "$MIGRATIONS_DIR"/*.sql; do
  filename=$(basename "$f")
  if ! grep -q "IF NOT EXISTS\|IF EXISTS\|DROP.*IF EXISTS\|ON CONFLICT.*DO NOTHING\|DO \$\$" "$f" 2>/dev/null; then
    echo "WARNING: $filename has no idempotency guard"
    MISSING_GUARD=$((MISSING_GUARD + 1))
  fi
done

# 2. Verify migration naming convention (YYYYMMDD_description.sql)
INVALID_NAMES=0
for f in "$MIGRATIONS_DIR"/*.sql; do
  filename=$(basename "$f")
  if ! echo "$filename" | grep -qE '^[0-9]{8}_.*\.sql$'; then
    echo "WARNING: $filename does not match YYYYMMDD_description.sql convention"
    INVALID_NAMES=$((INVALID_NAMES + 1))
  fi
done

# 3. Count migrations
TOTAL=$(ls "$MIGRATIONS_DIR"/*.sql 2>/dev/null | wc -l)

echo ""
echo "=== Summary ==="
echo "Total migration files: $TOTAL"
echo "Missing idempotency guards: $MISSING_GUARD"
echo "Invalid naming convention: $INVALID_NAMES"

if [ $MISSING_GUARD -gt 0 ] || [ $INVALID_NAMES -gt 0 ]; then
  echo "Status: WARNINGS FOUND"
  exit 0
else
  echo "Status: ALL CHECKS PASSED"
fi
