# ruff: noqa: RUF002
"""
Reports — Counterparty turnovers pivot (month × counterparty, by currency).

Multi-currency: RUB and CNY reported SEPARATELY (no conversion).
Cached 300s via @cached; invalidated by invalidate_project_reports().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models.counterparty import Counterparty
from backend.models.transactions import Transaction

# ─── Dataclasses (class-based report, used by tests) ────────────────────────


@dataclass
class CpMonthBucket:
    month: str  # "2026-01"
    in_sum: Decimal = Decimal("0")
    out_sum: Decimal = Decimal("0")

    @property
    def net(self) -> Decimal:
        return self.in_sum - self.out_sum


@dataclass
class CpTotalBucket:
    in_sum: Decimal = Decimal("0")
    out_sum: Decimal = Decimal("0")

    @property
    def net(self) -> Decimal:
        return self.in_sum - self.out_sum


@dataclass
class CpTurnoverRow:
    counterparty_id: int
    counterparty_name: str
    inn: str | None
    primary_type: str
    months: list[CpMonthBucket] = field(default_factory=list)
    total: CpTotalBucket = field(default_factory=CpTotalBucket)


@dataclass
class CpTurnoverResult:
    rows: list[CpTurnoverRow]
    period: dict
    currency: str


def _months_range(date_from: date, date_to: date) -> list[str]:
    """Enumerate months between two dates, inclusive. Returns ['YYYY-MM', ...]."""
    result: list[str] = []
    y, m = date_from.year, date_from.month
    while (y, m) <= (date_to.year, date_to.month):
        result.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return result


class CounterpartyTurnoversReport:
    """Class-based pivot report. One instance per request."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate(
        self,
        *,
        project_id: int,
        date_from: date,
        date_to: date,
        type_filter: str | None = None,
        currency: str = "RUB",
        min_turnover: Decimal | None = None,
    ) -> CpTurnoverResult:
        """Generate pivot. See module-level get_counterparty_turnovers for cached dict form."""
        months = _months_range(date_from, date_to)

        stmt = (
            select(
                Transaction.counterparty_id.label("cp_id"),
                func.to_char(Transaction.date, "YYYY-MM").label("month_str"),
                func.coalesce(func.sum(Transaction.income), 0).label("in_sum"),
                func.coalesce(func.sum(Transaction.expense), 0).label("out_sum"),
            )
            .where(
                Transaction.project_id == project_id,
                Transaction.is_deleted == False,  # noqa: E712
                Transaction.counterparty_id.isnot(None),
                Transaction.currency == currency.upper(),
                Transaction.date >= date_from,
                Transaction.date <= date_to,
            )
            .group_by(Transaction.counterparty_id, "month_str")
        )
        rows_res = await self.db.execute(stmt)
        raw_rows = rows_res.all()

        # Fetch counterparty meta
        cp_stmt = select(Counterparty).where(
            Counterparty.project_id == project_id,
            Counterparty.is_deleted == False,  # noqa: E712
        )
        if type_filter:
            cp_stmt = cp_stmt.where(Counterparty.primary_type == type_filter)
        cp_res = await self.db.execute(cp_stmt)
        cps = {cp.id: cp for cp in cp_res.scalars().all()}

        # Build rows with months scaffolding
        rows_by_cp: dict[int, CpTurnoverRow] = {}
        for row in raw_rows:
            cp = cps.get(row.cp_id)
            if cp is None:
                continue
            row_obj = rows_by_cp.get(cp.id)
            if row_obj is None:
                row_obj = CpTurnoverRow(
                    counterparty_id=cp.id,
                    counterparty_name=cp.name,
                    inn=cp.inn,
                    primary_type=cp.primary_type,
                    months=[CpMonthBucket(month=m) for m in months],
                    total=CpTotalBucket(),
                )
                rows_by_cp[cp.id] = row_obj

            in_val = Decimal(str(row.in_sum or 0))
            out_val = Decimal(str(row.out_sum or 0))
            for bucket in row_obj.months:
                if bucket.month == row.month_str:
                    bucket.in_sum += in_val
                    bucket.out_sum += out_val
                    break
            row_obj.total.in_sum += in_val
            row_obj.total.out_sum += out_val

        # min_turnover filter
        result_rows = list(rows_by_cp.values())
        if min_turnover is not None and min_turnover > 0:
            result_rows = [r for r in result_rows if (r.total.in_sum + r.total.out_sum) >= min_turnover]

        result_rows.sort(
            key=lambda r: abs(r.total.in_sum) + abs(r.total.out_sum),
            reverse=True,
        )

        return CpTurnoverResult(
            rows=result_rows,
            period={"from": date_from, "to": date_to},
            currency=currency.upper(),
        )


