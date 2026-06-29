"""Tests for the localization-target redesign of `get_warehouse_need` step 4.6.

Covers:
  * HIGH-1 regression — greedy step 4.6 never silently loses units the way the
    old proportional-Hamilton block did (anchor with small need + high weight,
    leftover that the old code could not place).
  * HIGH-2 — unmapped (abroad / CIS) demand is no longer dropped: it inflates
    the global `total_need` (procurement-KPI is mode-invariant) and surfaces as
    `summary.unmapped_demand_qty`, while NOT entering the per-cell WB matrix.
  * 75% target — allocation concentrates on the anchor warehouse first and the
    leftover `can_send` is not smeared onto far warehouses past the target.
  * Conservation — Σ per-cell need ≤ can_send_per_nm.

The function is DB-driven and calls the WB API, so we monkeypatch the API layer
(`get_wb_key` / `fetch_supplier_orders`) and the warehouse-filter helpers, then
seed the minimal rows (Warehouse FF + RF stock + WB stock + Nomenclature).

`@cached` is bypassed via `.__wrapped__` so each call recomputes against the DB.
"""
import uuid

import pytest
from sqlalchemy import text

from backend.services import warehouse_need_service as wns

# Two CENTRAL-district warehouses with deterministic priority weights (verified
# against warehouse_speed): Тула is an anchor (weight 1.0, not a stealer),
# Коледино is a "воришка" (weight ~0.753, penalty 0.6). Anchor must fill first.
ANCHOR_WH = "Тула"
THIEF_WH = "Коледино"

pytestmark = pytest.mark.asyncio


async def _seed_nomenclature(db_session, project_id, nm_id, barcode):
    """Insert one Nomenclature row, return its id."""
    res = await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, article_seller, article_wb, "
            "use_box_multiplicity, updated_at) "
            "VALUES (:p, :bc, :art, :nm, false, NOW()) RETURNING id"
        ),
        {"p": project_id, "bc": barcode, "art": f"ART-{nm_id}", "nm": nm_id},
    )
    return res.scalar()


async def _seed_ff_warehouse(db_session, project_id, name):
    """Insert one FULFILLMENT warehouse, return its id."""
    res = await db_session.execute(
        text(
            "INSERT INTO warehouses (project_id, name, warehouse_type, assembly_days, "
            "wb_acceptance_days, sort_order, is_active, is_deleted, created_at, updated_at) "
            "VALUES (:p, :n, 'FULFILLMENT', 1, 1, 0, true, false, NOW(), NOW()) RETURNING id"
        ),
        {"p": project_id, "n": name},
    )
    return res.scalar()


async def _seed_rf_stock(db_session, project_id, wh_id, nom_id, barcode, qty):
    await db_session.execute(
        text(
            "INSERT INTO warehouse_stock (project_id, warehouse_id, nomenclature_id, "
            "barcode, quantity, in_transit, defect_quantity, defect_in_transit, updated_at) "
            "VALUES (:p, :w, :n, :bc, :q, 0, 0, 0, NOW())"
        ),
        {"p": project_id, "w": wh_id, "n": nom_id, "bc": barcode, "q": qty},
    )


async def _seed_wb_stock(db_session, project_id, nm_id, wh_name, qty):
    """Seed a WbWarehouseStock row.

    NB: in `mode="actual"` the matrix columns (`wh_names_to_show`) come ONLY from
    WbWarehouseStock — order warehouses alone do not create a column. So seeding a
    qty=0 WB-stock row is how we register a WB warehouse as a matrix column.
    """
    await db_session.execute(
        text(
            "INSERT INTO wb_warehouse_stocks (project_id, nm_id, warehouse_name, quantity, "
            "quantity_full, in_way_to_client, in_way_from_client, updated_at) "
            "VALUES (:p, :nm, :wh, :q, :q, 0, 0, NOW())"
        ),
        {"p": project_id, "nm": nm_id, "wh": wh_name, "q": qty},
    )


def _patch_api(monkeypatch, orders):
    """Make the WB API layer return our synthetic orders, no network."""

    async def _fake_get_wb_key(db, project_id, service):
        return "fake-key"

    async def _fake_fetch(api_key, date_from):
        return orders

    monkeypatch.setattr(wns_wb := __import__(
        "backend.services.funnel.wb_api_client", fromlist=["x"]
    ), "get_wb_key", _fake_get_wb_key)
    monkeypatch.setattr(wns_wb, "fetch_supplier_orders", _fake_fetch)

    # Keep all warehouses open: no excluded / no acceptance-closed.
    async def _no_excluded(db, project_id):
        return []

    async def _no_blocked(db, project_id):
        return set()

    monkeypatch.setattr(
        __import__("backend.services.settings_service", fromlist=["x"]),
        "get_excluded_warehouses",
        _no_excluded,
    )
    monkeypatch.setattr(
        __import__("backend.services.warehouse_acceptance_service", fromlist=["x"]),
        "get_acceptance_blocked_warehouses",
        _no_blocked,
    )


