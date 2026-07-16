"""
Scheduler job: bulk-sync live WB supply states for all projects.

Зеркалит кнопку «Обновить» WB-панели в фоне: тянет справочник статусов +
поставки кабинета (listSupplies) и обновляет живое состояние (wb_supply_state)
локальных связей заявка↔WB-поставка. Заодно добирает авто-занос пропуска в WB
(F3+), когда дата забронирована и пропуск заполнен. Runs every 30 min.
"""

import asyncio
import logging

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids

logger = logging.getLogger("dds.scheduler")


async def sync_all_projects_wb_supply_states():
    """
    Iterate all projects and bulk-sync WB supply states. Проекты без WB-заявок
    возвращают {0,0,0} без похода в кабинет; без активной портал-сессии — тихо
    пропускаются (WbSupplyError → debug). Called by APScheduler every 30 min.
    """
    logger.info("WB supply states sync: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("WB supply states sync: no projects with WB keys, skipping")
        return

    from backend.services import wb_supply_service
    from backend.services.wb_supply_service import WbSupplyError

    ok = 0
    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                res = await wb_supply_service.sync_all_states(db, project_id)
            if res["checked"]:
                logger.info(
                    "WB supply states sync: project %d — %d checked, %d updated, "
                    "%d supplies, %d pass auto-pushed",
                    project_id,
                    res["checked"],
                    res["updated"],
                    res["supplies_seen"],
                    res.get("autopushed", 0),
                )
            ok += 1
        except WbSupplyError as e:
            # Нет портал-сессии / истекла / WB отклонил — не ошибка цикла.
            logger.debug("WB supply states sync: project %d skipped — %s", project_id, e)
        except asyncio.CancelledError:
            raise  # propagate cancellation — never swallow it to keep looping
        except Exception as e:
            logger.error(
                "WB supply states sync: project %d failed — %s",
                project_id,
                str(e),
                exc_info=True,
            )

    logger.info("WB supply states sync: done — %d projects processed", ok)
