"""
DDS Financial Management System - FastAPI Backend

Entry point: lifespan, middleware, router registration.
See AGENTS.md for full architecture overview.
"""

import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from backend.auth import ensure_default_admin, get_current_user, require_admin
from backend.config import settings
from backend.database import AsyncSessionLocal, async_engine
from backend.routers import (
    auth,
    cost,
    fbo_supplies,
    funnel,
    import_txn,
    integrations,
    planning,
    projects,
    refs,
    reports,
    telegram,
    telegram_webhook,
    warehouse,
    ws,
)

# ─── Sentry Error Tracking ──────────────────────────────────────────────────

if settings.SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.DDS_ENV,
        traces_sample_rate=0.2,  # 20% performance traces
        profiles_sample_rate=0.1,  # 10% profiling
        send_default_pii=False,  # Don't send personal data
    )
    logging.getLogger("dds").info("Sentry initialized (env=%s)", settings.DDS_ENV)


# ─── Telegram Alerts ────────────────────────────────────────────────────────

from backend.utils.telegram import configure as configure_telegram

configure_telegram(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID, settings.TELEGRAM_PROXY)


# ─── Structured JSON Logging ─────────────────────────────────────────────────


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id
        return json.dumps(log_entry, ensure_ascii=False)


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)

logger = logging.getLogger("dds")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Add X-Request-ID to each request for traceability."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # NOTE: Schema creation is handled by Alembic migrations (entrypoint.sh).
    # Base.metadata.create_all was removed — it competed with Alembic for
    # schema locks and ran redundantly on every worker process.

    # Seed default categories only in worker (avoid running in all API workers)
    if settings.DDS_ROLE == "worker":
        async with async_engine.begin() as conn:
            from backend.seeds.default_categories import seed_default_categories

            project_rows = await conn.execute(text("SELECT id FROM projects"))
            project_ids = [r[0] for r in project_rows]
            await seed_default_categories(conn, project_ids)

    # Create default admin user
    async with AsyncSessionLocal() as session:
        await ensure_default_admin(session)

    # Start background scheduler ONLY in worker container
    from backend.scheduler import start_scheduler, stop_scheduler

    if settings.DDS_ROLE == "worker":
        # Cleanup stale RUNNING sync_log entries (from previous crashes)
        async with AsyncSessionLocal() as session:
            stale = await session.execute(
                text(
                    "UPDATE sync_log SET status = 'STALE', error_msg = 'Process restarted while running' "
                    "WHERE status = 'RUNNING' AND started_at < NOW() - INTERVAL '10 minutes'"
                )
            )
            await session.commit()
            if stale.rowcount:
                logger.warning(f"Cleaned {stale.rowcount} stale RUNNING sync_log entries")

        start_scheduler()

        # Start Telegram analytics bot
        if settings.TELEGRAM_BOT_TOKEN_ANALYTICS:
            try:
                from backend.integrations.telegram_bot import create_bot, set_bot_commands

                tg_bot_ref, tg_dp = create_bot()

                if settings.TELEGRAM_USE_POLLING:
                    import asyncio

                    # Start polling in background task (non-blocking)
                    _polling_task = asyncio.create_task(tg_dp.start_polling(tg_bot_ref))
                    logger.info("Telegram bot: polling started (local dev)")
                else:
                    webhook_url = "https://app.vyatkin-wb.ru/api/v1/bot/webhook"
                    await tg_bot_ref.set_webhook(
                        url=webhook_url,
                        secret_token=settings.TELEGRAM_WEBHOOK_SECRET,
                    )
                    logger.info("Telegram bot webhook set: %s", webhook_url)

                # Set bot commands (non-critical, don't fail startup)
                try:
                    await set_bot_commands()
                except Exception:
                    logger.warning("Failed to set bot commands (non-critical)")
            except Exception:
                logger.exception("Failed to start Telegram analytics bot")
    else:
        logger.info("⏭️ Scheduler skipped (DDS_ROLE=%s, scheduler runs in worker container)", settings.DDS_ROLE)

    yield

    # Shutdown: stop scheduler + close Redis + Telegram bot
    stop_scheduler()

    # Cleanup Telegram bot
    try:
        from backend.integrations.telegram_bot import bot as tg_bot_shutdown, dp as tg_dp_shutdown

        if tg_dp_shutdown and settings.TELEGRAM_USE_POLLING:
            await tg_dp_shutdown.stop_polling()
        if tg_bot_shutdown:
            if not settings.TELEGRAM_USE_POLLING:
                await tg_bot_shutdown.delete_webhook()
            await tg_bot_shutdown.session.close()
    except Exception:
        pass

    from backend.cache import close_redis

    await close_redis()


