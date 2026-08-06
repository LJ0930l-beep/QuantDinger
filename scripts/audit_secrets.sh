#!/bin/bash
# audit_secrets.sh — scan repository for exposed secrets
# Usage: bash scripts/audit_secrets.sh [repo_root]
# Requires: git, grep

set -euo pipefail

ROOT="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
echo "=== Secret Audit ==="
echo "Scanning: $ROOT"
echo ""

EXPOSURES=0

# Patterns that indicate potential secrets
PATTERNS=(
  "api_key"
  "secret_key"
  "SECRET_KEY"
  "password"
  "PASSWORD"
  "API_SECRET"
  "private_key"
  "PRIVATE_KEY"
  "GATE_KEY"
  "GATE_SECRET"
  "ALPACA_KEY"
  "ALPACA_SECRET"
  "IBKR_"
  "BINANCE_SECRET"
  "OKX_SECRET"
)

# File patterns to scan (exclude binary, lock files, node_modules, dist)
SCAN_EXTS="\.py$|\.env$|\.yml$|\.yaml$|\.json$|\.toml$|\.ini$|\.cfg$|\.sh$"

echo "Step 1: Scanning source files for potential secrets..."
for pattern in "${PATTERNS[@]}"; do
  matches=$(grep -rn --include="*.py" --include="*.env" --include="*.yml" --include="*.yaml" \
    --include="*.json" --include="*.toml" --include="*.ini" --include="*.cfg" \
    --exclude-dir=".git" --exclude-dir="node_modules" --exclude-dir="dist" \
    --exclude-dir=".venv" --exclude-dir="__pycache__" --exclude-dir=".pytest_cache" \
    --exclude-dir=".workbuddy" --exclude="*.lock" --exclude="*.pyc" \
    "$pattern" "$ROOT" 2>/dev/null | grep -v "example\|placeholder\|test_\|#\|mock\|MOCK\|dummy\|REDACTED\|your-" || true)
  if [ -n "$matches" ]; then
    while IFS= read -r line; do
      echo "  FOUND: $line"
      EXPOSURES=$((EXPOSURES + 1))
    done <<< "$matches"
  fi
done

echo ""
echo "Step 2: Verifying .gitignore coverage..."
GITIGNORE="$ROOT/.gitignore"
if [ -f "$GITIGNORE" ]; then
  for rule in ".env" "*.pem" "*.key" "credentials*.json" "*.log" "logs/"; do
    if ! grep -qF "$rule" "$GITIGNORE" 2>/dev/null; then
      echo "  MISSING from .gitignore: $rule"
      EXPOSURES=$((EXPOSURES + 1))
    fi
  done
else
  echo "  WARNING: .gitignore not found"
  EXPOSURES=$((EXPOSURES + 1))
fi

echo ""
echo "Step 3: Checking for hardcoded URLs with embedded credentials..."
URL_LEAKS=$(grep -rn '://[^@]*:[^@]*@' "$ROOT" --include="*.py" --include="*.env" \
  --exclude-dir=".git" --exclude-dir="node_modules" --exclude-dir=".venv" \
  2>/dev/null | grep -v "example\|placeholder\|mock\|//.*:.*@" | head -5 || true)
if [ -n "$URL_LEAKS" ]; then
  echo "  WARNING: Potential credential URLs found:"
  echo "$URL_LEAKS"
  EXPOSURES=$((EXPOSURES + 1))
fi

echo ""
echo "=== Audit Summary ==="
echo "Potential exposures found: $EXPOSURES"
if [ $EXPOSURES -eq 0 ]; then
  echo "Status: CLEAN — No suspicious patterns detected."
else
  echo "Status: INVESTIGATE — Review findings above. Ensure no real secrets are exposed."
fi
