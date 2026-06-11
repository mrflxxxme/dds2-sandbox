"""
Tests for GET /warehouse/assembly/flow-analytics — аналитика потока сборки.

Covers:
1. Длительности этапов и матрица переходов из AssemblyStatusHistory.
2. Fallback для draft-созданных заявок без стартовой записи (вход в
   IN_PROGRESS = created_at).
3. _create_one_request (assembly_draft_service) пишет стартовую запись истории.
4. Каждая из 4 категорий аномалий: stuck_assembly, stuck_shipment,
   wb_accepted_not_shipped (приоритетная, в т.ч. для IN_PROGRESS),
   shipped_not_accepted.
5. Пороги из query-параметров отключают аномалии; пороги в дробных днях
   (3.5 дн > порога 3 — аномалия).
6. Аномалии считаются по текущему состоянию (период не применяется);
   date_from=None — «всё время», без дефолта 90 дней.
7. completed_in_period — только DELIVERED (CLOSED — возврат, не успех).
8. Изоляция по project_id; warehouse_ids=abc → 422.
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from backend.cache import invalidate_cache
from backend.models.assembly import (
    AssemblyDraft,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.wb_fbo import WbFboSupply, WbSupplyStatus
from backend.services.assembly.analytics import get_assembly_flow_analytics
from backend.utils.time import utcnow

BARCODE = "TEST_BC_FLOW_001"


@pytest_asyncio.fixture
async def env(db_session, project, other_project):
    """FULFILLMENT-склад + номенклатура в свежем проекте."""
    wh_id = (
        await db_session.execute(
            text(
                "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
                "VALUES (:pid, 'Flow FF WH', 'FULFILLMENT', 1, true, false, NOW(), NOW()) RETURNING id"
            ),
            {"pid": project.id},
        )
    ).scalar()
    nom_id = (
        await db_session.execute(
            text("INSERT INTO nomenclature (project_id, barcode, updated_at) VALUES (:pid, :bc, NOW()) RETURNING id"),
            {"pid": project.id, "bc": BARCODE},
        )
    ).scalar()
    await db_session.commit()
    return SimpleNamespace(
        project_id=project.id,
        other_project_id=other_project.id,
        wh_id=wh_id,
        nom_id=nom_id,
    )


async def _flow(db_session, project_id: int, **kwargs) -> dict:
    """Вызов аналитики со сбросом кэша (данные мутируются внутри теста)."""
    await invalidate_cache("reports:assembly_flow")
    return await get_assembly_flow_analytics(db_session, project_id, **kwargs)


async def _mk_request(
    db_session,
    env,
    *,
    status: AssemblyStatus,
    created_at: datetime,
    shipped_at: datetime | None = None,
    actual_ready_date=None,
    wb_fbo_supply_id: int | None = None,
    item_qtys: list[int] | None = None,
) -> AssemblyRequest:
    req = AssemblyRequest(
        project_id=env.project_id,
        warehouse_id=env.wh_id,
        number=f"ASM-T{uuid.uuid4().hex[:8]}",
        status=status,
        wb_fbo_supply_id=wb_fbo_supply_id,
        pallets_count=1,
        pallet_weight_kg=Decimal("100.00"),
        created_at=created_at,
        updated_at=created_at,
        shipped_at=shipped_at,
        actual_ready_date=actual_ready_date,
    )
    db_session.add(req)
    await db_session.flush()
    for qty in item_qtys or []:
        db_session.add(
            AssemblyRequestItem(
                project_id=env.project_id,
                assembly_request_id=req.id,
                nomenclature_id=env.nom_id,
                barcode=BARCODE,
                quantity=qty,
            )
        )
    await db_session.commit()
    return req


async def _add_history(db_session, env, req_id: int, old: str | None, new: str, at: datetime) -> None:
    db_session.add(
        AssemblyStatusHistory(
            project_id=env.project_id,
            assembly_request_id=req_id,
            old_status=old,
            new_status=new,
            changed_at=at,
            changed_by="user",
        )
    )
    await db_session.commit()


async def _mk_supply(db_session, env, wb_status: WbSupplyStatus, warehouse_name: str = "Казань") -> WbFboSupply:
    supply = WbFboSupply(
        project_id=env.project_id,
        wb_supply_id=f"FLOW-{uuid.uuid4().hex[:8]}",
        wb_status=wb_status,
        warehouse_name=warehouse_name,
        created_at_wb=utcnow() - timedelta(days=5),
    )
    db_session.add(supply)
    await db_session.commit()
    return supply


def _stage(res: dict, name: str) -> dict:
    return next(s for s in res["stages"] if s["stage"] == name)


def _transition(res: dict, old: str | None, new: str) -> dict | None:
    return next(
        (t for t in res["transitions"] if t["from_status"] == old and t["to_status"] == new),
        None,
    )


# --- Stage durations & transitions -------------------------------------------


@pytest.mark.asyncio
class TestStageDurations:
    async def test_durations_from_history(self, db_session, env):
        """Полная история: IN_PROGRESS 2д → READY 1д → VEHICLE_ASSIGNED 1д → SHIPPED."""
        t0 = utcnow() - timedelta(days=10)
        req = await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.SHIPPED,
            created_at=t0,
            shipped_at=t0 + timedelta(days=4),
            actual_ready_date=(t0 + timedelta(days=2)).date(),
        )
        await _add_history(db_session, env, req.id, None, "IN_PROGRESS", t0)
        await _add_history(db_session, env, req.id, "IN_PROGRESS", "READY", t0 + timedelta(days=2))
        await _add_history(db_session, env, req.id, "READY", "VEHICLE_ASSIGNED", t0 + timedelta(days=3))
        await _add_history(db_session, env, req.id, "VEHICLE_ASSIGNED", "SHIPPED", t0 + timedelta(days=4))

        res = await _flow(db_session, env.project_id)

        assert _stage(res, "IN_PROGRESS")["avg_days"] == pytest.approx(2.0, abs=0.05)
        assert _stage(res, "IN_PROGRESS")["median_days"] == pytest.approx(2.0, abs=0.05)
        assert _stage(res, "IN_PROGRESS")["count"] == 1
        assert _stage(res, "READY")["avg_days"] == pytest.approx(1.0, abs=0.05)
        assert _stage(res, "VEHICLE_ASSIGNED")["avg_days"] == pytest.approx(1.0, abs=0.05)
        # SHIPPED — текущий этап, выхода нет → не посчитан
        assert _stage(res, "SHIPPED")["count"] == 0
        assert _stage(res, "SHIPPED")["avg_days"] is None

        # Матрица переходов
        created = _transition(res, None, "IN_PROGRESS")
        assert created is not None and created["count"] == 1 and created["avg_days"] is None
        to_ready = _transition(res, "IN_PROGRESS", "READY")
        assert to_ready is not None and to_ready["count"] == 1
        assert to_ready["avg_days"] == pytest.approx(2.0, abs=0.05)
        to_shipped = _transition(res, "VEHICLE_ASSIGNED", "SHIPPED")
        assert to_shipped is not None and to_shipped["avg_days"] == pytest.approx(1.0, abs=0.05)

        # Summary: цикл создание→отгрузка 4д, сборка создание→READY 2д
        assert res["summary"]["avg_cycle_days"] == pytest.approx(4.0, abs=0.05)
        assert res["summary"]["avg_assembly_days"] == pytest.approx(2.0, abs=0.5)
        assert res["summary"]["active_count"] == 1  # SHIPPED — в работе
        assert res["summary"]["completed_in_period"] == 0

        # by_warehouse
        wh_row = next(w for w in res["by_warehouse"] if w["warehouse_id"] == env.wh_id)
        assert wh_row["warehouse_name"] == "Flow FF WH"
        assert wh_row["active_count"] == 1
        assert wh_row["avg_cycle_days"] == pytest.approx(4.0, abs=0.05)

        # Пороги отражают дефолты
        assert res["thresholds"] == {"assembly_days": 3, "ship_days": 2, "delivery_days": 7}

    async def test_draft_created_fallback_to_created_at(self, db_session, env):
        """Заявка из черновика без стартовой записи: вход в IN_PROGRESS = created_at."""
        t0 = utcnow() - timedelta(days=5)
        req = await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.READY,
            created_at=t0,
            actual_ready_date=(t0 + timedelta(days=3)).date(),
        )
        # Единственная запись — переход в READY (стартовой записи нет)
        await _add_history(db_session, env, req.id, "IN_PROGRESS", "READY", t0 + timedelta(days=3))

        res = await _flow(db_session, env.project_id)

        assert _stage(res, "IN_PROGRESS")["count"] == 1
        assert _stage(res, "IN_PROGRESS")["avg_days"] == pytest.approx(3.0, abs=0.05)
        to_ready = _transition(res, "IN_PROGRESS", "READY")
        assert to_ready is not None
        assert to_ready["avg_days"] == pytest.approx(3.0, abs=0.05)

    async def test_completed_in_period(self, db_session, env):
        t0 = utcnow() - timedelta(days=10)
        await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.DELIVERED,
            created_at=t0,
            shipped_at=t0 + timedelta(days=2),
        )
        # CLOSED («ВБ не принял», возврат) — НЕ успешное завершение
        await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.CLOSED,
            created_at=t0,
            shipped_at=t0 + timedelta(days=3),
        )
        res = await _flow(db_session, env.project_id)
        assert res["summary"]["completed_in_period"] == 1
        assert res["summary"]["active_count"] == 0
        assert res["anomalies"] == []

    async def test_no_date_from_means_all_time(self, db_session, env):
        """date_from=None — без нижней границы: старая заявка попадает в статистику."""
        t0 = utcnow() - timedelta(days=200)
        req = await _mk_request(db_session, env, status=AssemblyStatus.READY, created_at=t0)
        await _add_history(db_session, env, req.id, None, "IN_PROGRESS", t0)
        await _add_history(db_session, env, req.id, "IN_PROGRESS", "READY", t0 + timedelta(days=2))

        res = await _flow(db_session, env.project_id)
        assert _stage(res, "IN_PROGRESS")["count"] == 1
        assert _stage(res, "IN_PROGRESS")["avg_days"] == pytest.approx(2.0, abs=0.05)

        # А с явным date_from (90 дней) — заявка вне окна
        res2 = await _flow(db_session, env.project_id, date_from=(utcnow() - timedelta(days=90)).date())
        assert _stage(res2, "IN_PROGRESS")["count"] == 0


# --- Draft service writes start history --------------------------------------


@pytest.mark.asyncio
class TestDraftStartHistory:
    async def test_create_one_request_writes_start_record(self, db_session, env):
        from backend.models.cost import Nomenclature
        from backend.schemas.assembly_draft import AssemblyDraftDistribution
        from backend.services.assembly_draft_service import _create_one_request

        draft = AssemblyDraft(project_id=env.project_id, name="Тест-черновик", distribution={})
        db_session.add(draft)
        await db_session.flush()

        nom = (
            await db_session.execute(
                select(Nomenclature).where(
                    Nomenclature.project_id == env.project_id,
                    Nomenclature.barcode == BARCODE,
                )
            )
        ).scalar_one()

        req = await _create_one_request(
            db_session,
            env.project_id,
            source_ff_id=env.wh_id,
            target_wb_name="Электросталь",
            package_type="BOX",
            has_newcomer=False,
            barcodes={BARCODE: 5},
            nom_map={BARCODE: nom},
            distribution=AssemblyDraftDistribution(),
            base_comment=None,
            source_draft_id=draft.id,
        )
        await db_session.commit()

        rows = (
            (
                await db_session.execute(
                    select(AssemblyStatusHistory).where(
                        AssemblyStatusHistory.project_id == env.project_id,
                        AssemblyStatusHistory.assembly_request_id == req.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].old_status is None
        assert rows[0].new_status == "IN_PROGRESS"
        assert rows[0].changed_by == "system"
        assert rows[0].comment == "Создана из черновика"


# --- Anomalies ----------------------------------------------------------------


@pytest.mark.asyncio
class TestAnomalies:
    async def test_stuck_assembly(self, db_session, env):
        t0 = utcnow() - timedelta(days=5)
        req = await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, created_at=t0, item_qtys=[3, 4])
        await _add_history(db_session, env, req.id, None, "IN_PROGRESS", t0)
        # Свежая заявка — не аномалия
        await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, created_at=utcnow() - timedelta(days=1))

        res = await _flow(db_session, env.project_id)
        assert len(res["anomalies"]) == 1
        a = res["anomalies"][0]
        assert a["id"] == req.id
        assert a["kind"] == "stuck_assembly"
        assert a["days_stuck"] == 5
        assert a["total_qty"] == 7
        assert a["warehouse_id"] == env.wh_id
        assert a["warehouse_name"] == "Flow FF WH"
        assert a["wb_fbo_status"] is None
        assert a["since"] is not None
        assert res["summary"]["anomaly_count"] == 1

        wh_row = next(w for w in res["by_warehouse"] if w["warehouse_id"] == env.wh_id)
        assert wh_row["anomaly_count"] == 1
        assert wh_row["active_count"] == 2

    async def test_stuck_assembly_threshold_from_query(self, db_session, env):
        t0 = utcnow() - timedelta(days=5)
        req = await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, created_at=t0)
        await _add_history(db_session, env, req.id, None, "IN_PROGRESS", t0)

        res = await _flow(db_session, env.project_id, assembly_threshold_days=10)
        assert res["anomalies"] == []
        assert res["thresholds"]["assembly_days"] == 10

    async def test_stuck_shipment(self, db_session, env):
        t0 = utcnow() - timedelta(days=10)
        req = await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.VEHICLE_ASSIGNED,
            created_at=t0,
            actual_ready_date=(utcnow() - timedelta(days=4)).date(),
        )
        await _add_history(db_session, env, req.id, None, "IN_PROGRESS", t0)

        res = await _flow(db_session, env.project_id)
        assert len(res["anomalies"]) == 1
        a = res["anomalies"][0]
        assert a["id"] == req.id
        assert a["kind"] == "stuck_shipment"
        assert a["days_stuck"] >= 4

        # Порог из query отключает
        res2 = await _flow(db_session, env.project_id, ship_threshold_days=10)
        assert res2["anomalies"] == []

    async def test_wb_accepted_not_shipped_priority(self, db_session, env):
        """ВБ принял поставку, заявка не отгружена → самая критичная категория,
        даже если по времени заявка ещё не «застряла»."""
        supply = await _mk_supply(db_session, env, WbSupplyStatus.ACCEPTED, warehouse_name="Казань")
        req = await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.READY,
            created_at=utcnow() - timedelta(days=2),
            actual_ready_date=utcnow().date(),  # готова только что — порог не превышен
            wb_fbo_supply_id=supply.id,
        )

        # Второй кейс: VEHICLE_ASSIGNED, давно готова И поставка принята —
        # приоритет wb_accepted_not_shipped, НЕ stuck_shipment (одна категория).
        supply2 = await _mk_supply(db_session, env, WbSupplyStatus.ACCEPTED)
        req2 = await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.VEHICLE_ASSIGNED,
            created_at=utcnow() - timedelta(days=10),
            actual_ready_date=(utcnow() - timedelta(days=5)).date(),
            wb_fbo_supply_id=supply2.id,
        )

        res = await _flow(db_session, env.project_id)
        by_id = {a["id"]: a for a in res["anomalies"]}
        assert set(by_id) == {req.id, req2.id}
        assert by_id[req.id]["kind"] == "wb_accepted_not_shipped"
        assert by_id[req.id]["wb_fbo_status"] == "ACCEPTED"
        assert by_id[req.id]["wb_warehouse_name"] == "Казань"
        assert by_id[req2.id]["kind"] == "wb_accepted_not_shipped"

    async def test_shipped_not_accepted(self, db_session, env):
        supply = await _mk_supply(db_session, env, WbSupplyStatus.ON_DELIVERY)
        req = await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.SHIPPED,
            created_at=utcnow() - timedelta(days=15),
            shipped_at=utcnow() - timedelta(days=10),
            wb_fbo_supply_id=supply.id,
        )
        # DELIVERED — не активная, не аномалия
        await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.DELIVERED,
            created_at=utcnow() - timedelta(days=30),
            shipped_at=utcnow() - timedelta(days=25),
        )

        res = await _flow(db_session, env.project_id)
        assert len(res["anomalies"]) == 1
        a = res["anomalies"][0]
        assert a["id"] == req.id
        assert a["kind"] == "shipped_not_accepted"
        assert a["days_stuck"] == 10
        assert a["wb_fbo_status"] == "ON_DELIVERY"

        res2 = await _flow(db_session, env.project_id, delivery_threshold_days=30)
        assert res2["anomalies"] == []

    async def test_anomalies_ignore_period(self, db_session, env):
        """Аномалии — по текущему состоянию: заявка вне периода всё равно видна."""
        t0 = utcnow() - timedelta(days=200)
        req = await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, created_at=t0)
        await _add_history(db_session, env, req.id, None, "IN_PROGRESS", t0)

        res = await _flow(db_session, env.project_id, date_from=(utcnow() - timedelta(days=90)).date())
        assert len(res["anomalies"]) == 1
        assert res["anomalies"][0]["id"] == req.id
        assert res["anomalies"][0]["days_stuck"] == 200
        # Но в периодную статистику она не попала (создана до date_from)
        assert _stage(res, "IN_PROGRESS")["count"] == 0
        assert res["transitions"] == []

    async def test_wb_accepted_not_shipped_for_in_progress(self, db_session, env):
        """ВБ принял поставку, а заявка ещё В СБОРКЕ (IN_PROGRESS) — тоже
        wb_accepted_not_shipped, даже если порог сборки не превышен."""
        supply = await _mk_supply(db_session, env, WbSupplyStatus.ACCEPTED, warehouse_name="Тула")
        t0 = utcnow() - timedelta(days=1)  # свежая — порог 3 дн не превышен
        req = await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.IN_PROGRESS,
            created_at=t0,
            wb_fbo_supply_id=supply.id,
        )
        await _add_history(db_session, env, req.id, None, "IN_PROGRESS", t0)

        res = await _flow(db_session, env.project_id)
        assert len(res["anomalies"]) == 1
        a = res["anomalies"][0]
        assert a["id"] == req.id
        assert a["kind"] == "wb_accepted_not_shipped"
        assert a["wb_fbo_status"] == "ACCEPTED"
        assert a["wb_warehouse_name"] == "Тула"

    async def test_fractional_threshold_days(self, db_session, env):
        """Пороги в дробных днях: заявка 3.5 дн при пороге 3 — аномалия
        (целочисленное усечение дало бы 3 > 3 == False и пропуск)."""
        t0 = utcnow() - timedelta(days=3, hours=12)
        req = await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, created_at=t0)
        await _add_history(db_session, env, req.id, None, "IN_PROGRESS", t0)

        res = await _flow(db_session, env.project_id, assembly_threshold_days=3)
        assert len(res["anomalies"]) == 1
        assert res["anomalies"][0]["id"] == req.id
        assert res["anomalies"][0]["kind"] == "stuck_assembly"

        # Порог 4 (4.0 > 3.5) — не аномалия
        res2 = await _flow(db_session, env.project_id, assembly_threshold_days=4)
        assert res2["anomalies"] == []

    async def test_ready_since_from_history_not_midnight(self, db_session, env):
        """since для READY — точное время перехода из истории, а не полночь
        actual_ready_date: переход час назад → stuck_shipment нет."""
        t0 = utcnow() - timedelta(days=6)
        req = await _mk_request(
            db_session,
            env,
            status=AssemblyStatus.READY,
            created_at=t0,
            # Дата готовности давняя — старая семантика дала бы 5 дн «застоя»
            actual_ready_date=(utcnow() - timedelta(days=5)).date(),
        )
        await _add_history(db_session, env, req.id, None, "IN_PROGRESS", t0)
        await _add_history(db_session, env, req.id, "IN_PROGRESS", "READY", utcnow() - timedelta(hours=1))

        res = await _flow(db_session, env.project_id)
        assert res["anomalies"] == []


# --- Router validation ----------------------------------------------------------


@pytest.mark.asyncio
async def test_warehouse_ids_not_numeric_422(client, auth_headers):
    """warehouse_ids=abc → 422 (а не 500) на обоих analytics-роутах."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"test_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    headers = {**auth_headers, "X-Project-Id": str(resp.json()["id"])}

    r_flow = await client.get(
        "/api/v1/warehouse/assembly/flow-analytics",
        params={"warehouse_ids": "abc"},
        headers=headers,
    )
    assert r_flow.status_code == 422, r_flow.text
    assert "warehouse_ids" in r_flow.text

    r_log = await client.get(
        "/api/v1/warehouse/assembly/shipments/analytics",
        params={"warehouse_ids": "1,abc"},
        headers=headers,
    )
    assert r_log.status_code == 422, r_log.text


# --- Project isolation ----------------------------------------------------------


@pytest.mark.asyncio
class TestProjectIsolation:
    async def test_other_project_sees_nothing(self, db_session, env):
        t0 = utcnow() - timedelta(days=5)
        req = await _mk_request(db_session, env, status=AssemblyStatus.IN_PROGRESS, created_at=t0)
        await _add_history(db_session, env, req.id, None, "IN_PROGRESS", t0)

        res = await _flow(db_session, env.other_project_id)
        assert res["summary"]["active_count"] == 0
        assert res["summary"]["anomaly_count"] == 0
        assert res["anomalies"] == []
        assert res["transitions"] == []
        assert res["by_warehouse"] == []
        assert all(s["count"] == 0 for s in res["stages"])
