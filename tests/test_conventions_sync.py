"""
Tests that project conventions stay in sync.

Catches drift between:
- SoftDeleteMixin models ↔ check_conventions.sh SOFT_MODELS
- @cached prefixes ↔ invalidate_project_reports() prefixes
"""

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).parent.parent / "backend"
SCRIPTS = Path(__file__).parent.parent / "scripts"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SoftDeleteMixin — every model must be in check_conventions.sh
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


def _get_conventions_soft_models() -> set[str]:
    """Extract SOFT_MODELS list from check_conventions.sh."""
    script = SCRIPTS / "check_conventions.sh"
    content = script.read_text()
    match = re.search(r'SOFT_MODELS="([^"]+)"', content)
    assert match, "SOFT_MODELS not found in check_conventions.sh"
    # Format: Model1\|Model2\|Model3
    raw = match.group(1)
    return set(raw.replace("\\|", "|").split("|"))


class TestSoftDeleteSync:
    def test_all_soft_delete_models_in_conventions(self):
        """Every SoftDeleteMixin model must appear in check_conventions.sh."""
        code_models = _find_soft_delete_models()
        script_models = _get_conventions_soft_models()
        missing = code_models - script_models
        assert not missing, (
            f"SoftDeleteMixin models NOT in check_conventions.sh: {missing}. "
            f"Add them to SOFT_MODELS in scripts/check_conventions.sh"
        )

    def test_no_stale_models_in_conventions(self):
        """check_conventions.sh should not list models that no longer use SoftDeleteMixin."""
        code_models = _find_soft_delete_models()
        script_models = _get_conventions_soft_models()
        stale = script_models - code_models
        assert not stale, (
            f"Models in check_conventions.sh but NOT SoftDeleteMixin: {stale}. " f"Remove them from SOFT_MODELS."
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
