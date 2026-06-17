"""Уведомление в чат склада ФФ о новых заявках на сборку.

Покрывает builder build_assembly_created_text (чистый, без БД) и оркестрацию
notify_assembly_created (группировка по складу, отправка только в привязанные
чаты, изоляция по проекту). Отправка (send_analytics_message) монкейпатчится —
в тестах нет сети.
"""

import os
from datetime import date
from decimal import Decimal

from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.telegram import TelegramChatBinding
from backend.models.warehouse import Warehouse
from backend.services import telegram_service
from backend.services.fulfillment_notify import (
    _fmt_qty_n,
    build_assembly_created_text,
    notify_assembly_created,
)

_seq = 0


def _uid() -> str:
    global _seq
    _seq += 1
    return f"{_seq:05d}"


async def _wh(db, project_id, name) -> Warehouse:
    w = Warehouse(project_id=project_id, name=name, warehouse_type="FULFILLMENT")
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return w


async def _asm(db, project_id, warehouse_id, **kw) -> AssemblyRequest:
    doc = AssemblyRequest(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=f"ASM-{_uid()}",
        status=AssemblyStatus.IN_PROGRESS.value,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
        **kw,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def _binding(db, project_id, owner_id, **kw) -> TelegramChatBinding:
    # chat_id глобально UNIQUE — мешаем pid воркера, чтобы под xdist (-n2) разные
    # воркеры не сгенерировали одинаковый id (IntegrityError).
    b = TelegramChatBinding(
        chat_id=-(os.getpid() * 1_000_000 + int(_uid())),
        project_id=project_id,
        created_by_id=owner_id,
        **kw,
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b


def _capture(monkeypatch) -> list[tuple]:
    sent: list[tuple] = []

    async def fake_send(chat_id, text, *, reply_markup=None):
        sent.append((chat_id, text))
        return True

    monkeypatch.setattr(telegram_service, "send_analytics_message", fake_send)
    return sent


# ─── build_assembly_created_text (чистый) ────────────────────────────────────


class TestBuildText:
    def test_single(self):
        items = [{"number": "ASM-1", "qty": 1416, "dest": "ЕКБ", "eta": date(2026, 6, 17)}]
        text = build_assembly_created_text(items)
        assert "Новая заявка на сборку" in text
        assert "<code>ASM-1</code>" in text
        assert "ЕКБ" in text
        assert f"{_fmt_qty_n(1416)} шт" in text
        assert "17.06" in text

    def test_single_no_optional_fields(self):
        text = build_assembly_created_text([{"number": "ASM-2", "qty": 5, "dest": None, "eta": None}])
        assert "Новая заявка на сборку" in text
        assert "Назначение" not in text
        assert "План готовности" not in text

    def test_bulk_summary(self):
        items = [{"number": f"ASM-{i}", "qty": 10, "dest": None, "eta": None} for i in range(3)]
        text = build_assembly_created_text(items)
        assert "3 новых заявок" in text
        assert "Итого" in text
        assert f"{_fmt_qty_n(30)} шт" in text
        assert "<blockquote expandable>" in text

    def test_bulk_truncates_tail(self):
        items = [{"number": f"ASM-{i}", "qty": 1, "dest": None, "eta": None} for i in range(25)]
        text = build_assembly_created_text(items)
        assert "…ещё 5" in text


# ─── notify_assembly_created (оркестрация) ───────────────────────────────────


class TestNotifyOrchestration:
    async def test_notifies_linked_chat(self, db_session, project, monkeypatch):
        sent = _capture(monkeypatch)
        wh = await _wh(db_session, project.id, "Газпром")
        b = await _binding(db_session, project.id, project.owner_id, ff_board_enabled=True, ff_board_warehouse_id=wh.id)
        a = await _asm(db_session, project.id, wh.id, wb_warehouse_name_manual="ЕКБ")
        await notify_assembly_created(db_session, project.id, [a.id])
        assert len(sent) == 1
        assert sent[0][0] == b.chat_id
        assert a.number in sent[0][1]
        assert "ЕКБ" in sent[0][1]

    async def test_no_linked_chat_no_send(self, db_session, project, monkeypatch):
        sent = _capture(monkeypatch)
        wh = await _wh(db_session, project.id, "Газпром")  # без привязанного чата
        a = await _asm(db_session, project.id, wh.id)
        await notify_assembly_created(db_session, project.id, [a.id])
        assert sent == []

    async def test_groups_by_warehouse_and_skips_unlinked(self, db_session, project, monkeypatch):
        sent = _capture(monkeypatch)
        wh_a = await _wh(db_session, project.id, "Газпром")
        wh_b = await _wh(db_session, project.id, "Целиком")  # без чата
        await _binding(db_session, project.id, project.owner_id, ff_board_enabled=True, ff_board_warehouse_id=wh_a.id)
        a1 = await _asm(db_session, project.id, wh_a.id)
        a2 = await _asm(db_session, project.id, wh_a.id)
        b1 = await _asm(db_session, project.id, wh_b.id)
        await notify_assembly_created(db_session, project.id, [a1.id, a2.id, b1.id])
        # одно сводное сообщение только в чат склада A; склад B без чата — пропущен
        assert len(sent) == 1
        text = sent[0][1]
        assert "2 новых заявок" in text
        assert a1.number in text and a2.number in text
        assert b1.number not in text

    async def test_empty_ids_noop(self, db_session, project, monkeypatch):
        sent = _capture(monkeypatch)
        await notify_assembly_created(db_session, project.id, [])
        assert sent == []

    async def test_project_isolation(self, db_session, project, other_project, monkeypatch):
        sent = _capture(monkeypatch)
        wh = await _wh(db_session, project.id, "Газпром")
        await _binding(db_session, project.id, project.owner_id, ff_board_enabled=True, ff_board_warehouse_id=wh.id)
        a = await _asm(db_session, project.id, wh.id)
        # вызов под другим проектом — заявка чужая, ничего не шлём
        await notify_assembly_created(db_session, other_project.id, [a.id])
        assert sent == []
