# ruff: noqa: RUF002
"""Scheduler job: sync WB supplier orders → таблица `wb_orders`.

Расписание: 03:30, 09:30, 15:30 MSK (3×/день). WB Statistics API rate-limit
~1 req/min на ключ → запросы по проектам идут последовательно.

Каждый прогон:
- Тянет supplier orders за последние 30 дней.
- Идемпотентный UPSERT по (project_id, srid).
- Обновляет is_cancel / cancel_date / warehouse_* / last_change_date.
- Инвалидирует кэш отчётов локализации.

Используется отчётом «Индекс локализации» (per-district breakdown).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids
from backend.services.wb_orders_sync_service import sync_wb_orders

logger = logging.getLogger("dds.scheduler")


async def sync_all_projects_wb_orders() -> dict[str, Any]:
    """Iterate all projects with active WB API keys and sync supplier orders.

    Called by APScheduler 3×/day at 03:30, 09:30, 15:30 MSK.
    """
    started = time.monotonic()
    logger.info("wb_orders sync: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("wb_orders sync: no projects with WB keys, skipping")
        return {"projects": 0, "ok": 0, "errors": 0}

    ok = 0
    errors = 0
    total_inserted = 0
    total_updated = 0

    for project_id in project_ids:
        proj_started = time.monotonic()
        try:
            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    sync_wb_orders(db, project_id, days_back=30),
                    timeout=600,
                )
            total_inserted += int(result.get("inserted", 0))
            total_updated += int(result.get("updated", 0))
            duration = time.monotonic() - proj_started
            logger.info(
                "wb_orders sync: project %d done — inserted=%d updated=%d in %.1fs",
                project_id,
                result.get("inserted", 0),
                result.get("updated", 0),
                duration,
            )
            if result.get("errors"):
                logger.warning(
                    "wb_orders sync: project %d had partial errors: %s",
                    project_id,
                    "; ".join(result.get("errors", [])[:3]),
                )
            ok += 1
        except TimeoutError:
            errors += 1
            logger.error("wb_orders sync: project %d TIMEOUT (10 min)", project_id)
        except asyncio.CancelledError:
            # MUST come BEFORE except Exception (DDS2 scheduler rule).
            raise
        except Exception as e:
            errors += 1
            logger.error(
                "wb_orders sync: project %d failed — %s",
                project_id,
                str(e),
                exc_info=True,
            )

    total_duration = time.monotonic() - started
    logger.info(
        "wb_orders sync: done — projects=%d ok=%d errors=%d " "inserted=%d updated=%d total_took=%.1fs",
        len(project_ids),
        ok,
        errors,
        total_inserted,
        total_updated,
        total_duration,
    )
    return {
        "projects": len(project_ids),
        "ok": ok,
        "errors": errors,
        "inserted": total_inserted,
        "updated": total_updated,
    }
