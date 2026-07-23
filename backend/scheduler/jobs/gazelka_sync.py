# ruff: noqa: RUF002, RUF003
"""
Scheduler job: sync Gazelka order states for all projects with a Gazelka integration.

Runs every 15 min. Для связанных заявок из кабинета перевозчика:
  • Газелька назначила машину → сборка «Машина назначена» + WB-пропуск (авто-занос в кабинет);
  • статус «В маршруте» → авто-шип сборки (газельный тариф → стоимость забора в листе оплаты).
Best-effort: сбой одного проекта не роняет остальные.
"""

import asyncio
import logging

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import IntegrationKey

logger = logging.getLogger("dds.scheduler")

_GAZELKA_SERVICE = "gazelka"


async def _gazelka_project_ids() -> list[int]:
    """Проекты с активной интеграцией Газельки."""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(IntegrationKey.project_id)
            .where(
                IntegrationKey.service == _GAZELKA_SERVICE,
                IntegrationKey.is_active.is_(True),
                IntegrationKey.is_deleted.is_(False),
            )
            .distinct()
        )
        return [r[0] for r in rows.all()]


async def sync_all_projects_gazelka_states() -> None:
    """Итерируем проекты с ключом Газельки и синкаем статусы заявок. Каждые 15 мин."""
    from backend.services.gazelka_service import sync_gazelka_states

    project_ids = await _gazelka_project_ids()
    if not project_ids:
        return

    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                await asyncio.wait_for(sync_gazelka_states(db, project_id), timeout=300)
        except Exception:  # noqa: BLE001
            logger.warning("Gazelka states sync failed for project %d", project_id, exc_info=True)
