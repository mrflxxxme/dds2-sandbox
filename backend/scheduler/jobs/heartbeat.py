"""Synthetic heartbeat — ping backend every 2 min to prevent false BackendNoUserTraffic alerts.

The alert fires when http_requests_total (excluding /health, /metrics) == 0 for 5 min.
At night (low traffic) this causes false positives.  This job generates a guaranteed
user-level request so the alert only fires on real deadlocks.
"""

import asyncio
import logging

import httpx

logger = logging.getLogger("dds.scheduler")

_BACKEND_URL = "http://backend:8000/api/v1/ping"
_TIMEOUT = 10.0


async def heartbeat_ping():
    """Hit /api/v1/ping on the backend container.

    If the request fails, the backend is genuinely unresponsive
    and BackendNoUserTraffic will correctly fire.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_BACKEND_URL)
            resp.raise_for_status()
        logger.debug("Heartbeat OK")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Heartbeat failed: %s", e)
