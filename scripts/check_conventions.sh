#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# DDS Convention Checker
# Automated domain-specific checks that ruff/pylint cannot catch.
# Run: bash scripts/check_conventions.sh
# CI:  added as step in .github/workflows/ci.yml
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

ERRORS=0
WARNINGS=0

error() {
    echo -e "${RED}❌ ERROR: $1${NC}"
    ERRORS=$((ERRORS + 1))
}

warn() {
    echo -e "${YELLOW}⚠️  WARNING: $1${NC}"
    WARNINGS=$((WARNINGS + 1))
}

ok() {
    echo -e "${GREEN}✅ $1${NC}"
}

echo "═══════════════════════════════════════════════════════"
echo "  DDS Convention Checker"
echo "═══════════════════════════════════════════════════════"
echo ""

# ─── 1. Banned: asyncio.get_event_loop() ─────────────────────────────
echo "── Check 1: asyncio.get_event_loop() (use get_running_loop) ──"
FOUND=$(grep -rn "get_event_loop()" backend/ --include="*.py" 2>/dev/null || true)
if [ -n "$FOUND" ]; then
    error "asyncio.get_event_loop() found — use asyncio.get_running_loop()"
    echo "$FOUND"
else
    ok "No get_event_loop() found"
fi
echo ""

# ─── 2. Banned: print() in backend (use logger) ─────────────────────
echo "── Check 2: print() in backend code (use logger) ──"
# Exclude __pycache__, alembic, tests, seeds
FOUND=$(grep -rn "^\s*print(" backend/ --include="*.py" \
    --exclude-dir="__pycache__" \
    --exclude-dir="alembic" \
    --exclude-dir="seeds" \
    2>/dev/null || true)
if [ -n "$FOUND" ]; then
    warn "print() found in backend — use logging.getLogger()"
    echo "$FOUND"
else
    ok "No print() in backend"
fi
echo ""

# ─── 3. Large files (>500 lines) ─────────────────────────────────────
echo "── Check 3: Large files (>500 lines) ──"
LARGE_FILES=$(find backend/ -name "*.py" -not -path "*__pycache__*" \
    -exec wc -l {} + 2>/dev/null \
    | awk '$1 > 500 && !/total$/ { print $1 " lines: " $2 }' \
    | sort -rn || true)
if [ -n "$LARGE_FILES" ]; then
    warn "Files >500 lines — consider splitting"
    echo "$LARGE_FILES"
else
    ok "All files ≤500 lines"
fi
echo ""

# ─── 4. Float for money (should be Numeric) ─────────────────────────
echo "── Check 4: Float columns for money (use Numeric) ──"
FOUND=$(grep -rn "mapped_column(Float" backend/models/ --include="*.py" 2>/dev/null || true)
if [ -n "$FOUND" ]; then
    error "Float used for column — use Numeric(18, 2) for money"
    echo "$FOUND"
else
    ok "No Float columns in models"
fi
echo ""

# ─── 5. f-string in SQL text() — potential injection ─────────────────
echo "── Check 5: f-string in SQL text() — potential injection ──"
FOUND=$(grep -rn 'text(f"' backend/ --include="*.py" 2>/dev/null || true)
FOUND2=$(grep -rn "text(f'" backend/ --include="*.py" 2>/dev/null || true)
if [ -n "$FOUND" ] || [ -n "$FOUND2" ]; then
    error "f-string in text() — SQL injection risk! Use :param binding"
    echo "$FOUND"
    echo "$FOUND2"
else
    ok "No f-strings in text()"
fi
echo ""

# ─── 6. Missing is_deleted filter on SoftDelete models ───────────────
echo "── Check 6: Queries on SoftDelete models without is_deleted filter ──"
# Models with SoftDeleteMixin: Transaction, Account, CounterpartyCategory, Override,
# Order, PlannedPayment, PlannedIncome, WbPayout, PaymentFactLink, CostOrder,
# DutyRule, CustomsTopup, CustomsDT, IntegrationKey, Warehouse, InboundReceipt,
# OutboundShipment, StockTransfer
SOFT_MODELS="Transaction\|Account\|CounterpartyCategory\|Override\|IntegrationKey\|PlannedPayment\|PlannedIncome\|WbPayout\|PaymentFactLink\|CostOrder\|DutyRule\|CustomsTopup\|CustomsDT\|Order\|CategoryRef\|CategoryRule\|WbTariff\|Warehouse\|InboundReceipt\|OutboundShipment\|StockTransfer"
FOUND=$(grep -rn "select($SOFT_MODELS)" backend/services/ backend/etl/ --include="*.py" \
    | grep -v "is_deleted" \
    | grep -v "__pycache__" \
    | grep -v "# no-soft-delete-check" \
    | grep -v "\.soft_delete\|\.add\|test_" \
    2>/dev/null || true)
if [ -n "$FOUND" ]; then
    warn "Queries on SoftDelete models without is_deleted filter"
    echo "$FOUND"
else
    ok "All SoftDelete model queries filter is_deleted"
fi
echo ""

# ─── 7. Missing project_id in service functions ─────────────────────
echo "── Check 6: Service functions without project_id parameter ──"
# Check for async def functions in services that do DB queries but miss project_id
# This is a heuristic — warns about functions that have 'db' but no 'project_id'
FOUND=$(grep -rn "async def.*db:.*Session" backend/services/ --include="*.py" \
    | grep -v "project_id" \
    | grep -v "__pycache__" \
    | grep -v "# no-project-check" \
    | grep -v "_helper\|_compute\|_build\|_serialize\|_empty\|_metric\|_accumulate\|_calc" \
    2>/dev/null || true)
if [ -n "$FOUND" ]; then
    warn "Service functions with db: Session but no project_id (may be intentional)"
    echo "$FOUND"
else
    ok "All service DB functions include project_id"
fi
echo ""

# ─── 8. Banned: datetime.utcnow() or datetime.now() ─────────────────
echo "── Check 8: datetime.utcnow() / datetime.now() (use utcnow()) ──"
FOUND=$(grep -rn "datetime\.utcnow()\|datetime\.now()" backend/ --include="*.py" \
    | grep -v "__pycache__" \
    | grep -v "utils/time.py" \
    | grep -v "# allowed-datetime" \
    2>/dev/null || true)
if [ -n "$FOUND" ]; then
    warn "datetime.utcnow()/now() found — use 'from backend.utils.time import utcnow'"
    echo "$FOUND"
else
    ok "No banned datetime patterns"
fi
echo ""

# ─── 9. Unbounded .all() without limit ──────────────────────────────
echo "── Check 9: .scalars().all() without .limit() (potential OOM) ──"
# Only check service files, not tests or routers
FOUND=$(grep -rn "\.scalars()\.all()" backend/services/ --include="*.py" -l 2>/dev/null || true)
if [ -n "$FOUND" ]; then
    COUNT=$(echo "$FOUND" | wc -l)
    warn "$COUNT service files use .scalars().all() — verify they have .limit() or bounded input"
else
    ok "No unbounded .all() calls"
fi
echo ""

# ─── Summary ─────────────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════════"
if [ "$ERRORS" -gt 0 ]; then
    echo -e "${RED}  FAILED: $ERRORS errors, $WARNINGS warnings${NC}"
    exit 1
elif [ "$WARNINGS" -gt 0 ]; then
    echo -e "${YELLOW}  PASSED with $WARNINGS warnings${NC}"
    exit 0
else
    echo -e "${GREEN}  ALL CHECKS PASSED${NC}"
    exit 0
fi
