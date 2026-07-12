"""
Demo data seeder for staging/development.

Usage:
    docker compose exec backend python -m scripts.seed_demo

Creates:
    - Demo user (demo / demo1234, role=admin)
    - Demo project + membership
    - Sample accounts
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


# (account_no, bank, account_name, currency)
DEMO_ACCOUNTS = [
    ("40702810000000000001", "Tinkoff", "Тинькофф Бизнес", "RUB"),
    ("40702810000000000002", "Sberbank", "Сбербанк", "RUB"),
    ("40702810000000000003", "WB", "Wildberries", "RUB"),
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
    # transactions.date is `timestamp without time zone` → use naive UTC
    now = utcnow().replace(tzinfo=None)

    async with async_engine.begin() as conn:
        # Set project_id context (RLS)
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
                    "INSERT INTO users "
                    "(username, email, first_name, last_name, password_hash, is_active, role, created_at) "
                    "VALUES (:u, :e, :fn, :ln, :h, true, 'admin', :now) RETURNING id"
                ),
                {
                    "u": "demo",
                    "e": "demo@dds.local",
                    "fn": "Demo",
                    "ln": "User",
                    "h": password_hash,
                    "now": now,
                },
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
                    "INSERT INTO projects (name, slug, owner_id, created_at) "
                    "VALUES (:n, :s, :o, :now) RETURNING id"
                ),
                {"n": "Demo Project", "s": "demo", "o": user_id, "now": now},
            )
            project_id = result.fetchone()[0]
            print(f"  ✅ Demo project created (id={project_id})")

        # Link user to project (idempotent)
        await conn.execute(
            text(
                "INSERT INTO project_members (project_id, user_id, role) "
                "VALUES (:p, :u, 'owner') ON CONFLICT DO NOTHING"
            ),
            {"u": user_id, "p": project_id},
        )

        # Set RLS context for this project (SET LOCAL can't bind params → set_config)
        await conn.execute(
            text("SELECT set_config('app.project_id', :pid, true)"),
            {"pid": str(project_id)},
        )

        # 3. Seed default categories
        from backend.seeds.default_categories import seed_default_categories

        await seed_default_categories(conn, [project_id])
        print("  ✅ Default categories seeded")

        # 4. Create accounts
        for account_no, bank, account_name, currency in DEMO_ACCOUNTS:
            await conn.execute(
                text(
                    "INSERT INTO accounts "
                    "(project_id, account, bank, currency, account_name, is_our_account) "
                    "VALUES (:p, :acc, :bank, :cur, :name, true) ON CONFLICT DO NOTHING"
                ),
                {
                    "p": project_id,
                    "acc": account_no,
                    "bank": bank,
                    "cur": currency,
                    "name": account_name,
                },
            )
        print(f"  ✅ {len(DEMO_ACCOUNTS)} accounts created")

        # 5. Pick an account for transactions (account + bank are stored as strings)
        result = await conn.execute(
            text(
                "SELECT account, bank, currency FROM accounts "
                "WHERE project_id = :p AND is_deleted = false LIMIT 1"
            ),
            {"p": project_id},
        )
        acc_row = result.fetchone()
        acc_no, acc_bank, acc_cur = acc_row[0], acc_row[1], acc_row[2]

        # 6. Create sample transactions
        count = 0
        for i, (days_ago, direction, amount, desc, cat1, cat2) in enumerate(DEMO_TRANSACTIONS):
            txn_date = now - timedelta(days=days_ago)
            amt = Decimal(str(amount))
            if direction == "income":
                income, expense, net = amt, Decimal("0"), amt
            else:
                income, expense, net = Decimal("0"), amt, -amt
            counterparty = desc.split("—")[0].strip() if "—" in desc else desc
            txn_id = f"demo-{i}-{days_ago}-{acc_no}"
            await conn.execute(
                text(
                    "INSERT INTO transactions "
                    "(project_id, date, bank, account, currency, txn_id, counterparty, purpose, "
                    " income, expense, net, is_cashflow, event_type, "
                    " cat_lvl1, cat_lvl2, cat_lvl1_2, cat_lvl2_2) "
                    "VALUES (:p, :d, :bank, :acc, :cur, :txn, :cp, :purpose, "
                    " :income, :expense, :net, 1, 'OPER', "
                    " :c1, :c2, :c1, :c2) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "p": project_id,
                    "d": txn_date,
                    "bank": acc_bank,
                    "acc": acc_no,
                    "cur": acc_cur,
                    "txn": txn_id,
                    "cp": counterparty,
                    "purpose": desc,
                    "income": income,
                    "expense": expense,
                    "net": net,
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
    print("  Login:    demo")
    print("  Password: demo1234")
    print("  Project:  Demo Project (/demo)")
    print("═" * 50)


if __name__ == "__main__":
    print("🌱 Seeding demo data...")
    asyncio.run(seed())
