"""Unified Ad Sync orchestrator — combines campaigns + funnel sync with progress tracking.

Provides:
- run_unified_ad_sync_bg: campaigns → budgets → funnel sync (sequential)
- run_first_sync_bg: nomenclature → campaigns → funnel 30d → backfill 60d
- In-memory progress dicts for polling from frontend
"""

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from backend.utils.time import utcnow

logger = logging.getLogger("dds.funnel")

# In-memory progress: {project_id: {phase, detail, error, ...}}
_unified_progress: dict[int, dict[str, Any]] = {}
_first_sync_progress: dict[int, dict[str, Any]] = {}


# ─── Unified Ad Sync ─────────────────────────────────────────────────────────


def get_unified_sync_progress(project_id: int) -> dict:
    """Get current unified sync progress for a project."""
    return _unified_progress.get(project_id, {"phase": "idle"})


async def run_unified_ad_sync_bg(project_id: int, date_from: str, date_to: str) -> None:
    """Run unified ad sync in background: campaigns → funnel.

    Sequential pipeline:
    1. sync_ad_campaigns (list + statuses + budgets)
    2. run_funnel_sync (funnel for the date range)
    """
    from backend.database import AsyncSessionLocal

    try:
        # Phase 1: Campaigns + budgets
        _unified_progress[project_id] = {
            "phase": "campaigns",
            "detail": "Загрузка списка кампаний...",
            "started_at": utcnow().isoformat(),
        }

        async with AsyncSessionLocal() as db:
            # Proxy progress from ad_campaigns_service into unified progress
            from backend.services.funnel.ad_campaigns_service import (
                get_sync_progress as _get_camp_progress,
                sync_ad_campaigns,
            )

            async def _poll_campaigns():
                """Background task to proxy campaign sync progress."""
                while True:
                    await asyncio.sleep(2)
                    p = _get_camp_progress(project_id)
                    status = p.get("status", "idle")
                    if status == "fetching_campaigns":
                        _unified_progress[project_id]["phase"] = "campaigns"
                        _unified_progress[project_id]["detail"] = "Загрузка списка кампаний..."
                    elif status == "fetching_budgets":
                        _unified_progress[project_id]["phase"] = "budgets"
                        _unified_progress[project_id]["budgets_done"] = p.get("budgets_done", 0)
                        _unified_progress[project_id]["budgets_total"] = p.get("budgets_total", 0)
                        _unified_progress[project_id]["campaigns_total"] = p.get("campaigns_total", 0)
                    elif status == "saving":
                        _unified_progress[project_id]["detail"] = "Сохранение кампаний..."
                    elif status in ("done", "idle", "error"):
                        break

            poll_task = asyncio.create_task(_poll_campaigns())
            campaigns_result = await sync_ad_campaigns(db, project_id)
            poll_task.cancel()

        _unified_progress[project_id] = {
            "phase": "funnel",
            "campaigns_total": campaigns_result.get("synced", 0),
            "funnel_days_done": 0,
            "funnel_days_total": 0,
            "detail": f"Кампании готовы ({campaigns_result.get('synced', 0)}). Загрузка воронки...",
        }

        # Phase 2: Funnel sync — proxy progress from _funnel_sync_progress
        async with AsyncSessionLocal() as db:
            from backend.services.funnel.sync import get_funnel_sync_progress, run_funnel_sync

            async def _poll_funnel():
                """Background task to proxy funnel sync progress."""
                while True:
                    await asyncio.sleep(2)
                    p = get_funnel_sync_progress(project_id)
                    status = p.get("status", "idle")
                    _unified_progress[project_id]["funnel_days_done"] = p.get("days_done", 0)
                    _unified_progress[project_id]["funnel_days_total"] = p.get("days_total", 0)
                    if status == "fetching_ads":
                        _unified_progress[project_id]["detail"] = p.get("detail", "Загрузка рекламных данных...")
                    elif status == "fetching_funnel":
                        _unified_progress[project_id]["detail"] = (
                            f"Воронка: {p.get('days_done', 0)} / {p.get('days_total', 0)} дней"
                        )
                    elif status == "saving":
                        _unified_progress[project_id]["detail"] = "Сохранение данных..."
                    elif status in ("done", "error"):
                        break

            poll_task = asyncio.create_task(_poll_funnel())
            funnel_result = await run_funnel_sync(db, project_id, date_from, date_to)
            poll_task.cancel()

        _unified_progress[project_id] = {
            "phase": "done",
            "detail": "Синхронизация завершена",
            "campaigns_total": campaigns_result.get("synced", 0),
            "funnel_days_done": funnel_result.get("days", 0),
            "funnel_days_total": funnel_result.get("days", 0),
            "rows": funnel_result.get("rows", 0),
            "finished_at": utcnow().isoformat(),
        }
        logger.info(
            f"Unified sync done for project {project_id}: "
            f"{campaigns_result.get('synced', 0)} campaigns, "
            f"{funnel_result.get('rows', 0)} funnel rows"
        )

    except Exception as e:
        _unified_progress[project_id] = {
            "phase": "error",
            "error": str(e)[:300],
            "finished_at": utcnow().isoformat(),
        }
        logger.error(f"Unified sync failed for project {project_id}: {e}")


