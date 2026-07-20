#!/usr/bin/env python3
"""JSON от CI → БД. Читает stdin, кладёт статистику вайбкодинга.

Зовётся воркфлоу .github/workflows/vibe-stats.yml по SSH внутри контейнера:

    docker compose -f docker-compose.app.yml exec -T -w /app backend \\
        python3 -m scripts.ingest_vibe < vibe.json

Формат stdin — вывод scripts/vibe_stats.py: {"commits": [...]}.
Идемпотентно: UPSERT по sha, повторный прогон тех же коммитов не задваивает.
"""

from __future__ import annotations

import asyncio
import json
import sys


async def main() -> int:
    payload = json.load(sys.stdin)
    commits = payload.get("commits", [])
    if not commits:
        print("ingest_vibe: пусто, нечего писать")
        return 0

    # Импорты внутри: скрипт запускается из /app, где backend уже на sys.path.
    from backend.database import AsyncSessionLocal
    from backend.schemas.vibe import VibeIngestRequest
    from backend.services import vibe_service

    # Валидируем через VibeIngestRequest (он проверит вложенные files_list),
    # а сервису отдаём список — такова его сигнатура.
    req = VibeIngestRequest(commits=commits)
    async with AsyncSessionLocal() as db:
        res = await vibe_service.ingest(db, req.commits)

    print(
        f"ingest_vibe: получено {res.received}, добавлено {res.inserted}, "
        f"обновлено {res.updated}, файлов {res.files}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
