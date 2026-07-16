"""АБ-тесты главного фото — периодический тик.

Каждый тик: дельты накопительных счётчиков WB → метрики открытого круга → ротация
фото по границе круга (N показов + мин. время) → финиш теста. Проекты без идущих
тестов и без ключей пропускаются тихо. Логика — services/funnel/ab_photo_tests.py.
"""

import asyncio
import logging

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids

logger = logging.getLogger("dds.scheduler")


async def ab_tests_tick_all_projects():
    """Тик АБ-тестов по всем проектам (интервал — 5 мин; круг закрывается по времени или досрочно по показам)."""
    from backend.services.funnel.ab_photo_tests import tick_project

    project_ids = await get_sync_project_ids()
    if not project_ids:
        return

    for pid in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(tick_project(db, pid), timeout=240)
            if result.get("ticked"):
                logger.info(f"AB tests tick: project {pid} — {result['ticked']} tests")
        except TimeoutError:
            logger.error(f"AB tests tick TIMEOUT for project {pid}")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — один проект не роняет остальные
            logger.error(f"AB tests tick failed for project {pid}: {e}")