async def _call(db_session, project_id, **kw):
    """Invoke get_warehouse_need bypassing the @cached wrapper."""
    return await wns.get_warehouse_need.__wrapped__(db_session, project_id, **kw)


# ── Conservation + anchor-first (only_available, non-localization) ────────────
async def test_step46_conserves_and_prefers_anchor(db_session, project, monkeypatch):
    """only_available greedy: Σ per-cell need ≤ can_send and anchor fills first.

    Both warehouses are central; cap is the binder (RF stock plentiful, WB stock
    zero). The anchor (Тула) must receive at least as much as the воришка
    (Коледино), and total distributed must never exceed can_send.
    """
    pid = project.id
    nm_id = 700100 + (uuid.uuid4().int % 1000)
    bc = f"BC{nm_id}"
    nom_id = await _seed_nomenclature(db_session, pid, nm_id, bc)
    ff_id = await _seed_ff_warehouse(db_session, pid, "FF-Central")
    # Lots of RF stock so can_send is limited by demand, not stock.
    await _seed_rf_stock(db_session, pid, ff_id, nom_id, bc, 10_000)
    # Register both WB warehouses as matrix columns (qty=0 → no surplus).
    await _seed_wb_stock(db_session, pid, nm_id, ANCHOR_WH, 0)
    await _seed_wb_stock(db_session, pid, nm_id, THIEF_WH, 0)
    await db_session.commit()

    # Heavy demand on both central warehouses, no WB stock anywhere.
    orders = [{"nmId": nm_id, "quantity": 50, "warehouseName": ANCHOR_WH, "supplierArticle": "A"}] * 4
    orders += [{"nmId": nm_id, "quantity": 50, "warehouseName": THIEF_WH, "supplierArticle": "A"}] * 4
    _patch_api(monkeypatch, orders)

    res = await _call(
        db_session, pid, supply_days=14, analysis_days=14, only_available=True,
        localization_target=100,  # full target so cap (not 75%) is the binder
    )

    art = next(a for a in res["articles"] if a["nm_id"] == nm_id)
    can_send = art["can_send"]
    assert can_send > 0

    by_wh = {w["name"]: w for w in res["warehouses"]}
    anchor_need = by_wh.get(ANCHOR_WH, {}).get("articles", {}).get(nm_id, {}).get("need", 0)
    thief_need = by_wh.get(THIEF_WH, {}).get("articles", {}).get(nm_id, {}).get("need", 0)

    # Conservation: distributed ≤ can_send.
    assert anchor_need + thief_need <= can_send
    # Anchor (higher weight) is filled at least as much as the воришка.
    assert anchor_need >= thief_need
    # And the anchor actually got something.
    assert anchor_need > 0


# ── HIGH-1 regression: no silent unit loss ────────────────────────────────────
async def test_high1_no_silent_unit_loss(db_session, project, monkeypatch):
    """Anchor with a tiny per-cell need + high weight; cap larger than that need.

    Old proportional-Hamilton: a warehouse capped at its small `need` could not
    absorb the leftover, and if the остальные warehouses were also capped the
    leftover silently vanished (Σ < min(cap, Σ need)). The greedy fills by округ
    headroom, so Σ distributed == min(cap, Σ need) up to the localization target.
    Here target=100 so the округ is allowed to be fully covered.
    """
    pid = project.id
    nm_id = 701100 + (uuid.uuid4().int % 1000)
    bc = f"BC{nm_id}"
    nom_id = await _seed_nomenclature(db_session, pid, nm_id, bc)
    ff_id = await _seed_ff_warehouse(db_session, pid, "FF-Central2")
    await _seed_rf_stock(db_session, pid, ff_id, nom_id, bc, 10_000)
    await _seed_wb_stock(db_session, pid, nm_id, ANCHOR_WH, 0)
    await _seed_wb_stock(db_session, pid, nm_id, THIEF_WH, 0)
    await db_session.commit()

    # Anchor has small demand, воришка has large demand → asymmetric per-cell need.
    orders = [{"nmId": nm_id, "quantity": 7, "warehouseName": ANCHOR_WH, "supplierArticle": "A"}]
    orders += [{"nmId": nm_id, "quantity": 200, "warehouseName": THIEF_WH, "supplierArticle": "A"}]
    _patch_api(monkeypatch, orders)

    res = await _call(
        db_session, pid, supply_days=14, analysis_days=14, only_available=True,
        localization_target=100,
    )

    art = next(a for a in res["articles"] if a["nm_id"] == nm_id)
    can_send = art["can_send"]

    by_wh = {w["name"]: w for w in res["warehouses"]}
    distributed = 0
    raw_need_sum = 0
    for wh_name in (ANCHOR_WH, THIEF_WH):
        cell = by_wh.get(wh_name, {}).get("articles", {}).get(nm_id)
        if cell:
            distributed += cell["need"]
    # Σ raw per-cell need (pre-cap) — reconstruct from orders since WB stock is 0.
    # avg_daily*supply_days rounded per cell.
    # Anchor: 7/14*14 = 7 ; Thief: 200/14*14 = 200 → 207.
    raw_need_sum = 207

    # No silent loss: everything that can be shipped within the округ headroom is.
    expected = min(can_send, raw_need_sum)
    assert distributed == expected, f"distributed={distributed} expected={expected} can_send={can_send}"


