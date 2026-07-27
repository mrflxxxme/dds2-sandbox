# ruff: noqa: RUF002 — русские комментарии и docstring
"""
DEV-утилита (не для прода): синк зеркала карточек WB + импорт КБ из карточек.

Зачем: на dev-машине прямой TLS-egress из docker-контейнера к WB режется
сетевым фильтром (DPI) — ходим через хостовый SOCKS5 (raw-сокет рукопожатие
в backend.services.wb_cards_service, httpx в контейнере без socksio).

Что делает: собирает distinct nm_id проекта из wb_product_kb/wb_questions/
wb_feedbacks (collect_project_nm_ids), скачивает card.json + detail для каждого
(троттлинг 0.5 сек), upsert'ит в wb_product_cards, затем прогоняет
import_kb_from_cards (source='card', дедуп/upsert по hash).

Публичные API WB, ключ продавца НЕ нужен и нигде не читается.

Использование:
    docker compose exec -T -e PYTHONPATH=/app backend \
        python -m scripts.dev_wb_cards_sync --project-id 1010888 \
        --proxy-host host.docker.internal --proxy-port 1080 [--limit 150 --offset 0]
"""

import argparse
import asyncio
import sys
import time


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--proxy-host", default="host.docker.internal")
    parser.add_argument("--proxy-port", type=int, default=1080)
    parser.add_argument("--limit", type=int, default=0, help="сколько nm_id обработать (0 — все)")
    parser.add_argument("--offset", type=int, default=0, help="сдвиг по отсортированному списку nm_id")
    parser.add_argument("--throttle", type=float, default=0.5, help="пауза между товарами, сек")
    args = parser.parse_args()

    from backend.database import AsyncSessionLocal
    from backend.services import wb_cards_service

    proxy = (args.proxy_host, args.proxy_port)

    async with AsyncSessionLocal() as db:
        nm_ids = await wb_cards_service.collect_project_nm_ids(db, args.project_id)
        total = len(nm_ids)
        if args.offset:
            nm_ids = nm_ids[args.offset :]
        if args.limit:
            nm_ids = nm_ids[: args.limit]
        print(f"nm_ids: total={total}, в прогоне={len(nm_ids)} (offset={args.offset})", flush=True)

        async def fetcher(nm: int) -> dict:
            return await asyncio.to_thread(wb_cards_service.fetch_nm_card, nm, proxy)

        t0 = time.monotonic()
        sync_res = await wb_cards_service.sync_project_cards(
            db, args.project_id, nm_ids, fetcher=fetcher, throttle_sec=args.throttle
        )
        print(
            f"sync: {sync_res} ({time.monotonic() - t0:.0f} сек)",
            flush=True,
        )

        imp_res = await wb_cards_service.import_kb_from_cards(db, args.project_id)
        print(f"kb_import: {imp_res}", flush=True)

    print("OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
