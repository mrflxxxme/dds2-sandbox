"""
Slow request logging middleware (pure ASGI — no BaseHTTPMiddleware).
Logs requests slower than SLOW_REQUEST_THRESHOLD_MS.
"""

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("dds.perf")

SLOW_REQUEST_THRESHOLD_MS = 500


class SlowRequestMiddleware:
    """Log requests that take longer than the threshold.

    Pure ASGI implementation avoids CancelledError connection leaks
    that occur with BaseHTTPMiddleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        start = time.perf_counter()
        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        await self.app(scope, receive, send_wrapper)

        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms > SLOW_REQUEST_THRESHOLD_MS:
            path = scope.get("path", "?")
            method = scope.get("method", "?")
            logger.warning(
                "🐢 SLOW %s %s — %.0fms (status=%d)",
                method,
                path,
                elapsed_ms,
                status_code,
            )
