#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# DDS Documentation Checker
# Verifies that CLAUDE.md and MAP.md match actual codebase structure.
# Run: bash scripts/check_docs.sh
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail
export LC_ALL=C

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

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

# ─── Helper: compare count from docs vs actual ──────────────────────
check_count() {
    local label="$1"
    local doc_count="$2"
    local actual_count="$3"

    if [ -z "$doc_count" ]; then
        warn "Could not parse $label count from docs"
    elif [ "$doc_count" -ne "$actual_count" ]; then
        warn "$label: docs say $doc_count, actual $actual_count"
    else
        ok "$label: $actual_count (matches docs)"
    fi
}

# ─── Helper: compare list from docs vs actual files ─────────────────
check_list() {
    local label="$1"
    local doc_list="$2"    # comma or space separated
    local actual_list="$3" # newline separated

    local doc_sorted
    doc_sorted=$(echo "$doc_list" | tr ',' '\n' | sed 's/^ *//;s/ *$//' | grep -v '^$' | sort)
    local actual_sorted
    actual_sorted=$(echo "$actual_list" | grep -v '^$' | sort)

    local only_in_docs
    only_in_docs=$(comm -23 <(echo "$doc_sorted") <(echo "$actual_sorted") || true)
    local only_in_code
    only_in_code=$(comm -13 <(echo "$doc_sorted") <(echo "$actual_sorted") || true)

    if [ -n "$only_in_docs" ]; then
        warn "$label — in docs but NOT in code:\n  $(echo "$only_in_docs" | tr '\n' ', ' | sed 's/,$//')"
    fi
    if [ -n "$only_in_code" ]; then
        warn "$label — in code but NOT in docs:\n  $(echo "$only_in_code" | tr '\n' ', ' | sed 's/,$//')"
    fi
    if [ -z "$only_in_docs" ] && [ -z "$only_in_code" ]; then
        ok "$label: all items match"
    fi
}

echo "═══════════════════════════════════════════════════════"
echo "  DDS Documentation Checker"
echo "═══════════════════════════════════════════════════════"
echo ""

CLAUDE_MD="CLAUDE.md"
MAP_MD="backend/MAP.md"

if [ ! -f "$CLAUDE_MD" ]; then
    error "CLAUDE.md not found"
    exit 1
fi
if [ ! -f "$MAP_MD" ]; then
    error "backend/MAP.md not found"
    exit 1
fi

# ─── Check 1: AI tools module count ─────────────────────────────────
echo "── Check 1: AI tools module count ──"
ACTUAL_TOOLS=$(find backend/services/ai/tools -maxdepth 1 -name "*.py" \
    ! -name "__init__.py" ! -name "all_tools.py" ! -name "common.py" 2>/dev/null | wc -l | tr -d ' ')
DOC_TOOLS=$(grep -oE '[0-9]+ модул' "$CLAUDE_MD" | head -1 | grep -oE '[0-9]+' || echo "")
check_count "AI tools modules" "$DOC_TOOLS" "$ACTUAL_TOOLS"
echo ""

# ─── Check 2: AI agents count ───────────────────────────────────────
echo "── Check 2: AI agents count ──"
ACTUAL_AGENTS=$(find backend/services/ai/agents -maxdepth 1 -name "*.py" \
    ! -name "__init__.py" ! -name "base.py" 2>/dev/null | wc -l | tr -d ' ')
DOC_AGENTS_LINE=$(grep -n "services/ai/agents" "$MAP_MD" | head -1 || echo "")
if [ -n "$DOC_AGENTS_LINE" ]; then
    # Count names in parenthesized list
    DOC_AGENTS=$(echo "$DOC_AGENTS_LINE" | sed 's/.*(\(.*\)).*/\1/' | tr ',' '\n' | grep -v '^$' | wc -l | tr -d ' ')
    check_count "AI agents" "$DOC_AGENTS" "$ACTUAL_AGENTS"
else
    warn "Could not find AI agents list in MAP.md"
fi
echo ""

# ─── Check 3: Frontend main pages ───────────────────────────────────
echo "── Check 3: Frontend main pages ──"
PAGES_DIR='frontend-react/src/app/(main)/p/[slug]'
ACTUAL_PAGES=$(ls -1 "$PAGES_DIR" 2>/dev/null | grep -v '\.tsx$' | sort)
ACTUAL_PAGES_COUNT=$(echo "$ACTUAL_PAGES" | grep -c . || echo 0)

DOC_PAGES_COUNT=$(grep -oE '[0-9]+ страниц' "$CLAUDE_MD" | head -1 | grep -oE '[0-9]+' || echo "")
check_count "Frontend pages" "$DOC_PAGES_COUNT" "$ACTUAL_PAGES_COUNT"

# Also check named list: extract page names from the line after "страниц:"
DOC_PAGES_LINE=$(grep -A1 'страниц' "$CLAUDE_MD" | tail -1 || echo "")
if echo "$DOC_PAGES_LINE" | grep -q "dds"; then
    DOC_PAGES=$(echo "$DOC_PAGES_LINE" | sed 's/.*: //' | sed 's/)$//' | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sed 's|/\*||' | grep -v '^$' | sort)
    check_list "Frontend pages list" "$(echo "$DOC_PAGES" | tr '\n' ',')" "$ACTUAL_PAGES"
fi
echo ""

