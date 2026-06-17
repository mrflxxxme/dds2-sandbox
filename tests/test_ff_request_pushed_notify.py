"""Уведомление в чат склада ФФ при «Создать заявку на ФФ» (push на провайдера).

Покрывает builder build_ff_request_pushed_text (чистый) и оркестрацию
notify_ff_request_pushed (шлёт только в чаты, привязанные к ЭТОМУ складу через
ff_board_warehouse_id; изоляция по проекту). send_analytics_message
монкейпатчится — в тестах нет сети.
"""

import os
from datetime import date

from backend.models.telegram import TelegramChatBinding
from backend.models.warehouse import Warehouse
from backend.services import telegram_service
from backend.services.fulfillment_notify import (
    build_ff_request_pushed_text,
    notify_ff_request_pushed,
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


async def _binding(db, project_id, owner_id, **kw) -> TelegramChatBinding:
    # chat_id глобально UNIQUE — мешаем pid воркера (xdist -n2).
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


# ─── build_ff_request_pushed_text (чистый) ───────────────────────────────────


class TestBuildText:
    def test_full(self):
        text = build_ff_request_pushed_text(
            "WH-R-198032", 18, 512, "Невинномысск", date(2026, 6, 17), date(2026, 6, 18)
        )
        assert "Новая заявка на ФФ" in text
        assert "<code>WH-R-198032</code>" in text
        assert "18" in text and "512" in text
        assert "Невинномысск" in text
        assert "17.06" in text and "18.06" in text

    def test_minimal(self):
        text = build_ff_request_pushed_text("WH-R-1", 1, 5)
        assert "Назначение" not in text
        assert "Забор" not in text
        assert "Разгрузка" not in text


# ─── notify_ff_request_pushed (оркестрация) ──────────────────────────────────


class TestNotify:
    async def test_sends_to_bound_chat(self, db_session, project, monkeypatch):
        sent = _capture(monkeypatch)
        wh = await _wh(db_session, project.id, "Газпром")
        b = await _binding(db_session, project.id, project.owner_id, ff_board_enabled=True, ff_board_warehouse_id=wh.id)
        await notify_ff_request_pushed(
            db_session, project.id, wh.id, ff_number="WH-R-9", items_sent=3, total_qty=100, dest="ЕКБ"
        )
        assert len(sent) == 1
        assert sent[0][0] == b.chat_id
        assert "WH-R-9" in sent[0][1]
        assert "ЕКБ" in sent[0][1]

    async def test_other_warehouse_chat_not_notified(self, db_session, project, monkeypatch):
        sent = _capture(monkeypatch)
        wh = await _wh(db_session, project.id, "Газпром")
        other = await _wh(db_session, project.id, "Целиком")
        # чат привязан к ДРУГОМУ складу
        await _binding(db_session, project.id, project.owner_id, ff_board_enabled=True, ff_board_warehouse_id=other.id)
        await notify_ff_request_pushed(db_session, project.id, wh.id, ff_number="WH-R-9", items_sent=3, total_qty=100)
        assert sent == []

    async def test_no_linked_chat(self, db_session, project, monkeypatch):
        sent = _capture(monkeypatch)
        wh = await _wh(db_session, project.id, "Газпром")
        await notify_ff_request_pushed(db_session, project.id, wh.id, ff_number="WH-R-9", items_sent=1, total_qty=5)
        assert sent == []

    async def test_project_isolation(self, db_session, project, other_project, monkeypatch):
        sent = _capture(monkeypatch)
        wh = await _wh(db_session, project.id, "Газпром")
        await _binding(db_session, project.id, project.owner_id, ff_board_enabled=True, ff_board_warehouse_id=wh.id)
        # вызов под другим проектом — у него нет привязок
        await notify_ff_request_pushed(
            db_session, other_project.id, wh.id, ff_number="WH-R-9", items_sent=1, total_qty=5
        )
        assert sent == []
