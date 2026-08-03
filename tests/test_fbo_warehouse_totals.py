# ruff: noqa: RUF001, RUF002, RUF003
"""`fbo_warehouse_totals` — селектор «Склады FBO» на матрице FBS.

WB отдаёт остаток сгоревших складов как живой → колонка FBO завышается.
Функция кормит галочки «какие склады считать»: отдаёт ВСЕ склады WB проекта
(в т.ч. снятые с учёта — их надо видеть, чтобы вернуть) с их остатком и
флагом `counted` (учитывается ли склад сейчас в FBO). Транзит «в пути»,
сортировочные центры и итоговую псевдострочку не показываем — это не склады
хранения.
"""

import uuid

import pytest
from sqlalchemy import text

from backend.services.settings_service import (
    set_excluded_warehouses,
    set_stock_ignored_warehouses,
)
from backend.services.wb_fbs.stock_service import fbo_warehouse_totals

BURNED = "Краснодар"
ALIVE = "Тула"


async def _seed_wb_stock(db, pid, nm_id, wh_name, qty):
    await db.execute(
        text(
            "INSERT INTO wb_warehouse_stocks (project_id, nm_id, warehouse_name, quantity, "
            "quantity_full, in_way_to_client, in_way_from_client, updated_at) "
            "VALUES (:p, :nm, :wh, :q, :q, 0, 0, NOW())"
        ),
        {"p": pid, "nm": nm_id, "wh": wh_name, "q": qty},
    )


@pytest.mark.asyncio
class TestFboWarehouseTotals:
    async def test_lists_all_with_qty_sorted_desc(self, db_session, project):
        """Все склады с остатком, по умолчанию counted=True, сортировка ↓ по qty."""
        pid = project.id
        nm = 810100 + (uuid.uuid4().int % 1000)
        await _seed_wb_stock(db_session, pid, nm, ALIVE, 20)
        await _seed_wb_stock(db_session, pid, nm, BURNED, 100)
        await db_session.commit()

        out = await fbo_warehouse_totals(db_session, pid)
        by = {w["name"]: w for w in out}
        assert by[ALIVE]["qty"] == 20 and by[ALIVE]["counted"] is True
        assert by[BURNED]["qty"] == 100 and by[BURNED]["counted"] is True
        # Самый крупный остаток — первым (помогает найти фантомный склад).
        assert out[0]["name"] == BURNED

    async def test_paren_variants_grouped_by_base(self, db_session, project):
        """Скобочные написания одного склада суммируются в базовое имя."""
        pid = project.id
        nm = 810500 + (uuid.uuid4().int % 1000)
        await _seed_wb_stock(db_session, pid, nm, "Рязань (Тюшевское)", 30)
        await _seed_wb_stock(db_session, pid, nm, "Рязань (Дубовое)", 12)
        await db_session.commit()

        by = {w["name"]: w for w in await fbo_warehouse_totals(db_session, pid)}
        assert "Рязань" in by
        assert by["Рязань"]["qty"] == 42

    async def test_ignored_shown_but_not_counted(self, db_session, project):
        """Сгоревший склад ВИДЕН с фантомным остатком, но counted=False."""
        pid = project.id
        nm = 811100 + (uuid.uuid4().int % 1000)
        await _seed_wb_stock(db_session, pid, nm, ALIVE, 20)
        await _seed_wb_stock(db_session, pid, nm, BURNED, 100)
        await set_stock_ignored_warehouses(db_session, pid, ["Краснодар (Тихорецкая)"])
        await db_session.commit()

        by = {w["name"]: w for w in await fbo_warehouse_totals(db_session, pid)}
        assert by[BURNED]["qty"] == 100
        assert by[BURNED]["counted"] is False
        assert by[ALIVE]["counted"] is True

    async def test_excluded_also_not_counted(self, db_session, project):
        """excluded_warehouses тоже роняет counted (тот же фильтр доверия)."""
        pid = project.id
        nm = 812100 + (uuid.uuid4().int % 1000)
        await _seed_wb_stock(db_session, pid, nm, ALIVE, 5)
        await set_excluded_warehouses(db_session, pid, ["Тула"])
        await db_session.commit()

        by = {w["name"]: w for w in await fbo_warehouse_totals(db_session, pid)}
        assert by[ALIVE]["counted"] is False

    async def test_transit_sc_total_row_hidden(self, db_session, project):
        """Транзит «в пути», СЦ и итоговая псевдострочка не показываются."""
        pid = project.id
        nm = 813100 + (uuid.uuid4().int % 1000)
        await _seed_wb_stock(db_session, pid, nm, ALIVE, 5)
        await _seed_wb_stock(db_session, pid, nm, "СЦ Ижевск", 9)
        await _seed_wb_stock(db_session, pid, nm, "В пути до получателей", 9)
        await _seed_wb_stock(db_session, pid, nm, "Всего находится на складах", 999)
        await db_session.commit()

        names = {w["name"] for w in await fbo_warehouse_totals(db_session, pid)}
        assert ALIVE in names
        assert "СЦ Ижевск" not in names
        assert not any("пути" in n.casefold() for n in names)
        assert "Всего находится на складах" not in names

    async def test_project_isolation(self, db_session, project, other_project):
        pid = project.id
        nm = 814100 + (uuid.uuid4().int % 1000)
        await _seed_wb_stock(db_session, pid, nm, ALIVE, 5)
        await db_session.commit()
        assert await fbo_warehouse_totals(db_session, other_project.id) == []
