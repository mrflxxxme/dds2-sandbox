#!/usr/bin/env python3
"""Frontend convention checker for DDS2 (Next.js 15 / React 19).

Advisory PostToolUse hook for frontend-react/**/*.ts(x). Always exits 0 — it
prints hints to stderr, never blocks the edit loop. Mirrors post_edit_check.py.

Only two HIGH-signal / LOW-noise checks are enforced (measured against the live
codebase: 2 and 3 offending files respectively). Manual number formatting
(.toFixed / .toLocaleString) is intentionally NOT flagged — too common to be
useful as an edit-time nag; clean it up as a one-off task instead.

1. Raw fetch() outside src/lib/api -- requests must go through the api client.
2. A data-fetching page.tsx with no loading/error state references.
"""

import json
import re
import sys

FETCH_PATTERN = re.compile(r"(?<![.\w])fetch\(")
DATA_FETCH_PATTERN = re.compile(r"useQuery|useSWR|useMutation|\bapi\.")
STATE_PATTERN = re.compile(r"isLoading|loading|isError|\berror\b|isPending")


def check_frontend_file(filepath: str, content: str) -> list[str]:
    warnings: list[str] = []
    norm = filepath.replace("\\", "/")

    # 1. Raw fetch() outside the api client.
    if "/lib/api" not in norm and "// ok-fetch" not in content:
        if any(FETCH_PATTERN.search(line) for line in content.split("\n") if not line.strip().startswith("//")):
            warnings.append(
                "[DDS2-FE] raw fetch() -- route requests through src/lib/api/*"
                " (typed client). Suppress: // ok-fetch"
            )

    # 2. A data-fetching page with no loading/error state handling.
    if norm.endswith("page.tsx") and "// ok-no-states" not in content:
        if DATA_FETCH_PATTERN.search(content) and not STATE_PATTERN.search(content):
            warnings.append(
                "[DDS2-FE] page fetches data but has no loading/error/empty state --"
                " every page needs loading / error / empty / data. Suppress: // ok-no-states"
            )

    return warnings


def main() -> None:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    filepath = data.get("tool_input", {}).get("file_path", "")
    norm = filepath.replace("\\", "/")
    if not norm.endswith((".ts", ".tsx")) or "frontend-react" not in norm:
        sys.exit(0)

    try:
        with open(filepath, encoding="utf-8") as fh:
            content = fh.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        sys.exit(0)

    for w in check_frontend_file(filepath, content):
        print(w, file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
