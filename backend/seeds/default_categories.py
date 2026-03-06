"""
Seed data — default category references for new projects.

Called during app startup (lifespan) and when new projects are created.
"""

from sqlalchemy import text

# Default category_ref entries for every project
DEFAULT_CATEGORIES = [
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


async def seed_default_categories(conn, project_ids: list[int]):
    """
    Insert default category_ref entries for given projects (idempotent).

    Args:
        conn: SQLAlchemy async connection (within a transaction)
        project_ids: list of project IDs to seed
    """
    for pid in project_ids:
        for d, c1, c2, s in DEFAULT_CATEGORIES:
            await conn.execute(text(
                "INSERT INTO category_ref (project_id, direction, cat_lvl1, cat_lvl2, sort_order) "
                "VALUES (:pid, :d, :c1, :c2, :s) "
                "ON CONFLICT (project_id, direction, cat_lvl1, cat_lvl2) DO NOTHING"
            ), {"pid": pid, "d": d, "c1": c1, "c2": c2, "s": s})
