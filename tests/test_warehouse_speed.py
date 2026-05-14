"""Тесты API `/api/v1/warehouse/speed/*` — справочный модуль скоростей WB.

Эндпоинты требуют только `Depends(get_current_user)` — БД не дёргается,
данные читаются из `backend/data/wb_warehouse_speed.json`.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_meta_endpoint(client, auth_headers):
    """/meta возвращает version, source, cities_count==97 и непустой okrug_keys."""
    resp = await client.get("/api/v1/warehouse/speed/meta", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["version"] == "2026-05-14"
    assert "POSTAVLENOru_BOT" in data["source"]
    assert data["cities_count"] == 97
    assert isinstance(data["okrug_keys"], list)
    assert "volga" in data["okrug_keys"]
    assert "central" in data["okrug_keys"]


@pytest.mark.asyncio
async def test_okrug_info_basic(client, auth_headers):
    """/okrug-info: volga.anchors[0]==Казань, volga.stealers[0]==Владимир."""
    resp = await client.get("/api/v1/warehouse/speed/okrug-info", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    by_key = {row["okrug_key"]: row for row in data}
    # Не должно быть abroad/unknown
    assert "abroad" not in by_key
    assert "unknown" not in by_key
    # Должны быть основные ФО
    assert "volga" in by_key
    assert "central" in by_key

    volga = by_key["volga"]
    assert volga["okrug_label"] == "Приволжский"
    assert len(volga["anchors_top"]) > 0
    assert volga["anchors_top"][0]["warehouse_name"] == "Казань"
    assert volga["anchors_top"][0]["cities_count"] > 0

    assert len(volga["stealers_top"]) > 0
    assert volga["stealers_top"][0]["warehouse_name"] == "Владимир"

    # Не более 5 элементов
    assert len(volga["anchors_top"]) <= 5
    assert len(volga["stealers_top"]) <= 5


@pytest.mark.asyncio
async def test_evaluate_empty_basket(client, auth_headers):
    """Пустая корзина: ceiling=0, нет warnings, все города uncovered."""
    # Передаём один невалидный токен — даст пустую канон-корзину.
    resp = await client.get(
        "/api/v1/warehouse/speed/evaluate",
        params={"warehouses": ""},
        headers=auth_headers,
    )
    # FastAPI 422 для пустого значения с min_length=0 — не сработает (нет min_length).
    # `warehouses=""` парсится как пустой str → loaded=[].
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["loaded_warehouses"] == []
    assert data["realistic_ceiling_pct"] == 0.0
    assert data["cities_local"] == 0
    assert data["cities_stealer"] == 0
    assert data["cities_uncovered"] == data["cities_total"]
    assert data["cities_total"] == 97
    # Пустая корзина — нет stealer-ов → нет warnings про stealer_without_anchor.
    assert data["warnings"] == []
    # per_okrug заполнен и пустыми списками anchors/stealers.
    assert len(data["per_okrug"]) > 0
    assert all(p["anchors_in_basket"] == [] for p in data["per_okrug"])
    assert all(p["stealers_in_basket"] == [] for p in data["per_okrug"])
    # cities=null по умолчанию.
    assert data["cities"] is None


@pytest.mark.asyncio
async def test_evaluate_full_anchors(client, auth_headers):
    """Покрытый набор anchor-складов: ceiling>50%, нет stealer-warnings для volga/central."""
    wh = ",".join(
        [
            "Казань",
            "Краснодар",
            "Электросталь",
            "СПБ Шушары",
            "Екатеринбург - Перспективная 14",
            "Новосибирск",
            "Невинномысск",
        ]
    )
    resp = await client.get(
        "/api/v1/warehouse/speed/evaluate",
        params={"warehouses": wh},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["loaded_warehouses"]  # канонизированный список не пуст
    assert data["realistic_ceiling_pct"] > 50.0
    # Для волги/центра в этой корзине anchors_in_basket не пуст → нет warning «stealer_without_anchor»
    warning_okrugs = {w["okrug"] for w in data["warnings"]}
    assert "volga" not in warning_okrugs
    assert "central" not in warning_okrugs
    # cities_local > cities_uncovered
    assert data["cities_local"] > data["cities_uncovered"]


@pytest.mark.asyncio
async def test_evaluate_stealers_without_anchor(client, auth_headers):
    """Stealer без anchor — должен быть warning с suggested_anchors не пустым."""
    # Воронеж — центр (stealer для волги), Котовск — центр (stealer для волги),
    # Электросталь — центр (anchor для центра). Для волги anchor-ов нет → warning.
    wh = "Воронеж,Котовск,Электросталь"
    resp = await client.get(
        "/api/v1/warehouse/speed/evaluate",
        params={"warehouses": wh},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    warnings = data["warnings"]
    okrugs_with_warning = {w["okrug"]: w for w in warnings}
    assert "volga" in okrugs_with_warning, f"ожидался warning для volga, есть: {okrugs_with_warning.keys()}"
    volga_w = okrugs_with_warning["volga"]
    assert volga_w["kind"] == "stealer_without_anchor"
    assert volga_w["severity"] == "warning"
    assert volga_w["stealer"] is not None
    assert len(volga_w["suggested_anchors"]) > 0
    assert volga_w["okrug_label"] == "Приволжский"


@pytest.mark.asyncio
async def test_evaluate_include_cities(client, auth_headers):
    """include_cities=true → cities[] непустой; false (default) → cities=null."""
    resp = await client.get(
        "/api/v1/warehouse/speed/evaluate",
        params={"warehouses": "Казань", "include_cities": "true"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cities"] is not None
    assert len(data["cities"]) == 97


@pytest.mark.asyncio
async def test_cities_endpoint(client, auth_headers):
    """/cities: 97 городов, у Нижнего Новгорода первый приоритет — Владимир."""
    resp = await client.get("/api/v1/warehouse/speed/cities", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 97

    nn = next((c for c in data if c["city"] == "Нижний Новгород"), None)
    assert nn is not None, "Нижний Новгород должен быть в списке"
    assert nn["okrug_key"] == "volga"
    assert nn["okrug_label"] == "Приволжский"
    assert len(nn["priorities"]) > 0
    assert nn["priorities"][0]["warehouse_name"] == "Владимир"
    assert nn["priorities"][0]["hours"] > 0


@pytest.mark.asyncio
async def test_meta_requires_auth(client):
    """Без auth_headers — 401/403."""
    resp = await client.get("/api/v1/warehouse/speed/meta")
    assert resp.status_code in (401, 403)


# ─── find_priority_warehouse ────────────────────────────────────────────────


def test_find_priority_warehouse_by_city_top1_open():
    """Если top-1 priority открыт — возвращаем его."""
    from backend.services.warehouse_speed import find_priority_warehouse

    # Казань: top-1 = Казань, top-2 = Самара (Новосемейкино)
    open_set = {"Казань", "Самара (Новосемейкино)", "Пенза"}
    assert find_priority_warehouse("Казань", open_set) == "Казань"


def test_find_priority_warehouse_by_city_top1_excluded_fallback_top2():
    """Если top-1 закрыт — возвращаем top-2 ТОГО ЖЕ ГОРОДА (не haversine)."""
    from backend.services.warehouse_speed import find_priority_warehouse

    # Город Казань: priority chain [Казань, Новосемейкино, Владимир, Сарапул, ...]
    # Закроем Казань → ожидаем Самара (Новосемейкино) — priority-2.
    open_set = {"Самара (Новосемейкино)", "Сарапул", "Пенза", "Владимир"}
    assert find_priority_warehouse("Казань", open_set) == "Самара (Новосемейкино)"


def test_find_priority_warehouse_okrug_fallback_when_city_missing():
    """Если city не в speed-карте, но okrug известен — берём anchor ФО по score."""
    from backend.services.warehouse_speed import find_priority_warehouse

    # Несуществующий город ПФО: Казань — top anchor ПФО, score максимальный.
    open_set = {"Казань", "Самара (Новосемейкино)", "Сарапул"}
    assert find_priority_warehouse("несуществующий-город", open_set, okrug="volga") == "Казань"


def test_find_priority_warehouse_okrug_skips_excluded_anchor():
    """okrug-fallback: если top anchor ФО закрыт — берём следующий по score."""
    from backend.services.warehouse_speed import find_priority_warehouse

    # ПФО: Казань (top) закрыта → ожидаем Сарапул/Самара (следующие anchor ПФО).
    open_set = {"Самара (Новосемейкино)", "Сарапул", "Пенза"}
    res = find_priority_warehouse(None, open_set, okrug="volga")
    assert res in {"Самара (Новосемейкино)", "Сарапул", "Пенза"}
    # Самара выше в priority-листах ПФО городов, чем Пенза/Сарапул.
    assert res == "Самара (Новосемейкино)"


def test_find_priority_warehouse_returns_none_when_no_open():
    """Если ни city, ни okrug не дают match — None (caller fallback на haversine)."""
    from backend.services.warehouse_speed import find_priority_warehouse

    assert find_priority_warehouse(None, set(), okrug=None) is None
    assert find_priority_warehouse(None, {"non-existent"}, okrug="volga") is None


def test_find_priority_warehouse_unknown_okrug_returns_none():
    """okrug='unknown' или 'abroad' → не должно возвращать рандомный склад."""
    from backend.services.warehouse_speed import find_priority_warehouse

    open_set = {"Казань", "Краснодар"}
    # 'unknown' не в speed cities_by_okrug → пустой scored → None
    assert find_priority_warehouse(None, open_set, okrug="unknown") is None
