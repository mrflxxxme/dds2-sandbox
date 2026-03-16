"""
DDS Financial Management System - FastAPI Backend

Entry point: lifespan, middleware, router registration.
See AGENTS.md for full architecture overview.
"""

import logging
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text

from backend.config import settings
from backend.database import async_engine, AsyncSessionLocal, Base
from backend.auth import get_current_user, ensure_default_admin, require_admin
from backend.routers import import_txn, refs, reports, planning, cost, auth, integrations, projects, funnel, ws


# ─── Sentry Error Tracking ──────────────────────────────────────────────────

if settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.DDS_ENV,
        traces_sample_rate=0.2,    # 20% performance traces
        profiles_sample_rate=0.1,  # 10% profiling
        send_default_pii=False,    # Don't send personal data
    )
    logging.getLogger("dds").info("Sentry initialized (env=%s)", settings.DDS_ENV)


# ─── Telegram Alerts ────────────────────────────────────────────────────────

from backend.utils.telegram import configure as configure_telegram
configure_telegram(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)


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
    # Create tables on startup
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Seed default categories for all existing projects (idempotent)
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
            stale = await session.execute(text(
                "UPDATE sync_log SET status = 'STALE', error_msg = 'Process restarted while running' "
                "WHERE status = 'RUNNING' AND started_at < NOW() - INTERVAL '10 minutes'"
            ))
            await session.commit()
            if stale.rowcount:
                logger.warning(f"Cleaned {stale.rowcount} stale RUNNING sync_log entries")

        start_scheduler()
    else:
        logger.info("⏭️ Scheduler skipped (DDS_ROLE=%s, scheduler runs in worker container)", settings.DDS_ROLE)

    yield

    # Shutdown: stop scheduler + close Redis
    stop_scheduler()
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

    SKIP_PATHS = {"/health", "/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/refresh"}

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
                token = auth_header[7:]
                payload = jwt.decode(token, options={"verify_signature": False})
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
    projects.router, prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

# Protected routes (auth required)
app.include_router(
    import_txn.router, prefix="/api/v1", tags=["Import & Transactions"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    refs.router, prefix="/api/v1", tags=["Reference Data"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    reports.router, prefix="/api/v1", tags=["Reports"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    planning.router, prefix="/api/v1", tags=["Planning"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    cost.router, prefix="/api/v1", tags=["Cost"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    integrations.router, prefix="/api/v1", tags=["Integrations"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    funnel.router, prefix="/api/v1", tags=["Funnel"],
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
        from backend.storage import get_minio_client
        client = get_minio_client()
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
    from backend.database import SyncSessionLocal
    from backend.models import Account, LeadTime
    from sqlalchemy import select

    with SyncSessionLocal() as db:
        # Default accounts from REF_ACCOUNTS
        default_accounts = [
            {"account": "40702810400810052145", "bank": "VTB", "currency": "RUB",
             "account_type": "OPER", "is_our_account": True, "account_name": "VTB RUB Основной",
             "is_customs_payee": False},
            {"account": "42102810316110029573", "bank": "VTB", "currency": "RUB",
             "account_type": "TRANSIT", "is_our_account": True, "account_name": "VTB RUB Транзит",
             "is_customs_payee": False},
            {"account": "40702156916110000346", "bank": "VTB", "currency": "CNY",
             "account_type": "OPER", "is_our_account": True, "account_name": "VTB CNY",
             "is_customs_payee": False},
            {"account": "40702810800000001893", "bank": "WB", "currency": "RUB",
             "account_type": "OPER", "is_our_account": True, "account_name": "WB RUB Основной",
             "is_customs_payee": False},
            {"account": "4070281050001001752", "bank": "WB", "currency": "RUB",
             "account_type": "TRANSIT", "is_our_account": True, "account_name": "WB RUB Транзит",
             "is_customs_payee": False},
            {"account": "3100643000000019502", "bank": "CUSTOMS", "currency": "RUB",
             "account_type": "CUSTOMS_PAYEE", "is_our_account": False, "account_name": "Таможня (получатель)",
             "is_customs_payee": True},
        ]

        for acc_data in default_accounts:
            existing = db.execute(
                select(Account).where(Account.account == acc_data["account"])
            ).scalar_one_or_none()
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
