"""
Tests that project conventions stay in sync.

Catches drift between:
- SoftDeleteMixin models ↔ dynamic discovery in post_edit_check.py (--list-soft-models)
- @cached prefixes ↔ invalidate_project_reports() prefixes
"""

import ast
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).parent.parent / "backend"
SCRIPTS = Path(__file__).parent.parent / "scripts"
FRONTEND = Path(__file__).parent.parent / "frontend-react"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SoftDeleteMixin — dynamic discovery must see every model
# ═══════════════════════════════════════════════════════════════════════════════


def _find_soft_delete_models() -> set[str]:
    """Scan backend/models/*.py for classes inheriting SoftDeleteMixin."""
    models_dir = BACKEND / "models"
    soft_models = set()
    for py_file in models_dir.glob("*.py"):
        source = py_file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:  # noqa: S112
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    name = ""
                    if isinstance(base, ast.Name):
                        name = base.id
                    elif isinstance(base, ast.Attribute):
                        name = base.attr
                    if name == "SoftDeleteMixin":
                        soft_models.add(node.name)
    return soft_models


def _get_enforcer_soft_models() -> set[str]:
    """Discovered model list as the enforcers see it (post_edit_check.py --list-soft-models).

    Same single source of truth that check_conventions.sh Check 6 consumes.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "hooks" / "post_edit_check.py"), "--list-soft-models"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


class TestSoftDeleteSync:
    def test_all_soft_delete_models_in_conventions(self):
        """Every SoftDeleteMixin model must be seen by the dynamic enforcer discovery."""
        code_models = _find_soft_delete_models()
        enforcer_models = _get_enforcer_soft_models()
        assert code_models, "Test AST scan found no SoftDeleteMixin models — scan broken?"
        missing = code_models - enforcer_models
        assert not missing, (
            f"SoftDeleteMixin models NOT discovered by post_edit_check.py --list-soft-models: {missing}. "
            f"Discovery in scripts/hooks/post_edit_check.py is under-counting."
        )

    def test_no_stale_models_in_conventions(self):
        """Discovery must not report models that no longer use SoftDeleteMixin."""
        code_models = _find_soft_delete_models()
        enforcer_models = _get_enforcer_soft_models()
        stale = enforcer_models - code_models
        assert not stale, (
            f"Models discovered by --list-soft-models but NOT SoftDeleteMixin: {stale}. "
            f"Discovery in scripts/hooks/post_edit_check.py is over-counting."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Cache prefixes — @cached() must match invalidate_project_reports()
# ═══════════════════════════════════════════════════════════════════════════════


def _find_cached_prefixes() -> set[str]:
    """Find all @cached(prefix="...") prefixes in backend/."""
    prefixes = set()
    for py_file in BACKEND.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        content = py_file.read_text()
        # Match @cached(prefix="reports:xxx", ...)
        for m in re.finditer(r'@cached\(\s*prefix\s*=\s*"([^"]+)"', content):
            prefixes.add(m.group(1))
    return prefixes


def _find_invalidate_prefixes() -> set[str]:
    """Extract prefixes from invalidate_project_reports() in cache.py."""
    cache_py = BACKEND / "cache.py"
    content = cache_py.read_text()
    # Find the function body
    match = re.search(
        r"async def invalidate_project_reports.*?for prefix in \((.*?)\):",
        content,
        re.DOTALL,
    )
    assert match, "invalidate_project_reports() not found in cache.py"
    body = match.group(1)
    return set(re.findall(r'"([^"]+)"', body))


class TestCachePrefixSync:
    def test_all_cached_prefixes_invalidated(self):
        """Every @cached prefix must be in invalidate_project_reports()."""
        cached = _find_cached_prefixes()
        invalidated = _find_invalidate_prefixes()
        # Only check reports: prefixes (other caches may have different invalidation)
        report_cached = {p for p in cached if p.startswith("reports:")}
        missing = report_cached - invalidated
        assert not missing, (
            f"@cached prefixes NOT in invalidate_project_reports(): {missing}. "
            f"Add them to backend/cache.py invalidate_project_reports()."
        )

    def test_no_stale_invalidation_prefixes(self):
        """invalidate_project_reports() should not list removed prefixes.

        Exception: prefixes registered in Phase 1 for services that will be
        added in Phase 2 (counterparties-loans feature). These are intentionally
        pre-registered so Phase 2 services can add @cached without touching cache.py.
        """
        # Prefixes pre-registered in cache.py for Phase 2 services (counterparties-loans).
        # Remove entries here once the corresponding @cached service is implemented.
        PHASE2_PENDING = {
            "counterparty_list",
            "counterparty_detail",
            "reports:counterparty_turnovers",
            "loan_list",
        }
        cached = _find_cached_prefixes()
        invalidated = _find_invalidate_prefixes()
        stale = invalidated - cached - PHASE2_PENDING
        assert not stale, (
            f"Prefixes in invalidate_project_reports() but no @cached: {stale}. " f"Remove them from backend/cache.py."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RBAC page keys — frontend permission checkboxes ⊆ backend ALL_PAGES
# ═══════════════════════════════════════════════════════════════════════════════
#
# The team page (frontend) renders a checkbox per page key; on save it POSTs the
# selected keys, and update_member_role() rejects the whole request (HTTP 400) if
# any key is absent from backend ALL_PAGES. A frontend-only key thus silently
# breaks "Save" for any member whose selection includes it — the exact prod bug
# that stranded the "Генератор ШК" (barcode-labels) key under «Склад».


def _find_frontend_page_keys() -> set[str]:
    """Extract page keys from SECTION_PAGES in the team page component."""
    team_page = FRONTEND / "src" / "app" / "(main)" / "p" / "[slug]" / "team" / "page.tsx"
    content = team_page.read_text()
    # Isolate the SECTION_PAGES object literal (other `key:` occurrences in the
    # file are TanStack column defs, not permission keys).
    match = re.search(r"const SECTION_PAGES.*?=\s*\{(.*?)\n\};", content, re.DOTALL)
    assert match, "SECTION_PAGES literal not found in team/page.tsx"
    block = match.group(1)
    return set(re.findall(r"key:\s*'([^']+)'", block))


class TestRbacPageKeySync:
    def test_frontend_page_keys_are_known_to_backend(self):
        """Every permission checkbox key must be in backend ALL_PAGES.

        Otherwise saving a member's access explodes with HTTP 400
        «Неизвестные страницы: …» and the Save button appears dead.
        """
        from backend.rbac import ALL_PAGES

        frontend_keys = _find_frontend_page_keys()
        assert frontend_keys, "Frontend page-key scan found nothing — regex broken?"
        unknown = frontend_keys - set(ALL_PAGES)
        assert not unknown, (
            f"Frontend team-page permission keys missing from backend ALL_PAGES: {unknown}. "
            f"Add them to ALL_PAGES (and the matching SECTION_PAGES group) in backend/rbac.py, "
            f"else update_member_role() returns 400 and 'Сохранить' silently fails."
        )