@cached(prefix="reports:counterparty_turnovers", ttl=300)
async def get_counterparty_turnovers(
    db: AsyncSession,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    type: str | None = None,  # - mirrors API query arg
    currency: str | None = None,
) -> dict:
    """
    Pivot report: counterparties × months, in/out/net per currency.

    Returns dict:
        {
            "currencies": ["RUB", "CNY"],
            "months": ["2026-01", ...],
            "rows": [
                {
                    "counterparty_id": int,
                    "counterparty_name": str,
                    "inn": str | None,
                    "primary_type": str,
                    "currency": "RUB" | "CNY",
                    "monthly": {"2026-01": {"in": D, "out": D, "net": D}, ...},
                    "total": {"in": D, "out": D, "net": D},
                }, ...
            ],
        }
    """
    stmt = (
        select(
            Transaction.counterparty_id.label("cp_id"),
            Transaction.currency.label("currency"),
            func.to_char(Transaction.date, "YYYY-MM").label("month_str"),
            func.coalesce(func.sum(Transaction.income), 0).label("in_sum"),
            func.coalesce(func.sum(Transaction.expense), 0).label("out_sum"),
        )
        .where(
            Transaction.project_id == project_id,
            Transaction.is_deleted == False,  # noqa: E712
            Transaction.counterparty_id.isnot(None),
        )
        .group_by(Transaction.counterparty_id, Transaction.currency, "month_str")
    )

    if date_from:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.date <= date_to)
    if currency:
        stmt = stmt.where(Transaction.currency == currency.upper())

    res = await db.execute(stmt)
    rows = res.all()

    # Fetch counterparties for project (for name / type filter)
    cp_stmt = select(Counterparty).where(
        Counterparty.project_id == project_id,
        Counterparty.is_deleted == False,  # noqa: E712
    )
    if type:
        cp_stmt = cp_stmt.where(Counterparty.primary_type == type)
    cp_res = await db.execute(cp_stmt)
    counterparties = {cp.id: cp for cp in cp_res.scalars().all()}

    # Build pivot: key = (cp_id, currency)
    pivot: dict[tuple[int, str], dict] = {}
    months_set: set[str] = set()

    for row in rows:
        if row.cp_id not in counterparties:
            continue
        key = (row.cp_id, row.currency)
        bucket = pivot.setdefault(
            key,
            {
                "monthly": {},
                "total_in": Decimal("0"),
                "total_out": Decimal("0"),
            },
        )
        in_val = Decimal(str(row.in_sum or 0))
        out_val = Decimal(str(row.out_sum or 0))
        bucket["monthly"][row.month_str] = {
            "in": in_val,
            "out": out_val,
            "net": in_val - out_val,
        }
        bucket["total_in"] += in_val
        bucket["total_out"] += out_val
        months_set.add(row.month_str)

    months = sorted(months_set)
    result_rows: list[dict] = []
    for (cp_id, cur), bucket in pivot.items():
        cp = counterparties[cp_id]
        total_in = bucket["total_in"]
        total_out = bucket["total_out"]
        result_rows.append(
            {
                "counterparty_id": cp_id,
                "counterparty_name": cp.name,
                "inn": cp.inn,
                "primary_type": cp.primary_type,
                "currency": cur,
                "monthly": {
                    # Decimal → float for JSON serialisation (via _DecimalEncoder)
                    m: {
                        "in": float(v["in"]),
                        "out": float(v["out"]),
                        "net": float(v["net"]),
                    }
                    for m, v in bucket["monthly"].items()
                },
                "total": {
                    "in": float(total_in),
                    "out": float(total_out),
                    "net": float(total_in - total_out),
                },
            }
        )

    # Sort: highest absolute turnover first
    result_rows.sort(
        key=lambda r: abs(r["total"]["in"]) + abs(r["total"]["out"]),
        reverse=True,
    )

    currencies = sorted({r["currency"] for r in result_rows})
    return {
        "currencies": currencies,
        "months": months,
        "rows": result_rows,
    }
