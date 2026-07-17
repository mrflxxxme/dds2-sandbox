# ruff: noqa: RUF001, RUF002, RUF003
"""Гонки выдачи номеров документов (`_next_number`, warehouse_stock_engine).

Детерминированные тесты на инвариант/порядок операций (честная многопоточная
гонка не эмулируется): под READ COMMITTED конкурентные транзакции не видят
незакоммиченных INSERT друг друга → одинаковый count(*) → два документа с одним
номером (unique-индекса на number нет). Фикс — pg_advisory_xact_lock по
(int32-хэш префикса, project_id) ПЕРЕД подсчётом, по образцу
wb_returns_service._next_receipt_number и _CURRENT_DRAFT_LOCK_NS черновиков.
"""

import pytest

from backend.models.assembly import AssemblyRequest
from backend.services.warehouse_stock_engine import _next_number, _number_lock_ns


def test_number_lock_ns_stable_int32():
    """Неймспейс из префикса: стабильный между процессами (не hash() с
    PYTHONHASHSEED), знаковый int32 (требование pg_advisory_xact_lock(int4,int4)),
    различный для всех используемых префиксов документов."""
    assert _number_lock_ns("ASM") == _number_lock_ns("ASM")
    prefixes = ["ASM", "IN", "OUT", "TR", "DM", "PB"]
    for p in prefixes:
        ns = _number_lock_ns(p)
        assert -(2**31) <= ns < 2**31
    assert len({_number_lock_ns(p) for p in prefixes}) == len(prefixes)


@pytest.mark.asyncio
async def test_next_number_takes_advisory_lock_before_count(db_session, project, monkeypatch):
    """_next_number обязан взять pg_advisory_xact_lock(_number_lock_ns(prefix), project_id)
    ДО SELECT count(*): лок сериализует выдачу номера per (документ, проект) и
    снимается на commit — к этому моменту строка с номером уже видна следующему
    ожидающему, дубль «ASM-N» невозможен."""
    executed: list[tuple[str, dict | None]] = []
    orig_execute = db_session.execute

    async def spy(statement, *args, **kwargs):
        params = args[0] if args else kwargs.get("parameters")
        executed.append((str(statement), params if isinstance(params, dict) else None))
        return await orig_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", spy)
    number = await _next_number(db_session, project.id, "ASM", AssemblyRequest)
    assert number.startswith("ASM-")

    assert len(executed) >= 2
    assert "pg_advisory_xact_lock" in executed[0][0], "лок не взят / взят не первым"
    assert executed[0][1] == {"ns": _number_lock_ns("ASM"), "pid": project.id}
    assert "count" in executed[1][0].lower()  # подсчёт — строго ПОСЛЕ лока
