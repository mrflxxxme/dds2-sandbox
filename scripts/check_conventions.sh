#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# DDS Convention Checker
# Automated domain-specific checks that ruff/pylint cannot catch.
# Run: bash scripts/check_conventions.sh
# CI:  added as step in .github/workflows/ci.yml
# ─────────────────────────────────────────────────────────────────────

set -uo pipefail

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
# OutboundShipment, StockTransfer, AssemblyRequest
SOFT_MODELS="Transaction\|Account\|CounterpartyCategory\|Override\|IntegrationKey\|PlannedPayment\|PlannedIncome\|WbPayout\|PaymentFactLink\|CostOrder\|CostOrderItem\|DutyRule\|CustomsTopup\|CustomsDT\|Order\|CategoryRef\|CategoryRule\|WbTariff\|Warehouse\|InboundReceipt\|OutboundShipment\|StockTransfer\|AssemblyRequest\|AssemblyDraft\|ProductTag\|FactoryOrder\|FactoryOrderItem\|Project\|ProjectMember\|AiConversation\|VehicleDocument\|Supplier\|DefectMarkOperation\|Counterparty\|CounterpartyDocument\|Loan\|WbGoodsReturn"
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
echo "── Check 7: Service functions without project_id parameter ──"
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

# ─── 10. db.delete() on SoftDelete models ────────────────────────────
echo "── Check 10: db.delete() on SoftDelete models (use soft_delete()) ──"
# Whitelist via inline comment `# no-soft-delete-check: <reason>` для моделей БЕЗ SoftDeleteMixin.
# Прецедент audit 2026-04-13 #1: FactoryOrderItem.delete() обходил soft-delete → восстановление невозможно.
FOUND=$(grep -rn "db\.delete(" backend/services/ backend/routers/ --include="*.py" \
    | grep -v "__pycache__" \
    | grep -v "# no-soft-delete-check" \
    | grep -v "test_" \
    2>/dev/null || true)
if [ -n "$FOUND" ]; then
    error "db.delete() found без whitelist — добавь soft_delete() ИЛИ '# no-soft-delete-check: <reason>' если модель без SoftDeleteMixin"
    echo "$FOUND"
else
    ok "No db.delete() calls found (whitelist via '# no-soft-delete-check' OK)"
fi
echo ""

# ─── 11. ilike() without escape parameter ────────────────────────────
echo "── Check 11: ilike() without escape parameter ──"
FOUND=$(grep -rn "\.ilike(f\"" backend/ --include="*.py" \
    | grep -v "escape=" \
    | grep -v "__pycache__" \
    | grep -v "test_" \
    2>/dev/null || true)
FOUND2=$(grep -rn "\.ilike(f'" backend/ --include="*.py" \
    | grep -v "escape=" \
    | grep -v "__pycache__" \
    | grep -v "test_" \
    2>/dev/null || true)
if [ -n "$FOUND" ] || [ -n "$FOUND2" ]; then
    warn "ilike() with f-string but no escape= — add escape=\"\\\\\" parameter"
    echo "$FOUND"
    echo "$FOUND2"
else
    ok "All ilike() calls have escape parameter"
fi
echo ""

# ─── 12. float() in financial calculations (use Decimal) ────────────
echo "── Check 12: float() in financial services (use Decimal) ──"
FOUND=$(grep -rn "float(" backend/services/cost/ backend/services/planning/ --include="*.py" \
    | grep -v "__pycache__" \
    | grep -v "# allow-float" \
    | grep -v "test_\|safe_float\|def .*float" \
    | grep -v "isinstance.*float" \
    2>/dev/null || true)
if [ -n "$FOUND" ]; then
    warn "float() in financial services — use Decimal(str(value)) for precision"
    echo "$FOUND"
else
    ok "No float() in financial services"
fi
echo ""

# ─── 13. asyncio.CancelledError not re-raised in scheduler ─────────
echo "── Check 13: except Exception without CancelledError in scheduler ──"
# Find files with 'except Exception' but no 'CancelledError' anywhere in the file
FOUND=""
for f in $(find backend/scheduler/ -name "*.py" -not -path "*__pycache__*" 2>/dev/null); do
    if grep -q "except Exception" "$f" && ! grep -q "CancelledError" "$f"; then
        FOUND="$FOUND\n$f"
    fi
