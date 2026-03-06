"""
Slow request logging middleware.
Logs requests slower than SLOW_REQUEST_THRESHOLD_MS.
"""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("dds.perf")

SLOW_REQUEST_THRESHOLD_MS = 500  # Log requests slower than this


class SlowRequestMiddleware(BaseHTTPMiddleware):
    """Log requests that take longer than the threshold."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        if elapsed_ms > SLOW_REQUEST_THRESHOLD_MS:
            logger.warning(
                "🐢 SLOW %s %s — %.0fms (status=%d)",
                request.method,
                request.url.path,
                elapsed_ms,
                response.status_code,
            )

        return response
