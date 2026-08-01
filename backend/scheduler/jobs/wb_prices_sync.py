# ruff: noqa: RUF001, RUF002, RUF003
"""Scheduler job: синк текущих цен витрины ВБ для всех проектов с активным WB-ключом.

Запускается 2×/день. Питает страницу «Ценообразование» (наценка по артикулам).
"""

import asyncio
import logging

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids

logger = logging.getLogger("dds.scheduler")


async def sync_all_projects_wb_prices():
    """Пройтись по всем проектам с активным WB-ключом и синкнуть цены."""
    logger.info("WB prices sync: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("WB prices sync: no projects with WB keys, skipping")
        return

    from backend.services.integrations_service import _get_wb_key
    from backend.services.pricing.sync import sync_wb_prices

    ok = 0
    errors = 0

    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                try:
                    await _get_wb_key(db, project_id)
                except ValueError:
                    logger.debug("WB prices sync: project %d has no WB key, skipping", project_id)
                    continue

                sync_log = await asyncio.wait_for(sync_wb_prices(db, project_id), timeout=600)
                if sync_log.status == "OK":
                    logger.info(
                        "WB prices sync: project %d — %d rows", project_id, sync_log.rows_inserted
                    )
                    ok += 1
                    # После цен витрины — реальная цена покупателя с СПП из
                    # публичного card-API (без ключа, best-effort: флак не валит синк).
                    try:
                        from backend.services.pricing.sync import sync_card_spp

                        spp = await asyncio.wait_for(sync_card_spp(db, project_id), timeout=600)
                        logger.info(
                            "card-СПП sync: project %d — %d/%d nm",
                            project_id, spp.get("fetched", 0), spp.get("requested", 0),
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning("card-СПП sync: project %d failed — %s", project_id, str(e))
                else:
                    logger.warning(
                        "WB prices sync: project %d — ERROR %s", project_id, sync_log.error_msg
                    )
                    errors += 1

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("WB prices sync: project %d failed — %s", project_id, str(e), exc_info=True)
            errors += 1

    logger.info("WB prices sync: done — %d ok, %d errors", ok, errors)


async def snapshot_all_projects_spp():
    """Часовой снимок СПП витрины по всем проектам, у которых есть цены.

    Ключ WB не нужен — card-API публичный, а список nm берём из уже синканых
    `wb_prices`. Поэтому джоб не гейтится на активный ключ интеграции: даже если
    ключ отключили, лестница СПП продолжает наполняться.
    """
    from sqlalchemy import distinct, select

    from backend.models import WbPrice
    from backend.services.pricing.spp_points import snapshot_from_card

    async with AsyncSessionLocal() as db:
        project_ids = list(
            (await db.execute(select(distinct(WbPrice.project_id)))).scalars().all()
        )

    if not project_ids:
        logger.info("СПП-снимок: нет проектов с ценами, пропускаем")
        return

    ok = errors = written = 0
    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                res = await asyncio.wait_for(snapshot_from_card(db, project_id), timeout=900)
            written += res.get("written", 0)
            ok += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("СПП-снимок: project %d failed — %s", project_id, e)
            errors += 1

    logger.info("СПП-снимок: %d проектов ok, %d ошибок, %d точек", ok, errors, written)
