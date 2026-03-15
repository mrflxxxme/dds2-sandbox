"""
Demo data seeder for staging/development.

Usage:
    docker compose exec backend python -m scripts.seed_demo

Creates:
    - Demo user (demo / demo1234)
    - Demo project
    - Sample bank accounts
    - Sample transactions (30 days)
    - Default categories
"""

import asyncio
import sys
import os
from decimal import Decimal
from datetime import timedelta

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from backend.database import async_engine
from backend.utils.time import utcnow


DEMO_USER = {
    "username": "demo",
    "email": "demo@dds.local",
    "name": "Demo User",
    "password_hash": "",  # Will be set below
}

DEMO_PROJECT = {
    "name": "Demo Project",
    "slug": "demo",
}

DEMO_ACCOUNTS = [
    ("Тинькофф Бизнес", "Tinkoff"),
    ("Сбербанк", "Sberbank"),
    ("Wildberries", "WB"),
]

# Sample transactions: (days_ago, direction, amount, description, cat_lvl1, cat_lvl2)
DEMO_TRANSACTIONS = [
    (1, "income", 150000, "Wildberries — выплата за неделю", "Маркетплейсы", "Wildberries"),
    (2, "expense", 45000, "Поставщик ИП Иванов", "Поставщики", "Оплата товара"),
    (3, "income", 82000, "OZON — выплата", "Маркетплейсы", "OZON"),
    (4, "expense", 12000, "Доставка СДЭК", "Логистика", "Доставка по РФ"),
    (5, "expense", 3500, "Комиссия банка", "Банки", "Комиссии банка"),
    (7, "income", 210000, "Wildberries — выплата за неделю", "Маркетплейсы", "Wildberries"),
    (8, "expense", 65000, "Поставщик ООО Текстиль", "Поставщики", "Оплата товара"),
    (10, "expense", 25000, "Зарплата менеджер", "Зарплата", "Сотрудники"),
    (10, "expense", 15000, "Зарплата упаковщик", "Зарплата", "Сотрудники"),
    (12, "expense", 8000, "Аренда склада", "Фулфилмент", "Склад / упаковка"),
    (14, "income", 175000, "Wildberries — выплата за неделю", "Маркетплейсы", "Wildberries"),
    (15, "expense", 55000, "Поставщик Китай", "Поставщики", "Оплата товара"),
    (16, "expense", 18000, "Доставка из Китая", "Логистика", "Доставка из Китая"),
    (18, "expense", 5000, "Яндекс Директ", "Прочие расходы", "Реклама"),
    (20, "income", 95000, "OZON — выплата", "Маркетплейсы", "OZON"),
    (21, "income", 195000, "Wildberries — выплата за неделю", "Маркетплейсы", "Wildberries"),
    (22, "expense", 72000, "Крупная поставка товара", "Поставщики", "Оплата товара"),
    (25, "expense", 4200, "Комиссия банка", "Банки", "Комиссии банка"),
    (27, "expense", 35000, "НДС за квартал", "Налоги", "НДС"),
    (28, "income", 180000, "Wildberries — выплата за неделю", "Маркетплейсы", "Wildberries"),
    (30, "expense", 12000, "Канцтовары и офис", "Прочие расходы", "Офис"),
]


async def seed():
    """Create demo data."""
    from passlib.context import CryptContext

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    password_hash = pwd_context.hash("demo1234")
    now = utcnow()

    async with async_engine.begin() as conn:
        # Set project_id context
        await conn.execute(text("SET LOCAL app.project_id = '0'"))

        # 1. Create demo user (if not exists)
        result = await conn.execute(
            text("SELECT id FROM users WHERE username = :u"),
            {"u": "demo"},
        )
        user = result.fetchone()

        if user:
            user_id = user[0]
            print(f"  ℹ️  Demo user already exists (id={user_id})")
        else:
            result = await conn.execute(
                text(
                    "INSERT INTO users (username, email, name, hashed_password, is_active) "
                    "VALUES (:u, :e, :n, :h, true) RETURNING id"
                ),
                {"u": "demo", "e": "demo@dds.local", "n": "Demo User", "h": password_hash},
            )
            user_id = result.fetchone()[0]
            print(f"  ✅ Demo user created (id={user_id})")

        # 2. Create demo project (if not exists)
        result = await conn.execute(
            text("SELECT id FROM projects WHERE slug = :s"),
            {"s": "demo"},
        )
        project = result.fetchone()

        if project:
            project_id = project[0]
            print(f"  ℹ️  Demo project already exists (id={project_id})")
        else:
            result = await conn.execute(
                text(
                    "INSERT INTO projects (name, slug, owner_id) "
                    "VALUES (:n, :s, :o) RETURNING id"
                ),
                {"n": "Demo Project", "s": "demo", "o": user_id},
            )
            project_id = result.fetchone()[0]

            # Link user to project
            await conn.execute(
                text(
                    "INSERT INTO user_projects (user_id, project_id, role) "
                    "VALUES (:u, :p, 'owner') ON CONFLICT DO NOTHING"
                ),
                {"u": user_id, "p": project_id},
            )
            print(f"  ✅ Demo project created (id={project_id})")

        # Set RLS context for this project
        await conn.execute(text("SET LOCAL app.project_id = :pid"), {"pid": str(project_id)})

        # 3. Seed default categories
        from backend.seeds.default_categories import seed_default_categories

        await seed_default_categories(conn, [project_id])
        print("  ✅ Default categories seeded")

        # 4. Create bank accounts
        for name, bank in DEMO_ACCOUNTS:
            await conn.execute(
                text(
                    "INSERT INTO bank_accounts (project_id, name, bank_name) "
                    "VALUES (:p, :n, :b) ON CONFLICT DO NOTHING"
                ),
                {"p": project_id, "n": name, "b": bank},
            )
        print(f"  ✅ {len(DEMO_ACCOUNTS)} bank accounts created")

        # 5. Get account ID for transactions
        result = await conn.execute(
            text("SELECT id FROM bank_accounts WHERE project_id = :p LIMIT 1"),
            {"p": project_id},
        )
        account_id = result.fetchone()[0]

        # 6. Create sample transactions
        count = 0
        for days_ago, direction, amount, desc, cat1, cat2 in DEMO_TRANSACTIONS:
            txn_date = now - timedelta(days=days_ago)
            await conn.execute(
                text(
                    "INSERT INTO transactions "
                    "(project_id, bank_account_id, date, amount, direction, "
                    " description, counterparty, cat_lvl1, cat_lvl2, is_categorized) "
                    "VALUES (:p, :a, :d, :amt, :dir, :desc, :cp, :c1, :c2, true) "
                ),
                {
                    "p": project_id,
                    "a": account_id,
                    "d": txn_date,
                    "amt": Decimal(str(amount)),
                    "dir": direction,
                    "desc": desc,
                    "cp": desc.split("—")[0].strip() if "—" in desc else desc,
                    "c1": cat1,
                    "c2": cat2,
                },
            )
            count += 1
        print(f"  ✅ {count} transactions created (last {DEMO_TRANSACTIONS[-1][0]} days)")

    print()
    print("═" * 50)
    print("✅ Demo data seeded!")
    print()
    print(f"  Login:    demo")
    print(f"  Password: demo1234")
    print(f"  Project:  Demo Project (/{DEMO_PROJECT['slug']})")
    print("═" * 50)


if __name__ == "__main__":
    print("🌱 Seeding demo data...")
    asyncio.run(seed())