# ── 75% target: leftover not smeared past target ──────────────────────────────
async def test_target_75_caps_localization(db_session, project, monkeypatch):
    """With a high cap and target=75, greedy stops once округ is ~75% localized.

    Demand is large; cap (RF stock) is even larger. Distribution must NOT cover
    100% of округ demand — it stops near the 75% threshold (округ headroom shrinks
    as covered approaches target). So Σ distributed < округ gross demand.
    """
    pid = project.id
    nm_id = 702100 + (uuid.uuid4().int % 1000)
    bc = f"BC{nm_id}"
    nom_id = await _seed_nomenclature(db_session, pid, nm_id, bc)
    ff_id = await _seed_ff_warehouse(db_session, pid, "FF-Central3")
    await _seed_rf_stock(db_session, pid, ff_id, nom_id, bc, 100_000)
    await _seed_wb_stock(db_session, pid, nm_id, ANCHOR_WH, 0)
    await db_session.commit()

    # 140 orders / 14 days = 10/day → gross demand = 10 * 14 = 140 on the anchor.
    orders = [{"nmId": nm_id, "quantity": 10, "warehouseName": ANCHOR_WH, "supplierArticle": "A"}] * 14
    _patch_api(monkeypatch, orders)

    res = await _call(
        db_session, pid, supply_days=14, analysis_days=14, only_available=True,
        localization_target=75,
    )

    by_wh = {w["name"]: w for w in res["warehouses"]}
    anchor_need = by_wh.get(ANCHOR_WH, {}).get("articles", {}).get(nm_id, {}).get("need", 0)

    gross_demand = 140  # 10/day * 14 days, single округ
    # 75% target → about 105 covered, not the full 140. Allow rounding slack.
    assert anchor_need > 0
    assert anchor_need < gross_demand, f"anchor_need={anchor_need} should be < {gross_demand} (75% cap)"
    assert anchor_need <= round(0.75 * gross_demand) + 2


# ── HIGH-2: unmapped (CIS / abroad) demand counted in total, not in cells ──────
async def test_high2_unmapped_demand_counted_globally_not_in_cells(db_session, project, monkeypatch):
    """An order that cannot be localized (unknown region, no city) is unmapped.

    It must:
      * NOT be dropped silently;
      * inflate the SKU's global total_need (mode-invariant procurement);
      * surface as summary.unmapped_demand_qty > 0;
      * NOT appear in any per-cell WB need.
    """
    pid = project.id
    nm_id = 703100 + (uuid.uuid4().int % 1000)
    bc = f"BC{nm_id}"
    nom_id = await _seed_nomenclature(db_session, pid, nm_id, bc)
    ff_id = await _seed_ff_warehouse(db_session, pid, "FF-Central4")
    await _seed_rf_stock(db_session, pid, ff_id, nom_id, bc, 10_000)
    await db_session.commit()

    UNMAPPED_QTY = 28
    # One unmappable order (unknown region, no srid/city) + verify total_need.
    orders = [
        {"nmId": nm_id, "quantity": UNMAPPED_QTY, "warehouseName": "ignored-in-loc-mode",
         "regionName": "Зимбабве Хараре", "srid": "no-such-srid", "supplierArticle": "A"},
    ]
    _patch_api(monkeypatch, orders)

    res = await _call(
        db_session, pid, supply_days=14, analysis_days=14, localization_optimized=True,
    )

    # Surfaced in summary.
    assert res["summary"]["unmapped_demand_qty"] == UNMAPPED_QTY

    # Global total_need includes the unmapped demand:
    # avg_daily = 28/14 = 2/day; gross = 2 * 14 = 28; no WB stock → total_need 28.
    art = next((a for a in res["articles"] if a["nm_id"] == nm_id), None)
    assert art is not None, "unmapped-only SKU must still appear in articles"
    assert art["total_need"] == UNMAPPED_QTY

    # NOT in any per-cell WB matrix (cannot be localized).
    for w in res["warehouses"]:
        cell = w.get("articles", {}).get(nm_id)
        if cell:
            assert cell["need"] == 0, f"unmapped demand leaked into cell {w['name']}"


