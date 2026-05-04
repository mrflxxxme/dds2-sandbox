"""
Unified error handling for DDS API.
Consistent error format for all endpoints.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str | None = None
    issue: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


async def http_exception_handler(request: Request, exc: HTTPException):
    """Unified error response format.

    Когда detail — dict (структурированная ошибка типа FactoryQtyExceeded),
    кладём его как payload, а в message пишем человекочитаемый код.
    Frontend читает `error.payload` напрямую без JSON.parse(message).
    """
    payload = None
    if isinstance(exc.detail, dict):
        payload = exc.detail
        message = exc.detail.get("error") or _status_to_code(exc.status_code)
    else:
        message = str(exc.detail)

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": _status_to_code(exc.status_code),
                "message": message,
                "details": None,
                "payload": payload,
            }
        },
    )


async def generic_exception_handler(request: Request, exc: Exception):
    """Catch-all for unhandled errors."""
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Внутренняя ошибка сервера",
                "details": None,
            }
        },
    )


def _status_to_code(status: int) -> str:
    codes = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "TOO_MANY_REQUESTS",
        500: "INTERNAL_ERROR",
    }
    return codes.get(status, f"HTTP_{status}")


def register_exception_handlers(app: FastAPI):
    """Register all exception handlers on the app."""
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
