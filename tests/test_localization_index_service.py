"""Тесты сервиса localization_index_service.

Покрытие:
- happy path с WB-канонiческим примером (37%/62%, по 100 заказов → ИРП=1.05%)
- multi-tenancy (другой проект не виден)
- пустой период
- per-SKU агрегация
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.localization_index_service import (
    _aggregate_by_sku,
    _build_sku_rows,
    _build_summary,
    get_by_sku,
    get_daily,
    get_summary,
)

D = Decimal


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


async def _create_loc_funnel_row(
    db: AsyncSession,
    project_id: int,
    nm_id: int,
    funnel_date: date,
    orders_count: int,
    localization_percent: float | None,
    *,
    vendor_code: str | None = None,
    subject: str | None = None,
    brand: str | None = None,
    time_to_ready_minutes: int | None = None,
):
    """Insert a wb_funnel_daily row with localization data filled in."""
    await db.execute(
        text(
            "INSERT INTO wb_funnel_daily "
            "(project_id, nm_id, date, orders_count, orders_sum_rub, "
            "localization_percent, time_to_ready_minutes, "
            "vendor_code, subject, brand, "
            "open_card, add_to_cart, stocks_wb, stocks_mp, "
            "adv_views, adv_clicks, adv_sum) "
            "VALUES (:pid, :nm, :d, :oc, 0, "
            ":loc, :ttr, :vc, :subj, :br, "
            "0, 0, 0, 0, 0, 0, 0)"
        ),
        {
            "pid": project_id,
            "nm": nm_id,
            "d": funnel_date,
            "oc": orders_count,
            "loc": localization_percent,
            "ttr": time_to_ready_minutes,
            "vc": vendor_code,
            "subj": subject,
            "br": brand,
        },
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Pure-formula tests (no DB) — на _build_sku_rows / _build_summary
# ═══════════════════════════════════════════════════════════════════════════════


class TestWbCanonicalExample:
    """WB-канонiческий пример из официальной инструкции:

    2 артикула, по 100 заказов:
        SKU1: localization=37% → КРП=2.10
        SKU2: localization=62% → КРП=0.00 (≥60%)

    ИРП (взвешенно) = (100*2.10 + 100*0.00) / 200 = 1.05%
    """

    def test_irp_is_1_05_percent(self):
        agg = {
            1: {
                "orders": 100,
                "weighted_loc_sum": D("37.00") * D("100"),
                "vendor_code": "VC1",
                "subject": "S1",
                "brand": "B",
            },
            2: {
                "orders": 100,
                "weighted_loc_sum": D("62.00") * D("100"),
                "vendor_code": "VC2",
                "subject": "S2",
                "brand": "B",
            },
        }
        rows = _build_sku_rows(agg)
        summary = _build_summary(rows)
        assert summary["irp_percent"] == D("1.05"), f"Expected ИРП=1.05% (WB example), got {summary['irp_percent']}"

    def test_localization_index_is_weighted_ktr(self):
        """ИЛ = средневзвеш. КТР. SKU1 КТР=1.40 (37%), SKU2 КТР=1.00 (62%)."""
        agg = {
            1: {
                "orders": 100,
                "weighted_loc_sum": D("37.00") * D("100"),
                "vendor_code": "A",
                "subject": None,
                "brand": None,
            },
            2: {
                "orders": 100,
                "weighted_loc_sum": D("62.00") * D("100"),
                "vendor_code": "B",
                "subject": None,
                "brand": None,
            },
        }
        rows = _build_sku_rows(agg)
        summary = _build_summary(rows)
        # (100*1.40 + 100*1.00) / 200 = 1.20
        assert summary["localization_index"] == D("1.20"), summary["localization_index"]

    def test_articles_local_count_at_60_threshold(self):
        """SKU2 (62%) считается локализованным (КРП=0), SKU1 (37%) — критическим."""
        agg = {
            1: {
                "orders": 100,
                "weighted_loc_sum": D("37.00") * D("100"),
                "vendor_code": None,
                "subject": None,
                "brand": None,
            },
            2: {
                "orders": 100,
                "weighted_loc_sum": D("62.00") * D("100"),
                "vendor_code": None,
                "subject": None,
                "brand": None,
            },
        }
        rows = _build_sku_rows(agg)
        summary = _build_summary(rows)
        assert summary["articles_local_count"] == 1
        assert summary["articles_critical_count"] == 1
        assert summary["articles_count"] == 2

    def test_local_orders_split(self):
        """SKU1 (37%, 100 orders) → ~37 local, 63 non_local."""
        agg = {
            1: {
                "orders": 100,
                "weighted_loc_sum": D("37.00") * D("100"),
                "vendor_code": None,
                "subject": None,
                "brand": None,
            },
        }
        rows = _build_sku_rows(agg)
        assert rows[0]["local"] == 37
        assert rows[0]["non_local"] == 63
        assert rows[0]["total"] == 100

    def test_empty_aggregate(self):
        """Без данных — нулевые KPI и пустой список."""
        rows = _build_sku_rows({})
        summary = _build_summary(rows)
        assert rows == []
        assert summary["total_orders"] == 0
        assert summary["irp_percent"] == D("0.00")
        # ИЛ при отсутствии данных — нейтральный 1.00 (избегаем deception 0.00)
        assert summary["localization_index"] == D("1")
        assert summary["articles_count"] == 0


class TestAggregation:
    """Корректность взвешенного среднего по дням."""

    def test_weighted_avg_loc_pct(self):
        """SKU присутствует 2 дня с разными loc_pct и orders. loc_pct = взвеш. среднее."""

        # Два дня: day1 — 100 заказов с loc=80, day2 — 50 заказов с loc=20
        # Взвешенное: (100*80 + 50*20)/150 = 9000/150 = 60.00
        class _Row:
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)

        r1 = _Row(
            nm_id=1,
            orders_count=100,
            localization_percent=D("80.00"),
            vendor_code="A",
            subject="S",
            brand="B",
        )
        r2 = _Row(
            nm_id=1,
            orders_count=50,
            localization_percent=D("20.00"),
            vendor_code=None,
            subject=None,
            brand=None,
        )
        agg = _aggregate_by_sku([r1, r2])
        rows = _build_sku_rows(agg)
        assert len(rows) == 1
        assert rows[0]["loc_pct"] == D("60.00")
        assert rows[0]["total"] == 150

    def test_skips_zero_orders_and_null_loc(self):
        """Строки без orders или без localization_percent игнорируются."""

        class _Row:
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)

        r1 = _Row(
            nm_id=1,
            orders_count=0,
            localization_percent=D("80.00"),
            vendor_code=None,
            subject=None,
            brand=None,
        )
        r2 = _Row(
            nm_id=2,
            orders_count=10,
            localization_percent=None,
            vendor_code=None,
            subject=None,
            brand=None,
        )
        agg = _aggregate_by_sku([r1, r2])
        assert agg == {}


# ═══════════════════════════════════════════════════════════════════════════════
# DB-tests (use db_session + project / other_project fixtures)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetSummaryDB:
    """Сервис на реальной БД: фильтрация по project_id, период."""

    @pytest.mark.asyncio
    async def test_empty_period(self, db_session: AsyncSession, project):
        """Нет данных → нулевые KPI и пустой список."""
        result = await get_summary(db_session, project.id, "2026-04-01", "2026-04-30")
        assert result["total_orders"] == 0
        assert result["articles_count"] == 0

    @pytest.mark.asyncio
    async def test_happy_path_two_skus(self, db_session: AsyncSession, project):
        """Канонический WB-пример через БД: ИРП = 1.05%."""
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=1,
            funnel_date=date(2026, 4, 10),
            orders_count=100,
            localization_percent=37.00,
            vendor_code="LOC-1",
        )
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=2,
            funnel_date=date(2026, 4, 10),
            orders_count=100,
            localization_percent=62.00,
            vendor_code="LOC-2",
        )
        result = await get_summary(db_session, project.id, "2026-04-01", "2026-04-30")
        assert Decimal(str(result["irp_percent"])) == D("1.05"), result["irp_percent"]
        assert Decimal(str(result["localization_index"])) == D("1.20")
        assert result["total_orders"] == 200
        assert result["articles_count"] == 2

    @pytest.mark.asyncio
    async def test_multi_tenancy(self, db_session: AsyncSession, project, other_project):
        """Данные другого проекта не попадают в выборку."""
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=10,
            funnel_date=date(2026, 4, 10),
            orders_count=100,
            localization_percent=37.00,
        )
        await _create_loc_funnel_row(
            db_session,
            other_project.id,
            nm_id=10,
            funnel_date=date(2026, 4, 10),
            orders_count=999,  # должен быть невидим
            localization_percent=10.00,
        )
        result = await get_summary(db_session, project.id, "2026-04-01", "2026-04-30")
        assert result["total_orders"] == 100, "other_project не должен попасть"

    @pytest.mark.asyncio
    async def test_period_filter(self, db_session: AsyncSession, project):
        """Строки вне диапазона не учитываются."""
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=20,
            funnel_date=date(2026, 1, 10),  # вне периода
            orders_count=50,
            localization_percent=80.00,
        )
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=21,
            funnel_date=date(2026, 4, 10),  # внутри периода
            orders_count=80,
            localization_percent=80.00,
        )
        result = await get_summary(db_session, project.id, "2026-04-01", "2026-04-30")
        assert result["total_orders"] == 80, "только апрельская строка"

    @pytest.mark.asyncio
    async def test_get_by_sku_returns_per_article(self, db_session: AsyncSession, project):
        """get_by_sku отдаёт per-article строки с КТР/КРП/статусом."""
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=30,
            funnel_date=date(2026, 4, 10),
            orders_count=10,
            localization_percent=90.00,
            vendor_code="VC30",
        )
        rows = await get_by_sku(db_session, project.id, "2026-04-01", "2026-04-30")
        assert len(rows) == 1
        row = rows[0]
        assert int(row["nm_id"]) == 30
        # 90% → КТР=0.60 (90..95), КРП=0.00 (≥60), статус excellent
        assert Decimal(str(row["ktr"])) == D("0.60")
        assert Decimal(str(row["krp"])) == D("0.00")
        assert row["status"] == "excellent"


class TestGetDailyDB:
    """get_daily — динамика ИЛ/ИРП по дням за период."""

    @pytest.mark.asyncio
    async def test_empty_period(self, db_session: AsyncSession, project):
        """Нет данных → пустой список (UI рисует «нет данных»)."""
        result = await get_daily(db_session, project.id, "2026-04-01", "2026-04-30")
        assert result == []

    @pytest.mark.asyncio
    async def test_two_days_separate_points(self, db_session: AsyncSession, project):
        """Каждая дата — отдельная точка с собственным взвешенным КТР/КРП."""
        # День 1: 1 SKU, 100 заказов, 37% → КТР=1.40, КРП=2.10, ИЛ=1.40, ИРП=2.10
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=1,
            funnel_date=date(2026, 4, 10),
            orders_count=100,
            localization_percent=37.00,
            vendor_code="A",
        )
        # День 2: 1 SKU, 50 заказов, 90% → КТР=0.60, КРП=0.00, ИЛ=0.60, ИРП=0.00
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=1,
            funnel_date=date(2026, 4, 11),
            orders_count=50,
            localization_percent=90.00,
            vendor_code="A",
        )
        result = await get_daily(db_session, project.id, "2026-04-01", "2026-04-30")
        assert len(result) == 2
        assert result[0]["date"] == "2026-04-10"
        assert Decimal(str(result[0]["localization_index"])) == D("1.40")
        assert Decimal(str(result[0]["irp_percent"])) == D("2.10")
        assert result[0]["total_orders"] == 100
        assert result[1]["date"] == "2026-04-11"
        assert Decimal(str(result[1]["localization_index"])) == D("0.60")
        assert Decimal(str(result[1]["irp_percent"])) == D("0.00")
        assert result[1]["total_orders"] == 50

    @pytest.mark.asyncio
    async def test_multi_tenancy(self, db_session: AsyncSession, project, other_project):
        """Другой проект не попадает в выборку."""
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=1,
            funnel_date=date(2026, 4, 10),
            orders_count=100,
            localization_percent=80.00,
        )
        await _create_loc_funnel_row(
            db_session,
            other_project.id,
            nm_id=1,
            funnel_date=date(2026, 4, 10),
            orders_count=999,
            localization_percent=10.00,
        )
        result = await get_daily(db_session, project.id, "2026-04-01", "2026-04-30")
        assert len(result) == 1
        assert result[0]["total_orders"] == 100

    @pytest.mark.asyncio
    async def test_subject_filter(self, db_session: AsyncSession, project):
        """Фильтр по subject отсекает строки с другим предметом."""
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=1,
            funnel_date=date(2026, 4, 10),
            orders_count=100,
            localization_percent=80.00,
            subject="Платья",
        )
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=2,
            funnel_date=date(2026, 4, 10),
            orders_count=50,
            localization_percent=20.00,
            subject="Брюки",
        )
        result = await get_daily(
            db_session,
            project.id,
            "2026-04-01",
            "2026-04-30",
            subject="Платья",
        )
        assert len(result) == 1
        assert result[0]["total_orders"] == 100

    @pytest.mark.asyncio
    async def test_brand_filter(self, db_session: AsyncSession, project):
        """Фильтр по brand отсекает строки с другим брендом."""
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=1,
            funnel_date=date(2026, 4, 10),
            orders_count=100,
            localization_percent=80.00,
            brand="X",
        )
        await _create_loc_funnel_row(
            db_session,
            project.id,
            nm_id=2,
            funnel_date=date(2026, 4, 10),
            orders_count=50,
            localization_percent=20.00,
            brand="Y",
        )
        result = await get_daily(
            db_session,
            project.id,
            "2026-04-01",
            "2026-04-30",
            brand="Y",
        )
        assert len(result) == 1
        assert result[0]["total_orders"] == 50

    @pytest.mark.asyncio
    async def test_dates_sorted_asc(self, db_session: AsyncSession, project):
        """Точки возвращаются упорядоченными по дате (ASC)."""
        for d in [date(2026, 4, 15), date(2026, 4, 10), date(2026, 4, 12)]:
            await _create_loc_funnel_row(
                db_session,
                project.id,
                nm_id=1,
                funnel_date=d,
                orders_count=10,
                localization_percent=80.00,
            )
        result = await get_daily(db_session, project.id, "2026-04-01", "2026-04-30")
        assert [p["date"] for p in result] == ["2026-04-10", "2026-04-12", "2026-04-15"]
