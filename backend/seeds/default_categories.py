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

    Skips projects that already have ANY category_ref rows. Typical case at
    startup is "everything already seeded" and we don't want N x 30 INSERTs
    every lifespan call. Per-project granularity is enough: a project either
    was seeded once (full set) or never (new project).

    Args:
        conn: SQLAlchemy async connection (within a transaction)
        project_ids: list of project IDs to seed
    """
    if not project_ids:
        return

    seeded_rows = await conn.execute(
        text("SELECT project_id FROM category_ref WHERE project_id = ANY(:pids) GROUP BY project_id"),
        {"pids": project_ids},
    )
    already_seeded = {r[0] for r in seeded_rows}
    to_seed = [pid for pid in project_ids if pid not in already_seeded]
    if not to_seed:
        return

    rows = [{"pid": pid, "d": d, "c1": c1, "c2": c2, "s": s} for pid in to_seed for d, c1, c2, s in DEFAULT_CATEGORIES]
    await conn.execute(
        text(
            "INSERT INTO category_ref (project_id, direction, cat_lvl1, cat_lvl2, sort_order) "
            "VALUES (:pid, :d, :c1, :c2, :s) "
            "ON CONFLICT (project_id, direction, cat_lvl1, cat_lvl2) DO NOTHING"
        ),
        rows,
    )
