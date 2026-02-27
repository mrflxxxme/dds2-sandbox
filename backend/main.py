"""
DDS Financial Management System - FastAPI Backend
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.database import async_engine, Base
from backend.routers import import_txn, refs, reports, planning, cost


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrations for existing DBs
        await conn.execute(text(
            "ALTER TABLE cost_orders ADD COLUMN IF NOT EXISTS transport_type VARCHAR(30) DEFAULT 'AUTO'"
        ))
        await conn.execute(text(
            "ALTER TABLE planned_payments ADD COLUMN IF NOT EXISTS paid_rub NUMERIC(18,2) DEFAULT 0"
        ))
        await conn.execute(text(
            "ALTER TABLE cost_orders ADD COLUMN IF NOT EXISTS actual_arrival_date DATE"
        ))
        await conn.execute(text(
            "ALTER TABLE cost_orders ADD COLUMN IF NOT EXISTS dt_number VARCHAR(100)"
        ))
        # customs_dt table
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS customs_dt (
                id SERIAL PRIMARY KEY,
                dt_number VARCHAR(100) NOT NULL,
                dt_date DATE NOT NULL,
                amount_rub NUMERIC(18,2) NOT NULL,
                order_no INTEGER,
                note TEXT
            )
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_customs_dt_number ON customs_dt (dt_number)
        """))
    yield


app = FastAPI(
    title="DDS Financial Management",
    description="ДДС — система управленческого учёта",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(import_txn.router, prefix="/api", tags=["Import & Transactions"])
app.include_router(refs.router, prefix="/api", tags=["Reference Data"])
app.include_router(reports.router, prefix="/api", tags=["Reports"])
app.include_router(planning.router, prefix="/api", tags=["Planning"])
app.include_router(cost.router, prefix="/api", tags=["Cost"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/seed_defaults")
async def seed_defaults(db=None):
    """Seed default reference data (accounts, lead times)."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from fastapi import Depends
    return {"message": "Use POST /api/refs/accounts and /api/planning/lead_times"}


@app.post("/api/seed")
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