done
if [ -n "$FOUND" ]; then
    error "scheduler files with 'except Exception' but no CancelledError handling"
    echo -e "$FOUND"
else
    ok "All scheduler jobs handle CancelledError"
fi
echo ""

# ─── 14. Upload endpoints without file size check ──────────────────
echo "── Check 14: File upload without size check ──"
# Find router functions that read uploaded files but don't check size
FOUND=""
for f in $(find backend/routers/ -name "*.py" -not -path "*__pycache__*" 2>/dev/null); do
    if grep -q "file\.read()\|await file\.read()" "$f" && ! grep -q "MAX_UPLOAD_SIZE\|max_upload\|max_size\|file_size\|content_length" "$f"; then
        LINES=$(grep -n "file\.read()\|await file\.read()" "$f" 2>/dev/null || true)
        if [ -n "$LINES" ]; then
            FOUND="$FOUND\n$LINES"
        fi
    fi
done
if [ -n "$FOUND" ]; then
    error "Upload endpoints without file size check — add MAX_UPLOAD_SIZE_MB validation"
    echo -e "$FOUND"
else
    ok "All upload endpoints check file size"
fi
echo ""

# ─── 15. Cache invalidation after project settings mutation ─────────
echo "── Check 15: Direct project mutation in routers (use service) ──"
FOUND=$(grep -rn "project\.\(tax_rate\|vat_rate\|currency\|name\) =" backend/routers/ --include="*.py" \
    | grep -v "__pycache__" \
    | grep -v "# allow-direct-mutation" \
    2>/dev/null || true)
if [ -n "$FOUND" ]; then
    error "Direct project field mutation in router — use project_settings_service"
    echo "$FOUND"
else
    ok "No direct project mutations in routers"
fi
echo ""

# ─── 16. Numeric precision mismatch in models ──────────────────────
echo "── Check 16: Numeric precision not 18,2 for money columns ──"
FOUND=$(grep -rn "Numeric(" backend/models/ --include="*.py" \
    | grep -v "Numeric(18" \
    | grep -v "__pycache__" \
    | grep -v "# allow-precision" \
    2>/dev/null || true)
if [ -n "$FOUND" ]; then
    warn "Numeric columns not using standard (18, 2) — verify intentional"
    echo "$FOUND"
else
    ok "All Numeric columns use standard precision"
fi
echo ""

# ─── 17. New services not documented in MAP.md ───────────────────────
echo "── Check 17: Services not documented in MAP.md ──"
UNDOCUMENTED=""
for f in $(find backend/services/ -maxdepth 1 -name "*_service.py" -not -path "*__pycache__*" 2>/dev/null | sort); do
    basename=$(basename "$f")
    # Skip backward-compat shims (< 50 lines, just re-exports from package)
    lines=$(wc -l < "$f" 2>/dev/null || echo 999)
    if [ "$lines" -lt 50 ]; then continue; fi
    if ! grep -q "$basename" backend/MAP.md 2>/dev/null; then
        UNDOCUMENTED="$UNDOCUMENTED\n  $basename"
    fi
done
# Also check service packages (directories with __init__.py)
for d in $(find backend/services/ -maxdepth 1 -type d -not -name services -not -name __pycache__ -not -name ai 2>/dev/null | sort); do
    if [ -f "$d/__init__.py" ]; then
        dirname=$(basename "$d")
        if ! grep -q "$dirname" backend/MAP.md 2>/dev/null; then
            UNDOCUMENTED="$UNDOCUMENTED\n  $dirname/"
        fi
    fi
done
if [ -n "$UNDOCUMENTED" ]; then
    warn "Services not documented in backend/MAP.md — add entries:$UNDOCUMENTED"
else
    ok "All services documented in MAP.md"
fi
echo ""

