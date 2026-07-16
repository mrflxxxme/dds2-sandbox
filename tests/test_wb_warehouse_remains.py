"""Tests: wb_warehouse_remains — зеркало отчёта WB «Остатки на складах».

Покрывает:
- sync_warehouse_remains: разбор строк отчёта (warehouses[] с псевдо-складами),
  full replace, дедуп ключей, пустой отчёт не стирает данные, изоляция project_id;
- get_unified_stock_summary: колонка «WB склады» из remains (join по barcode,
  псевдо-склады «В пути…» входят в total_wb, итоговая строка «Всего находится
  на складах» исключена), fallback на nm_id для незаведённого баркода,
  fallback на statistics supplier/stocks пока remains пуст.
"""

import pytest
from sqlalchemy import delete, select, text

from backend.models import Nomenclature, WbWarehouseRemains, WbWarehouseStock
from backend.services.warehouse_stock_engine import (
    WB_REMAINS_TOTAL_ROW,
    get_unified_stock_summary,
)
from backend.services.warehouse_stock_service import sync_warehouse_remains


def _report_row(nm_id: int, barcode: str, warehouses: list[tuple[str, int]], **kw) -> dict:
    """One row of the WB analytics warehouse_remains download payload."""
    return {
        "brand": kw.get("brand", "Уютопия"),
        "subjectName": kw.get("subject", "Дивандеки"),
        "vendorCode": kw.get("vendor_code", "DIVANDEK_210x90_160x90_кофе"),
        "nmId": nm_id,
        "barcode": barcode,
        "techSize": "0",
        "volume": 1.33,
        "warehouses": [{"warehouseName": n, "quantity": q} for n, q in warehouses],
    }


@pytest.fixture
async def _clean_remains(db_session, project, other_project):
    for pid in (project.id, other_project.id):
        await db_session.execute(delete(WbWarehouseRemains).where(WbWarehouseRemains.project_id == pid))
        await db_session.execute(delete(WbWarehouseStock).where(WbWarehouseStock.project_id == pid))
        await db_session.execute(delete(Nomenclature).where(Nomenclature.project_id == pid))
    await db_session.execute(
        text(
            "SELECT setval('wb_warehouse_remains_id_seq', COALESCE((SELECT MAX(id) FROM wb_warehouse_remains), 0) + 1, false)"
        )
    )
    await db_session.commit()
    yield


# ═══════════════════════════════════════════════════════════════════════════
# sync_warehouse_remains
# ═══════════════════════════════════════════════════════════════════════════