# ─── First Sync Pipeline ────────────────────────────────────────────────────


def get_first_sync_progress(project_id: int) -> dict:
    """Get current first sync progress for a project."""
    return _first_sync_progress.get(project_id, {"phase": "idle"})


async def run_first_sync_bg(project_id: int) -> None:
    """Run first-time sync pipeline for a newly connected project.

    Sequential:
    1. Nomenclature (barcodes, brands, categories)
    2. Ad campaigns + budgets
    3. Funnel — last 30 days
    4. Signal done
    5. asyncio.create_task — backfill remaining 60 days
    """
    from backend.database import AsyncSessionLocal

    try:
        # Phase 1: Nomenclature
        _first_sync_progress[project_id] = {
            "phase": "nomenclature",
            "detail": "Загрузка номенклатуры...",
            "started_at": utcnow().isoformat(),
        }

        async with AsyncSessionLocal() as db:
            from backend.services.integrations_service import sync_wb_nomenclature

            try:
                nom_result = await sync_wb_nomenclature(db, project_id)
                nom_count = nom_result.rows_inserted if nom_result else 0
            except Exception as e:
                logger.warning(f"First sync: nomenclature failed for project {project_id}: {e}")
                nom_count = 0

        # Phase 2: Campaigns + budgets
        _first_sync_progress[project_id] = {
            "phase": "campaigns",
            "detail": f"Номенклатура загружена ({nom_count}). Синхронизация кампаний...",
            "nomenclature_count": nom_count,
        }

        async with AsyncSessionLocal() as db:
            from backend.services.funnel.ad_campaigns_service import sync_ad_campaigns

            try:
                campaigns_result = await sync_ad_campaigns(db, project_id)
                campaigns_count = campaigns_result.get("synced", 0)
            except Exception as e:
                logger.warning(f"First sync: campaigns failed for project {project_id}: {e}")
                campaigns_count = 0

        # Phase 3: Funnel — last 30 days
        today = date.today()
        date_from_30 = (today - timedelta(days=30)).isoformat()
        date_to = today.isoformat()

        _first_sync_progress[project_id] = {
            "phase": "funnel",
            "detail": f"Кампании ({campaigns_count}). Загрузка воронки за 30 дней...",
            "nomenclature_count": nom_count,
            "campaigns_count": campaigns_count,
        }

        async with AsyncSessionLocal() as db:
            from backend.services.funnel.sync import run_funnel_sync

            funnel_result = await run_funnel_sync(db, project_id, date_from_30, date_to)
            funnel_rows = funnel_result.get("rows", 0)

        # Phase 4: Done signal
        _first_sync_progress[project_id] = {
            "phase": "done",
            "detail": "Первичная синхронизация завершена. Фоновая дозагрузка запущена.",
            "nomenclature_count": nom_count,
            "campaigns_count": campaigns_count,
            "funnel_rows": funnel_rows,
            "finished_at": utcnow().isoformat(),
        }

        logger.info(
            f"First sync done for project {project_id}: "
            f"nom={nom_count}, campaigns={campaigns_count}, funnel={funnel_rows}"
        )

        # Phase 5: Backfill remaining 60 days in background
        asyncio.create_task(_backfill_remaining_60(project_id, today))  # noqa: RUF006

    except Exception as e:
        _first_sync_progress[project_id] = {
            "phase": "error",
            "error": str(e)[:300],
            "finished_at": utcnow().isoformat(),
        }
        logger.error(f"First sync failed for project {project_id}: {e}")


async def _backfill_remaining_60(project_id: int, today: date) -> None:
    """Backfill days 31-90 after first sync completes."""
    from backend.database import AsyncSessionLocal

    try:
        date_from_90 = (today - timedelta(days=90)).isoformat()
        date_to_30 = (today - timedelta(days=31)).isoformat()

        logger.info(f"First sync backfill: project {project_id} — {date_from_90} to {date_to_30}")

        async with AsyncSessionLocal() as db:
            from backend.services.funnel.sync import run_funnel_sync

            result = await run_funnel_sync(db, project_id, date_from_90, date_to_30)
            logger.info(
                f"First sync backfill done: project {project_id} — "
                f"{result.get('rows', 0)} rows, {result.get('days', 0)} days"
            )
    except Exception as e:
        logger.error(f"First sync backfill failed for project {project_id}: {e}")
