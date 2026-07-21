# ruff: noqa: RUF001, RUF002, RUF003
"""
Scheduler job: почасовой срез наполнения черновиков сборки по категориям.

Копит динамику «сколько штук/коробов/паллет предлагает Сборка» вперёд
(distribution черновика хранит только текущее состояние) — вкладка
«Динамика черновика» на «Анализ сборки». Worker-контейнер, раз в час.
"""

import asyncio
import logging

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import AssemblyDraft

logger = logging.getLogger("dds.scheduler")


async def snapshot_all_projects_draft_categories():
    """Срез текущего часа для всех проектов с живыми черновиками (idempotent)."""
    from backend.services.assembly.draft_category_history import (
        purge_old_snapshots,
        snapshot_project,
    )

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(AssemblyDraft.project_id)
            .where(AssemblyDraft.is_deleted == False)  # noqa: E712 — SQLAlchemy expression
            .distinct()
            .limit(1000)
        )
        project_ids = [pid for (pid,) in rows.all() if pid]

    if not project_ids:
        logger.info("Draft category snapshot: no projects with drafts, skipping")
        return

    ok = 0
    errors = 0
    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                written = await snapshot_project(db, project_id)
            logger.info("Draft category snapshot: project %d — %d categories", project_id, written)
            ok += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "Draft category snapshot: project %d failed — %s", project_id, str(e), exc_info=True
            )
            errors += 1

    try:
        async with AsyncSessionLocal() as db:
            await purge_old_snapshots(db)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Draft category snapshot: retention purge failed", exc_info=True)

    logger.info("Draft category snapshot: done — %d ok, %d errors", ok, errors)
