# ruff: noqa: RUF001, RUF002, RUF003
"""«🔥 Остатки не учитывать» — сгоревшие склады WB (stock_ignored_warehouses).

WB продолжает отдавать остатки сгоревших складов (Краснодар, Невинномысск,
Электросталь) как живые → 33к фантомных шт в расчётах. Настройка заставляет
РАСЧЁТНЫХ читателей (потребность, прогнозы, cold-start, кратность) не верить
стоку этих складов. Страницы факта/история/БДР и СПРОС — сознательно не
трогаются. Флаг независим от excluded_warehouses.

Плюс справочник нового склада WB «Красный Бор» (тот же комплекс, что
«Красный Бор СГТ», Ленобласть).
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from backend.services import warehouse_need_service as wns
from backend.services.settings_service import (
    get_excluded_warehouses,
    get_stock_ignored_set,
    get_stock_ignored_warehouses,
    set_excluded_warehouses,
    set_stock_ignored_warehouses,
)

BURNED_WH = "Краснодар"
ALIVE_WH = "Тула"


# ═══════════════════════════════════════════════════════════════════════════════
# Настройка: roundtrip + нормализация
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestStockIgnoredSetting:
    async def test_empty_by_default(self, db_session, project):
        assert await get_stock_ignored_warehouses(db_session, project.id) == []
        assert await get_stock_ignored_set(db_session, project.id) == set()

    async def test_roundtrip_and_parens_normalized(self, db_session, project):
        """Скобочный суффикс срезается, пустые выкидываются (как у excluded)."""
        result = await set_stock_ignored_warehouses(
            db_session, project.id, ["Краснодар (Тихорецкая)", "", "   ", "Невинномысск"]
        )
        assert result == ["Краснодар", "Невинномысск"]
        assert await get_stock_ignored_warehouses(db_session, project.id) == ["Краснодар", "Невинномысск"]

    async def test_independent_from_excluded(self, db_session, project):
        """Флаг живёт в своём ключе и не пересекается с excluded_warehouses."""
        await set_excluded_warehouses(db_session, project.id, ["Новосибирск"])
        await set_stock_ignored_warehouses(db_session, project.id, ["Краснодар"])
        assert await get_excluded_warehouses(db_session, project.id) == ["Новосибирск"]
        assert await get_stock_ignored_warehouses(db_session, project.id) == ["Краснодар"]

    async def test_project_isolation(self, db_session, project, other_project):
        await set_stock_ignored_warehouses(db_session, project.id, ["Краснодар"])
        assert await get_stock_ignored_warehouses(db_session, other_project.id) == []

    async def test_ignored_set_expands_aliases(self, db_session, project):
        """get_stock_ignored_set содержит канон + сырые написания (алиасы) —
        SQL-notin_ читатели фильтруют по сырому warehouse_name."""
        await set_stock_ignored_warehouses(db_session, project.id, ["Краснодар"])
        ignored = await get_stock_ignored_set(db_session, project.id)
        assert "Краснодар" in ignored
        # Сырые формы из ACCEPTANCE_TO_STOCK_NAME тоже накрыты.
        assert "Краснодар (Тихорецкая)" in ignored
        assert "Краснодар СГТ" in ignored

    async def test_ignored_set_covers_paren_canonical_without_alias(self, db_session, project):
        """Ревью-кейс: канон белого списка СО скобками БЕЗ self-алиаса
        («Рязань (Тюшевское)»). Настройка хранит срезанное «Рязань», а мост
        кладёт в warehouse_name сырую скобочную форму — сет обязан вернуть
        обе, иначе SQL-notin_ читатели пропустят фантомный сток."""
        await set_stock_ignored_warehouses(db_session, project.id, ["Рязань (Тюшевское)"])
        ignored = await get_stock_ignored_set(db_session, project.id)
        assert "Рязань" in ignored
        assert "Рязань (Тюшевское)" in ignored


# ═══════════════════════════════════════════════════════════════════════════════
# Потребность (get_warehouse_need): сток ignored-склада выпадает, спрос — нет
# ═══════════════════════════════════════════════════════════════════════════════


async def _seed_nomenclature(db_session, project_id, nm_id, barcode):
    res = await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, article_seller, article_wb, "
            "use_box_multiplicity, updated_at) "
            "VALUES (:p, :bc, :art, :nm, false, NOW()) RETURNING id"
        ),
        {"p": project_id, "bc": barcode, "art": f"ART-{nm_id}", "nm": nm_id},
    )
    return res.scalar()


async def _seed_ff_warehouse(db_session, project_id, name):
    res = await db_session.execute(
        text(
            "INSERT INTO warehouses (project_id, name, warehouse_type, assembly_days, "
            "wb_acceptance_days, sort_order, is_active, is_deleted, created_at, updated_at) "
            "VALUES (:p, :n, 'FULFILLMENT', 1, 1, 0, true, false, NOW(), NOW()) RETURNING id"
        ),
        {"p": project_id, "n": name},
    )
    return res.scalar()


async def _seed_rf_stock(db_session, project_id, wh_id, nom_id, barcode, qty):
    await db_session.execute(
        text(
            "INSERT INTO warehouse_stock (project_id, warehouse_id, nomenclature_id, "
            "barcode, quantity, in_transit, defect_quantity, defect_in_transit, updated_at) "
            "VALUES (:p, :w, :n, :bc, :q, 0, 0, 0, NOW())"
        ),
        {"p": project_id, "w": wh_id, "n": nom_id, "bc": barcode, "q": qty},
    )


async def _seed_wb_stock(db_session, project_id, nm_id, wh_name, qty, in_way_to=0):
    await db_session.execute(
        text(
            "INSERT INTO wb_warehouse_stocks (project_id, nm_id, warehouse_name, quantity, "
            "quantity_full, in_way_to_client, in_way_from_client, updated_at) "
            "VALUES (:p, :nm, :wh, :q, :q, :iwt, 0, NOW())"
        ),
        {"p": project_id, "nm": nm_id, "wh": wh_name, "q": qty, "iwt": in_way_to},
    )


def _patch_api(monkeypatch, orders):
    """WB API → синтетика; excluded/acceptance — пустые (ignored НЕ патчим: из БД)."""

    async def _fake_get_wb_key(db, project_id, service):
        return "fake-key"

    async def _fake_fetch(api_key, date_from):
        return orders

    wb_api = __import__("backend.services.funnel.wb_api_client", fromlist=["x"])
    monkeypatch.setattr(wb_api, "get_wb_key", _fake_get_wb_key)
    monkeypatch.setattr(wb_api, "fetch_supplier_orders", _fake_fetch)

    async def _no_excluded(db, project_id):
        return []

    async def _no_blocked(db, project_id):
        return set()

    monkeypatch.setattr(
        __import__("backend.services.settings_service", fromlist=["x"]),
        "get_excluded_warehouses",
        _no_excluded,
    )
    monkeypatch.setattr(
        __import__("backend.services.warehouse_acceptance_service", fromlist=["x"]),
        "get_acceptance_blocked_warehouses",
        _no_blocked,
    )


@pytest.mark.asyncio
class TestWarehouseNeedIgnoresBurnedStock:
    async def test_burned_stock_out_of_coverage_demand_kept(self, db_session, project, monkeypatch):
        """Сток сгоревшего склада выпадает из stocks_wb/total_need, спрос сохранён."""
        pid = project.id
        nm_id = 710100 + (uuid.uuid4().int % 1000)
        bc = f"BC{nm_id}"
        nom_id = await _seed_nomenclature(db_session, pid, nm_id, bc)
        ff_id = await _seed_ff_warehouse(db_session, pid, "FF-Burn")
        await _seed_rf_stock(db_session, pid, ff_id, nom_id, bc, 1000)
        # Фантомный сток на сгоревшем складе + живой на Туле.
        await _seed_wb_stock(db_session, pid, nm_id, BURNED_WH, 100)
        await _seed_wb_stock(db_session, pid, nm_id, ALIVE_WH, 7)
        await db_session.commit()

        orders = [{"nmId": nm_id, "quantity": 5, "warehouseName": BURNED_WH, "supplierArticle": "A"}] * 14
        _patch_api(monkeypatch, orders)

        # Baseline: флаг не задан — фантом учитывается.
        res0 = await wns.get_warehouse_need.__wrapped__(db_session, pid, supply_days=14, analysis_days=14)
        art0 = next(a for a in res0["articles"] if a["nm_id"] == nm_id)
        assert art0["stocks_wb"] == 107

        await set_stock_ignored_warehouses(db_session, pid, ["Краснодар (Тихорецкая)"])

        res1 = await wns.get_warehouse_need.__wrapped__(db_session, pid, supply_days=14, analysis_days=14)
        art1 = next(a for a in res1["articles"] if a["nm_id"] == nm_id)
        # Сток сгоревшего склада исчез из покрытия, живой остался.
        assert art1["stocks_wb"] == 7
        # Дефицит вырос: фантом больше не гасит потребность.
        assert art1["total_need"] > art0["total_need"]
        # СПРОС сохранён (заказы реальные, :448 не трогаем).
        assert art1["eff_avg_daily"] == art0["eff_avg_daily"]
        assert art1["eff_avg_daily"] > 0
        # Колонка живого склада не пострадала.
        by_wh = {w["name"]: w for w in res1["warehouses"]}
        assert ALIVE_WH in by_wh


# ═══════════════════════════════════════════════════════════════════════════════
# stock_forecast: _load_wb_breakdown исключает ignored (питает «На WB»/days_left)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestStockForecastBreakdown:
    async def test_breakdown_excludes_ignored(self, db_session, project):
        from backend.services.stock_forecast_service import _load_wb_breakdown

        pid = project.id
        nm_id = 720100 + (uuid.uuid4().int % 1000)
        await _seed_wb_stock(db_session, pid, nm_id, BURNED_WH, 40, in_way_to=5)
        await _seed_wb_stock(db_session, pid, nm_id, ALIVE_WH, 10)
        await db_session.commit()

        base = await _load_wb_breakdown(db_session, pid)
        assert base[nm_id]["quantity"] == 50

        await set_stock_ignored_warehouses(db_session, pid, ["Краснодар"])
        filtered = await _load_wb_breakdown(db_session, pid)
        assert filtered[nm_id]["quantity"] == 10
        assert filtered[nm_id]["in_way_to_client"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# cold_start: wb_qty / wb_by_wh исключают ignored (raw SQL, :param binding)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestColdStartIgnored:
    async def test_fetch_sku_wb_qty_excludes_ignored(self, db_session, project):
        from backend.services.cold_start_distribution_service import fetch_sku

        pid = project.id
        nm_id = 730100 + (uuid.uuid4().int % 1000)
        bc = f"BC{nm_id}"
        await _seed_nomenclature(db_session, pid, nm_id, bc)
        await _seed_wb_stock(db_session, pid, nm_id, BURNED_WH, 30)
        await _seed_wb_stock(db_session, pid, nm_id, ALIVE_WH, 20)
        await db_session.commit()

        base = await fetch_sku(db_session, pid, nm_id)
        assert base is not None and int(base["wb_qty"]) == 50

        await set_stock_ignored_warehouses(db_session, pid, ["Краснодар"])
        filtered = await fetch_sku(db_session, pid, nm_id)
        assert filtered is not None and int(filtered["wb_qty"]) == 20

    async def test_cold_start_segment_wb_by_wh_excludes_ignored(self, db_session, project):
        from backend.services.cold_start_distribution_service import fetch_cold_start_segment

        pid = project.id
        nm_id = 740100 + (uuid.uuid4().int % 1000)
        bc = f"BC{nm_id}"
        nom_id = await _seed_nomenclature(db_session, pid, nm_id, bc)
        ff_id = await _seed_ff_warehouse(db_session, pid, "FF-Cold")
        await _seed_rf_stock(db_session, pid, ff_id, nom_id, bc, 50)
        await _seed_wb_stock(db_session, pid, nm_id, BURNED_WH, 30)
        await _seed_wb_stock(db_session, pid, nm_id, ALIVE_WH, 20)
        await set_stock_ignored_warehouses(db_session, pid, ["Краснодар"])
        await db_session.commit()

        rows = await fetch_cold_start_segment(db_session, pid)
        row = next(r for r in rows if r["nm_id"] == nm_id)
        assert int(row["wb_qty"]) == 20
        assert set(row["wb_by_warehouse"].keys()) == {ALIVE_WH}


# ═══════════════════════════════════════════════════════════════════════════════
# Мост remains→stocks: носитель in_way уходит на НЕ-ignored склад
# ═══════════════════════════════════════════════════════════════════════════════


class TestBridgeCarrierPrefersAlive:
    def _rows(self, nm_id):
        from backend.services.warehouse_stock_engine import WB_REMAINS_IN_WAY_TO_CLIENT

        return [
            {"nm_id": nm_id, "warehouse_name": BURNED_WH, "quantity": 100},
            {"nm_id": nm_id, "warehouse_name": ALIVE_WH, "quantity": 50},
            {"nm_id": nm_id, "warehouse_name": WB_REMAINS_IN_WAY_TO_CLIENT, "quantity": 7},
        ]

    def test_carrier_avoids_ignored(self):
        from backend.services.warehouse_stock_service import _bridge_rows_from_remains

        nm_id = 750100
        rows = _bridge_rows_from_remains(1, self._rows(nm_id), stock_ignored={BURNED_WH})
        by_wh = {r["warehouse_name"]: r for r in rows}
        # Носитель — живой склад (иначе notin_-читатели потеряют общекарточный in_way).
        assert by_wh[ALIVE_WH]["in_way_to_client"] == 7
        assert by_wh[BURNED_WH]["in_way_to_client"] == 0

    def test_carrier_fallback_when_all_ignored(self):
        """Все склады ignored → носитель как раньше (max qty), в-пути не теряется."""
        from backend.services.warehouse_stock_service import _bridge_rows_from_remains

        nm_id = 750200
        rows = _bridge_rows_from_remains(
            1, self._rows(nm_id), stock_ignored={BURNED_WH, ALIVE_WH}
        )
        by_wh = {r["warehouse_name"]: r for r in rows}
        assert by_wh[BURNED_WH]["in_way_to_client"] == 7

    def test_no_ignored_keeps_old_behaviour(self):
        from backend.services.warehouse_stock_service import _bridge_rows_from_remains

        nm_id = 750300
        rows = _bridge_rows_from_remains(1, self._rows(nm_id))
        by_wh = {r["warehouse_name"]: r for r in rows}
        assert by_wh[BURNED_WH]["in_way_to_client"] == 7


# ═══════════════════════════════════════════════════════════════════════════════
# Справочник: «Красный Бор» (новый обычный склад WB, СЗФО)
# ═══════════════════════════════════════════════════════════════════════════════


class TestKrasnyBorDirectory:
    def test_coords_present(self):
        from backend.services.warehouse_geo_data import WAREHOUSE_COORDS

        assert WAREHOUSE_COORDS.get("Красный Бор") == (59.6833, 30.6238)

    def test_district_northwest(self):
        from backend.services.warehouse_district import (
            DISTRICT_NORTHWEST,
            WAREHOUSE_TO_DISTRICT,
            warehouse_to_district,
        )

        assert WAREHOUSE_TO_DISTRICT.get("Красный Бор") == DISTRICT_NORTHWEST
        assert warehouse_to_district("Красный Бор") == DISTRICT_NORTHWEST

    def test_logistics_coef(self):
        from backend.services.warehouse_district import WAREHOUSE_LOGISTICS_COEF

        assert WAREHOUSE_LOGISTICS_COEF.get("Красный Бор") == Decimal("2.20")
