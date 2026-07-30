"""
API-тесты домена WB FBS (`/api/v1/fbs/*`) + тип ключа `wb_marketplace`.

Проверяем HTTP-контур, а не поход в WB: аутентификацию, изоляцию по проекту,
409 без ключа «Маркетплейс», валидацию лимитов WB (стикеры ≤100) и happy path
списков (они читают наше зеркало и наружу не ходят).
"""

import asyncio

import pytest
from sqlalchemy import delete, select

from backend.models import (
    IntegrationKey,
    Nomenclature,
    WbFbsOrder,
    WbFbsStockOverride,
    WbFbsSupply,
    WbFbsWarehouse,
)
from backend.utils.crypto import encrypt
from backend.utils.time import utcnow

# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _make_project(client, auth_headers, name: str = "FBS Test") -> dict:
    """Создать проект и вернуть заголовки с X-Project-Id."""
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=auth_headers)
    assert resp.status_code in (200, 201), f"Не создан проект: {resp.text}"
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


def _pid(headers: dict) -> int:
    return int(headers["X-Project-Id"])


# ─── Аутентификация ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fbs_requires_auth(client):
    """Без токена ручки FBS недоступны."""
    for method, url in (
        ("get", "/api/v1/fbs/warehouses"),
        ("get", "/api/v1/fbs/orders"),
        ("get", "/api/v1/fbs/supplies"),
        ("get", "/api/v1/fbs/supplies/WB-GI-1/orders"),
    ):
        resp = await getattr(client, method)(url)
        assert resp.status_code in (401, 403), f"{url}: {resp.status_code}"

    for url in (
        "/api/v1/fbs/stock/push",
        "/api/v1/fbs/supplies/plan",
        "/api/v1/fbs/supplies/bulk",
    ):
        resp = await client.post(url, json={"order_ids": [1]})
        assert resp.status_code in (401, 403), f"{url}: {resp.status_code}"

    # Ручное количество — тоже мутация: без токена внутрь не пускаем.
    resp = await client.post(
        "/api/v1/fbs/stock/override",
        json={"wb_warehouse_id": 1, "nomenclature_ids": [1], "qty": 1},
    )
    assert resp.status_code in (401, 403), resp.status_code


@pytest.mark.asyncio
async def test_fbs_foreign_project_forbidden(client, auth_headers):
    """X-Project-Id чужого проекта → 403 (пользователь не участник)."""
    headers = await _make_project(client, auth_headers, "FBS Foreign")
    alien = {**auth_headers, "X-Project-Id": str(_pid(headers) + 10_000_000)}
    resp = await client.get("/api/v1/fbs/warehouses", headers=alien)
    assert resp.status_code == 403


# ─── Изоляция по проекту ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fbs_orders_isolated_by_project(client, auth_headers, db_session):
    """Задание проекта A не видно из проекта B (и наоборот)."""
    headers_a = await _make_project(client, auth_headers, "FBS Iso A")
    headers_b = await _make_project(client, auth_headers, "FBS Iso B")
    pid_a, pid_b = _pid(headers_a), _pid(headers_b)

    wb_order_id = 900_000_000 + pid_a
    db_session.add(
        WbFbsOrder(
            project_id=pid_a,
            wb_order_id=wb_order_id,
            supplier_status="new",
            wb_warehouse_id=1234567,
            barcode="2000000000001",
            synced_at=utcnow(),
        )
    )
    await db_session.commit()

    try:
        resp_a = await client.get("/api/v1/fbs/orders", headers=headers_a)
        assert resp_a.status_code == 200, resp_a.text
        data_a = resp_a.json()
        assert wb_order_id in [row["wb_order_id"] for row in data_a["items"]]

        resp_b = await client.get("/api/v1/fbs/orders", headers=headers_b)
        assert resp_b.status_code == 200, resp_b.text
        data_b = resp_b.json()
        assert wb_order_id not in [row["wb_order_id"] for row in data_b["items"]]
        assert data_b["total"] == 0
    finally:
        await db_session.execute(delete(WbFbsOrder).where(WbFbsOrder.project_id.in_([pid_a, pid_b])))
        await db_session.commit()


@pytest.mark.asyncio
async def test_fbs_warehouses_isolated_by_project(client, auth_headers, db_session):
    """Склад продавца проекта A не попадает в список проекта B."""
    headers_a = await _make_project(client, auth_headers, "FBS WH Iso A")
    headers_b = await _make_project(client, auth_headers, "FBS WH Iso B")
    pid_a, pid_b = _pid(headers_a), _pid(headers_b)

    wb_warehouse_id = 700_000_000 + pid_a
    db_session.add(
        WbFbsWarehouse(
            project_id=pid_a,
            wb_warehouse_id=wb_warehouse_id,
            name="Тестовый склад продавца",
            is_active=True,
        )
    )
    await db_session.commit()

    try:
        resp_a = await client.get("/api/v1/fbs/warehouses", headers=headers_a)
        assert resp_a.status_code == 200, resp_a.text
        assert wb_warehouse_id in [row["wb_warehouse_id"] for row in resp_a.json()]

        resp_b = await client.get("/api/v1/fbs/warehouses", headers=headers_b)
        assert resp_b.status_code == 200, resp_b.text
        assert wb_warehouse_id not in [row["wb_warehouse_id"] for row in resp_b.json()]
    finally:
        await db_session.execute(delete(WbFbsWarehouse).where(WbFbsWarehouse.project_id.in_([pid_a, pid_b])))
        await db_session.commit()


