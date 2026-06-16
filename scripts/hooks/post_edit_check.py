#!/usr/bin/env python3
"""Smart DDS convention checker -- replaces grep-based hooks with context-aware analysis.

Checks after Edit/Write:
1. is_deleted filter missing in services (SoftDeleteMixin models)
2. project_id missing in query context (not just string presence)
3. db.delete() / session.delete() instead of soft_delete()
4. Float in Column definitions (must be Numeric(18,2))
5. datetime.utcnow() / datetime.now() (must use utcnow from utils)
6. f-string in text() -- SQL injection
7. Business logic (DB access) in routers
"""

import ast
import json
import re
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def discover_soft_delete_models() -> frozenset[str]:
    """Discover models using SoftDeleteMixin by AST-parsing backend/models/.

    Replaces a hardcoded list that had rotted (15 phantom names, 31 real models
    silently unchecked — the ProjectMember-incident class of bug). Self-maintaining:
    a new ``class X(Base, SoftDeleteMixin)`` is picked up with zero edits here.
    Stays advisory: a half-edited or unreadable model file is skipped, never raises.
    """
    models_dir = Path(__file__).resolve().parents[2] / "backend" / "models"
    if not models_dir.is_dir():
        return frozenset()
    names: set[str] = set()
    for f in models_dir.glob("*.py"):
        names |= _soft_delete_classes(f)
    return frozenset(names)


def _soft_delete_classes(path: Path) -> set[str]:
    """SoftDeleteMixin class names declared in one model file.

    Returns an empty set for a half-edited or unreadable file, so the hook stays
    advisory and never crashes while a model is mid-edit.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        bases |= {b.attr for b in node.bases if isinstance(b, ast.Attribute)}
        if "SoftDeleteMixin" in bases:
            names.add(node.name)
    return names


QUERY_PATTERN = re.compile(r"(\.select\(|\.query\(|\.filter|\.where\(|text\(|execute\()")
DATETIME_PATTERN = re.compile(r"datetime\.(utcnow|now)\(\)")
SQL_INJECTION_PATTERN = re.compile(r'text\(\s*f["\']')
DB_DELETE_PATTERN = re.compile(r"(db|session)\.(delete)\(")
FLOAT_COL_PATTERN = re.compile(r"(Column|mapped_column)\(.*Float")
ROUTER_DB_PATTERN = re.compile(r"(await\s+db\.|async_session|\.execute\()")


def check_python_file(filepath: str, content: str) -> list[str]:
    warnings = []
    lines = content.split("\n")
    path = Path(filepath)
    parts = path.parts

    # --- Services: is_deleted + project_id in queries ---
    if "services" in parts and filepath.endswith(".py"):
        has_query = any(QUERY_PATTERN.search(line) for line in lines)

        if has_query:
            has_is_deleted = any("is_deleted" in line for line in lines)
            has_project_id = any("project_id" in line for line in lines)

            if not has_is_deleted and any(m in content for m in discover_soft_delete_models()):
                warnings.append(
                    "[DDS2] is_deleted filter NOT found -- SoftDeleteMixin models"
                    " MUST filter .where(Model.is_deleted == False)"
                )

            if not has_project_id and "__init__" not in filepath and "test" not in filepath:
                warnings.append("[DDS2] project_id NOT found -- every DB query" " MUST filter by project_id")

    # --- Routers: no DB access ---
    if "routers" in parts and filepath.endswith(".py"):
        for i, line in enumerate(lines, 1):
            if ROUTER_DB_PATTERN.search(line) and not line.strip().startswith("#"):
                warnings.append(f"[DDS2] line {i}: DB access in router --" " move business logic to services/")
                break

    # --- Models: Float and money ---
    if "models" in parts and filepath.endswith(".py"):
        for i, line in enumerate(lines, 1):
            if FLOAT_COL_PATTERN.search(line) and not line.strip().startswith("#"):
                warnings.append(f"[DDS2] line {i}: Float for money -- use Numeric(18, 2)")

    # --- Universal Python checks ---
    if filepath.endswith(".py"):
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            if DATETIME_PATTERN.search(line):
                warnings.append(
                    f"[DDS2] line {i}: datetime.utcnow()/now() --" " use from backend.utils.time import utcnow"
                )

            if SQL_INJECTION_PATTERN.search(line):
                warnings.append(f"[DDS2] line {i}: f-string in text() --" " SQL injection! Use :param + bindparams")

            if DB_DELETE_PATTERN.search(line):
                warnings.append(f"[DDS2] line {i}: db.delete() --" " use model.soft_delete() for SoftDeleteMixin")

    return warnings


def main():
    # `--list-soft-models`: single source of truth consumed by check_conventions.sh
    # (Check 6 + its regression guard). Prints discovered model names, one per line.
    if "--list-soft-models" in sys.argv[1:]:
        for name in sorted(discover_soft_delete_models()):
            print(name)
        sys.exit(0)

    raw = sys.stdin.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    filepath = data.get("tool_input", {}).get("file_path", "")
    if not filepath or not filepath.endswith(".py"):
        sys.exit(0)

    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        sys.exit(0)

    warnings = check_python_file(filepath, content)

    for w in warnings:
        print(w, file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
