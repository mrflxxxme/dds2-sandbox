"""Growth-aware скорость + плечо доставки в глобальном total_need.

Мотивация (прод 2026-07-14, ШК 2043788816553 «150х200_серый»): заказы выросли
с ~7/д до ~20/д за последние 3 дня, но плоское среднее за 14д давало скорость
10/д → total_need 61 → план 52 шт при реальном расходе 20/д и плече ~8 дней —
разрыв стока на WB ещё до приезда поставки.

Формула после фикса:
  eff = max(avg_окно, avg_7д, avg_3д)  (полные дни, сегодня не входит)
  total_need = round(eff × (supply_days + lead_взвеш) − WB − сборка − путь)

Хелперы сидинга/патча переиспользуются из test_warehouse_need_localization_target.
"""
import uuid
from datetime import date, timedelta

import pytest

from tests.test_warehouse_need_localization_target import (
    ANCHOR_WH,
    _call,
    _patch_api,
    _seed_ff_warehouse,
    _seed_nomenclature,
    _seed_rf_stock,
    _seed_wb_stock,
)

pytestmark = pytest.mark.asyncio

# Лид для ANCHOR_WH (Тула, central) в этих фикстурах детерминирован:
# assembly_days=1 + fallback-transport central=2 + wb_acceptance_days=1.
LEAD = 4


def _dated_orders(nm_id: int, per_day: dict[int, int]) -> list[dict]:
    """Заказы по дням: {дней_назад: штук} → по 1 заказу на день с quantity."""
    today = date.today()
    out = []
    for days_ago, qty in per_day.items():
        if qty <= 0:
            continue
        d = (today - timedelta(days=days_ago)).isoformat()
        out.append({
            "nmId": nm_id, "quantity": qty, "warehouseName": ANCHOR_WH,
            "supplierArticle": "A", "date": f"{d}T10:00:00",
        })
    return out


async def _seed_sku(db_session, pid, nm_id, wb_stock=0, rf_stock=10_000):
    bc = f"BC{nm_id}"
    nom_id = await _seed_nomenclature(db_session, pid, nm_id, bc)
    ff_id = await _seed_ff_warehouse(db_session, pid, f"FF-G{nm_id % 1000}")
    await _seed_rf_stock(db_session, pid, ff_id, nom_id, bc, rf_stock)
    await _seed_wb_stock(db_session, pid, nm_id, ANCHOR_WH, wb_stock)
    await db_session.commit()


async def test_growth_velocity_uses_recent_window(db_session, project, monkeypatch):
    """Взрывной SKU: 5/д базово, 20/д последние 3 полных дня → скорость 20/д."""
    pid = project.id
    nm_id = 710100 + (uuid.uuid4().int % 90)
    await _seed_sku(db_session, pid, nm_id, wb_stock=40)

    # Дни 14..4 назад — по 5 шт; дни 3..1 назад — по 20 шт; сегодня 2 шт
    # (неполный день — в короткие окна не входит).
    per_day = {k: 5 for k in range(4, 15)} | {3: 20, 2: 20, 1: 20, 0: 2}
    _patch_api(monkeypatch, _dated_orders(nm_id, per_day))

    res = await _call(db_session, pid, supply_days=14, analysis_days=14)
    art = next(a for a in res["articles"] if a["nm_id"] == nm_id)

    total_qty = 5 * 11 + 20 * 3 + 2  # 117, включая сегодняшний хвост
    avg_full = total_qty / 14
    assert art["avg_daily_base"] == round(avg_full, 2)
    # eff = max(avg_full≈8.36, avg7=(5+5+5+5+20+20+20)/7≈11.43, avg3=20) = 20.
    assert art["eff_avg_daily"] == 20.0
    assert art["growth_ratio"] == round(20 / avg_full, 2)
    assert art["lead_days"] == LEAD
    # total_need = round(20 × (14+4) − 40) = 320 — а не round(avg_full×14−40)=77.
    assert art["total_need"] == 320
    # Остатка WB 40 при 20/д хватит на 2 дня — меньше плеча (алярм-кейс юзера).
    assert art["wb_days_left"] == 2.0
    assert art["wb_days_left"] < art["lead_days"]
    assert art["wb_days_left_inbound"] == 2.0  # ничего не едет


async def test_steady_sku_lead_in_cap(db_session, project, monkeypatch):
    """Ровный SKU: рост 1.0, но горизонт капа теперь supply+lead (дефект аудита
    «рационирование всегда»): 10/д × (14+4) = 180, а не 140."""
    pid = project.id
    nm_id = 710200 + (uuid.uuid4().int % 90)
    await _seed_sku(db_session, pid, nm_id)

    per_day = {k: 10 for k in range(1, 15)}  # 14 полных дней по 10
    _patch_api(monkeypatch, _dated_orders(nm_id, per_day))

    res = await _call(db_session, pid, supply_days=14, analysis_days=14)
    art = next(a for a in res["articles"] if a["nm_id"] == nm_id)

    assert art["growth_ratio"] == 1.0
    assert art["eff_avg_daily"] == 10.0
    assert art["lead_days"] == LEAD
    assert art["total_need"] == 10 * (14 + LEAD)
    assert art["can_send"] == 10 * (14 + LEAD)


async def test_declining_sku_keeps_window_average(db_session, project, monkeypatch):
    """Затухающий SKU не режется ниже среднего за окно (перезаклад безопаснее
    разрыва; культура «дозагруз до локализации»)."""
    pid = project.id
    nm_id = 710300 + (uuid.uuid4().int % 90)
    await _seed_sku(db_session, pid, nm_id)

    per_day = {k: 20 for k in range(4, 15)}  # продажи были, последние 3 дня — 0
    _patch_api(monkeypatch, _dated_orders(nm_id, per_day))

    res = await _call(db_session, pid, supply_days=14, analysis_days=14)
    art = next(a for a in res["articles"] if a["nm_id"] == nm_id)

    assert art["growth_ratio"] == 1.0
    assert art["eff_avg_daily"] == art["avg_daily_base"]


async def test_orders_without_dates_no_growth(db_session, project, monkeypatch):
    """Заказы без поля date (легаси-фикстуры/сбой API) → g консервативно 1.0."""
    pid = project.id
    nm_id = 710400 + (uuid.uuid4().int % 90)
    await _seed_sku(db_session, pid, nm_id)

    orders = [{"nmId": nm_id, "quantity": 140, "warehouseName": ANCHOR_WH, "supplierArticle": "A"}]
    _patch_api(monkeypatch, orders)

    res = await _call(db_session, pid, supply_days=14, analysis_days=14)
    art = next(a for a in res["articles"] if a["nm_id"] == nm_id)

    assert art["growth_ratio"] == 1.0
    assert art["total_need"] == 10 * (14 + LEAD)


async def test_days_left_null_without_sales(db_session, project, monkeypatch):
    """Без заказов скорость 0 → wb_days_left = None (не 0 и не ∞)."""
    pid = project.id
    nm_id = 710500 + (uuid.uuid4().int % 90)
    await _seed_sku(db_session, pid, nm_id, wb_stock=50)

    _patch_api(monkeypatch, [])

    res = await _call(db_session, pid, supply_days=14, analysis_days=14)
    art = next(a for a in res["articles"] if a["nm_id"] == nm_id)

    assert art["wb_days_left"] is None
    assert art["wb_days_left_inbound"] is None
    assert art["total_need"] == 0
