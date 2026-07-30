# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты WB FBS — гейт настроек «translate + ff_mirror при зеркале выше учёта»
(`warehouse_service.update_settings` → `FbsMirrorAboveLedger`).

Пара «translate + ff_mirror» транслировала бы в WB остаток, которого нет в
нашем учёте: WB продаст, а списывать нечего. Гейт считает по активным привязкам
Σ per-SKU GREATEST(россыпь_зеркала − учёт, 0) и при превышении отбивает PATCH
(роутер → 409) с цифрами разрыва; `force=true` применяет молча.

Что закрыто:
  • гейтится ТОЛЬКО эффективная пара translate+ff_mirror (в т.ч. собранная из
    старых настроек + частичного PATCH);
  • min_of_both / observe не гейтятся; зеркало ≤ учёта — проходит;
  • per-SKU GREATEST: перекос по одному SKU не маскируется профицитом другого;
  • короба зеркала (base_barcode) не считаются россыпью;
  • force=True обходит гейт; отбитый PATCH не сохраняет НИЧЕГО (не половину);
  • цифры mirror_over_ledger / ledger_total / mirror_total и слово «force»
    в сообщении ошибки.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models import FulfillmentStock, Nomenclature, WbFbsWarehouse, WbFbsWarehouseLink
from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType
from backend.schemas.wb_fbs import FbsWarehouseSettingsUpdate
from backend.services.wb_fbs import warehouse_service
from backend.services.wb_fbs.warehouse_service import FbsMirrorAboveLedger

WB_WH = 772001


@pytest_asyncio.fixture
async def env(db_session, project):
    """Склад продавца (observe/min_of_both) + привязанный наш склад + 2 SKU."""
    warehouse = Warehouse(
        project_id=project.id,
        name="Гейт-склад",
        warehouse_type=WarehouseType.FULFILLMENT,
        is_active=True,
    )
    db_session.add(warehouse)
    await db_session.flush()

    noms = []
    for i in range(2):
        nom = Nomenclature(project_id=project.id, barcode=f"GATE_BC_{i}", chrt_id=883000 + i)
        db_session.add(nom)
        noms.append(nom)
    await db_session.flush()

    db_session.add(
        WbFbsWarehouse(project_id=project.id, wb_warehouse_id=WB_WH, name="Продавец-гейт")
    )
    db_session.add(
        WbFbsWarehouseLink(
            project_id=project.id,
            wb_warehouse_id=WB_WH,
            warehouse_id=warehouse.id,
            is_active=True,
        )
    )
    await db_session.commit()

    from types import SimpleNamespace

    return SimpleNamespace(
        project_id=project.id,
        warehouse_id=warehouse.id,
        nom_ids=[n.id for n in noms],
        barcodes=[n.barcode for n in noms],
    )


async def _mirror(db_session, env, nom_idx, qty, *, base_barcode=None, units_per_box=1):
    db_session.add(
        FulfillmentStock(
            project_id=env.project_id,
            warehouse_id=env.warehouse_id,
            provider="skladbot",
            barcode=env.barcodes[nom_idx] if base_barcode is None else f"1000000000001{nom_idx}",
            base_barcode=base_barcode,
            units_per_box=units_per_box,
            nomenclature_id=env.nom_ids[nom_idx],
            qty_good=qty,
        )
    )
    await db_session.commit()


async def _ledger(db_session, env, nom_idx, qty):
    db_session.add(
        WarehouseStock(
            project_id=env.project_id,
            warehouse_id=env.warehouse_id,
            nomenclature_id=env.nom_ids[nom_idx],
            barcode=env.barcodes[nom_idx],
            quantity=qty,
        )
    )
    await db_session.commit()


async def _patch(db_session, env, **fields):
    payload = FbsWarehouseSettingsUpdate(**fields)
    return await warehouse_service.update_settings(db_session, env.project_id, WB_WH, payload)


