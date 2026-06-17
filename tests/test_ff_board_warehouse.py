"""Per-warehouse pinned FF-board: warehouse-scoped build_ff_board_text + the
telegram_service.set_ff_board_config setter, plus the v2 board signals
(светофор / просрочка / забор сегодня).

Self-contained (own helpers) so it doesn't couple to the other fulfillment test
modules. Assemblies are created without line items — the board tolerates that
(qty → «0 шт»); these tests target the warehouse filter, header and signals, not
quantity formatting (covered by TestBuildFfBoard in test_fulfillment_link_candidates).
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.telegram import TelegramChatBinding
from backend.models.warehouse import Warehouse
from backend.services import fulfillment_service, telegram_service
from backend.utils.time import utcnow

pytestmark = pytest.mark.asyncio

_seq = 0


def _uid() -> str:
    global _seq
    _seq += 1
    return f"{_seq:05d}"


def _today():
    return (utcnow() + timedelta(hours=3)).date()


async def _wh(db, project_id, name, wtype="FULFILLMENT") -> Warehouse:
    w = Warehouse(project_id=project_id, name=name, warehouse_type=wtype)
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return w


async def _asm(db, project_id, warehouse_id, *, status=AssemblyStatus.IN_PROGRESS.value, created_at=None, **kw):
    doc = AssemblyRequest(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=f"ASM-{_uid()}",
        status=status,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
        **kw,
    )
    if created_at is not None:
        doc.created_at = created_at
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def _binding(db, project_id, owner_id, **kw) -> TelegramChatBinding:
    b = TelegramChatBinding(
        chat_id=-int(_uid()) - 700000,
        project_id=project_id,
        created_by_id=owner_id,
        **kw,
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b


# ─── set_ff_board_config ─────────────────────────────────────────────────────


class TestSetFfBoardConfig:
    async def test_enable_with_warehouse(self, db_session, project):
        wh = await _wh(db_session, project.id, "Газпром")
        b = await _binding(db_session, project.id, project.owner_id)
        ok = await telegram_service.set_ff_board_config(db_session, b.id, project.id, True, wh.id)
        assert ok is True
        await db_session.refresh(b)
        assert b.ff_board_enabled is True
        assert b.ff_board_warehouse_id == wh.id

    async def test_enable_all_warehouses(self, db_session, project):
        b = await _binding(db_session, project.id, project.owner_id)
        ok = await telegram_service.set_ff_board_config(db_session, b.id, project.id, True, None)
        assert ok is True
        await db_session.refresh(b)
        assert b.ff_board_enabled is True
        assert b.ff_board_warehouse_id is None

    async def test_disable_clears_warehouse_and_message_id(self, db_session, project):
        wh = await _wh(db_session, project.id, "Газпром")
        b = await _binding(
            db_session,
            project.id,
            project.owner_id,
            ff_board_enabled=True,
            ff_board_warehouse_id=wh.id,
            ff_board_message_id=555,
        )
        ok = await telegram_service.set_ff_board_config(db_session, b.id, project.id, False, wh.id)
        assert ok is True
        await db_session.refresh(b)
        assert b.ff_board_enabled is False
        assert b.ff_board_warehouse_id is None
        assert b.ff_board_message_id is None

    async def test_invalid_warehouse_raises(self, db_session, project, other_project):
        foreign = await _wh(db_session, other_project.id, "Чужой")
        b = await _binding(db_session, project.id, project.owner_id)
        with pytest.raises(ValueError):
            await telegram_service.set_ff_board_config(db_session, b.id, project.id, True, foreign.id)

    async def test_binding_not_found(self, db_session, project):
        ok = await telegram_service.set_ff_board_config(db_session, 999999, project.id, True, None)
        assert ok is False


# ─── build_ff_board_text — warehouse filter + v2 signals ─────────────────────


class TestWarehouseScopedBoard:
    async def test_scoped_excludes_other_warehouse(self, db_session, project):
        wh_a = await _wh(db_session, project.id, "Газпром")
        wh_b = await _wh(db_session, project.id, "Целиком")
        a = await _asm(db_session, project.id, wh_a.id, status=AssemblyStatus.READY.value)
        b = await _asm(db_session, project.id, wh_b.id, status=AssemblyStatus.READY.value)

        text = await fulfillment_service.build_ff_board_text(db_session, project.id, warehouse_id=wh_a.id)
        assert text is not None
        assert a.number in text
        assert b.number not in text
        assert "Газпром" in text  # имя склада в заголовке
        assert "Целиком" not in text

    async def test_global_board_includes_all(self, db_session, project):
        wh_a = await _wh(db_session, project.id, "Газпром")
        wh_b = await _wh(db_session, project.id, "Целиком")
        a = await _asm(db_session, project.id, wh_a.id, status=AssemblyStatus.READY.value)
        b = await _asm(db_session, project.id, wh_b.id, status=AssemblyStatus.READY.value)

        text = await fulfillment_service.build_ff_board_text(db_session, project.id)
        assert text is not None
        assert a.number in text and b.number in text

    async def test_scoped_empty_returns_none(self, db_session, project):
        wh_a = await _wh(db_session, project.id, "Газпром")
        wh_b = await _wh(db_session, project.id, "Целиком")
        await _asm(db_session, project.id, wh_b.id, status=AssemblyStatus.READY.value)
        # склад A пуст → None
        assert await fulfillment_service.build_ff_board_text(db_session, project.id, warehouse_id=wh_a.id) is None

    async def test_overdue_signal(self, db_session, project):
        wh = await _wh(db_session, project.id, "Газпром")
        await _asm(
            db_session,
            project.id,
            wh.id,
            status=AssemblyStatus.IN_PROGRESS.value,
            estimated_ready_date=_today() - timedelta(days=2),
        )
        text = await fulfillment_service.build_ff_board_text(db_session, project.id, warehouse_id=wh.id)
        assert text is not None
        assert "🔴" in text
        assert "Просрочено" in text
        assert "<s>план" in text

    async def test_pickup_today_signal(self, db_session, project):
        wh = await _wh(db_session, project.id, "Газпром")
        await _asm(
            db_session,
            project.id,
            wh.id,
            status=AssemblyStatus.VEHICLE_ASSIGNED.value,
            pickup_date=_today(),
            pickup_time_slot="14–18",
            pickup_cost=Decimal("14400.00"),
            vehicle_brand="Газель",
        )
        text = await fulfillment_service.build_ff_board_text(db_session, project.id, warehouse_id=wh.id)
        assert text is not None
        assert "забор сегодня" in text
        assert f"{fulfillment_service._board_fmt_qty(14400)} ₽" in text
        assert "сегодня" in text

    async def test_calm_day_on_schedule(self, db_session, project):
        wh = await _wh(db_session, project.id, "Газпром")
        await _asm(db_session, project.id, wh.id, status=AssemblyStatus.IN_PROGRESS.value)  # свежая, без плана
        text = await fulfillment_service.build_ff_board_text(db_session, project.id, warehouse_id=wh.id)
        assert text is not None
        assert "всё по графику" in text

    async def test_excludes_soft_deleted_warehouse(self, db_session, project):
        # Soft-delete склада не триггерит FK ON DELETE SET NULL — заявка остаётся
        # активной; табло (общее и scoped) не должно её показывать (Iron Rule #2).
        wh = await _wh(db_session, project.id, "Газпром")
        await _asm(db_session, project.id, wh.id, status=AssemblyStatus.READY.value)
        wh.soft_delete()
        await db_session.commit()
        assert await fulfillment_service.build_ff_board_text(db_session, project.id) is None
        assert await fulfillment_service.build_ff_board_text(db_session, project.id, warehouse_id=wh.id) is None
