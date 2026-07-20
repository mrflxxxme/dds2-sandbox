"""Grouping tests for `_group_unified` in warehouse_stock_engine.

«Единые остатки» показывают отдельную колонку «В сборке на ФФ» — товар, который
физически лежит на наших складах, но уже разобран в активные заявки на сборку.
Значение считается в `get_unified_stock_summary` (блок 5), но при группировке
по бренду/категории/ярлыку строки пересобираются в `_group_unified` — и поле
обязано пережить агрегацию, иначе на групповом уровне резерв молча становится 0.

Второй инвариант: `reserved` — ЧАСТЬ `total_own`, а не отдельный товар. Он не
имеет права попасть в `total` (иначе двойной счёт и слом паритета с БДР, см.
test_warehouse_unified_stock_bdr_parity.py).
"""

import pytest

from backend.services.warehouse_stock_engine import _group_unified


def _row(
    nom_id: int,
    brand: str,
    *,
    total_own: int,
    reserved: int,
    details: list | None = None,
    by_wh: dict | None = None,
) -> dict:
    """Минимальная строка unified в форме, которую отдаёт _ensure()."""
    return {
        "reserved_details": details if details is not None else [],
        "reserved_by_warehouse": by_wh if by_wh is not None else ({"ФФ": reserved} if reserved else {}),
        "nomenclature_id": nom_id,
        "brand": brand,
        "subject": "Категория",
        "warehouses": {"ФФ": total_own},
        "wb_stocks": {},
        "total_own": total_own,
        "total_wb": 0,
        "wb_in_way_to_client": 0,
        "wb_in_way_from_client": 0,
        "in_transit": 0,
        "reserved": reserved,
        "total": total_own,
        "total_defect": 0,
        "factory_qty": 0,
        "vehicle_forming_qty": 0,
        "vehicle_transit_qty": 0,
        "avg_cost": 0.0,
        "avg_daily_revenue": 0.0,
        "avg_daily_profit": 0.0,
        "avg_price": 0.0,
        "avg_profit": 0.0,
        "trend_7": {"avg_daily_qty": 0.0, "revenue": 0.0, "profit": 0.0},
        "trend_14": {"avg_daily_qty": 0.0, "revenue": 0.0, "profit": 0.0},
        "trend_30": {"avg_daily_qty": 0.0, "revenue": 0.0, "profit": 0.0},
    }


class TestGroupedReserved:
    """group_by='brand' не ходит в БД (tag/imt-карты грузятся только для своих
    режимов), поэтому db здесь не нужен."""

    async def test_reserved_sums_across_group(self):
        rows = [
            _row(1, "Бренд", total_own=10, reserved=3),
            _row(2, "Бренд", total_own=20, reserved=5),
        ]

        groups = await _group_unified(None, 1, rows, "brand")

        assert len(groups) == 1
        assert groups[0]["reserved"] == 8
        assert groups[0]["total_own"] == 30

    async def test_reserved_not_double_counted_in_total(self):
        """Резерв уже сидит внутри total_own → total остаётся суммой строк."""
        rows = [
            _row(1, "Бренд", total_own=10, reserved=10),
            _row(2, "Бренд", total_own=20, reserved=0),
        ]

        groups = await _group_unified(None, 1, rows, "brand")

        assert groups[0]["total"] == 30

    async def test_details_merge_by_request_across_skus(self):
        """Одна заявка обычно держит несколько SKU одной группы. В раскрытой
        ячейке номер заявки обязан встретиться ОДИН раз с суммарным количеством,
        а не N строк подряд с одним ASM-номером."""
        d = {"request_id": 7, "number": "ASM-7", "status": "IN_PROGRESS",
             "warehouse_id": 1, "warehouse_name": "ФФ"}
        rows = [
            _row(1, "Бренд", total_own=10, reserved=3, details=[{**d, "quantity": 3}]),
            _row(2, "Бренд", total_own=20, reserved=5, details=[{**d, "quantity": 5}]),
        ]

        groups = await _group_unified(None, 1, rows, "brand")

        details = groups[0]["reserved_details"]
        assert len(details) == 1
        assert details[0]["number"] == "ASM-7"
        assert details[0]["quantity"] == 8

    async def test_details_sum_matches_reserved(self):
        """Инвариант раскрытой ячейки: сумма разбивки == число в колонке."""
        rows = [
            _row(1, "Бренд", total_own=10, reserved=3, details=[
                {"request_id": 1, "number": "ASM-1", "status": "READY",
                 "warehouse_id": 1, "warehouse_name": "ФФ", "quantity": 3}]),
            _row(2, "Бренд", total_own=20, reserved=5, details=[
                {"request_id": 2, "number": "ASM-2", "status": "IN_PROGRESS",
                 "warehouse_id": 1, "warehouse_name": "ФФ", "quantity": 5}]),
        ]

        groups = await _group_unified(None, 1, rows, "brand")

        assert sum(d["quantity"] for d in groups[0]["reserved_details"]) == groups[0]["reserved"]

    async def test_reserved_by_warehouse_sums_per_warehouse(self):
        """Колонка каждого ФФ показывает «свободно / в сборке», поэтому резерв
        обязан агрегироваться в разрезе складов, а не только общей суммой."""
        rows = [
            _row(1, "Бренд", total_own=10, reserved=7, by_wh={"Газпром": 4, "натали": 3}),
            _row(2, "Бренд", total_own=20, reserved=5, by_wh={"Газпром": 5}),
        ]

        groups = await _group_unified(None, 1, rows, "brand")

        assert groups[0]["reserved_by_warehouse"] == {"Газпром": 9, "натали": 3}

    async def test_reserved_by_warehouse_matches_total(self):
        """Инвариант блока «В сборке на ФФ»: сумма по складам == общий резерв."""
        rows = [
            _row(1, "Бренд", total_own=10, reserved=7, by_wh={"Газпром": 4, "натали": 3}),
            _row(2, "Бренд", total_own=20, reserved=5, by_wh={"натали": 5}),
        ]

        groups = await _group_unified(None, 1, rows, "brand")

        assert sum(groups[0]["reserved_by_warehouse"].values()) == groups[0]["reserved"]

    async def test_reserved_present_when_group_has_none(self):
        """Поле обязано быть всегда — фронт считает «Свободно» как
        total_own − reserved и на undefined отдал бы NaN."""
        rows = [_row(1, "Бренд", total_own=10, reserved=0)]

        groups = await _group_unified(None, 1, rows, "brand")

        assert groups[0]["reserved"] == 0