async def _wh(db_session, env) -> WbFbsWarehouse:
    return (
        await db_session.execute(
            select(WbFbsWarehouse).where(
                WbFbsWarehouse.project_id == env.project_id,
                WbFbsWarehouse.wb_warehouse_id == WB_WH,
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_gate_blocks_translate_ff_mirror_when_mirror_above(db_session, env):
    """Зеркало выше учёта → PATCH отбит с цифрами разрыва и подсказкой force."""
    await _mirror(db_session, env, 0, 10)
    await _ledger(db_session, env, 0, 4)

    with pytest.raises(FbsMirrorAboveLedger) as err:
        await _patch(db_session, env, mode="translate", stock_source="ff_mirror")

    msg = str(err.value)
    assert "mirror_over_ledger=6" in msg
    assert "ledger_total=4" in msg
    assert "mirror_total=10" in msg
    assert "force" in msg


@pytest.mark.asyncio
async def test_gate_does_not_save_half(db_session, env):
    """Отбитый PATCH не сохраняет ни одного поля (проверка ДО записи)."""
    await _mirror(db_session, env, 0, 10)

    with pytest.raises(FbsMirrorAboveLedger):
        await _patch(
            db_session, env, mode="translate", stock_source="ff_mirror", safety_stock_abs=5
        )

    db_session.expire_all()
    wh = await _wh(db_session, env)
    assert wh.mode == "observe"
    assert wh.stock_source == "min_of_both"
    assert wh.safety_stock_abs == 0


@pytest.mark.asyncio
async def test_force_bypasses_gate(db_session, env):
    """force=True — применяем молча (решение человека)."""
    await _mirror(db_session, env, 0, 10)
    await _ledger(db_session, env, 0, 4)

    result = await _patch(db_session, env, mode="translate", stock_source="ff_mirror", force=True)
    assert result["mode"] == "translate"
    assert result["stock_source"] == "ff_mirror"

    db_session.expire_all()
    wh = await _wh(db_session, env)
    assert wh.mode == "translate"
    assert wh.stock_source == "ff_mirror"


@pytest.mark.asyncio
async def test_min_of_both_not_gated(db_session, env):
    """min_of_both не обещает больше учёта — не гейтится даже при разрыве."""
    await _mirror(db_session, env, 0, 10)

    result = await _patch(db_session, env, mode="translate", stock_source="min_of_both")
    assert result["mode"] == "translate"


@pytest.mark.asyncio
async def test_observe_with_ff_mirror_not_gated(db_session, env):
    """В observe в кабинет не пишем — источник ff_mirror безопасен."""
    await _mirror(db_session, env, 0, 10)

    result = await _patch(db_session, env, stock_source="ff_mirror")
    assert result["stock_source"] == "ff_mirror"


@pytest.mark.asyncio
async def test_effective_pair_from_partial_patch(db_session, env):
    """Пара собирается из старых настроек + PATCH: источник уже ff_mirror,
    патчим ТОЛЬКО mode=translate → гейт обязан сработать."""
    await _mirror(db_session, env, 0, 10)
    await _patch(db_session, env, stock_source="ff_mirror")  # observe — прошло

    with pytest.raises(FbsMirrorAboveLedger):
        await _patch(db_session, env, mode="translate")


@pytest.mark.asyncio
async def test_mirror_not_above_ledger_passes(db_session, env):
    """Зеркало ≤ учёта → пара translate+ff_mirror проходит без force."""
    await _mirror(db_session, env, 0, 4)
    await _ledger(db_session, env, 0, 10)

    result = await _patch(db_session, env, mode="translate", stock_source="ff_mirror")
    assert result["mode"] == "translate"


@pytest.mark.asyncio
async def test_per_sku_greatest_not_masked_by_surplus(db_session, env):
    """GREATEST per-SKU: недостача одного SKU не гасится профицитом другого."""
    # SKU0: зеркало 10, учёт 2 → over 8. SKU1: зеркало 1, учёт 100 → over 0.
    await _mirror(db_session, env, 0, 10)
    await _ledger(db_session, env, 0, 2)
    await _mirror(db_session, env, 1, 1)
    await _ledger(db_session, env, 1, 100)

    with pytest.raises(FbsMirrorAboveLedger) as err:
        await _patch(db_session, env, mode="translate", stock_source="ff_mirror")
    assert "mirror_over_ledger=8" in str(err.value)


@pytest.mark.asyncio
async def test_box_rows_not_counted_as_loose(db_session, env):
    """Короб зеркала (base_barcode) — не россыпь: в mirror_loose не идёт."""
    # Только короб: 3 короба × 10 шт — но россыпи ноль → гейт молчит.
    await _mirror(db_session, env, 0, 3, base_barcode=env.barcodes[0], units_per_box=10)

    result = await _patch(db_session, env, mode="translate", stock_source="ff_mirror")
    assert result["mode"] == "translate"


@pytest.mark.asyncio
async def test_no_links_passes(db_session, env):
    """Без активных привязок сравнивать нечего — гейт пропускает."""
    link = (
        await db_session.execute(
            select(WbFbsWarehouseLink).where(
                WbFbsWarehouseLink.project_id == env.project_id,
                WbFbsWarehouseLink.wb_warehouse_id == WB_WH,
            )
        )
    ).scalar_one()
    link.is_active = False
    await db_session.commit()
    await _mirror(db_session, env, 0, 10)

    result = await _patch(db_session, env, mode="translate", stock_source="ff_mirror")
    assert result["mode"] == "translate"
