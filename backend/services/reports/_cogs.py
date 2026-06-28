"""COGS («себестоимость товара») exclusion for expense reports.

Расходные категории, помеченные ``CategoryRef.is_cogs``, считаются стоимостью
товара (таможня/ФТС, поставщики, межд. логистика), а не операционным расходом —
их выплаты исключаются из расходных агрегатов дашборда и ДДС.

Гранулярность пометки:
- категория без под-уровня (cat_lvl2 пуст) → исключается ВЕСЬ уровень-1;
- конкретная пара (cat_lvl1, cat_lvl2) → исключается только она.
"""

from __future__ import annotations

from sqlalchemy import and_, func, not_, or_, select, true, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend.models import Transaction
from backend.models.refs import CategoryRef


async def get_cogs_keys(db: AsyncSession, project_id: int) -> tuple[set[str], set[tuple[str, str]]]:
    """Return ``(lvl1_only, pairs)`` COGS keys for a project's expense categories."""
    res = await db.execute(
        select(CategoryRef.cat_lvl1, CategoryRef.cat_lvl2).where(
            CategoryRef.project_id == project_id,
            CategoryRef.direction == "expense",
            CategoryRef.is_cogs == True,  # noqa: E712
            CategoryRef.is_deleted == False,  # noqa: E712
        )
    )
    lvl1_only: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for r in res.all():
        name = (r.cat_lvl1 or "").strip()
        sub = (r.cat_lvl2 or "").strip()
        if not name:
            continue
        if sub:
            pairs.add((name, sub))
        else:
            lvl1_only.add(name)
    return lvl1_only, pairs


def cogs_include_clause(lvl1_only: set[str], pairs: set[tuple[str, str]]) -> ColumnElement[bool]:
    """SQL boolean — TRUE for a transaction row that is NOT a COGS *expense*.

    The match is gated on ``expense > 0``, so an income row (e.g. a refund) that
    happens to carry a COGS-flagged category name is NEVER excluded — income stays
    untouched. Safe both as a flat WHERE (drops only COGS expense rows) and inside
    a CASE on the expense leg of a mixed income+expense sum. Returns ``true()``
    when nothing is flagged (no-op).
    """
    t_l1 = func.coalesce(Transaction.cat_lvl1_2, "")
    t_l2 = func.coalesce(Transaction.cat_lvl2_2, "")
    conds: list[ColumnElement[bool]] = []
    if lvl1_only:
        conds.append(t_l1.in_(sorted(lvl1_only)))
    if pairs:
        conds.append(tuple_(t_l1, t_l2).in_(sorted(pairs)))
    if not conds:
        return true()
    return not_(and_(or_(*conds), Transaction.expense > 0))


async def get_cogs_include_clause(db: AsyncSession, project_id: int) -> ColumnElement[bool]:
    """Convenience: fetch COGS keys and build the include-clause in one call."""
    lvl1_only, pairs = await get_cogs_keys(db, project_id)
    return cogs_include_clause(lvl1_only, pairs)
