"""
One-off backfill: для ACCEPTED-поставок без items вызвать WB goods API и заполнить items.

Запуск:
  docker exec dds_app-worker-1 python /tmp/fbo_backfill_items.py [LIMIT] [PROJECT_ID]

LIMIT=5 — dry-run-подобный режим (обработать только 5 поставок).
LIMIT=0 или отсутствует — все.
PROJECT_ID — если задан, фильтр только по этому проекту.

Логи: stdout + file (backfill.log в CWD).
Идемпотентность: после успешной обработки ставим synced_at=utcnow() и создаём items, повторный запуск
скипнет эту поставку (items уже есть). При ошибке — rollback, supply не тронут, лог записан.
"""

import asyncio
import logging
import sys
from datetime import datetime

from sqlalchemy import and_, exists, not_, select

from backend.database import AsyncSessionLocal
from backend.integrations.wb_api import WBApiClient
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem, WbSupplyStatus
from backend.services.fbo_supply.mappers import _upsert_supply_items_fbw
from backend.services.integrations_service import _get_wb_key
from backend.utils.time import utcnow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("fbo_backfill")

RATE_LIMIT_DELAY = 11  # seconds between WB API calls per project


async def process_project(project_id: int, supply_ids: list[int]) -> tuple[int, int]:
    """Process all supplies for one project with own rate limiting."""
    ok = 0
    err = 0
    api_client = None

    async with AsyncSessionLocal() as db:
        try:
            _key, api_key = await _get_wb_key(db, project_id)
        except ValueError:
            log.warning("project=%s: no WB key → skip %d supplies", project_id, len(supply_ids))
            return 0, len(supply_ids)
        api_client = WBApiClient(api_key, project_id=project_id)

    total = len(supply_ids)
    for idx, supply_id in enumerate(supply_ids, start=1):
        try:
            async with AsyncSessionLocal() as db:
                supply = (await db.execute(select(WbFboSupply).where(WbFboSupply.id == supply_id))).scalar_one_or_none()
                if supply is None:
                    log.warning("supply_db_id=%s not found → skip", supply_id)
                    continue

                wb_id = int(supply.wb_supply_id)

                has_items = (
                    await db.execute(select(WbFboSupplyItem.id).where(WbFboSupplyItem.supply_id == supply_id).limit(1))
                ).scalar_one_or_none()
                if has_items:
                    log.info(
                        "[%d/%d] project=%s supply=%s already has items → skip",
                        idx,
                        total,
                        project_id,
                        wb_id,
                    )
                    ok += 1
                    continue

                goods = await api_client.get_fbw_supply_goods(wb_id, limit=100, offset=0)
                if not goods:
                    log.warning(
                        "[%d/%d] project=%s supply=%s: goods API returned empty",
                        idx,
                        total,
                        project_id,
                        wb_id,
                    )
                    err += 1
                    continue

                await _upsert_supply_items_fbw(db, project_id, supply_id, wb_id, goods)
                supply.accepted_qty = sum(int(g.get("acceptedQuantity") or 0) for g in goods)
                supply.total_qty = sum(int(g.get("quantity") or 0) for g in goods)
                supply.synced_at = utcnow()
                await db.commit()
                log.info(
                    "[%d/%d] project=%s supply=%s: %d items, accepted=%d/%d",
                    idx,
                    total,
                    project_id,
                    wb_id,
                    len(goods),
                    supply.accepted_qty,
                    supply.total_qty,
                )
                ok += 1

        except Exception as e:
            err += 1
            log.error(
                "[%d/%d] project=%s supply_db_id=%s FAILED: %s",
                idx,
                total,
                project_id,
                supply_id,
                str(e)[:200],
            )

        await asyncio.sleep(RATE_LIMIT_DELAY)

    return ok, err


async def main(limit: int = 0, project_filter: int | None = None) -> None:
    started = datetime.utcnow()
    log.info("fbo_backfill starting limit=%s project=%s", limit or "ALL", project_filter or "ALL")

    async with AsyncSessionLocal() as db:
        conditions = [
            WbFboSupply.wb_status == WbSupplyStatus.ACCEPTED,
            not_(
                exists().where(WbFboSupplyItem.supply_id == WbFboSupply.id),
            ),
        ]
        if project_filter is not None:
            conditions.append(WbFboSupply.project_id == project_filter)

        q = (
            select(WbFboSupply.id, WbFboSupply.project_id)
            .where(and_(*conditions))
            .order_by(WbFboSupply.project_id, WbFboSupply.id)
        )

        if limit:
            q = q.limit(limit)

        rows = (await db.execute(q)).all()

    by_project: dict[int, list[int]] = {}
    for supply_id, project_id in rows:
        by_project.setdefault(project_id, []).append(supply_id)

    log.info(
        "TOTAL %d supplies across %d projects: %s",
        len(rows),
        len(by_project),
        {p: len(v) for p, v in by_project.items()},
    )

    tasks = [process_project(pid, ids) for pid, ids in by_project.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total_ok = 0
    total_err = 0
    for (pid, _ids), res in zip(by_project.items(), results, strict=False):
        if isinstance(res, Exception):
            log.error("project=%s crashed: %s", pid, res)
            total_err += len(_ids)
        else:
            ok, err = res
            total_ok += ok
            total_err += err
            log.info("project=%s DONE ok=%d err=%d", pid, ok, err)

    elapsed = (datetime.utcnow() - started).total_seconds()
    log.info("fbo_backfill DONE: ok=%d err=%d in %.0fs", total_ok, total_err, elapsed)


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    project_filter = int(sys.argv[2]) if len(sys.argv) > 2 else None
    asyncio.run(main(limit, project_filter))
