"""
Telegram webhook endpoint — receives updates from Telegram servers.
NO JWT auth — validated via X-Telegram-Bot-Api-Secret-Token header.
"""

import logging

from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request

from backend.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/bot/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram updates via webhook."""
    # Validate secret token
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    from backend.integrations.telegram_bot import bot, dp

    if not bot or not dp:
        logger.warning("Telegram bot not initialized, ignoring webhook")
        return {"ok": True}

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception:
        logger.exception("Error processing Telegram update")

    return {"ok": True}