app = FastAPI(
    title="DDS Financial Management",
    description="ДДС — система управленческого учёта",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")],
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Project-Id"],
    allow_credentials=True,
)

# Rate limiting (Redis-based)
from backend.rate_limit import RateLimitMiddleware

app.add_middleware(RateLimitMiddleware)

# Request ID for traceability
app.add_middleware(RequestIdMiddleware)

# Slow request detection (>500ms)
from backend.slow_query import SlowRequestMiddleware

app.add_middleware(SlowRequestMiddleware)


# ─── Audit Log Middleware ────────────────────────────────────────────────────


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Log all mutation requests (POST/PUT/PATCH/DELETE) to audit_log table."""

    SKIP_PATHS = {
        "/health",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/refresh",
        "/api/v1/bot/webhook",
    }

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Only log mutations
        if request.method in ("GET", "OPTIONS", "HEAD"):
            return response

        # Skip non-API and auth paths
        path = request.url.path
        if path in self.SKIP_PATHS or not path.startswith("/api/"):
            return response

        # Fire-and-forget audit write
        try:
            user_id = self._extract_user_id(request)
            project_id = self._extract_project_id(request)
            ip = request.headers.get("X-Real-IP", request.client.host if request.client else None)

            async with AsyncSessionLocal() as session:
                from backend.models.audit import AuditLog
                from backend.utils.time import utcnow

                log = AuditLog(
                    user_id=user_id or 0,
                    project_id=project_id,
                    method=request.method,
                    endpoint=path,
                    status_code=response.status_code,
                    ip=ip,
                    created_at=utcnow(),
                )
                session.add(log)
                await session.commit()
        except Exception as e:
            logger.warning("AuditLog write failed: %s", e)

        return response

    @staticmethod
    def _extract_user_id(request: Request) -> int | None:
        """Extract user_id from JWT Authorization header."""
        try:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                import jwt

                from backend.config import settings

                token = auth_header[7:]
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
                return payload.get("sub") or payload.get("user_id")
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_project_id(request: Request) -> int | None:
        """Extract project_id from X-Project-Id header."""
        try:
            pid = request.headers.get("X-Project-Id")
            return int(pid) if pid else None
        except (ValueError, TypeError):
            return None


app.add_middleware(AuditLogMiddleware)

# Register unified error handlers
from backend.exceptions import register_exception_handlers

register_exception_handlers(app)

# ─── Prometheus Metrics ─────────────────────────────────────────────────────

from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator(
    excluded_handlers=["/health", "/metrics"],
    inprogress_name="http_requests_in_progress",
    inprogress_labels=True,
).instrument(app).expose(app, include_in_schema=False)

# Public routes (no auth required)
app.include_router(auth.router, prefix="/api/v1", tags=["Auth"])
app.include_router(
    projects.router,
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

# Protected routes (auth required)
app.include_router(
    import_txn.router,
    prefix="/api/v1",
    tags=["Import & Transactions"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    refs.router,
    prefix="/api/v1",
    tags=["Reference Data"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    reports.router,
    prefix="/api/v1",
    tags=["Reports"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    planning.router,
    prefix="/api/v1",
    tags=["Planning"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    cost.router,
    prefix="/api/v1",
    tags=["Cost"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    integrations.router,
    prefix="/api/v1",
    tags=["Integrations"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    funnel.router,
    prefix="/api/v1",
    tags=["Funnel"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    fbo_supplies.router,
    prefix="/api/v1",
    tags=["FBO Supplies"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    warehouse.router,
    prefix="/api/v1",
    tags=["Warehouse"],
    dependencies=[Depends(get_current_user)],
)

# Telegram webhook (NO auth — Telegram calls this directly)
app.include_router(telegram_webhook.router, prefix="/api/v1", tags=["Telegram Webhook"])

# Telegram web API (auth required — for frontend settings)
app.include_router(
    telegram.router,
    prefix="/api/v1",
    tags=["Telegram"],
    dependencies=[Depends(get_current_user)],
)

# WebSocket (handles auth internally via query param)
app.include_router(ws.router, prefix="/api/v1", tags=["WebSocket"])


@app.get("/health")
async def health():
    """Extended health check — verify DB, Redis, MinIO connectivity."""
    checks = {}

    # DB check
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    # Redis check
    try:
        from backend.cache import get_redis

        redis = await get_redis()
        if redis:
            await redis.ping()
            checks["redis"] = "ok"
        else:
            checks["redis"] = "not configured"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # MinIO check
    try:
        from backend.storage import get_minio

        client = await get_minio()
        if client:
            await client.list_buckets()
            checks["minio"] = "ok"
        else:
            checks["minio"] = "not configured"
    except Exception as e:
        checks["minio"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values() if v != "not configured")
    return {"status": "ok" if all_ok else "degraded", "checks": checks}


@app.post("/api/v1/seed", dependencies=[Depends(require_admin)])
async def seed_data():
    """Seed default accounts, lead times, etc. from the Excel files."""
    from sqlalchemy import select

    from backend.database import SyncSessionLocal
    from backend.models import Account, LeadTime

    with SyncSessionLocal() as db:
        # Default accounts from REF_ACCOUNTS
        default_accounts = [
            {
                "account": "40702810400810052145",
                "bank": "VTB",
                "currency": "RUB",
                "account_type": "OPER",
                "is_our_account": True,
                "account_name": "VTB RUB Основной",
                "is_customs_payee": False,
            },
            {
                "account": "42102810316110029573",
                "bank": "VTB",
                "currency": "RUB",
                "account_type": "TRANSIT",
                "is_our_account": True,
                "account_name": "VTB RUB Транзит",
                "is_customs_payee": False,
            },
            {
                "account": "40702156916110000346",
                "bank": "VTB",
                "currency": "CNY",
                "account_type": "OPER",
                "is_our_account": True,
                "account_name": "VTB CNY",
                "is_customs_payee": False,
            },
            {
                "account": "40702810800000001893",
                "bank": "WB",
                "currency": "RUB",
                "account_type": "OPER",
                "is_our_account": True,
                "account_name": "WB RUB Основной",
                "is_customs_payee": False,
            },
            {
                "account": "4070281050001001752",
                "bank": "WB",
                "currency": "RUB",
                "account_type": "TRANSIT",
                "is_our_account": True,
                "account_name": "WB RUB Транзит",
                "is_customs_payee": False,
            },
            {
                "account": "3100643000000019502",
                "bank": "CUSTOMS",
                "currency": "RUB",
                "account_type": "CUSTOMS_PAYEE",
                "is_our_account": False,
                "account_name": "Таможня (получатель)",
                "is_customs_payee": True,
            },
        ]

        for acc_data in default_accounts:
            existing = db.execute(select(Account).where(Account.account == acc_data["account"])).scalar_one_or_none()
            if not existing:
                db.add(Account(**acc_data))

        # Default lead times
        default_lt = [
            {"direction": "ORDER", "days": 50},
            {"direction": "AUTO", "days": 14},
            {"direction": "CONTAINER", "days": 40},
            {"direction": "CUSTOMS", "days": 17},
        ]
        for lt_data in default_lt:
            existing = db.execute(
                select(LeadTime).where(LeadTime.direction == lt_data["direction"])
            ).scalar_one_or_none()
            if not existing:
                db.add(LeadTime(**lt_data))

        db.commit()

    return {"status": "seeded"}
