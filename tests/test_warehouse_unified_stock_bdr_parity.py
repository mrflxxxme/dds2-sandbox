"""
BDR-parity tests for `_build_finance_query` in warehouse_stock_engine.

The unified-stock report builds its own finance aggregation over
`wb_finance_rows`. For numbers on «Единые остатки» to match БДР row-for-row,
the filter MUST mirror `wb_bdr_helpers._DATE_FILTER` + the неопознанный-товар
exclusion applied in `build_bdr_aggregate_sql`.

Prior to the fix (2026-04-15) the unified-stock query:
  1. missed WB finance rows where sale_dt IS NULL AND rr_dt IS NULL but
     date_from/date_to fell inside the period (BDR includes them via the
     NULL-date fallback branch);
  2. did not exclude the `sa_name = 'Неопознанный товар'` bucket, which BDR
     filters out explicitly so it never contributes to realization.

The tests below lock both behaviours so the two reports cannot drift again.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from backend.services.warehouse_stock_engine import _build_finance_query

# ─── Test 1: SQL compilation contract (pure-function, no DB) ──────────────────


class TestFinanceQuerySqlContract:
    """Compile the Select to SQL text and assert BDR-parity clauses are present."""

    def _compile(self) -> str:
        stmt = _build_finance_query(
            project_id=1,
            cutoff=date(2026, 4, 1),
            today=date(2026, 4, 15),
        )
        return str(
            stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).lower()

    def test_null_date_fallback_present(self):
        """Rows with NULL sale_dt/rr_dt but date_from/date_to in period must be included."""
        sql = self._compile()
        assert "sale_dt is null" in sql, "NULL-date fallback missing — BDR parity broken"
        assert "rr_dt is null" in sql, "NULL-date fallback missing — BDR parity broken"
        assert "date_from" in sql, "date_from bound missing from NULL-date fallback"
        assert "date_to" in sql, "date_to bound missing from NULL-date fallback"

    def test_excludes_unrecognized_bucket(self):
        """sa_name = 'Неопознанный товар' must be excluded (BDR does this too)."""
        sql = self._compile()
        assert "неопознанный товар" in sql, (
            "Неопознанный-товар exclusion missing — unified stock will leak " "realization that BDR filters out"
        )

    def test_effective_date_between_still_present(self):
        """Primary COALESCE(sale_dt, rr_dt) BETWEEN branch is unchanged."""
        sql = self._compile()
        assert "coalesce(wb_finance_rows.sale_dt, wb_finance_rows.rr_dt)" in sql
        assert "between" in sql

    def test_project_id_filter_preserved(self):
        """project_id filter must stay — multi-tenancy guard."""
        sql = self._compile()
        assert "wb_finance_rows.project_id" in sql


# ─── Test 2: integration — seed rows, execute, compare BDR pickup ─────────────


def _uid(base: int) -> int:
    """Unique rrd_id per row in a test run — UUID entropy avoids collisions
    when the test is re-run against a dirty DB without cleaning wb_finance_rows.
    """
    import uuid

    return base * 10**9 + (uuid.uuid4().int % 10**9)


async def _insert_row(db, project_id: int, **kwargs) -> None:
    """Insert one minimal wb_finance_rows record. Honours NULL-able dates."""
    params = {
        "pid": project_id,
        "rrd": kwargs["rrd_id"],
        "df": kwargs["date_from"],
        "dt": kwargs["date_to"],
        "sa": kwargs["sa_name"],
        "nm": kwargs["nm_id"],
        "br": kwargs.get("brand_name", "TestBrand"),
        "subj": kwargs.get("subject_name", "TestSubject"),
        "rrdt": kwargs.get("rr_dt"),
        "sdt": kwargs.get("sale_dt"),
        "doc": kwargs.get("doc_type_name", "Продажа"),
        "oper": kwargs.get("supplier_oper_name", "Продажа"),
        "bonus": kwargs.get("bonus_type_name"),
        "q": kwargs.get("quantity", 1),
        "retail": kwargs.get("retail_price_withdisc_rub", 0),
        "retail_amt": kwargs.get("retail_amount", 0),
        "ppvz": kwargs.get("ppvz_for_pay", 0),
    }
    await db.execute(
        text(
            """
            INSERT INTO wb_finance_rows (
                project_id, realizationreport_id, rrd_id, date_from, date_to,
                sa_name, nm_id, brand_name, subject_name,
                rr_dt, sale_dt, doc_type_name, supplier_oper_name, bonus_type_name,
                quantity, retail_price_withdisc_rub, retail_amount,
                ppvz_for_pay, synced_at
            ) VALUES (
                :pid, 0, :rrd, :df, :dt,
                :sa, :nm, :br, :subj,
                :rrdt, :sdt, :doc, :oper, :bonus,
                :q, :retail, :retail_amt,
                :ppvz, NOW()
            )
            """
        ),
        params,
    )


@pytest.mark.asyncio
async def test_finance_query_bdr_parity_row_selection(db_session, project):
    """End-to-end: seed 3 rows that exercise both divergent filters, run the
    unified-stock query, assert it picks up exactly the same set of rows as the
    BDR filter would.

    Kept as a single test function (not split) because the shared per-test
    fixture cycle is susceptible to the known asyncpg event-loop bug.
    """
    cutoff = date(2026, 4, 1)
    today = date(2026, 4, 15)

    # Row A: normal sale with sale_dt inside period → BOTH BDR and unified should include.
    await _insert_row(
        db_session,
        project.id,
        rrd_id=_uid(1),
        date_from=cutoff,
        date_to=today,
        sa_name="GOOD-A",
        nm_id=111_001,
        sale_dt=date(2026, 4, 10),
        retail_price_withdisc_rub=Decimal("1000"),
        quantity=2,
    )

    # Row B: NULL sale_dt AND rr_dt, but date_from/date_to inside the period.
    # BDR includes it via the NULL-date fallback; unified MUST too after the fix.
    await _insert_row(
        db_session,
        project.id,
        rrd_id=_uid(2),
        date_from=date(2026, 4, 5),
        date_to=date(2026, 4, 12),
        sa_name="GOOD-B",
        nm_id=111_002,
        sale_dt=None,
        rr_dt=None,
        retail_price_withdisc_rub=Decimal("500"),
        quantity=1,
    )

    # Row C: sa_name = 'Неопознанный товар' with an ordinary sale_dt.
    # BDR filters it out explicitly; unified MUST too after the fix.
    await _insert_row(
        db_session,
        project.id,
        rrd_id=_uid(3),
        date_from=cutoff,
        date_to=today,
        sa_name="Неопознанный товар",
        nm_id=111_003,
        sale_dt=date(2026, 4, 10),
        retail_price_withdisc_rub=Decimal("9999"),
        quantity=1,
    )

    await db_session.commit()

    stmt = _build_finance_query(project.id, cutoff, today)
    result = await db_session.execute(stmt)
    rows = {row.nm_id: row for row in result.all()}

    # Row A must be present (baseline — already worked).
    assert 111_001 in rows, "normal sale_dt row dropped — primary filter broken"
    assert Decimal(str(rows[111_001].realization)) == Decimal("1000")

    # Row B must be present (NULL-date fallback — the fix).
    assert 111_002 in rows, (
        "NULL sale_dt/rr_dt row with date_from/date_to inside period was dropped "
        "— unified stock diverges from BDR which includes this branch"
    )
    assert Decimal(str(rows[111_002].realization)) == Decimal("500")

    # Row C must be absent (Неопознанный товар exclusion — the fix).
    assert 111_003 not in rows, (
        "'Неопознанный товар' bucket leaked into unified stock — BDR filters "
        "this out, so the two reports will diverge by the leaked amount"
    )
