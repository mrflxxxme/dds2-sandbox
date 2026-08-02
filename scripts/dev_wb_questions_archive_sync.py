# ruff: noqa: RUF002 — русские комментарии и docstring
"""
DEV-утилита (не для прода): досинк АРХИВНЫХ (отвеченных) вопросов WB через SOCKS5.

Зачем: штатный dev-синк `dev_wb_socks_sync.py` тянет по одной странице каждого
типа, а архив отвеченных вопросов (isAnswered=true) — тысячи записей, нужных
для импорта базы знаний (wb_product_kb). Скрипт листает ВСЕ страницы архива
(take ≤ 10000, лимит WB 1 rps → пауза 1.1 сек между страницами) и upsert'ит
через ТЕ ЖЕ сервисные функции (`_row_from_question`/`_upsert_question_rows`),
что и штатный синк. Коммит — постранично (прогресс не теряется при обрыве).

WB-ключ читается из БД (integration_keys.id, расшифровка через
backend.utils.crypto.decrypt) — НЕ печатается, НЕ пишется в файлы.

Использование:
    docker compose exec -T -e PYTHONPATH=/app backend \
        python -m scripts.dev_wb_questions_archive_sync \
        --project-id 1010888 --key-id 744
"""

import argparse
import asyncio
import sys
import time

from sqlalchemy import select

from scripts.dev_wb_socks_sync import _wb_get

_THROTTLE_SEC = 1.1  # лимит WB на методы отзывов/вопросов — 1 rps


async def _load_wb_key(key_id: int) -> str:
    """Расшифровать WB-ключ из integration_keys (в логи/stdout не выводится)."""
    from backend.database import AsyncSessionLocal
    from backend.models import IntegrationKey
    from backend.utils.crypto import decrypt

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(IntegrationKey).where(IntegrationKey.id == key_id))
        ).scalar_one_or_none()
        if row is None:
            raise RuntimeError(f"integration_keys id={key_id} не найден")
        return decrypt(row.encrypted_key)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--key-id", type=int, required=True, help="id записи integration_keys")
    parser.add_argument("--proxy-host", default="host.docker.internal")
    parser.add_argument("--proxy-port", type=int, default=1080)
    parser.add_argument("--take", type=int, default=10000, help="размер страницы (макс 10000)")
    parser.add_argument("--max-pages", type=int, default=50, help="защита от runaway-пагинации")
    args = parser.parse_args()

    api_key = await _load_wb_key(args.key_id)

    from backend.database import AsyncSessionLocal
    from backend.services.reply_service import _row_from_question, _upsert_question_rows
    from backend.utils.time import utcnow

    total_fetched = total_upserted = 0
    archive_total: int | None = None
    async with AsyncSessionLocal() as db:
        for page in range(args.max_pages):
            if page:
                await asyncio.sleep(_THROTTLE_SEC)
            t0 = time.monotonic()
            data = await asyncio.to_thread(
                _wb_get, args.proxy_host, args.proxy_port, api_key,
                f"/api/v1/questions?isAnswered=true&take={args.take}"
                f"&skip={page * args.take}&order=dateDesc",
            )
            questions = data.get("questions") or []
            if archive_total is None:
                archive_total = data.get("countArchive")

            now = utcnow()
            rows: dict[str, dict] = {}
            for q in questions:
                if isinstance(q, dict):
                    row = _row_from_question(args.project_id, q, now)
                    if row:
                        rows[row["wb_id"]] = row
            upserted = await _upsert_question_rows(db, list(rows.values()))
            await db.commit()
            total_fetched += len(rows)
            total_upserted += upserted
            print(
                f"page {page}: fetched={len(rows)} upserted={upserted} "
                f"({time.monotonic() - t0:.1f}s)",
                flush=True,
            )
            if len(questions) < args.take:
                break

    print(
        f"OK: archive questions fetched={total_fetched} upserted={total_upserted} "
        f"(archive_total={archive_total})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
