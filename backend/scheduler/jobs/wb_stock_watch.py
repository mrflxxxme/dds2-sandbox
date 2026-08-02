# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Scheduler job: тик слежения за поступлением товара (wb_stock_watches).

Каждые 30 минут: для проектов с активными watches (status='watching') —
1) scan_stock_questions (добрать новые вопросы о наличии между ночными синками);
2) stock_watch_tick — проверить totalQuantity публичных карточек WB; товар
   появился → черновик ответа draft (отправка ТОЛЬКО вручную после одобрения).
Ошибки сети/WAF на одном проекте не валят остальные (тик сам мягок к сбоям).
SyncLog не пишем: тик частый (30 мин) и не привязан к ключу — итоги в logger.
"""

import asyncio
import logging

from sqlalchemy import select

from backend.database import AsyncSessionLocal

logger = logging.getLogger("dds.scheduler")


async def stock_watch_tick_all_projects():
    """Тик слежения за поступлением для всех проектов с активными watches."""
    from backend.models import WBStockWatch
    from backend.services import stock_watch_service

    async with AsyncSessionLocal() as db:
        project_ids = [
            int(r[0])
            for r in (
                await db.execute(
                    select(WBStockWatch.project_id)
                    .where(WBStockWatch.status == "watching")
                    .distinct()
                )
            ).all()
        ]
    if not project_ids:
        return

    total_drafted = 0
    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                await stock_watch_service.scan_stock_questions(db, project_id)
            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    stock_watch_service.stock_watch_tick(db, project_id),
                    timeout=300,
                )
            total_drafted += int(result.get("drafted", 0))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("stock watch tick: project %d failed — %s", project_id, e, exc_info=True)

    if total_drafted:
        logger.info("stock watch tick: done — drafted=%d", total_drafted)