async def test_high2_total_need_mode_invariant(db_session, project, monkeypatch):
    """total_need for a SKU with mixed (localizable + unmapped) demand is the
    same whether or not localization is on (procurement-KPI is mode-invariant).
    """
    pid = project.id
    nm_id = 704100 + (uuid.uuid4().int % 1000)
    bc = f"BC{nm_id}"
    nom_id = await _seed_nomenclature(db_session, pid, nm_id, bc)
    ff_id = await _seed_ff_warehouse(db_session, pid, "FF-Central5")
    await _seed_rf_stock(db_session, pid, ff_id, nom_id, bc, 10_000)
    await db_session.commit()

    # Non-loc (actual) mode: order routes to its literal warehouseName.
    orders = [
        {"nmId": nm_id, "quantity": 140, "warehouseName": ANCHOR_WH, "supplierArticle": "A"},
    ]
    _patch_api(monkeypatch, orders)
    res_actual = await _call(db_session, pid, supply_days=14, analysis_days=14, mode="actual")
    art_actual = next(a for a in res_actual["articles"] if a["nm_id"] == nm_id)
    total_actual = art_actual["total_need"]

    # Loc mode: same order is now unmappable (unknown region) → goes to unmapped,
    # but total_need must match the actual-mode value (same gross demand).
    orders_loc = [
        {"nmId": nm_id, "quantity": 140, "warehouseName": ANCHOR_WH,
         "regionName": "Зимбабве Хараре", "srid": "no-such-srid", "supplierArticle": "A"},
    ]
    _patch_api(monkeypatch, orders_loc)
    res_loc = await _call(db_session, pid, supply_days=14, analysis_days=14, localization_optimized=True)
    art_loc = next(a for a in res_loc["articles"] if a["nm_id"] == nm_id)
    total_loc = art_loc["total_need"]

    assert total_actual == total_loc, f"total_need mode-variant: actual={total_actual} loc={total_loc}"
    assert res_loc["summary"]["unmapped_demand_qty"] == 140


