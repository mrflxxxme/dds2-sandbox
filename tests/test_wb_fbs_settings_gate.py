# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты WB FBS — гейт настроек «is_active + translate + ff_mirror при зеркале
выше учёта» (`warehouse_service.update_settings` → `FbsMirrorAboveLedger`).

Включённая трансляция из зеркала ФФ обещала бы WB остаток, которого нет в
нашем учёте: WB продаст, а списывать нечего. Гейт считает разрыв ПУЛОМ активных
привязок на уровне номенклатуры (источник (склад, SKU) = зеркало при наличии
ключа, иначе учёт — фолбэк `_source_qty`; over = Σ_SKU GREATEST(источник −
учёт, 0)) и отбивает PATCH (роутер → 409 со структурированным detail) ТОЛЬКО
когда запрос повышает exposure; `force=true` применяет молча.

Что закрыто:
  • гейтится ТОЛЬКО эффективная тройка is_active+translate+ff_mirror И только
    запрос, ПОВЫШАЮЩИЙ exposure (смена mode/stock_source, включение is_active);
  • is_active→False (аварийный стоп), правки fbo_max_qty/буферов на рискованном
    складе — проходят БЕЗ гейта;
  • включение is_active=True на сохранённой рискованной паре — гейтится;
  • min_of_both / observe не гейтятся; зеркало ≤ учёта — проходит;
  • пул привязок: перекрёстное распределение mirror/ledger по двум складам
    НЕ гейтится (ложный 409 прежнего per-(склад, SKU) GREATEST);
  • per-SKU (номенклатура): перекос одного SKU не маскируется профицитом другого;
  • короба зеркала (base_barcode) не считаются россыпью;
  • force=True обходит гейт; отбитый PATCH не сохраняет НИЧЕГО (не половину);
  • цифры mirror_over_ledger / ledger_total / mirror_total — атрибутами
    исключения и в тексте, слово «force» в сообщении.
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
    """Склад продавца (is_active/observe/min_of_both) + привязанный наш склад + 2 SKU.

    Тумблер трансляции включён: условие (а) гейта требует эффективный
    `is_active=True` — выключенный склад в WB ничего не обещает.
    """
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
        WbFbsWarehouse(
            project_id=project.id, wb_warehouse_id=WB_WH, name="Продавец-гейт", is_active=True
        )
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
    # Цифры разрыва — атрибутами исключения: роутер кладёт их в detail 409.
    assert err.value.mirror_over_ledger == 6
    assert err.value.ledger_total == 4
    assert err.value.mirror_total == 10


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


# ─── Сужение гейта: только запрос, повышающий exposure ──────────────────────


async def _save_risky_pair(db_session, env, *, is_active: bool = True) -> None:
    """Сохранённая рискованная тройка НАПРЯМУЮ в БД (как после force-PATCH)."""
    wh = await _wh(db_session, env)
    wh.is_active = is_active
    wh.mode = "translate"
    wh.stock_source = "ff_mirror"
    await db_session.commit()


@pytest.mark.asyncio
async def test_is_active_off_passes_gate(db_session, env):
    """`is_active: false` — аварийный стоп: проходит без гейта и СОХРАНЯЕТСЯ."""
    await _save_risky_pair(db_session, env)
    await _mirror(db_session, env, 0, 10)  # зеркало выше учёта — гейту было бы с чего

    result = await _patch(db_session, env, is_active=False)
    assert result["is_active"] is False

    db_session.expire_all()
    wh = await _wh(db_session, env)
    assert wh.is_active is False


@pytest.mark.asyncio
async def test_fbo_and_buffer_patch_passes_on_risky_warehouse(db_session, env):
    """Правки fbo_max_qty/буферов на рискованном складе НЕ повышают exposure.

    StockTab шлёт PATCH одним `fbo_max_qty` без force-диалога — прежний гейт
    «по любому PATCH с эффективной парой» делал его тупиком 409.
    """
    await _save_risky_pair(db_session, env)
    await _mirror(db_session, env, 0, 10)

    result = await _patch(db_session, env, fbo_max_qty=5)
    assert result["fbo_max_qty"] == 5

    result = await _patch(db_session, env, safety_stock_abs=3, safety_stock_pct=10)
    assert result["safety_stock_abs"] == 3


@pytest.mark.asyncio
async def test_enabling_active_on_saved_risky_pair_gated(db_session, env):
    """Включение `is_active=True` на сохранённой паре translate+ff_mirror — гейтится."""
    await _save_risky_pair(db_session, env, is_active=False)
    await _mirror(db_session, env, 0, 10)

    with pytest.raises(FbsMirrorAboveLedger):
        await _patch(db_session, env, is_active=True)

    db_session.expire_all()
    wh = await _wh(db_session, env)
    assert wh.is_active is False  # отбитый PATCH ничего не сохранил