# ─── 18. uvicorn --workers >1 in Dockerfile.backend (race condition) ─
# Incident 2026-04-14: --workers 2 + --limit-max-requests 5000 race condition
# made backend hang `unhealthy` for 15h. uvicorn lacks --max-requests-jitter,
# so multiple workers restart simultaneously. See P24 in docs/KNOWN_PITFALLS.md.
echo "── Check 18: uvicorn --workers >1 in Dockerfile.backend ──"
if [ -f Dockerfile.backend ]; then
    FOUND=$(grep -E -- '"--workers", "[2-9]"' Dockerfile.backend 2>/dev/null || true)
    if [ -n "$FOUND" ]; then
        error "Dockerfile.backend uses uvicorn --workers >1 — race condition with --limit-max-requests (incident 2026-04-14, P24). Use --workers 1 or switch to gunicorn."
        echo "$FOUND"
    else
        ok "Dockerfile.backend uvicorn workers ≤ 1"
    fi
else
    ok "Dockerfile.backend not found (skipped)"
fi
echo ""

# ─── 19. dangerouslySetInnerHTML без sanitizeAIHtml ──────────────────
echo "── Check 19: dangerouslySetInnerHTML без sanitizeAIHtml (XSS защита) ──"
if [ -d frontend-react/src ]; then
    # Ищем use'ы dangerouslySetInnerHTML в файлах где НЕТ импорта sanitizeAIHtml
    FOUND=""
    while IFS= read -r file; do
        if ! grep -q "sanitizeAIHtml\|from '@/lib/sanitize'" "$file" 2>/dev/null; then
            FOUND="$FOUND$file"$'\n'
        fi
    done < <(grep -rln "dangerouslySetInnerHTML" frontend-react/src --include="*.tsx" --include="*.ts" 2>/dev/null || true)
    if [ -n "$FOUND" ]; then
        error "dangerouslySetInnerHTML используется без sanitizeAIHtml (XSS риск) — импортируй из @/lib/sanitize"
        echo "$FOUND"
    else
        ok "Все dangerouslySetInnerHTML через sanitizeAIHtml"
    fi
else
    ok "frontend-react/src not found (skipped)"
fi
echo ""

# ─── 20. URL template literals в src/lib/api/*.ts ────────────────────
echo "── Check 20: template literals в URL вместо URLSearchParams ──"
if [ -d frontend-react/src/lib/api ]; then
    # Ищем паттерн `?a=${...}` или `&a=${...}` в API-модулях
    FOUND=$(grep -rn -E '`[^`]*[?&][a-zA-Z_]+=\$\{[^}]+\}' frontend-react/src/lib/api --include="*.ts" 2>/dev/null | grep -v "test.ts" || true)
    if [ -n "$FOUND" ]; then
        warn "Template literals в URL — уязвимы к спецсимволам (H&M, +, %). Используй URLSearchParams."
        echo "$FOUND" | head -5
    else
        ok "URL-билдинг через URLSearchParams"
    fi
fi
echo ""

# ─── 21. Hardcoded project id in test INSERT (projects_id_seq collision) ─
# Incident 2026-05-18: tests doing `INSERT INTO projects (id, ...) VALUES (<fixed>)`
# plant a landmine — projects_id_seq on the long-lived local dev DB eventually
# climbs to that id and every auto-id INSERT then collides with projects_pkey.
# Use the sequence-allocated `project` / `other_project` fixtures from conftest.
# Whitelist a file with a `# allow-fixed-project-id: <reason>` comment.
echo "── Check 21: hardcoded project id in test INSERT (use project fixture) ──"
FOUND=""
while IFS= read -r file; do
    [ -z "$file" ] && continue
    grep -q "# allow-fixed-project-id" "$file" 2>/dev/null && continue
    HITS=$(grep -n -E "INSERT INTO projects ?\(id" "$file" 2>/dev/null || true)
    [ -n "$HITS" ] && FOUND="$FOUND$file:"$'\n'"$HITS"$'\n'
done < <(grep -rlE "INSERT INTO projects ?\(id" tests/ --include="*.py" 2>/dev/null || true)
if [ -n "$FOUND" ]; then
    error "INSERT INTO projects (id, ...) in tests — hardcoded id collides with projects_id_seq on the dev DB. Use conftest 'project'/'other_project' fixtures."
    echo "$FOUND"
else
    ok "No hardcoded project ids in test INSERTs"
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
