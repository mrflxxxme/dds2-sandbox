"""
DDS Financial Management System - FastAPI Backend
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.config import settings
from backend.database import async_engine, AsyncSessionLocal, Base
from backend.auth import get_current_user, ensure_default_admin
from backend.routers import import_txn, refs, reports, planning, cost, auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Seed default categories (idempotent)
        defaults = [
            # Income
            ("income", "Маркетплейсы", "Wildberries", 1),
            ("income", "Маркетплейсы", "OZON", 2),
            ("income", "Маркетплейсы", "Прочее", 3),
            ("income", "Банки", "Проценты", 10),
            ("income", "Внутренние переводы", "Между счетами", 20),
            ("income", "Внутренние переводы", "Между юрлицами", 21),
            ("income", "Прочие доходы", "Возврат", 30),
            ("income", "Прочие доходы", "Прочее", 31),
            # Expense
            ("expense", "Поставщики", "Оплата товара", 1),
            ("expense", "Поставщики", "Депозит", 2),
            ("expense", "Поставщики", "Прочее", 3),
            ("expense", "Логистика", "Доставка по РФ", 10),
            ("expense", "Логистика", "Доставка из Китая", 11),
            ("expense", "Логистика", "Таможня", 12),
            ("expense", "Банки", "Комиссии банка", 20),
            ("expense", "Фулфилмент", "Склад / упаковка", 30),
            ("expense", "Фулфилмент", "Прочее", 31),
            ("expense", "Зарплата", "Сотрудники", 40),
            ("expense", "Зарплата", "ИП", 41),
            ("expense", "Налоги", "НДС", 50),
            ("expense", "Налоги", "Прибыль", 51),
            ("expense", "Налоги", "Взносы", 52),
            ("expense", "Внутренние переводы", "Между счетами", 60),
            ("expense", "Внутренние переводы", "Между юрлицами", 61),
            ("expense", "Внутренние переводы", "Перевод собственнику / займы", 62),
            ("expense", "Прочие расходы", "Офис", 70),
            ("expense", "Прочие расходы", "IT", 71),
            ("expense", "Прочие расходы", "Реклама", 72),
            ("expense", "Прочие расходы", "Прочее", 73),
        ]
        for d, c1, c2, s in defaults:
            await conn.execute(text(
                "INSERT INTO category_ref (direction, cat_lvl1, cat_lvl2, sort_order) "
                "VALUES (:d, :c1, :c2, :s) ON CONFLICT (direction, cat_lvl1, cat_lvl2) DO NOTHING"
            ), {"d": d, "c1": c1, "c2": c2, "s": s})

    # Create default admin user
    async with AsyncSessionLocal() as session:
        await ensure_default_admin(session)

    yield


app = FastAPI(
    title="DDS Financial Management",
    description="ДДС — система управленческого учёта",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Public routes (no auth required)
app.include_router(auth.router, prefix="/api", tags=["Auth"])

# Protected routes (auth required)
app.include_router(
    import_txn.router, prefix="/api", tags=["Import & Transactions"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    refs.router, prefix="/api", tags=["Reference Data"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    reports.router, prefix="/api", tags=["Reports"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    planning.router, prefix="/api", tags=["Planning"],
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    cost.router, prefix="/api", tags=["Cost"],
    dependencies=[Depends(get_current_user)],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/seed_defaults")
async def seed_defaults(db=None):
    """Seed default reference data (accounts, lead times)."""
    return {"message": "Use POST /api/refs/accounts and /api/planning/lead_times"}


@app.post("/api/seed", dependencies=[Depends(get_current_user)])
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