@pytest.mark.asyncio
async def test_switching_source_to_ff_mirror_gated(db_session, env):
    """Смена stock_source → ff_mirror при включённом translate — повышает exposure."""
    wh = await _wh(db_session, env)
    wh.mode = "translate"  # источник остаётся min_of_both
    await db_session.commit()
    await _mirror(db_session, env, 0, 10)

    with pytest.raises(FbsMirrorAboveLedger):
        await _patch(db_session, env, stock_source="ff_mirror")


@pytest.mark.asyncio
async def test_idempotent_repatch_of_saved_pair_not_gated(db_session, env):
    """Повтор PATCH с ТЕМИ ЖЕ значениями тройки exposure не повышает — проходит."""
    await _save_risky_pair(db_session, env)
    await _mirror(db_session, env, 0, 10)

    result = await _patch(db_session, env, mode="translate", stock_source="ff_mirror")
    assert result["mode"] == "translate"


@pytest.mark.asyncio
async def test_inactive_warehouse_pair_not_gated(db_session, env):
    """Выключенный тумблер (`is_active=False`) в WB ничего не обещает — не гейтится."""
    wh = await _wh(db_session, env)
    wh.is_active = False
    await db_session.commit()
    await _mirror(db_session, env, 0, 10)

    result = await _patch(db_session, env, mode="translate", stock_source="ff_mirror")
    assert result["mode"] == "translate"


# ─── Агрегация разрыва пулом привязок ────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_warehouse_pool_not_gated(db_session, env):
    """Перекрёстное распределение НЕ гейтится: разрыв меряется пулом привязок.

    mirror wh1=10 / ledger wh1=0, mirror wh2=0 / ledger wh2=10: трансляция
    (`_sum_by_warehouse`) обещает WB 10 при учёте 10 — разрыва нет. Прежний
    per-(склад, SKU) GREATEST давал ложный 409 (max(10−0,0) + max(0−10,0) = 10).
    """
    second = Warehouse(
        project_id=env.project_id,
        name="Гейт-склад 2",
        warehouse_type=WarehouseType.FULFILLMENT,
        is_active=True,
    )
    db_session.add(second)
    await db_session.flush()
    db_session.add(
        WbFbsWarehouseLink(
            project_id=env.project_id,
            wb_warehouse_id=WB_WH,
            warehouse_id=second.id,
            is_active=True,
        )
    )
    # wh1: зеркало 10, учёта нет. wh2: ключ зеркала ЕСТЬ (0), учёт 10.
    db_session.add(
        FulfillmentStock(
            project_id=env.project_id,
            warehouse_id=second.id,
            provider="skladbot",
            barcode=env.barcodes[0],
            nomenclature_id=env.nom_ids[0],
            qty_good=0,
        )
    )
    db_session.add(
        WarehouseStock(
            project_id=env.project_id,
            warehouse_id=second.id,
            nomenclature_id=env.nom_ids[0],
            barcode=env.barcodes[0],
            quantity=10,
        )
    )
    await db_session.commit()
    await _mirror(db_session, env, 0, 10)  # wh1

    result = await _patch(db_session, env, mode="translate", stock_source="ff_mirror")
    assert result["mode"] == "translate"


@pytest.mark.asyncio
async def test_ledger_fallback_warehouse_counts_as_source(db_session, env):
    """Склад без зеркала кормится ledger'ом (`_source_qty`) — источник пула
    включает его учёт, и разрыв зеркального склада им НЕ маскируется."""
    second = Warehouse(
        project_id=env.project_id,
        name="Гейт-склад без зеркала",
        warehouse_type=WarehouseType.FULFILLMENT,
        is_active=True,
    )
    db_session.add(second)
    await db_session.flush()
    db_session.add(
        WbFbsWarehouseLink(
            project_id=env.project_id,
            wb_warehouse_id=WB_WH,
            warehouse_id=second.id,
            is_active=True,
        )
    )
    # Ключа зеркала у second НЕТ → его источник = ledger 10 (фолбэк).
    db_session.add(
        WarehouseStock(
            project_id=env.project_id,
            warehouse_id=second.id,
            nomenclature_id=env.nom_ids[0],
            barcode=env.barcodes[0],
            quantity=10,
        )
    )
    await db_session.commit()
    await _mirror(db_session, env, 0, 10)  # wh1: зеркало 10, учёта 0

    # Пул: источник 10 (зеркало wh1) + 10 (ledger wh2) = 20 против учёта 10.
    with pytest.raises(FbsMirrorAboveLedger) as err:
        await _patch(db_session, env, mode="translate", stock_source="ff_mirror")
    assert err.value.mirror_over_ledger == 10