# ─── Check 4: TMA pages ─────────────────────────────────────────────
echo "── Check 4: TMA pages ──"
TMA_DIR='frontend-react/src/app/(tma)/tma/[slug]'
ACTUAL_TMA=$(ls -1 "$TMA_DIR" 2>/dev/null | grep -v '\.tsx$' | sort)
ACTUAL_TMA_COUNT=$(echo "$ACTUAL_TMA" | grep -c . || echo 0)

DOC_TMA_LINE=$(grep "tma.*slug" "$CLAUDE_MD" | head -1 || echo "")
if [ -n "$DOC_TMA_LINE" ]; then
    DOC_TMA=$(echo "$DOC_TMA_LINE" | sed 's/.*(\(.*\)).*/\1/' | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sort)
    DOC_TMA_COUNT=$(echo "$DOC_TMA" | grep -c . || echo 0)
    check_count "TMA pages" "$DOC_TMA_COUNT" "$ACTUAL_TMA_COUNT"
    check_list "TMA pages list" "$(echo "$DOC_TMA" | tr '\n' ',')" "$ACTUAL_TMA"
fi
echo ""

# ─── Check 5: Frontend components ───────────────────────────────────
echo "── Check 5: Frontend components ──"
ACTUAL_COMPONENTS=$(find frontend-react/src/components -maxdepth 1 -name "*.tsx" 2>/dev/null | xargs -n1 basename | sed 's/\.tsx//' | sort)
ACTUAL_COMP_COUNT=$(echo "$ACTUAL_COMPONENTS" | grep -c . || echo 0)

DOC_COMP_LINE=$(grep 'src/components/' "$CLAUDE_MD" | head -1 || echo "")
if [ -n "$DOC_COMP_LINE" ]; then
    DOC_COMPONENTS=$(echo "$DOC_COMP_LINE" | sed 's/.*— //' | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sort)
    check_list "Components" "$(echo "$DOC_COMPONENTS" | tr '\n' ',')" "$ACTUAL_COMPONENTS"
fi
echo ""

# ─── Check 6: API client files ──────────────────────────────────────
echo "── Check 6: API client domain files ──"
ACTUAL_API=$(find frontend-react/src/lib/api -maxdepth 1 -name "*.ts" \
    ! -name "index.ts" ! -name "client.ts" 2>/dev/null | wc -l | tr -d ' ')
DOC_API=$(grep -oE '[0-9]+ доменных' "$CLAUDE_MD" | head -1 | grep -oE '[0-9]+' || echo "")
check_count "API client domain files" "$DOC_API" "$ACTUAL_API"
echo ""

# ─── Check 7: Routers in MAP.md ─────────────────────────────────────
echo "── Check 7: Routers in MAP.md ──"
ACTUAL_ROUTERS=$(find backend/routers -maxdepth 1 -name "*.py" ! -name "__init__.py" 2>/dev/null | xargs -n1 basename | sed 's/\.py//' | sort)

DOC_ROUTERS_LINE=$(grep 'routers/' "$MAP_MD" | grep '(' | head -1 || echo "")
if [ -n "$DOC_ROUTERS_LINE" ]; then
    DOC_ROUTERS=$(echo "$DOC_ROUTERS_LINE" | sed 's/.*(\(.*\)).*/\1/' | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sort)
    check_list "Routers" "$(echo "$DOC_ROUTERS" | tr '\n' ',')" "$ACTUAL_ROUTERS"
fi
echo ""

# ─── Check 8: Workflow files ────────────────────────────────────────
echo "── Check 8: Workflow files in docs ──"
ACTUAL_WORKFLOWS=$(find .github/workflows -maxdepth 1 -name "*.yml" 2>/dev/null | xargs -n1 basename | sed 's/\.yml//' | sort)
# Check which workflows are NOT mentioned anywhere in CLAUDE.md
UNDOCUMENTED=""
for wf in $ACTUAL_WORKFLOWS; do
    if ! grep -q "$wf" "$CLAUDE_MD" 2>/dev/null; then
        UNDOCUMENTED="$UNDOCUMENTED $wf"
    fi
done
if [ -n "$UNDOCUMENTED" ]; then
    warn "Workflows not mentioned in CLAUDE.md:$UNDOCUMENTED"
else
    ok "All workflows mentioned in CLAUDE.md"
fi
echo ""

# ─── Check 9: Schemas in MAP.md ─────────────────────────────────────
echo "── Check 9: Schemas in MAP.md ──"
ACTUAL_SCHEMAS=$(find backend/schemas -maxdepth 1 -name "*.py" ! -name "__init__.py" 2>/dev/null | xargs -n1 basename | sed 's/\.py//' | sort)

DOC_SCHEMAS_LINE=$(grep 'schemas/' "$MAP_MD" | grep '(' | head -1 || echo "")
if [ -n "$DOC_SCHEMAS_LINE" ]; then
    DOC_SCHEMAS=$(echo "$DOC_SCHEMAS_LINE" | sed 's/.*(\(.*\)).*/\1/' | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sort)
    check_list "Schemas" "$(echo "$DOC_SCHEMAS" | tr '\n' ',')" "$ACTUAL_SCHEMAS"
fi
echo ""

# ─── Summary ────────────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════════"
if [ "$ERRORS" -gt 0 ]; then
    echo -e "${RED}  FAILED with $ERRORS errors and $WARNINGS warnings${NC}"
    exit 1
elif [ "$WARNINGS" -gt 0 ]; then
    echo -e "${YELLOW}  PASSED with $WARNINGS warnings${NC}"
else
    echo -e "${GREEN}  ALL DOCS UP TO DATE${NC}"
fi
