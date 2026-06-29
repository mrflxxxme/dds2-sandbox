"""Tests for assembly_load_forecast_service — предпросмотр загрузки WB-складов.

Покрывает:
1. Прогноз по (склад × SKU): текущий остаток + входящая, дни покрытия, светофор.
2. Скорость продаж раскладывается по доле спроса округов (Коледино=ЦФО, Казань=ПФО).
3. Индекс локализации улучшается после поставки (loc% ↑, КТР-индекс ↓).
4. Lead-time из дефолтов при отсутствии истории.
5. Пустой черновик → пустой прогноз (не падает). 404 на чужой/несуществующий.
"""

from datetime import date

import pytest
import pytest_asyncio
from fastapi import HTTPException

from backend.models.integrations import WbFunnelDaily, WbWarehouseStock
from backend.models.wb_order import WbOrder
from backend.schemas.assembly_draft import (
    AssemblyDraftCreate,
    AssemblyDraftDistribution,
    AssemblyDraftRow,
)
from backend.services import assembly_draft_service, assembly_load_forecast_service
from backend.utils.time import utcnow

PROJECT_ID = 0
OTHER_PROJECT_ID = 0
NM_ID = 5_001_777


@pytest_asyncio.fixture(autouse=True)
async def setup(db_session, project, other_project):
    global PROJECT_ID, OTHER_PROJECT_ID
    PROJECT_ID = project.id
    OTHER_PROJECT_ID = other_project.id

    # Текущий остаток WB: Коледино (ЦФО) 30, Казань (ПФО) 10.
    for wh, qty in (("Коледино", 30), ("Казань", 10)):
        db_session.add(
            WbWarehouseStock(
                project_id=PROJECT_ID, nm_id=NM_ID, warehouse_name=wh,
                quantity=qty, quantity_full=qty, vendor_code="VC-FC",
            )
        )
    # Скорость продаж: 300 заказов за сегодня → велосити /30 = 10/день.
    db_session.add(
        WbFunnelDaily(project_id=PROJECT_ID, date=date.today(), nm_id=NM_ID, orders_count=300)
    )
    # География спроса: 60 ЦФО + 40 ПФО → доли 0.6 / 0.4.
    now = utcnow()
    for i in range(60):
        db_session.add(
            WbOrder(
                project_id=PROJECT_ID, srid=f"FC-C-{i}", order_date=now, nm_id=NM_ID,
                warehouse_type="Склад WB", country_name="Россия",
                oblast_okrug_name="Центральный федеральный округ", is_cancel=False,
            )
        )
    for i in range(40):
        db_session.add(
            WbOrder(
                project_id=PROJECT_ID, srid=f"FC-V-{i}", order_date=now, nm_id=NM_ID,
                warehouse_type="Склад WB", country_name="Россия",
                oblast_okrug_name="Приволжский федеральный округ", is_cancel=False,
            )
        )
    await db_session.commit()
    yield


def _draft_payload() -> AssemblyDraftCreate:
    return AssemblyDraftCreate(
        name="FC draft",
        distribution=AssemblyDraftDistribution(
            target_warehouse_names=["Коледино", "Казань"],
            rows=[
                AssemblyDraftRow(
                    nm_id=NM_ID,
                    barcode="FC_BC",
                    vendor_code="VC-FC",
                    src={"1": 150},
                    tgt={"Коледино": 100, "Казань": 50},
                )
            ],
        ),
    )


@pytest.mark.asyncio
async def test_forecast_per_warehouse_stock_and_incoming(db_session):
    """Прогноз содержит оба склада с верными остатком/входящей и положит. покрытием."""
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, _draft_payload())
    fc = await assembly_load_forecast_service.forecast_draft_load(db_session, PROJECT_ID, draft.id)

    by_wh = {it.warehouse_name: it for it in fc.items}
    assert set(by_wh) == {"Коледино", "Казань"}

    kol = by_wh["Коледино"]
    assert kol.district == "central"
    assert kol.current_stock == 30
    assert kol.incoming == 100
    assert kol.avg_daily > 0  # ЦФО-доля скорости досталась Коледино
    assert kol.days_cover > 0

    kaz = by_wh["Казань"]
    assert kaz.district == "volga"
    assert kaz.current_stock == 10
    assert kaz.incoming == 50

    # Скорость по доле спроса: ЦФО (0.6×10=6/д) > ПФО (0.4×10=4/д).
    assert kol.avg_daily > kaz.avg_daily

    assert fc.summary.sku_count == 1
    assert fc.summary.warehouse_count == 2
    assert fc.summary.total_incoming == 150


@pytest.mark.asyncio
async def test_forecast_localization_improves_after_supply(db_session):
    """Поставка повышает локализацию: доля локализации ↑, КТР-индекс ↓."""
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, _draft_payload())
    fc = await assembly_load_forecast_service.forecast_draft_load(db_session, PROJECT_ID, draft.id)

    loc = fc.localization
    assert loc.avg_loc_pct_after > loc.avg_loc_pct_current  # выше = лучше
    assert loc.index_after < loc.index_current  # КТР-индекс ниже = лучше
    assert loc.horizon_days == 30
    # Разбивка по округам: ЦФО и ПФО присутствуют со спросом > 0.
    by_d = {d.district: d for d in loc.by_district}
    assert "central" in by_d and "volga" in by_d
    assert by_d["central"].local_pct_after > by_d["central"].local_pct_current


@pytest.mark.asyncio
async def test_forecast_lead_time_defaults_without_history(db_session):
    """Без истории статусов lead-time берёт дефолты (5 + 7 = 12 дн)."""
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, _draft_payload())
    fc = await assembly_load_forecast_service.forecast_draft_load(db_session, PROJECT_ID, draft.id)
    assert fc.lead_time.has_history is False
    assert fc.lead_time.assembly_ship_days == 5.0
    assert fc.lead_time.delivery_days == 7.0
    assert fc.lead_time.total_days == 12.0


@pytest.mark.asyncio
async def test_forecast_empty_draft(db_session):
    """Пустой черновик → пустой прогноз без падения."""
    draft = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, AssemblyDraftCreate(distribution=AssemblyDraftDistribution()),
    )
    fc = await assembly_load_forecast_service.forecast_draft_load(db_session, PROJECT_ID, draft.id)
    assert fc.items == []
    assert fc.summary.sku_count == 0
    assert fc.warehouses == []


@pytest.mark.asyncio
async def test_forecast_excludes_closed_warehouses(db_session):
    """Закрытые (excluded) склады не показываются в прогнозе (матрица/список)."""
    from backend.services.settings_service import set_excluded_warehouses

    await set_excluded_warehouses(db_session, PROJECT_ID, ["Казань"])
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, _draft_payload())
    fc = await assembly_load_forecast_service.forecast_draft_load(db_session, PROJECT_ID, draft.id)

    whs = {it.warehouse_name for it in fc.items}
    assert "Казань" not in whs       # закрытый склад скрыт
    assert "Коледино" in whs         # открытый остался
    assert all(w.warehouse_name != "Казань" for w in fc.warehouses)


@pytest.mark.asyncio
async def test_forecast_404_other_project(db_session):
    """Чужой проект / несуществующий черновик → 404."""
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, _draft_payload())
    with pytest.raises(HTTPException) as ei:
        await assembly_load_forecast_service.forecast_draft_load(db_session, OTHER_PROJECT_ID, draft.id)
    assert ei.value.status_code == 404
