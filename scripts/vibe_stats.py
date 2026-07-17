#!/usr/bin/env python3
"""git → JSON для вкладки «Вайбкодинг». Гоняется в CI, где .git ещё доступен.

На проде репозитория НЕТ (.dockerignore исключает .git), поэтому статистику считает CI
после мёржа и отдаёт бэкенду через scripts/ingest_vibe.py.

    python3 scripts/vibe_stats.py --since 2026-07-01 > vibe.json
    python3 scripts/vibe_stats.py --last 30 --ref origin/main

Поставка = коммит, достижимый из ветки прода. Статус берётся ТОЛЬКО из git.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, timedelta

# Ветка, попадание в которую = деплой на прод (push в dev автомержится в main).
PROD_REF = "origin/main"

# Сгенерённое и залоченное — не работа человека.
NOISE = re.compile(r"(package-lock\.json|yarn\.lock|poetry\.lock|\.snap$|__snapshots__/)")

SUBJECT_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<title>.+)$")

# Продуктовая поставка — её видит пользователь DDS2.
PRODUCT_TYPES = {"feat", "fix", "perf"}
# ...но не в этих разделах: зелёный CI и починка миграции пользователю не видны.
INFRA_SCOPES = {"deps", "mypy", "ci", "tests", "migrations", "docs"}


def git(*args: str) -> str:
    res = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        sys.exit(f"git {' '.join(args)}:\n{res.stderr.strip()}")
    return res.stdout


def collect_new_paths(ref: str, lo: str) -> dict[str, set[str]]:
    """sha → файлы, созданные с нуля.

    Отдельным запросом, потому что --numstat этого не отличает: у нового файла удалений
    0, но и у чисто дополненного тоже.
    """
    out = git("log", ref, "--no-merges", "--diff-filter=A", "--format=%x1e%H",
              "--name-only", f"--since={lo}")
    res: dict[str, set[str]] = {}
    for record in out.split("\x1e"):
        lines = [ln for ln in record.strip("\n").split("\n") if ln.strip()]
        if not lines:
            continue
        res[lines[0]] = {p for p in lines[1:] if not NOISE.search(p)}
    return res


def patch_ids(ref: str, lo: str) -> dict[str, str]:
    """sha → отпечаток диффа. Одна работа может лежать в истории НЕСКОЛЬКО раз.

    Бот-автомёрж сквошит ветку в main, сквош возвращается в dev через «Merge branch
    'main' into dev» — и рядом с оригиналом `574eadf6` (citrus37) появляется
    `a571d022` «…(#682)» под GitHub-аккаунтом. Тот же эффект дают черри-пик и ребейз.
    Считать это двумя поставками — врать.

    Заголовок ключом не годится: у одного человека бывают два РАЗНЫХ коммита с одним
    заголовком. patch-id — отпечаток самого диффа, он не гадает по тексту.

    Сквош МНОГИХ коммитов в один даёт свой уникальный дифф и ни с чем не совпадёт —
    и правильно: для 98 из 100 таких сквошей это единственная запись работы в истории.
    """
    out = subprocess.run(
        f"git log {ref} --no-merges -p --format='%H' --since={lo} | git patch-id --stable",
        shell=True, capture_output=True, text=True, check=False,
    ).stdout
    res: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            pid, sha = parts
            res[sha] = pid
    return res


def collect(ref: str, since: date, until: date) -> list[dict]:
    # git фильтрует --since/--until по дате КОММИТЕРА, а нам нужна дата АВТОРА: ребейз
    # на ушедший dev перебивает коммитерскую днём пуша, и работа, сделанная во вторник,
    # уехала бы в четверг. Берём широко и режем по author date здесь.
    lo = (since - timedelta(days=90)).isoformat()
    new_by_sha = collect_new_paths(ref, lo)
    pids = patch_ids(ref, lo)

    fmt = "%x1e%H%x1f%ae%x1f%ad%x1f%s"
    out = git("log", ref, "--no-merges", "--date=short", f"--format={fmt}",
              "--numstat", f"--since={lo}")

    commits: list[dict] = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        head, *stat_lines = record.split("\n")
        sha, email, day, subject = head.split("\x1f")
        authored = date.fromisoformat(day)
        if not (since <= authored <= until):
            continue

        m = SUBJECT_RE.match(subject)
        if not m:
            # Не conventional commit (стеши, остатки мёржей) — не поставка.
            continue
        ctype = m["type"]
        # `fix(ads,raw-data):` — считаем по первому разделу.
        scope = (m["scope"] or "").split(",")[0].strip()

        files: list[dict] = []
        added = deleted = 0
        new_paths = new_by_sha.get(sha, set())
        for line in stat_lines:
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            if NOISE.search(path) or a == "-":  # бинарники дают "-"
                continue
            files.append({
                "path": path[:500],
                "added": int(a),
                "deleted": int(d),
                "is_new": path in new_paths,
            })
            added += int(a)
            deleted += int(d)

        commits.append({
            "sha": sha,
            "patch_id": pids.get(sha),
            "author_email": email.lower(),
            "authored_on": authored.isoformat(),
            "ctype": ctype[:20],
            "scope": scope[:50],
            "title": m["title"],
            "added": added,
            "deleted": deleted,
            "files": len(files),
            "is_product": ctype in PRODUCT_TYPES and scope not in INFRA_SCOPES,
            "files_list": files,
        })
    return dedupe(commits)


# GitHub-аккаунты автоматики: под ними лежат сквоши чужой работы, а не своя.
BOT_EMAIL = re.compile(r"users\.noreply\.github\.com$")
# Сквош через PR дописывает в заголовок «(#682)» — метка копии, а не оригинала.
SQUASH_SUFFIX = re.compile(r"\s\(#\d+\)$")


def _origin_rank(c: dict) -> tuple:
    """Чем меньше — тем «оригинальнее». Из копий одной работы берём лучшую по этому ключу.

    Дата автора у оригинала и сквоша ЧАСТО СОВПАДАЕТ (сквош её сохраняет), поэтому
    одной даты мало — нужны ещё два признака: суффикс `(#N)` и бот-аккаунт.
    """
    return (
        c["authored_on"],                              # раньше = оригинальнее
        1 if SQUASH_SUFFIX.search(c["title"]) else 0,  # без «(#N)» = оригинал
        1 if BOT_EMAIL.search(c["author_email"]) else 0,  # живой автор важнее бота
    )


def dedupe(commits: list[dict]) -> list[dict]:
    """Одна работа = одна поставка, сколько бы копий её ни лежало в истории."""
    best: dict[str, dict] = {}
    out: list[dict] = []
    for c in commits:
        pid = c.pop("patch_id", None)
        if not pid:  # пустой дифф (мёрж-остаток) — не с чем сверять
            out.append(c)
            continue
        prev = best.get(pid)
        if prev is None or _origin_rank(c) < _origin_rank(prev):
            best[pid] = c
    return out + list(best.values())


def main() -> None:
    p = argparse.ArgumentParser(description="git → JSON для вкладки «Вайбкодинг»")
    p.add_argument("--ref", default=PROD_REF, help=f"ветка прода (по умолчанию {PROD_REF})")
    p.add_argument("--since", help="дата начала YYYY-MM-DD")
    p.add_argument("--until", default=date.today().isoformat(), help="дата конца")
    p.add_argument("--last", type=int, metavar="N", help="за последние N дней")
    args = p.parse_args()

    until = date.fromisoformat(args.until)
    if args.last:
        since = until - timedelta(days=args.last - 1)
    elif args.since:
        since = date.fromisoformat(args.since)
    else:
        sys.exit("нужен --since или --last")

    commits = collect(args.ref, since, until)
    json.dump({"commits": commits}, sys.stdout, ensure_ascii=False)
    print(f"vibe_stats: {len(commits)} поставок за {since}..{until}", file=sys.stderr)


if __name__ == "__main__":
    main()
