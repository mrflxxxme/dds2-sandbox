#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# DDS Domain Documentation Checker
# Ensures DOMAIN_*.md files are up-to-date with actual code structure.
# Run: bash scripts/check_domain_docs.sh
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

WARNINGS=0

warn() {
    echo -e "${YELLOW}  WARNING: $1${NC}"
    ((WARNINGS++))
}

ok() {
    echo -e "${GREEN}  OK: $1${NC}"
}

echo "==============================================="
echo "  DDS Domain Documentation Checker"
echo "==============================================="
echo ""

# Check that all DOMAIN_*.md files exist
EXPECTED_DOMAINS=(
    "backend/DOMAIN_TRANSACTIONS.md"
    "backend/DOMAIN_REPORTS.md"
    "backend/DOMAIN_PLANNING.md"
    "backend/DOMAIN_COST.md"
    "backend/DOMAIN_WB.md"
    "frontend-react/DOMAIN_FRONTEND.md"
)

echo "-- Check 1: Domain files exist --"
for domain in "${EXPECTED_DOMAINS[@]}"; do
    if [ -f "$domain" ]; then
        ok "$domain exists"
    else
        warn "$domain MISSING"
    fi
done
echo ""

# Check that CLAUDE.md references all domain files
echo "-- Check 2: CLAUDE.md references all domains --"
if [ -f "CLAUDE.md" ]; then
    for domain in "${EXPECTED_DOMAINS[@]}"; do
        basename=$(basename "$domain")
        if grep -q "$basename" CLAUDE.md 2>/dev/null; then
            ok "CLAUDE.md references $basename"
        else
            warn "CLAUDE.md does NOT reference $basename"
        fi
    done
else
    warn "CLAUDE.md not found!"
fi
echo ""

# Check that new Python files in backend/ are mentioned in at least one DOMAIN_*.md
echo "-- Check 3: New backend files covered by domain docs --"
for pyfile in backend/services/*.py backend/services/**/*.py backend/routers/*.py backend/etl/*.py backend/etl/**/*.py; do
    [ -f "$pyfile" ] || continue
    basename=$(basename "$pyfile")
    [ "$basename" = "__init__.py" ] && continue

    FOUND=false
    for domain in backend/DOMAIN_*.md backend/CLAUDE.md; do
        if grep -q "$basename" "$domain" 2>/dev/null; then
            FOUND=true
            break
        fi
    done

    if [ "$FOUND" = false ]; then
        warn "$pyfile not mentioned in any DOMAIN_*.md or backend/CLAUDE.md"
    fi
done
echo ""

# Summary
echo "==============================================="
if [ "$WARNINGS" -gt 0 ]; then
    echo -e "${YELLOW}  $WARNINGS warnings found — update domain docs${NC}"
else
    echo -e "${GREEN}  ALL DOMAIN DOCS UP TO DATE${NC}"
fi
