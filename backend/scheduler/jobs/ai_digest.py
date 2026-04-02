"""
Scheduler job: AI morning digest.

Runs daily at 7:00 MSK. For each TelegramChatBinding with notify_enabled=True:
1. Generate digest via Claude (Haiku for cost efficiency)
2. Send to Telegram chat
"""

import asyncio
import logging

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.auth import Project
from backend.models.telegram import TelegramChatBinding

logger = logging.getLogger("dds.scheduler.digest")


async def send_daily_digests():
    """Send AI-generated morning digests to all enabled chat bindings."""
    logger.info("Starting daily AI digest generation")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(TelegramChatBinding).where(TelegramChatBinding.notify_enabled.is_(True)))
        bindings = list(result.scalars().all())

    if not bindings:
        logger.info("No bindings with notify_enabled, skipping digest")
        return

    # Import bot lazily to avoid circular imports
    from backend.integrations.telegram_bot import bot
    from backend.services.ai.agent import generate_digest

    if not bot:
        logger.warning("Bot not initialized, skipping digest")
        return

    sent = 0
    errors = 0

    for binding in bindings:
        try:
            async with AsyncSessionLocal() as db:
                project = await db.get(Project, binding.project_id)
                if not project:
                    continue
                tax_rate = float(project.tax_rate or 6)

                digest_text = await generate_digest(
                    db=db,
                    project_id=binding.project_id,
                    brand=binding.brand,
                    tax_rate=tax_rate,
                )

            await bot.send_message(chat_id=binding.chat_id, text=digest_text)
            sent += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to send digest to chat %s", binding.chat_id)
            errors += 1

    logger.info("Daily digest complete: sent=%d, errors=%d", sent, errors)
