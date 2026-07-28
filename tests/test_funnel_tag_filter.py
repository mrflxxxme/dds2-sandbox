"""
Tests for resolve_filter_nm_ids + nm_ids-фильтрация в запросах воронки.

Серверный фильтр по ярлыку/склейке для всех группировок (включая «По дням»).
"""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.funnel.queries import (
    NO_TAG_SENTINEL,
    get_funnel_aggregated,
    resolve_filter_nm_ids,
)
from backend.utils.time import utcnow

TAX_INFO = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _make_tag(db: AsyncSession, pid: int, name: str, nm_ids: list[int]) -> None:
    tag_id = (
        await db.execute(
            text(
                "INSERT INTO product_tags (project_id, name, color, is_deleted, updated_at) "
                "VALUES (:pid, :name, '#3B82F6', false, NOW()) RETURNING id"
            ),
            {"pid": pid, "name": name},
        )
    ).scalar()
    for nm in nm_ids:
        await db.execute(
            text("INSERT INTO product_tag_map (project_id, tag_id, nm_id) VALUES (:pid, :tag, :nm)"),
            {"pid": pid, "tag": tag_id, "nm": nm},
        )
    await db.commit()


async def _add_funnel_day(db: AsyncSession, pid: int, nm_id: int, d: date, orders: int) -> None:
    await db.execute(
        text(
            "INSERT INTO wb_funnel_daily "
            "(project_id, date, nm_id, open_card, add_to_cart, orders_count, orders_sum_rub, "
            " stocks_wb, stocks_mp, adv_views, adv_clicks, adv_sum) "
            "VALUES (:pid, :d, :nm, 10, 5, :cnt, :cnt * 100, 0, 0, 0, 0, 0)"
        ),
        {"pid": pid, "d": d, "nm": nm_id, "cnt": orders},
    )
    await db.commit()


class TestResolveFilterNmIds:
    @pytest.mark.asyncio
    async def test_no_filters_returns_none(self, db_session, project):
        assert await resolve_filter_nm_ids(db_session, project.id) is None
        assert await resolve_filter_nm_ids(db_session, project.id, tag=None, imt=None) is None

    @pytest.mark.asyncio
    async def test_tag_resolves_nm_ids(self, db_session, project):
        tag_name = f"tag-{_uid()}"
        await _make_tag(db_session, project.id, tag_name, [911001, 911002])

        result = await resolve_filter_nm_ids(db_session, project.id, tag=tag_name)

        assert result == {911001, 911002}

    @pytest.mark.asyncio
    async def test_unknown_tag_gives_empty_set(self, db_session, project):
        result = await resolve_filter_nm_ids(db_session, project.id, tag=f"nope-{_uid()}")
        assert result == set()

    @pytest.mark.asyncio
    async def test_no_tag_sentinel_returns_only_untagged(self, db_session, project):
        """`tag='__none__'` → товары БЕЗ ярлыка (есть в nomenclature, нет в tag_map)."""
        tagged_nm, untagged_nm = 911030, 911031
        for nm in (tagged_nm, untagged_nm):
            await db_session.execute(
                text(
                    "INSERT INTO nomenclature (project_id, barcode, article_wb, updated_at) "
                    "VALUES (:pid, :bc, :nm, NOW())"
                ),
                {"pid": project.id, "bc": f"NT-{_uid()}", "nm": nm},
            )
        await db_session.commit()
        await _make_tag(db_session, project.id, f"tag-{_uid()}", [tagged_nm])

        result = await resolve_filter_nm_ids(db_session, project.id, tag=NO_TAG_SENTINEL)

        assert result is not None
        assert untagged_nm in result
        assert tagged_nm not in result

    @pytest.mark.asyncio
    async def test_no_tag_sentinel_project_isolation(self, db_session, project, other_project):
        """Товар без ярлыка из чужого проекта не попадает в выборку."""
        alien_nm = 911032
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, article_wb, updated_at) "
                "VALUES (:pid, :bc, :nm, NOW())"
            ),
            {"pid": other_project.id, "bc": f"NT-{_uid()}", "nm": alien_nm},
        )
        await db_session.commit()

        result = await resolve_filter_nm_ids(db_session, project.id, tag=NO_TAG_SENTINEL)

        assert result is not None
        assert alien_nm not in result

    @pytest.mark.asyncio
    async def test_tag_project_isolation(self, db_session, project, other_project):
        tag_name = f"tag-{_uid()}"
        await _make_tag(db_session, other_project.id, tag_name, [911003])

        result = await resolve_filter_nm_ids(db_session, project.id, tag=tag_name)

        assert result == set()

    @pytest.mark.asyncio
    async def test_imt_by_alias_and_hash_form(self, db_session, project):
        imt_id = 555001
        barcode = f"TF-{_uid()}"
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, article_wb, imt_id, updated_at) "
                "VALUES (:pid, :bc, :nm, :imt, NOW())"
            ),
            {"pid": project.id, "bc": barcode, "nm": 911004, "imt": imt_id},
        )
        alias = f"alias-{_uid()}"
        await db_session.execute(
            text("INSERT INTO imt_aliases (project_id, imt_id, name) VALUES (:pid, :imt, :name)"),
            {"pid": project.id, "imt": imt_id, "name": alias},
        )
        await db_session.commit()

        assert await resolve_filter_nm_ids(db_session, project.id, imt=alias) == {911004}
        assert await resolve_filter_nm_ids(db_session, project.id, imt=f"#{imt_id}") == {911004}


class TestNmIdsFilterInQueries:
    @pytest.mark.asyncio
    async def test_day_aggregation_respects_nm_ids(self, db_session, project):
        """Группировка «По дням» суммирует только товары из nm_ids."""
        d = utcnow().date() - timedelta(days=2)
        nm_in, nm_out = 911005, 911006
        await _add_funnel_day(db_session, project.id, nm_in, d, orders=3)
        await _add_funnel_day(db_session, project.id, nm_out, d, orders=7)

        df, dt = (d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat()
        all_rows = await get_funnel_aggregated(db_session, project.id, TAX_INFO, df, dt, None, None)
        filtered = await get_funnel_aggregated(db_session, project.id, TAX_INFO, df, dt, None, None, nm_ids={nm_in})

        assert sum(r["orders_count"] for r in all_rows) == 10
        assert sum(r["orders_count"] for r in filtered) == 3

    @pytest.mark.asyncio
    async def test_empty_nm_ids_returns_no_rows(self, db_session, project):
        d = utcnow().date() - timedelta(days=2)
        await _add_funnel_day(db_session, project.id, 911007, d, orders=4)

        df, dt = (d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat()
        rows = await get_funnel_aggregated(db_session, project.id, TAX_INFO, df, dt, None, None, nm_ids=set())

        assert rows == []
