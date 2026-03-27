"""
Telegram Mini App authentication — validate initData from WebApp.

Implements the official Telegram verification algorithm:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qs

from backend.utils.time import utcnow

logger = logging.getLogger("dds.telegram_auth")

# initData is valid for 5 minutes
INIT_DATA_MAX_AGE_SECONDS = 300


def validate_telegram_webapp_data(init_data: str, bot_token: str) -> dict | None:
    """Validate Telegram WebApp initData and return parsed user data.

    Returns dict with user info on success, None on failure.
    The dict contains: id, first_name, last_name, username, language_code, etc.
    """
    try:
        parsed = parse_qs(init_data, keep_blank_values=True)

        # Extract hash
        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            logger.warning("Missing hash in initData")
            return None

        # Build data-check-string: alphabetically sorted key=value pairs, excluding hash
        data_pairs = []
        for key, values in parsed.items():
            if key == "hash":
                continue
            data_pairs.append(f"{key}={values[0]}")
        data_pairs.sort()
        data_check_string = "\n".join(data_pairs)

        # Compute HMAC: secret_key = HMAC_SHA256("WebAppData", bot_token)
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
        ).digest()
        computed_hash = hmac.new(
            secret_key, data_check_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            logger.warning("Invalid initData hash")
            return None

        # Check auth_date freshness
        auth_date_str = parsed.get("auth_date", [None])[0]
        if auth_date_str:
            auth_date = int(auth_date_str)
            now_ts = int(utcnow().timestamp())
            if now_ts - auth_date > INIT_DATA_MAX_AGE_SECONDS:
                logger.warning(
                    "initData expired: auth_date=%d, now=%d, diff=%ds",
                    auth_date, now_ts, now_ts - auth_date,
                )
                return None

        # Parse user JSON
        user_str = parsed.get("user", [None])[0]
        if not user_str:
            logger.warning("Missing user in initData")
            return None

        user_data = json.loads(user_str)
        return user_data

    except Exception:
        logger.exception("Failed to validate initData")
        return None