# ── MEDIUM regression: covered warehouse counts toward okrug localization ──────
async def test_covered_okrug_warehouse_not_overstocked(db_session, project, monkeypatch):
    """Склад, чей ФО уже покрыт WB-стоком СОСЕДНЕГО склада, не доливается сверх 75%.

    Регресс на MEDIUM из /review: demand_by_okrug/existing_by_okrug строились только
    по складам с need>0 → покрытый склад (need==0) выпадал, ФО выглядел недо-покрытым,
    greedy переливал. Фикс учитывает ВСЕ склады ФО. Проверяем достигнутую локализацию:
    с фиксом ≈75% (цель), без фикса был бы overshoot ~90% (перетаривание).

    Сетап: ЦФО — Тула WB-сток 100 покрывает свой спрос 50 (need 0, профицит);
    Коледино спрос 30, сток 0. ПФО — Казань спрос 200, сток 0 (тянет can_send>0).
    Σспрос 280, Σсток 100 → can_send 180. Цель 75% от 280 = 210 локального покрытия;
    Тула уже даёт 80 → долить надо ~130, а не 173 (как без учёта Тулы).
    """
    from backend.services.warehouse_district import warehouse_to_district as wd

    pid = project.id
    nm_id = 702100 + (uuid.uuid4().int % 1000)
    bc = f"BC{nm_id}"
    nom_id = await _seed_nomenclature(db_session, pid, nm_id, bc)
    ff_id = await _seed_ff_warehouse(db_session, pid, "FF-Cov")
    await _seed_rf_stock(db_session, pid, ff_id, nom_id, bc, 10_000)
    KAZAN = "Казань"  # volga anchor
    await _seed_wb_stock(db_session, pid, nm_id, ANCHOR_WH, 100)   # Тула покрыта
    await _seed_wb_stock(db_session, pid, nm_id, THIEF_WH, 0)      # Коледино нужна
    await _seed_wb_stock(db_session, pid, nm_id, KAZAN, 0)         # Казань нужна
    await db_session.commit()
    # sanity: разные ФО
    assert wd(ANCHOR_WH) == wd(THIEF_WH) and wd(KAZAN) != wd(ANCHOR_WH)

    orders = [
        {"nmId": nm_id, "quantity": 50, "warehouseName": ANCHOR_WH, "supplierArticle": "A"},
        {"nmId": nm_id, "quantity": 30, "warehouseName": THIEF_WH, "supplierArticle": "A"},
        {"nmId": nm_id, "quantity": 200, "warehouseName": KAZAN, "supplierArticle": "A"},
    ]
    _patch_api(monkeypatch, orders)

    res = await _call(
        db_session, pid, supply_days=14, analysis_days=14, only_available=True,
        localization_target=75,
    )
    art = next(a for a in res["articles"] if a["nm_id"] == nm_id)
    can_send = art["can_send"]

    # Достигнутая локализация = Σ_ФО min(спрос_ФО, сток_ФО + выделено_ФО) / Σспрос.
    gross = {ANCHOR_WH: 50.0, THIEF_WH: 30.0, KAZAN: 200.0}
    total_demand = sum(gross.values())
    dem_by_d: dict[str, float] = {}
    cov_by_d: dict[str, float] = {}
    distributed = 0
    for w in res["warehouses"]:
        cell = w["articles"].get(nm_id)
        if not cell:
            continue
        d = wd(w["name"])
        dem_by_d[d] = dem_by_d.get(d, 0.0) + gross.get(w["name"], 0.0)
        cov_by_d[d] = cov_by_d.get(d, 0.0) + cell.get("stock", 0) + cell["need"]
        distributed += cell["need"]
    local = sum(min(dem_by_d[d], cov_by_d[d]) for d in dem_by_d)
    achieved = local / total_demand

    # Консервация цела.
    assert distributed <= can_send
    # Покрытый склад (Тула) не доливается (его ФО уже локализован стоком).
    by_wh = {w["name"]: w for w in res["warehouses"]}
    assert by_wh[ANCHOR_WH]["articles"].get(nm_id, {}).get("need", 0) == 0
    # Главное: достигнутая локализация у цели ~75%, БЕЗ overshoot (без фикса было бы ~90%).
    assert 0.68 <= achieved <= 0.82, f"localization {achieved:.2%} вне [68%,82%] — перетаривание ФО?"


# ── Bump regression: covered okrug main warehouse is NOT bumped ────────────────
async def test_bump_skips_covered_okrug(db_session, project, monkeypatch):
    """Min-stock bump НЕ добивает главный склад ФО, если ФО уже покрыт стоком
    соседнего склада (кейс юзера: коробка на пустой главный ЦФО при заваленном
    стоком ЦФО = перетаривание). Пустой cold-start ФО по-прежнему бампится.
    """
    pid = project.id
    nm_id = 703100 + (uuid.uuid4().int % 1000)
    bc = f"BC{nm_id}"
    # SKU с кратностью коробки (ppb=24) → bump-секция активна.
    res_ins = await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, article_seller, article_wb, "
            "use_box_multiplicity, box_qty_override, updated_at) "
            "VALUES (:p, :bc, :art, :nm, true, 24, NOW()) RETURNING id"
        ),
        {"p": pid, "bc": bc, "art": f"ART-{nm_id}", "nm": nm_id},
    )
    nom_id = res_ins.scalar()
    ff_id = await _seed_ff_warehouse(db_session, pid, "FF-Bump")
    await _seed_rf_stock(db_session, pid, ff_id, nom_id, bc, 10_000)
    EL = "Электросталь"  # главный ЦФО (больше заказов), пустой
    await _seed_wb_stock(db_session, pid, nm_id, EL, 0)
    await _seed_wb_stock(db_session, pid, nm_id, ANCHOR_WH, 100)  # Тула закрывает ЦФО стоком
    await db_session.commit()

    orders = [{"nmId": nm_id, "quantity": 50, "warehouseName": EL, "supplierArticle": "A"}]
    orders += [{"nmId": nm_id, "quantity": 10, "warehouseName": ANCHOR_WH, "supplierArticle": "A"}]
    _patch_api(monkeypatch, orders)

    res = await _call(
        db_session, pid, supply_days=14, analysis_days=14, only_available=True,
        localization_target=75,
    )
    by_wh = {w["name"]: w for w in res["warehouses"]}
    el_need = by_wh.get(EL, {}).get("articles", {}).get(nm_id, {}).get("need", 0)
    # ЦФО покрыт Тулой (сток 100 ≥ спрос 60) → главный Электросталь НЕ бампится коробкой.
    assert el_need == 0, f"Электросталь забамплена на {el_need} в уже покрытый ЦФО"