# ─── 409 без ключа «Маркетплейс» ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fbs_offices_without_key_409(client, auth_headers):
    """Ручка, требующая похода в WB, без ключа отвечает 409 с внятным текстом."""
    headers = await _make_project(client, auth_headers, "FBS No Key")
    resp = await client.get("/api/v1/fbs/offices", headers=headers)
    assert resp.status_code == 409, resp.text
    assert "Маркетплейс" in resp.text


@pytest.mark.asyncio
async def test_fbs_stock_push_without_key_409(client, auth_headers):
    """Push проверяет ключ СИНХРОННО — иначе фронт получил бы 200 и тишину."""
    headers = await _make_project(client, auth_headers, "FBS Push No Key")
    resp = await client.post("/api/v1/fbs/stock/push", json={}, headers=headers)
    assert resp.status_code == 409, resp.text
    assert "Маркетплейс" in resp.text


@pytest.mark.asyncio
async def test_stock_push_returns_before_wb_call(client, auth_headers, db_session, monkeypatch):
    """С ключом push отвечает СРАЗУ, а поход в WB уезжает в фоновый таск."""
    from backend.routers import wb_fbs as fbs_router

    headers = await _make_project(client, auth_headers, "FBS Push BG")
    pid = _pid(headers)

    db_session.add(
        IntegrationKey(
            project_id=pid,
            service="wb_marketplace",
            label="Маркетплейс",
            encrypted_key=encrypt("fake_marketplace_token"),
            is_active=True,
        )
    )
    await db_session.commit()

    calls: list[dict] = []

    async def _fake_push(db, project_id, **kwargs):
        calls.append({"project_id": project_id, **kwargs})
        return [1]

    monkeypatch.setattr(fbs_router.stock_service, "push_stocks", _fake_push)

    try:
        resp = await client.post(
            "/api/v1/fbs/stock/push",
            json={"wb_warehouse_ids": [111, 111, 222], "force": True},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        # Дедуп складов: 111 передан дважды — прогон должен быть один.
        assert body["affected"] == 2

        # Фоновый таск получает управление на первом же await.
        for _ in range(50):
            if calls:
                break
            await asyncio.sleep(0.02)
        assert calls, "Фоновый прогон трансляции не стартовал"
        assert calls[0]["project_id"] == pid
        assert calls[0]["trigger"] == "manual"
        assert calls[0]["force"] is True
        assert calls[0]["wb_warehouse_ids"] == [111, 222]
        assert calls[0]["user_id"] is not None
    finally:
        await db_session.execute(delete(IntegrationKey).where(IntegrationKey.project_id == pid))
        await db_session.commit()


@pytest.mark.asyncio
async def test_fbs_warehouses_sync_without_key_409(client, auth_headers):
    """Синк справочника складов без ключа — тоже 409, не 500."""
    headers = await _make_project(client, auth_headers, "FBS Sync No Key")
    resp = await client.post("/api/v1/fbs/warehouses/sync", headers=headers)
    assert resp.status_code == 409, resp.text


# ─── Валидация входа ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stickers_over_wb_limit_422(client, auth_headers):
    """WB принимает ≤100 заданий на запрос стикеров — 101 отбиваем на входе."""
    headers = await _make_project(client, auth_headers, "FBS Stickers")
    resp = await client.post(
        "/api/v1/fbs/orders/stickers",
        json={"order_ids": list(range(1, 102)), "type": "png"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_stickers_empty_and_bad_type_422(client, auth_headers):
    """Пустой список и неизвестный формат стикера — 422."""
    headers = await _make_project(client, auth_headers, "FBS Stickers 2")

    resp = await client.post("/api/v1/fbs/orders/stickers", json={"order_ids": []}, headers=headers)
    assert resp.status_code == 422

    resp = await client.post(
        "/api/v1/fbs/orders/stickers",
        json={"order_ids": [1], "type": "pdf"},
        headers=headers,
    )
    assert resp.status_code == 422

    resp = await client.post(
        "/api/v1/fbs/orders/stickers",
        json={"order_ids": [1], "type": "png", "width": 100},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_orders_bad_status_422(client, auth_headers):
    """Неизвестный supplier_status в фильтре — 422, а не тихий пустой список."""
    headers = await _make_project(client, auth_headers, "FBS Orders Filter")
    resp = await client.get("/api/v1/fbs/orders?status=shipped", headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_stock_preview_requires_warehouse_id_422(client, auth_headers):
    """Превью без wb_warehouse_id — 422 (обязательный query-параметр)."""
    headers = await _make_project(client, auth_headers, "FBS Preview")
    resp = await client.get("/api/v1/fbs/stock/preview", headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_supply_barcode_bad_type_422(client, auth_headers):
    """Формат QR поставки валидируется до похода в WB."""
    headers = await _make_project(client, auth_headers, "FBS Barcode")
    resp = await client.get("/api/v1/fbs/supplies/WB-GI-1/barcode?type=pdf", headers=headers)
    assert resp.status_code == 422


# ─── Happy path списков (только наше зеркало, WB не дёргаем) ─────────────────


@pytest.mark.asyncio
async def test_fbs_lists_empty_for_new_project(client, auth_headers):
    """Новый проект: списки отдают 200 и пустой результат, а не 409/500."""
    headers = await _make_project(client, auth_headers, "FBS Empty Lists")

    resp = await client.get("/api/v1/fbs/warehouses", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    resp = await client.get("/api/v1/fbs/stock/pushes", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    resp = await client.get("/api/v1/fbs/supplies", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    resp = await client.get("/api/v1/fbs/orders", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert isinstance(data["status_counts"], dict)


@pytest.mark.asyncio
async def test_orders_filters_accepted(client, auth_headers):
    """Полный набор фильтров списка заданий принимается (200, пустой результат)."""
    headers = await _make_project(client, auth_headers, "FBS Orders Params")
    resp = await client.get(
        "/api/v1/fbs/orders?status=new&wb_warehouse_id=1&supply_id=WB-GI-1"
        "&date_from=2026-07-01&date_to=2026-07-24&limit=10&offset=0",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


# ─── Потоварная замена количества (/fbs/stock/override) ──────────────────────
#
# Система правил (четыре уровня с приоритетами и отдельной вкладкой) удалена
# целиком: количество задаётся одним полем в строке таблицы остатков.


async def _override_fixture(db_session, project_id: int, *, suffix: int = 0) -> tuple[int, int]:
    """Склад продавца + номенклатура проекта → (wb_warehouse_id, nomenclature_id).

    Оба нужны по FK/доменной проверке: ручное количество живёт на паре
    (склад WB, товар), и чужой товар в проект попасть не должен.
    """
    wb_warehouse_id = 750_000_000 + project_id * 10 + suffix
    db_session.add(
        WbFbsWarehouse(
            project_id=project_id,
            wb_warehouse_id=wb_warehouse_id,
            name="Склад продавца (ручное количество)",
            is_active=True,
        )
    )
    nomenclature = Nomenclature(
        project_id=project_id,
        barcode=f"29{project_id:08d}{suffix:02d}",
        brand="Бренд ручного количества",
        subject="Предмет",
        article_seller="ART-OVR",
        chrt_id=880_000_000 + project_id * 10 + suffix,
    )
    db_session.add(nomenclature)
    await db_session.commit()
    return wb_warehouse_id, nomenclature.id


async def _overrides(db_session, project_id: int) -> list[WbFbsStockOverride]:
    """Ручные количества проекта прямо из БД — минуя кэш превью."""
    db_session.expire_all()
    result = await db_session.execute(select(WbFbsStockOverride).where(WbFbsStockOverride.project_id == project_id))
    return list(result.scalars().all())


async def _cleanup_override(db_session, project_ids: list[int]) -> None:
    await db_session.execute(delete(WbFbsStockOverride).where(WbFbsStockOverride.project_id.in_(project_ids)))
    await db_session.execute(delete(WbFbsWarehouse).where(WbFbsWarehouse.project_id.in_(project_ids)))
    await db_session.execute(delete(Nomenclature).where(Nomenclature.project_id.in_(project_ids)))
    await db_session.commit()


@pytest.mark.asyncio
async def test_fbs_rules_endpoints_removed(client, auth_headers):
    """Ручек правил больше нет: 404 на каждой (а не 405/422 и не рабочий ответ)."""
    headers = await _make_project(client, auth_headers, "FBS Rules Gone")

    for url in ("/api/v1/fbs/rules", "/api/v1/fbs/rules/options"):
        resp = await client.get(url, headers=headers)
        assert resp.status_code == 404, f"{url} всё ещё отвечает {resp.status_code}"

    for url in ("/api/v1/fbs/rules", "/api/v1/fbs/rules/bulk"):
        resp = await client.post(url, json={}, headers=headers)
        assert resp.status_code == 404, f"{url} всё ещё отвечает {resp.status_code}"

    resp = await client.delete("/api/v1/fbs/rules/1", headers=headers)
    assert resp.status_code == 404, resp.status_code

    # И в самом роутере не осталось ни одного пути правил.
    from backend.routers.wb_fbs import router

    stale = [getattr(route, "path", "") for route in router.routes if "/rules" in getattr(route, "path", "")]
    assert stale == [], f"В роутере остались пути правил: {stale}"


@pytest.mark.asyncio
async def test_stock_override_set_cap_zero_and_clear(client, auth_headers, db_session):
    """Потолок → «не отдавать» (0) → снятие (null): одна строка на товар и склад."""
    headers = await _make_project(client, auth_headers, "FBS Override CRUD")
    pid = _pid(headers)
    wb_warehouse_id, nomenclature_id = await _override_fixture(db_session, pid)
    body = {"wb_warehouse_id": wb_warehouse_id, "nomenclature_ids": [nomenclature_id]}

    try:
        # Потолок: итог = min(7, расчёт).
        resp = await client.post("/api/v1/fbs/stock/override", json={**body, "qty": 7}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        rows = await _overrides(db_session, pid)
        assert len(rows) == 1, f"Ожидалась одна строка ручного количества: {rows}"
        assert rows[0].qty == 7
        assert rows[0].wb_warehouse_id == wb_warehouse_id
        assert rows[0].nomenclature_id == nomenclature_id

        # Повтор по тому же товару обновляет строку, а не плодит вторую (UNIQUE).
        resp = await client.post("/api/v1/fbs/stock/override", json={**body, "qty": 0}, headers=headers)
        assert resp.status_code == 200, resp.text
        rows = await _overrides(db_session, pid)
        assert len(rows) == 1, f"Повтор задвоил ручное количество: {rows}"
        assert rows[0].qty == 0, "qty=0 — «не отдавать», строка обязана остаться"

        # null — снять ограничение: строки быть не должно.
        resp = await client.post("/api/v1/fbs/stock/override", json={**body, "qty": None}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert await _overrides(db_session, pid) == []
    finally:
        await _cleanup_override(db_session, [pid])


@pytest.mark.asyncio
async def test_stock_override_works_in_safe_mode_without_key(client, auth_headers, db_session):
    """Ручное количество — запись в НАШУ базу: ни ключа, ни режима `prod` не требует.

    В тестах контур `safe` (запись в WB заблокирована), ключа «Маркетплейс» у
    проекта нет — 200 доказывает, что ручка не гейтится ни тем, ни другим.
    """
    from backend.integrations.wb_fbs_api import current_mode, is_write_enabled

    if is_write_enabled(current_mode()):
        pytest.skip("Проверка про режим safe: при WB_FBS_MODE=prod гейта записи в WB нет")

    headers = await _make_project(client, auth_headers, "FBS Override Safe")
    pid = _pid(headers)
    wb_warehouse_id, nomenclature_id = await _override_fixture(db_session, pid)

    try:
        resp = await client.post(
            "/api/v1/fbs/stock/override",
            json={"wb_warehouse_id": wb_warehouse_id, "nomenclature_ids": [nomenclature_id], "qty": 3},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        rows = await _overrides(db_session, pid)
        assert [r.qty for r in rows] == [3]
    finally:
        await _cleanup_override(db_session, [pid])


@pytest.mark.asyncio
async def test_stock_override_validation_422(client, auth_headers):
    """Границы контракта отбиваются на входе, до любой работы с БД."""
    headers = await _make_project(client, auth_headers, "FBS Override Limits")
    base = {"wb_warehouse_id": 1234567, "qty": 1}

    # Пустое выделение — нечего применять.
    resp = await client.post("/api/v1/fbs/stock/override", json={**base, "nomenclature_ids": []}, headers=headers)
    assert resp.status_code == 422, resp.text

    # Лимит 5000 позиций за раз.
    resp = await client.post(
        "/api/v1/fbs/stock/override",
        json={**base, "nomenclature_ids": list(range(1, 5002))},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    # Ровно 5000 — граница разрешена (дальше уже доменная логика).
    resp = await client.post(
        "/api/v1/fbs/stock/override",
        json={**base, "nomenclature_ids": list(range(1, 5001))},
        headers=headers,
    )
    assert resp.status_code != 422, f"Отбита разрешённая граница 5000: {resp.text}"

    # Отрицательное количество бессмысленно.
    resp = await client.post(
        "/api/v1/fbs/stock/override",
        json={"wb_warehouse_id": 1234567, "nomenclature_ids": [1], "qty": -1},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    # Склад обязателен: ручное количество всегда привязано к складу продавца.
    resp = await client.post(
        "/api/v1/fbs/stock/override",
        json={"nomenclature_ids": [1], "qty": 1},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_stock_override_isolated_by_project(client, auth_headers, db_session):
    """Товар проекта A из проекта B не переопределяется, и строка A не меняется."""
    headers_a = await _make_project(client, auth_headers, "FBS Override Iso A")
    headers_b = await _make_project(client, auth_headers, "FBS Override Iso B")
    pid_a, pid_b = _pid(headers_a), _pid(headers_b)

    wh_a, nom_a = await _override_fixture(db_session, pid_a)
    await _override_fixture(db_session, pid_b, suffix=1)

    try:
        resp = await client.post(
            "/api/v1/fbs/stock/override",
            json={"wb_warehouse_id": wh_a, "nomenclature_ids": [nom_a], "qty": 9},
            headers=headers_a,
        )
        assert resp.status_code == 200, resp.text

        # Из проекта B тот же товар и склад не резолвятся: доменное решение
        # сервиса — 404/422 либо 200 с affected=0, инвариант один.
        resp = await client.post(
            "/api/v1/fbs/stock/override",
            json={"wb_warehouse_id": wh_a, "nomenclature_ids": [nom_a], "qty": 1},
            headers=headers_b,
        )
        assert resp.status_code in (200, 404, 422), resp.text
        if resp.status_code == 200:
            assert resp.json()["affected"] == 0, "Чужой товар не должен порождать ручное количество"

        assert await _overrides(db_session, pid_b) == [], "В чужом проекте появилось ручное количество"
        rows_a = await _overrides(db_session, pid_a)
        assert [r.qty for r in rows_a] == [9], f"Соседний проект переписал ручное количество: {rows_a}"
    finally:
        await _cleanup_override(db_session, [pid_a, pid_b])


@pytest.mark.asyncio
async def test_warehouse_settings_accept_mode_and_fbo_gate(client, auth_headers, db_session):
    """PATCH настроек склада принимает `mode` и `fbo_max_qty` (гейт «чего нет на FBO»)."""
    headers = await _make_project(client, auth_headers, "FBS WH Settings")
    pid = _pid(headers)
    wb_warehouse_id = 760_000_000 + pid
    db_session.add(
        WbFbsWarehouse(
            project_id=pid,
            wb_warehouse_id=wb_warehouse_id,
            name="Склад настроек",
            is_active=True,
        )
    )
    await db_session.commit()
    url = f"/api/v1/fbs/warehouses/{wb_warehouse_id}/settings"

    try:
        resp = await client.patch(url, json={"mode": "translate", "fbo_max_qty": 5}, headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["mode"] == "translate"
        assert data["fbo_max_qty"] == 5

        # -1 = «снять гейт»: отрицательный порог не должен доехать до расчёта.
        resp = await client.patch(url, json={"fbo_max_qty": -1}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["fbo_max_qty"] is None, "-1 обязан сниматься, а не сохраняться порогом"

        # Неизвестный режим склада — 422.
        resp = await client.patch(url, json={"mode": "turbo"}, headers=headers)
        assert resp.status_code == 422, resp.text
    finally:
        await db_session.execute(delete(WbFbsWarehouse).where(WbFbsWarehouse.project_id == pid))
        await db_session.commit()


@pytest.mark.asyncio
async def test_warehouse_settings_gate_structured_409_not_422(client, auth_headers, db_session):
    """Гейт «зеркало выше учёта» → 409 со структурированным detail, НЕ 422.

    Фронт опознаёт «свой» конфликт по `code`, а не по тексту. Закрепление
    порядка обработки: `except FbsMirrorAboveLedger` стоит ВНУТРИ `_fbs_errors`,
    иначе общий `except Exception` перевёл бы класс `Fbs*` в 422 и 409
    деградировал бы молча.
    """
    from backend.models import FulfillmentStock, WbFbsWarehouseLink
    from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType

    headers = await _make_project(client, auth_headers, "FBS Gate 409")
    pid = _pid(headers)
    wb_warehouse_id = 761_000_000 + pid

    our_wh = Warehouse(
        project_id=pid,
        name="Наш склад гейта 409",
        warehouse_type=WarehouseType.FULFILLMENT,
        is_active=True,
    )
    db_session.add(our_wh)
    nomenclature = Nomenclature(
        project_id=pid, barcode=f"27{pid:08d}09", chrt_id=881_000_000 + pid
    )
    db_session.add(nomenclature)
    await db_session.flush()
    db_session.add(
        WbFbsWarehouse(
            project_id=pid, wb_warehouse_id=wb_warehouse_id, name="Продавец гейта 409"
        )
    )
    db_session.add(
        WbFbsWarehouseLink(
            project_id=pid,
            wb_warehouse_id=wb_warehouse_id,
            warehouse_id=our_wh.id,
            is_active=True,
        )
    )
    # Зеркало 10 против учёта 4 → разрыв 6.
    db_session.add(
        FulfillmentStock(
            project_id=pid,
            warehouse_id=our_wh.id,
            provider="skladbot",
            barcode=nomenclature.barcode,
            nomenclature_id=nomenclature.id,
            qty_good=10,
        )
    )
    db_session.add(
        WarehouseStock(
            project_id=pid,
            warehouse_id=our_wh.id,
            nomenclature_id=nomenclature.id,
            barcode=nomenclature.barcode,
            quantity=4,
        )
    )
    await db_session.commit()
    url = f"/api/v1/fbs/warehouses/{wb_warehouse_id}/settings"
    risky = {"is_active": True, "mode": "translate", "stock_source": "ff_mirror"}

    try:
        resp = await client.patch(url, json=risky, headers=headers)
        assert resp.status_code == 409, f"Ожидался 409, не {resp.status_code}: {resp.text}"
        # Глобальный обработчик (backend/exceptions.py) заворачивает dict-detail
        # в конверт {"error": {..., "payload": <dict>}} — верхнеуровневого
        # `detail` у этого приложения не бывает; фронт читает error.payload.
        detail = resp.json()["error"]["payload"]
        assert detail["code"] == "fbs_mirror_above_ledger"
        assert detail["mirror_over_ledger"] == 6
        assert detail["ledger_total"] == 4
        assert detail["mirror_total"] == 10
        assert "force" in detail["message"]

        # force=true — решение человека, применяет как есть.
        resp = await client.patch(url, json={**risky, "force": True}, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["stock_source"] == "ff_mirror"
    finally:
        await db_session.execute(delete(WbFbsWarehouseLink).where(WbFbsWarehouseLink.project_id == pid))
        await db_session.execute(delete(FulfillmentStock).where(FulfillmentStock.project_id == pid))
        await db_session.execute(delete(WarehouseStock).where(WarehouseStock.project_id == pid))
        await db_session.execute(delete(WbFbsWarehouse).where(WbFbsWarehouse.project_id == pid))
        await db_session.execute(delete(Nomenclature).where(Nomenclature.project_id == pid))
        await db_session.execute(delete(Warehouse).where(Warehouse.project_id == pid))
        await db_session.commit()


# ─── Поставки: план разбиения и массовое создание ────────────────────────────


def _fbs_order(
    project_id: int,
    wb_order_id: int,
    *,
    wb_warehouse_id: int,
    cargo_type: int = 1,
    cross_border_type: int = 0,
    supplier_status: str = "new",
    supply_id: str | None = None,
) -> WbFbsOrder:
    """Сборочное задание в зеркале — минимум полей, нужных для группировки."""
    return WbFbsOrder(
        project_id=project_id,
        wb_order_id=wb_order_id,
        supplier_status=supplier_status,
        wb_warehouse_id=wb_warehouse_id,
        cargo_type=cargo_type,
        cross_border_type=cross_border_type,
        supply_id=supply_id,
        barcode="2000000000001",
        synced_at=utcnow(),
    )


def _groups_by_warehouse(payload: dict) -> dict[int | None, dict]:
    return {g.get("wb_warehouse_id"): g for g in payload.get("groups", [])}


def test_supplies_static_routes_declared_before_path_params():
    """`/supplies/plan|bulk|sync` объявлены ДО `/supplies/{wb_supply_id}`.

    Иначе «plan» уехал бы в path-параметр и попал в сервис как id поставки.
    Проверяем интроспекцией `router.routes`: порядок объявления — это и есть
    порядок матчинга у Starlette, тест держит его при любых будущих вставках.
    """
    from backend.routers.wb_fbs import router

    paths = [getattr(route, "path", "") for route in router.routes]
    first_param_idx = min(
        (i for i, p in enumerate(paths) if p.startswith("/fbs/supplies/{")),
        default=len(paths),
    )
    for static in ("/fbs/supplies/sync", "/fbs/supplies/plan", "/fbs/supplies/bulk"):
        assert static in paths, f"Роут {static} не зарегистрирован: {paths}"
        assert paths.index(static) < first_param_idx, (
            f"{static} объявлен ПОСЛЕ path-параметра /fbs/supplies/{{…}} — будет перехвачен"
        )


def test_static_stock_routes_matched_before_path_params():
    """URL остатков достаётся своему роуту, а не более раннему path-параметру.

    Проверка сильнее сравнения индексов: берём ПЕРВЫЙ роут, чей скомпилированный
    regex матчит путь (ровно так выбирает Starlette), и требуем, чтобы это был
    он сам. Любая будущая вставка вида `/fbs/{something}` выше по файлу
    перехватила бы `/fbs/stock/override` — тест это поймает.
    """
    from backend.routers.wb_fbs import router

    for url in ("/fbs/stock/override", "/fbs/stock/preview", "/fbs/stock/push", "/fbs/stock/reconcile"):
        matched = next(
            (route for route in router.routes if route.path_regex.match(url)),
            None,
        )
        assert matched is not None, f"Роут {url} не зарегистрирован"
        assert matched.path == url, f"{url} перехватывает роут {matched.path}"


@pytest.mark.asyncio
async def test_supply_plan_groups_by_warehouse_in_safe_mode(client, auth_headers, db_session):
    """План разбивает выделенные задания по складам продавца и работает в `safe`.

    WB запрещает класть в одну поставку задания с РАЗНЫХ складов, поэтому при
    нескольких складах поставок обязано получиться несколько. Ключа
    «Маркетплейс» у проекта нет — 200 доказывает, что план читает зеркало и
    в WB не ходит (иначе был бы 409).
    """
    headers = await _make_project(client, auth_headers, "FBS Plan Split")
    pid = _pid(headers)
    wh_a, wh_b = 700_100_000 + pid, 700_200_000 + pid
    ids = [910_000_000 + pid * 10 + i for i in range(3)]

    db_session.add_all(
        [
            _fbs_order(pid, ids[0], wb_warehouse_id=wh_a),
            _fbs_order(pid, ids[1], wb_warehouse_id=wh_a),
            _fbs_order(pid, ids[2], wb_warehouse_id=wh_b),
        ]
    )
    await db_session.commit()

    try:
        resp = await client.post("/api/v1/fbs/supplies/plan", json={"order_ids": ids}, headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total_orders"] == 3, data
        assert data["supplies_count"] >= 2, f"Два склада — минимум две поставки: {data}"

        groups = _groups_by_warehouse(data)
        assert wh_a in groups and wh_b in groups, f"Группы по складам не собрались: {data}"
        # Группа обязана возвращать ТЕ ЖЕ id, что клиент прислал (`wb_order_id`):
        # их же он потом отправит в /supplies/bulk, иначе фронту нечем сматчить.
        assert sorted(groups[wh_a]["order_ids"]) == sorted(ids[:2]), (
            f"План вернул чужое id-пространство вместо wb_order_id: {groups[wh_a]}"
        )
        assert groups[wh_a]["orders_count"] == 2
        assert groups[wh_b]["order_ids"] == [ids[2]]
        # Задания однородные и свободные — блокировать нечего.
        assert groups[wh_a]["blocked_reason"] is None
        assert groups[wh_b]["blocked_reason"] is None
    finally:
        await db_session.execute(delete(WbFbsOrder).where(WbFbsOrder.project_id == pid))
        await db_session.commit()


@pytest.mark.asyncio
async def test_supply_plan_isolated_by_project(client, auth_headers, db_session):
    """Задание проекта A не попадает в отправляемую группу плана проекта B."""
    headers_a = await _make_project(client, auth_headers, "FBS Plan Iso A")
    headers_b = await _make_project(client, auth_headers, "FBS Plan Iso B")
    pid_a, pid_b = _pid(headers_a), _pid(headers_b)
    wb_order_id = 920_000_000 + pid_a

    db_session.add(_fbs_order(pid_a, wb_order_id, wb_warehouse_id=700_300_000 + pid_a))
    await db_session.commit()

    try:
        resp = await client.post(
            "/api/v1/fbs/supplies/plan",
            json={"order_ids": [wb_order_id]},
            headers=headers_b,
        )
        # Доменное решение сервиса — 200 с blocked-группой либо 404/422
        # «задание не найдено». Инвариант один: чужое задание нельзя отправить.
        assert resp.status_code in (200, 404, 422), resp.text
        if resp.status_code == 200:
            data = resp.json()
            sendable = [g for g in data["groups"] if not g.get("blocked_reason")]
            assert all(wb_order_id not in (g.get("order_ids") or []) for g in sendable), (
                f"Чужое задание попало в отправляемую группу: {data}"
            )
    finally:
        await db_session.execute(delete(WbFbsOrder).where(WbFbsOrder.project_id.in_([pid_a, pid_b])))
        await db_session.commit()


@pytest.mark.asyncio
async def test_supply_plan_and_bulk_limit_2000(client, auth_headers):
    """Пустой список и >2000 заданий отбиваются на входе — до любой работы."""
    headers = await _make_project(client, auth_headers, "FBS Plan Limits")
    over = list(range(1, 2002))

    for url in ("/api/v1/fbs/supplies/plan", "/api/v1/fbs/supplies/bulk"):
        resp = await client.post(url, json={"order_ids": []}, headers=headers)
        assert resp.status_code == 422, f"{url} на пустом списке: {resp.text}"

        resp = await client.post(url, json={"order_ids": over}, headers=headers)
        assert resp.status_code == 422, f"{url} на 2001 задании: {resp.text}"

        # Ровно 2000 — граница разрешена (дальше уже доменная логика).
        resp = await client.post(url, json={"order_ids": list(range(1, 2001))}, headers=headers)
        assert resp.status_code != 422, f"{url} отбил ровно 2000: {resp.text}"


@pytest.mark.asyncio
async def test_supply_bulk_without_key_creates_nothing(client, auth_headers, db_session):
    """Bulk — запись в WB: без ключа/в режиме `safe` ничего не создаётся."""
    headers = await _make_project(client, auth_headers, "FBS Bulk No Key")
    pid = _pid(headers)
    ids = [930_000_000 + pid * 10 + i for i in range(2)]

    db_session.add_all([_fbs_order(pid, oid, wb_warehouse_id=700_400_000 + pid) for oid in ids])
    await db_session.commit()

    try:
        resp = await client.post(
            "/api/v1/fbs/supplies/bulk",
            json={"order_ids": ids, "name_prefix": "Тест"},
            headers=headers,
        )
        # 409 (гейт ключа/режима) либо 200 с разбором по группам — но пустым.
        assert resp.status_code in (200, 409), resp.text
        if resp.status_code == 200:
            data = resp.json()
            assert data["created"] == [] and data["reused"] == [], data
            assert data["orders_attached"] == 0, data
            assert data["errors"], "Провал без ключа обязан быть виден в errors"

        # Главный инвариант: поставок в проекте не появилось.
        resp = await client.get("/api/v1/fbs/supplies", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == []
    finally:
        await db_session.execute(delete(WbFbsOrder).where(WbFbsOrder.project_id == pid))
        await db_session.execute(delete(WbFbsSupply).where(WbFbsSupply.project_id == pid))
        await db_session.commit()


@pytest.mark.asyncio
async def test_supply_orders_content_and_isolation(client, auth_headers, db_session):
    """Состав поставки читается из зеркала и не виден из чужого проекта."""
    headers_a = await _make_project(client, auth_headers, "FBS Supply Orders A")
    headers_b = await _make_project(client, auth_headers, "FBS Supply Orders B")
    pid_a, pid_b = _pid(headers_a), _pid(headers_b)
    supply_id = f"WB-GI-{pid_a}"
    ids = [940_000_000 + pid_a * 10 + i for i in range(2)]

    db_session.add(
        WbFbsSupply(
            project_id=pid_a,
            wb_supply_id=supply_id,
            name="Тестовая поставка",
            wb_warehouse_id=700_500_000 + pid_a,
            orders_count=2,
            synced_at=utcnow(),
        )
    )
    db_session.add_all(
        [
            _fbs_order(
                pid_a,
                oid,
                wb_warehouse_id=700_500_000 + pid_a,
                supplier_status="confirm",
                supply_id=supply_id,
            )
            for oid in ids
        ]
    )
    # Задание того же проекта ВНЕ поставки в состав попасть не должно.
    db_session.add(_fbs_order(pid_a, ids[1] + 1000, wb_warehouse_id=700_500_000 + pid_a))
    await db_session.commit()

    try:
        resp = await client.get(f"/api/v1/fbs/supplies/{supply_id}/orders", headers=headers_a)
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert sorted(row["wb_order_id"] for row in rows) == sorted(ids), rows
        assert all(row["supply_id"] == supply_id for row in rows)

        # Из проекта B поставки не существует: 404 либо пустой состав.
        resp = await client.get(f"/api/v1/fbs/supplies/{supply_id}/orders", headers=headers_b)
        assert resp.status_code in (200, 404), resp.text
        if resp.status_code == 200:
            assert resp.json() == []
    finally:
        await db_session.execute(delete(WbFbsOrder).where(WbFbsOrder.project_id.in_([pid_a, pid_b])))
        await db_session.execute(delete(WbFbsSupply).where(WbFbsSupply.project_id.in_([pid_a, pid_b])))
        await db_session.commit()


# ─── Тип ключа wb_marketplace ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_wb_marketplace_key(client, auth_headers, monkeypatch):
    """Ключ «Маркетплейс» валидируется пробой GET /ping и сохраняется."""

    async def _ok(_api_key: str, base_url: str | None = None) -> str:
        return "ok"

    monkeypatch.setattr("backend.services.integrations_service.check_marketplace_scope", _ok)

    headers = await _make_project(client, auth_headers, "FBS Key OK")
    resp = await client.post(
        "/api/v1/integrations/keys",
        json={"service": "wb_marketplace", "api_key": "marketplace_token_1234567890", "label": "Маркетплейс"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["service"] == "wb_marketplace"


@pytest.mark.asyncio
async def test_add_wb_marketplace_key_no_scope_400(client, auth_headers, monkeypatch):
    """Токен без категории «Маркетплейс» (401/403 → no_scope) отбивается с 400."""

    async def _no_scope(_api_key: str, base_url: str | None = None) -> str:
        return "no_scope"

    monkeypatch.setattr("backend.services.integrations_service.check_marketplace_scope", _no_scope)

    headers = await _make_project(client, auth_headers, "FBS Key No Scope")
    resp = await client.post(
        "/api/v1/integrations/keys",
        json={"service": "wb_marketplace", "api_key": "no_scope_token_1234567890"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "Маркетплейс" in resp.text


@pytest.mark.asyncio
async def test_add_wb_marketplace_key_transient_saves(client, auth_headers, monkeypatch):
    """Транзиент пробы ("unknown") НЕ считается невалидным ключом — ключ сохраняется."""

    async def _unknown(_api_key: str, base_url: str | None = None) -> str:
        return "unknown"

    monkeypatch.setattr("backend.services.integrations_service.check_marketplace_scope", _unknown)

    headers = await _make_project(client, auth_headers, "FBS Key Transient")
    resp = await client.post(
        "/api/v1/integrations/keys",
        json={"service": "wb_marketplace", "api_key": "rate_limited_token_1234567890"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_wb_marketplace_key_recreate_after_delete(client, auth_headers, monkeypatch):
    """Пересоздание ключа после удаления не падает на uq (строка restore-ится)."""

    async def _ok(_api_key: str, base_url: str | None = None) -> str:
        return "ok"

    monkeypatch.setattr("backend.services.integrations_service.check_marketplace_scope", _ok)

    headers = await _make_project(client, auth_headers, "FBS Key Recreate")
    body = {"service": "wb_marketplace", "api_key": "recreate_token_1234567890", "label": "Маркетплейс"}

    resp = await client.post("/api/v1/integrations/keys", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    key_id = resp.json()["id"]

    resp = await client.delete(f"/api/v1/integrations/keys/{key_id}", headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.post("/api/v1/integrations/keys", json=body, headers=headers)
    assert resp.status_code == 200, f"Пересоздание ключа упало: {resp.text}"

    resp = await client.get("/api/v1/integrations/keys", headers=headers)
    active = [k for k in resp.json() if k["service"] == "wb_marketplace" and k["is_active"]]
    assert len(active) == 1, f"Ожидался ровно один активный ключ Маркетплейса: {active}"
