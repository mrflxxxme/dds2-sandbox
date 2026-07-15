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

    # Depth-aware (top-2) подсчёт: Котовск ворует 2 города ПФО в top-2,
    # Владимир — 1 (НН#1); глубокие слоты больше не считаются.
    assert len(volga["stealers_top"]) > 0
    assert volga["stealers_top"][0]["warehouse_name"] == "Котовск"
    stealer_names = {s["warehouse_name"] for s in volga["stealers_top"]}
    assert "Владимир" in stealer_names

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


def test_find_priority_warehouse_stock_canon_open_matches_chain_canon():
    """Вход в stock-каноне («Тула» из WAREHOUSE_COORDS) обязан матчить
    цепочку, канонизированную в speed-канон («Алексин (Тула)»).

    Рассинхрон канон-пространств (аудит 2026-07-14): open_warehouses в
    warehouse_need_service = ключи WAREHOUSE_COORDS («Тула»), а chain-walk
    сравнивал их с speed-каноном цепочек → «Тула» (top-1 у 11 городов ЦФО)
    была недостижима через priority-chain, спрос уезжал в Коледино.
    """
    from backend.services.warehouse_speed import find_priority_warehouse

    # Город Тула: chain = [Алексин (Тула), Подольск, Коледино, ...].
    # Открыта «Тула» (stock-канон) → top-1 матчится, возвращаем ОРИГИНАЛЬНОЕ
    # имя caller'а («Тула»), не speed-канон — downstream живёт в stock-каноне.
    assert find_priority_warehouse("Тула", {"Тула", "Коледино"}) == "Тула"
    # Склад Тула закрыт → следующий open слот цепочки (Коледино, slot 3).
    assert find_priority_warehouse("Тула", {"Коледино", "Казань"}) == "Коледино"
    # Speed-канон на входе тоже матчится (обратная сторона той же монеты).
    assert find_priority_warehouse("Тула", {"Алексин (Тула)"}) == "Алексин (Тула)"


# ─── Depth-aware воришки + локализуемый знаменатель (аудит 2026-07-09) ──────


class TestStealerDepthAware:
    """Воришка = top-2 слот в цепочке города чужого ФО; глубже — fallback WB."""

    def test_shushary_not_stealer_anywhere(self):
        """Шушары в чужих цепочках только глубоко (Чита#4, Алматы#6) → не воришка."""
        from backend.services.warehouse_speed import is_stealer_for_okrug

        for okrug in ("central", "volga", "ural", "south_caucasus", "far_east_siberia"):
            assert not is_stealer_for_okrug("СПБ Шушары", okrug), okrug

    def test_elektrostal_still_stealer_for_northwest(self):
        """Электросталь — top-1 у Пскова/Вологды/Мурманска/Архангельска СЗФО."""
        from backend.services.warehouse_speed import is_stealer_for_okrug

        assert is_stealer_for_okrug("Электросталь", "northwest")

    def test_koledino_depth2_not_northwest_stealer(self):
        """Коледино в цепочках СЗФО только с глубины 3 → для СЗФО не воришка,
        но top-2 far_east (Брест#2) — там флаг остаётся."""
        from backend.services.warehouse_speed import is_stealer_for_okrug

        assert not is_stealer_for_okrug("Коледино", "northwest")
        assert is_stealer_for_okrug("Коледино", "far_east_siberia")

    def test_own_okrug_never_stealer(self):
        from backend.services.warehouse_speed import is_stealer_for_okrug

        assert not is_stealer_for_okrug("СПБ Шушары", "northwest")
        assert not is_stealer_for_okrug("Электросталь", "central")

    def test_stealers_list_depth_aware(self):
        """get_stealers_for_okrug той же семантики: Коледино/Рязань выпали из
        списка воришек СЗФО, Электросталь — 7 городов top-2."""
        from backend.services.warehouse_speed import get_stealers_for_okrug

        nw = dict(get_stealers_for_okrug("northwest"))
        assert nw.get("Электросталь") == 7
        assert "Коледино" not in nw
        assert "Рязань (Тюшевское)" not in nw


class TestAliasesMatchSpeedNames:
    """Дыры нормализации: регистр «Белая Дача», короткие «Тула»/«Рязань»."""

    def test_belaya_dacha_case_mismatch_fixed(self):
        """JSON пишет «Белая Дача», карта ФО — «Белая дача»: скор и thief-флаг
        (top-1 Петропавловска-Камчатского) теперь работают."""
        from backend.services.warehouse_speed import get_priority_score, is_stealer_for_okrug

        assert get_priority_score("Белая дача", "central") > 0
        assert is_stealer_for_okrug("Белая дача", "far_east_siberia")

    def test_tula_aliases_to_aleksin(self):
        from backend.services.warehouse_speed import get_priority_score, is_stealer_for_okrug

        assert get_priority_score("Тула", "central") > 0
        assert is_stealer_for_okrug("Тула", "far_east_siberia")

    def test_ryazan_aliases_to_tyushevskoe(self):
        """«Рязань» = «Рязань (Тюшевское)»: top-1 города Рязань (скор > 0),
        по top-2 чужих ФО не ворует."""
        from backend.services.warehouse_speed import get_priority_score, is_stealer_for_okrug

        assert get_priority_score("Рязань", "central") > 0
        for okrug in ("northwest", "volga", "south_caucasus", "far_east_siberia"):
            assert not is_stealer_for_okrug("Рязань", okrug), okrug


class TestLocalizableDenominator:
    """Знаменатель score — города ФО, достижимые складами своего ФО."""

    def test_northwest_denominator_is_4(self):
        """СЗФО: из 10 городов только 4 локализуемы (СПб, В.Новгород,
        Петрозаводск, Калининград) — Псков/Мурманск/… скор не размывают."""
        from backend.services.warehouse_speed import get_priority_score

        # Шушары: top-1 у 3 городов → 3×1.0 / 4 = 0.75 (было 0.300 при /10).
        assert get_priority_score("СПБ Шушары", "northwest") == pytest.approx(0.75)
        # Калининград: top-1 своего города → 1/4 = 0.25.
        assert get_priority_score("Калининград", "northwest") == pytest.approx(0.25)

    def test_fully_localizable_okrug_unchanged(self):
        """УФО: все 7 городов локализуемы — скор ЕКБ остаётся 1.0."""
        from backend.services.warehouse_speed import get_priority_score

        assert get_priority_score("Екатеринбург - Перспективная 14", "ural") == pytest.approx(1.0)

    def test_okrug_fallback_prefers_own_district(self):
        """Okrug-fallback: немапленный город СЗФО идёт в склад СВОЕГО ФО
        (Шушары), а не в чужой хаб с большим агрегатным скором
        (Электросталь ≈1.47 > Шушары 0.75 — дефект аудита 2026-07-09/14).
        Чужой хаб — только когда в своём ФО нет складов с ненулевым скором."""
        from backend.services.warehouse_speed import find_priority_warehouse

        open_set = {"Электросталь", "СПБ Шушары", "Казань"}
        assert find_priority_warehouse("несуществующий-город", open_set, okrug="northwest") == "СПБ Шушары"
        # Свой ФО закрыт целиком → фолбэк на чужой хаб по скору.
        assert find_priority_warehouse("несуществующий-город", {"Электросталь", "Казань"}, okrug="northwest") == "Электросталь"