class TestSyncWarehouseRemains:
    async def test_parses_rows_including_pseudo_warehouses(self, _clean_remains, db_session, project):
        items = [
            _report_row(
                396063762,
                "2043740032052",
                [
                    ("В пути до получателей", 307),
                    ("В пути возвраты на склад WB", 49),
                    (WB_REMAINS_TOTAL_ROW, 1249),
                    ("Коледино", 700),
                    ("Казань", 549),
                ],
            )
        ]
        count = await sync_warehouse_remains(db_session, project.id, items)
        assert count == 5

        rows = (
            (
                await db_session.execute(
                    select(WbWarehouseRemains).where(WbWarehouseRemains.project_id == project.id)
                )
            )
            .scalars()
            .all()
        )
        by_wh = {r.warehouse_name: r.quantity for r in rows}
        assert by_wh[WB_REMAINS_TOTAL_ROW] == 1249
        assert by_wh["В пути до получателей"] == 307
        assert by_wh["Коледино"] == 700
        assert all(r.barcode == "2043740032052" and r.nm_id == 396063762 for r in rows)

    async def test_full_replace_drops_stale_rows(self, _clean_remains, db_session, project):
        await sync_warehouse_remains(
            db_session, project.id, [_report_row(1, "bc-1", [("Коледино", 10), ("Тула", 5)])]
        )
        await sync_warehouse_remains(db_session, project.id, [_report_row(1, "bc-1", [("Коледино", 3)])])

        rows = (
            (
                await db_session.execute(
                    select(WbWarehouseRemains).where(WbWarehouseRemains.project_id == project.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].warehouse_name == "Коледино"
        assert rows[0].quantity == 3

    async def test_empty_report_keeps_previous_data(self, _clean_remains, db_session, project):
        await sync_warehouse_remains(db_session, project.id, [_report_row(1, "bc-1", [("Коледино", 10)])])
        count = await sync_warehouse_remains(db_session, project.id, [])
        assert count == 0

        rows = (
            (
                await db_session.execute(
                    select(WbWarehouseRemains).where(WbWarehouseRemains.project_id == project.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1  # старые данные не стёрты

    async def test_duplicate_keys_are_aggregated(self, _clean_remains, db_session, project):
        # Тот же (nm, barcode, warehouse) дважды — не CardinalityViolation, а сумма.
        items = [
            _report_row(1, "bc-1", [("Коледино", 10)]),
            _report_row(1, "bc-1", [("Коледино", 7)]),
        ]
        count = await sync_warehouse_remains(db_session, project.id, items)
        assert count == 1

        row = (
            await db_session.execute(
                select(WbWarehouseRemains).where(WbWarehouseRemains.project_id == project.id)
            )
        ).scalar_one()
        assert row.quantity == 17

    async def test_project_isolation(self, _clean_remains, db_session, project, other_project):
        await sync_warehouse_remains(db_session, project.id, [_report_row(1, "bc-1", [("Коледино", 10)])])
        await sync_warehouse_remains(
            db_session, other_project.id, [_report_row(2, "bc-2", [("Казань", 99)])]
        )
        # Full replace первого проекта не должен трогать второй
        await sync_warehouse_remains(db_session, project.id, [_report_row(1, "bc-1", [("Коледино", 4)])])

        other_rows = (
            (
                await db_session.execute(
                    select(WbWarehouseRemains).where(WbWarehouseRemains.project_id == other_project.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(other_rows) == 1
        assert other_rows[0].quantity == 99


# ═══════════════════════════════════════════════════════════════════════════
# get_unified_stock_summary — колонка «WB склады» из remains
# ═══════════════════════════════════════════════════════════════════════════


class TestUnifiedStockFromRemains:
    async def test_wb_column_matches_cabinet_math(self, _clean_remains, db_session, project):
        """Кабинет: 1249 на складах + 307 к клиенту + 49 возвраты = 1605.
        total_wb должен дать 1605; итоговая псевдо-строка не удваивает сумму."""
        db_session.add(
            Nomenclature(
                project_id=project.id,
                barcode="2043740032052",
                article_seller="DIVANDEK_210x90_160x90_кофе",
                article_wb=396063762,
                subject="Дивандеки",
                brand="Уютопия",
            )
        )
        await db_session.commit()
        await sync_warehouse_remains(
            db_session,
            project.id,
            [
                _report_row(
                    396063762,
                    "2043740032052",
                    [
                        ("В пути до получателей", 307),
                        ("В пути возвраты на склад WB", 49),
                        (WB_REMAINS_TOTAL_ROW, 1249),
                        ("Коледино", 700),
                        ("Казань", 549),
                    ],
                )
            ],
        )

        rows = await get_unified_stock_summary(db_session, project.id, group_by="sku")
        row = next(r for r in rows if r["barcode"] == "2043740032052")
        assert row["total_wb"] == 1605
        assert row["wb_stocks"]["Коледино"] == 700
        assert row["wb_stocks"]["В пути до получателей"] == 307
        assert row["wb_stocks"]["В пути возвраты на склад WB"] == 49
        assert WB_REMAINS_TOTAL_ROW not in row["wb_stocks"]

    async def test_unknown_barcode_falls_back_to_nm_id_join(self, _clean_remains, db_session, project):
        """Баркода из отчёта нет в номенклатуре → добор по article_wb."""
        db_session.add(
            Nomenclature(
                project_id=project.id,
                barcode="another-barcode",
                article_seller="art-nm-only",
                article_wb=555,
            )
        )
        await db_session.commit()
        await sync_warehouse_remains(
            db_session, project.id, [_report_row(555, "unknown-bc", [("Тула", 21)])]
        )

        rows = await get_unified_stock_summary(db_session, project.id, group_by="sku")
        row = next(r for r in rows if r["article_wb"] == 555)
        assert row["total_wb"] == 21

    async def test_fallback_to_supplier_stocks_when_no_remains(self, _clean_remains, db_session, project):
        """Пока remains не синкались — старый источник (quantity_full)."""
        db_session.add(
            Nomenclature(
                project_id=project.id,
                barcode="bc-legacy",
                article_seller="art-legacy",
                article_wb=777,
            )
        )
        db_session.add(
            WbWarehouseStock(
                project_id=project.id,
                nm_id=777,
                warehouse_name="Коледино",
                quantity=5,
                quantity_full=8,
                in_way_to_client=2,
                in_way_from_client=1,
            )
        )
        await db_session.commit()

        rows = await get_unified_stock_summary(db_session, project.id, group_by="sku")
        row = next(r for r in rows if r["article_wb"] == 777)
        assert row["total_wb"] == 8

    async def test_remains_of_other_project_do_not_leak(
        self, _clean_remains, db_session, project, other_project
    ):
        """remains есть только у чужого проекта → наш работает по fallback-пути."""
        await sync_warehouse_remains(
            db_session, other_project.id, [_report_row(9, "bc-9", [("Казань", 50)])]
        )
        db_session.add(
            Nomenclature(project_id=project.id, barcode="bc-9", article_seller="a9", article_wb=9)
        )
        db_session.add(
            WbWarehouseStock(
                project_id=project.id,
                nm_id=9,
                warehouse_name="Тула",
                quantity=3,
                quantity_full=3,
            )
        )
        await db_session.commit()

        rows = await get_unified_stock_summary(db_session, project.id, group_by="sku")
        row = next(r for r in rows if r["article_wb"] == 9)
        assert row["total_wb"] == 3  # из своего fallback-источника, не 50 чужих
