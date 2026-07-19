# ruff: noqa: RUF001, RUF002, RUF003
"""GET /reports/stock_need — поле summary.wb_stocks_updated_at (гард свежести).

Поле инжектится на уровне РОУТЕРА, после сервиса get_warehouse_need (он обёрнут
@cached ttl=300): повторный — закэшированный — вызов обязан отдавать СВЕЖИЙ
timestamp остатков WB, а не запечённый в Redis вместе с ответом сервиса.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from backend.utils.time import utcnow


async def _create_project(client, auth_headers, name: str) -> dict:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=auth_headers)
    assert resp.status_code == 200, f"Create project failed: {resp.text}"
    return resp.json()


async def _insert_wb_stock(db_session, project_id: int, ts: datetime) -> None:
    await db_session.execute(
        text(
            "INSERT INTO wb_warehouse_stocks "
            "(project_id, nm_id, warehouse_name, quantity, quantity_full, "
            " in_way_to_client, in_way_from_client, updated_at) "
            "VALUES (:p, 111222, 'Коледино', 5, 5, 0, 0, :ts)"
        ),
        {"p": project_id, "ts": ts},
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_stock_need_returns_wb_stocks_updated_at(client, auth_headers, db_session):
    """summary.wb_stocks_updated_at = max(updated_at) остатков WB проекта (ISO)."""
    project = await _create_project(client, auth_headers, "StockNeed Freshness A")
    headers = {**auth_headers, "X-Project-Id": str(project["id"])}

    # Свежий сток (<1ч) — чтобы эндпоинт не дёргал фоновый авто-синк WB.
    ts = utcnow() - timedelta(minutes=5)
    await _insert_wb_stock(db_session, project["id"], ts)

    resp = await client.get("/api/v1/reports/stock_need", headers=headers)
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    assert "wb_stocks_updated_at" in summary
    got = datetime.fromisoformat(summary["wb_stocks_updated_at"])
    assert abs((got - ts).total_seconds()) < 2


@pytest.mark.asyncio
async def test_stock_need_no_stocks_returns_null(client, auth_headers):
    """Остатков WB нет вовсе → wb_stocks_updated_at = null (не отсутствие ключа)."""
    project = await _create_project(client, auth_headers, "StockNeed Freshness B")
    headers = {**auth_headers, "X-Project-Id": str(project["id"])}

    resp = await client.get("/api/v1/reports/stock_need", headers=headers)
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    assert "wb_stocks_updated_at" in summary
    assert summary["wb_stocks_updated_at"] is None


@pytest.mark.asyncio
async def test_stock_need_timestamp_fresh_on_cached_call(client, auth_headers, db_session):
    """Повторный вызов (сервис уже в кэше) отдаёт НОВЫЙ timestamp после синка.

    Если бы поле пекла закэшированная часть — второй ответ вернул бы старый ts.
    """
    project = await _create_project(client, auth_headers, "StockNeed Freshness C")
    headers = {**auth_headers, "X-Project-Id": str(project["id"])}

    ts1 = utcnow() - timedelta(minutes=30)
    await _insert_wb_stock(db_session, project["id"], ts1)

    resp1 = await client.get("/api/v1/reports/stock_need", headers=headers)
    assert resp1.status_code == 200, resp1.text
    got1 = datetime.fromisoformat(resp1.json()["summary"]["wb_stocks_updated_at"])
    assert abs((got1 - ts1).total_seconds()) < 2

    # «Синк прошёл»: остатки обновились, кэш сервиса (ttl=300) ещё жив.
    ts2 = utcnow()
    await db_session.execute(
        text("UPDATE wb_warehouse_stocks SET updated_at = :ts WHERE project_id = :p"),
        {"ts": ts2, "p": project["id"]},
    )
    await db_session.commit()

    resp2 = await client.get("/api/v1/reports/stock_need", headers=headers)
    assert resp2.status_code == 200, resp2.text
    got2 = datetime.fromisoformat(resp2.json()["summary"]["wb_stocks_updated_at"])
    assert got2 > got1, "timestamp пришёл из запечённого кэша, а не с уровня роутера"
    assert abs((got2 - ts2).total_seconds()) < 2
