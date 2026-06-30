"""Scheduler job: sync the national CBR BIC directory (ED807) into `cbr_bic`.

Раз в неделю (Пн 04:00 MSK) + один прогон сразу на старте воркера (наполнить таблицу
после деплоя). Даёт авторитетный БИК→корр.счёт для платёжек (`resolve_bank_db`), покрывая
любой банк РФ. Сеть к cbr.ru обязательна (прод ходит наружу к WB/Faktura — доступна)."""

import asyncio
import logging

from backend.database import AsyncSessionLocal

logger = logging.getLogger("dds.scheduler")


async def sync_cbr_bic_directory():
    """Скачать справочник БИК ЦБ и upsert'нуть в cbr_bic. Ошибка/сеть — логируем, не валим воркер."""
    from backend.services.cbr_bic_loader import sync_cbr_bic

    async with AsyncSessionLocal() as db:
        try:
            n = await sync_cbr_bic(db)
            logger.info("CBR BIC directory sync done: %d banks", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("CBR BIC directory sync failed")
