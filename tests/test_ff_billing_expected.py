# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests: FF billing — тарифы (resolve по датам, закрытие периода) и ожидаемая
стоимость услуг ФФ по заявке сборки (compute_assembly_expected_cost).
"""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import BoxQtyPerWarehouse
from backend.models.ff_billing import WarehouseTariff
from backend.models.warehouse import Warehouse
from backend.schemas.ff_billing import FfTariffPayload
from backend.services.ff_billing import (
    compute_assembly_expected_cost,
    create_tariff,
    resolve_rates,
)

BC1 = "FFB_EXP_BC1"
BC2 = "FFB_EXP_BC2"


async def _mk_nom(db, pid: int, barcode: str) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, updated_at) "
                "VALUES (:pid, :bc, NOW()) RETURNING id"
            ),
            {"pid": pid, "bc": barcode},
        )
    ).scalar()


@pytest_asyncio.fixture
async def env(db_session, project, other_project):
    """ФФ-склад + номенклатура + заявка сборки SHIPPED (2 SKU) + кратности."""
    wh = Warehouse(project_id=project.id, name="FFB EXP WH", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.flush()

    nom1 = await _mk_nom(db_session, project.id, BC1)
    nom2 = await _mk_nom(db_session, project.id, BC2)

    # Кратность только у BC1 (10 шт/короб); BC2 — без кратности (короба=0).
    db_session.add(
        BoxQtyPerWarehouse(project_id=project.id, barcode=BC1, warehouse_id=wh.id, box_qty=10)
    )

    req = AssemblyRequest(
        project_id=project.id,
        warehouse_id=wh.id,
        number="ASM-FFB-1",
        status=AssemblyStatus.SHIPPED,
        pallets_count=2,
        pallet_weight_kg=Decimal("100"),
        shipped_at=datetime(2026, 7, 10, 12, 0, 0),
    )
    db_session.add(req)
    await db_session.flush()
    db_session.add_all(
        [
            AssemblyRequestItem(
                project_id=project.id, assembly_request_id=req.id,
                nomenclature_id=nom1, barcode=BC1, quantity=25,
            ),
            AssemblyRequestItem(
                project_id=project.id, assembly_request_id=req.id,
                nomenclature_id=nom2, barcode=BC2, quantity=7,
            ),
        ]
    )
    await db_session.commit()
    return SimpleNamespace(
        project_id=project.id,
        other_project_id=other_project.id,
        wh_id=wh.id,
        req_id=req.id,
    )


def _tariff(pid, wh_id, service, rate, *, unit=None, vf=date(2026, 7, 1), vt=None):
    return WarehouseTariff(
        project_id=pid, warehouse_id=wh_id, service_type=service,
        unit=unit, rate=Decimal(rate), valid_from=vf, valid_to=vt,
    )


# ─── resolve_rates ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_rates_date_history(db_session, env):
    """История ставок: активная = valid_from<=d<=valid_to; открытый хвост = ∞."""
    db_session.add_all(
        [
            _tariff(env.project_id, env.wh_id, "PALLETIZING", "200",
                    vf=date(2026, 7, 1), vt=date(2026, 7, 14)),
            _tariff(env.project_id, env.wh_id, "PALLETIZING", "500", vf=date(2026, 7, 15)),
        ]
    )
    await db_session.commit()

    r_before = await resolve_rates(db_session, env.project_id, env.wh_id, date(2026, 7, 10))
    r_after = await resolve_rates(db_session, env.project_id, env.wh_id, date(2026, 7, 20))
    assert r_before["PALLETIZING"][0] == Decimal("200")
    assert r_after["PALLETIZING"][0] == Decimal("500")
    # До начала действия ставки — пусто.
    r_none = await resolve_rates(db_session, env.project_id, env.wh_id, date(2026, 6, 1))
    assert "PALLETIZING" not in r_none


@pytest.mark.asyncio
async def test_resolve_rates_overlap_max_valid_from_wins(db_session, env):
    db_session.add_all(
        [
            _tariff(env.project_id, env.wh_id, "STORAGE", "20", vf=date(2026, 7, 1)),
            _tariff(env.project_id, env.wh_id, "STORAGE", "30", vf=date(2026, 7, 5)),
        ]
    )
    await db_session.commit()
    rates = await resolve_rates(db_session, env.project_id, env.wh_id, date(2026, 7, 10))
    assert rates["STORAGE"][0] == Decimal("30")


@pytest.mark.asyncio
async def test_resolve_rates_ignores_soft_deleted_and_other_project(db_session, env):
    dead = _tariff(env.project_id, env.wh_id, "LOADING", "99", unit="BOX")
    dead.soft_delete()
    db_session.add(dead)
    db_session.add(_tariff(env.other_project_id, env.wh_id, "STORAGE", "77"))
    await db_session.commit()
    rates = await resolve_rates(db_session, env.project_id, env.wh_id, date(2026, 7, 10))
    assert "LOADING" not in rates
    assert "STORAGE" not in rates


@pytest.mark.asyncio
async def test_create_tariff_closes_previous_open(db_session, env):
    t1 = await create_tariff(
        db_session, env.project_id, env.wh_id,
        FfTariffPayload(service_type="PALLETIZING", rate=Decimal("200"), valid_from=date(2026, 7, 1)),
    )
    assert t1.valid_to is None
    t2 = await create_tariff(
        db_session, env.project_id, env.wh_id,
        FfTariffPayload(service_type="PALLETIZING", rate=Decimal("300"), valid_from=date(2026, 7, 15)),
    )
    await db_session.refresh(t1)
    assert t1.valid_to == date(2026, 7, 14)  # закрыт день-в-день без пересечения
    assert t2.valid_to is None
    rates = await resolve_rates(db_session, env.project_id, env.wh_id, date(2026, 7, 20))
    assert rates["PALLETIZING"][0] == Decimal("300")


@pytest.mark.asyncio
async def test_create_tariff_rejects_earlier_valid_from(db_session, env):
    await create_tariff(
        db_session, env.project_id, env.wh_id,
        FfTariffPayload(service_type="BOX_PROCESSING", rate=Decimal("30"), valid_from=date(2026, 7, 10)),
    )
    with pytest.raises(HTTPException) as ei:
        await create_tariff(
            db_session, env.project_id, env.wh_id,
            FfTariffPayload(service_type="BOX_PROCESSING", rate=Decimal("40"), valid_from=date(2026, 7, 5)),
        )
    assert ei.value.status_code == 422


# ─── compute_assembly_expected_cost ─────────────────────────────────────────


@pytest_asyncio.fixture
async def tariffs_full(db_session, env):
    db_session.add_all(
        [
            _tariff(env.project_id, env.wh_id, "PALLETIZING", "200"),
            _tariff(env.project_id, env.wh_id, "BOX_PROCESSING", "30"),
            _tariff(env.project_id, env.wh_id, "LOADING", "15", unit="BOX"),
        ]
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_expected_cost_full(db_session, env, tariffs_full):
    """PALLETIZING 2×200 + BOX 3×30 + LOADING(BOX) 3×15 = 535; units=32, boxes=3."""
    res = await compute_assembly_expected_cost(db_session, env.project_id, env.req_id)
    assert res.units == 32
    assert res.boxes == 3  # ceil(25/10)=3; BC2 без кратности → 0 коробов
    assert res.pallets == 2
    by_type = {c.service_type: c for c in res.components}
    assert by_type["PALLETIZING"].cost == Decimal("400.00")
    assert by_type["BOX_PROCESSING"].cost == Decimal("90.00")
    assert by_type["LOADING"].cost == Decimal("45.00")
    assert res.total == Decimal("535.00")
    assert res.missing_tariffs == []


@pytest.mark.asyncio
async def test_expected_cost_loading_by_pallet(db_session, env):
    db_session.add(_tariff(env.project_id, env.wh_id, "LOADING", "150", unit="PALLET"))
    await db_session.commit()
    res = await compute_assembly_expected_cost(db_session, env.project_id, env.req_id)
    loading = next(c for c in res.components if c.service_type == "LOADING")
    assert loading.qty == 2  # паллеты, не короба
    assert loading.cost == Decimal("300.00")


@pytest.mark.asyncio
async def test_expected_cost_custom_and_missing(db_session, env):
    """Без тарифов: только CUSTOM в total, остальные — в missing_tariffs."""
    req = await db_session.get(AssemblyRequest, env.req_id)
    req.ff_custom_cost = Decimal("100.50")
    req.ff_custom_cost_comment = "стрейч"
    await db_session.commit()

    res = await compute_assembly_expected_cost(db_session, env.project_id, env.req_id)
    assert res.custom_cost == Decimal("100.50")
    assert res.custom_cost_comment == "стрейч"
    assert res.total == Decimal("100.50")
    assert set(res.missing_tariffs) == {"PALLETIZING", "BOX_PROCESSING", "LOADING"}
    custom = next(c for c in res.components if c.service_type == "CUSTOM")
    assert custom.cost == Decimal("100.50")


@pytest.mark.asyncio
async def test_expected_cost_rate_on_shipped_date(db_session, env):
    """Ставка берётся на дату отгрузки (2026-07-10), а не на сегодня."""
    db_session.add_all(
        [
            _tariff(env.project_id, env.wh_id, "PALLETIZING", "200",
                    vf=date(2026, 7, 1), vt=date(2026, 7, 14)),
            _tariff(env.project_id, env.wh_id, "PALLETIZING", "999", vf=date(2026, 7, 15)),
        ]
    )
    await db_session.commit()
    res = await compute_assembly_expected_cost(db_session, env.project_id, env.req_id)
    pal = next(c for c in res.components if c.service_type == "PALLETIZING")
    assert pal.rate == Decimal("200")


@pytest.mark.asyncio
async def test_expected_cost_project_isolation(db_session, env):
    with pytest.raises(HTTPException) as ei:
        await compute_assembly_expected_cost(db_session, env.other_project_id, env.req_id)
    assert ei.value.status_code == 404
