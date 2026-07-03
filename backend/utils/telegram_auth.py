"""
Telegram WebApp initData validation (HMAC-SHA256).

Used for Telegram Mini App (TMA) authentication.
See: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qs, unquote

logger = logging.getLogger(__name__)

# initData is considered expired after 5 minutes
AUTH_DATE_MAX_AGE_SECONDS = 300


def validate_telegram_webapp_data(init_data: str, bot_token: str) -> dict | None:
    """Validate Telegram WebApp initData and return parsed user dict.

    Args:
        init_data: Raw initData query string from Telegram WebApp.
        bot_token: Bot token from @BotFather.

    Returns:
        Parsed user dict (id, first_name, last_name, username, etc.)
        or None if validation fails.
    """
    try:
        parsed = parse_qs(init_data, keep_blank_values=True)

        # Extract hash
        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            logger.warning("TMA auth: missing hash in initData")
            return None

        # Build data-check-string: sorted key=value pairs excluding "hash", joined by \n
        data_pairs = []
        for key, values in parsed.items():
            if key == "hash":
                continue
            # parse_qs returns lists; take first value, unquote it
            value = values[0] if values else ""
            data_pairs.append(f"{key}={value}")

        data_pairs.sort()
        data_check_string = "\n".join(data_pairs)

        # secret_key = HMAC_SHA256("WebAppData", bot_token)
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        # computed_hash = HMAC_SHA256(secret_key, data_check_string)
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Compare hashes
        if not hmac.compare_digest(computed_hash, received_hash):
            logger.warning("TMA auth: hash mismatch")
            return None

        # Check auth_date freshness
        auth_date_str = parsed.get("auth_date", [None])[0]
        if not auth_date_str:
            logger.warning("TMA auth: missing auth_date")
            return None

        auth_date = int(auth_date_str)
        # time.time() is a true UTC epoch; utcnow() is naive and .timestamp() would
        # reinterpret it in the container's local TZ, skewing the freshness check.
        now_ts = int(time.time())
        if now_ts - auth_date > AUTH_DATE_MAX_AGE_SECONDS:
            logger.warning(
                "TMA auth: initData expired (auth_date=%d, now=%d, diff=%ds)",
                auth_date,
                now_ts,
                now_ts - auth_date,
            )
            return None

        # Parse user JSON
        user_str = parsed.get("user", [None])[0]
        if not user_str:
            logger.warning("TMA auth: missing user field")
            return None

        user_data = json.loads(unquote(user_str) if "%7B" in user_str else user_str)
        return user_data

    except Exception:
        logger.exception("TMA auth: validation error")
        return None
