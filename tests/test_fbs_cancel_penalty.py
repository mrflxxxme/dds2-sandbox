"""
Тесты штрафа WB за отмену продавцом (services/wb_fbs/cancel_penalty).

Формула — оценка СВЕРХУ по правилам WB «Невыполненный заказ (отмена
продавцом)»: двойная комиссия предмета с потолками 50 % цены / тир рейтинга /
10 000 ₽ и полом 10 ₽. Коэффициент срока отмены и авто-пол 100 ₽ намеренно НЕ
применяются (момента отмены у нас нет) — см. докстринг модуля.

Что закрыто:
  • ветки estimate_penalty: двойная ставка, потолок 50 %, потолки тира и
    10 000 ₽, пол 10 ₽, тир ≥97 % (одинарная комиссия), вырожденные входы;
  • build_cancel_stats: выручка по всей выборке, счётчики оценки,
    no_commission_count (без ставки штраф пропущен, не выдуман), факт из
    финотчёта по типу удержания и окну дат заказа, fact_covered_to,
    fact_scoped_out при фильтре склада, клиентская корзина без штрафа;
  • изоляция по project_id.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from backend.models.wb_fbs import WbFbsOrder
from backend.models.wb_finance import WbFinanceRow
from backend.services.wb_fbs import cancel_penalty
from backend.services.wb_fbs.cancel_penalty import (
    build_cancel_stats,
    estimate_penalty,
    order_price,
)

# ─── estimate_penalty: чистая формула ────────────────────────────────────────


def test_double_commission_below_ceiling() -> None:
    # 2 × 10 % = 20 % < 50 % → штраф ровно двойная комиссия.
    assert estimate_penalty(Decimal("1000"), Decimal("10")) == Decimal("200.00")


def test_rate_ceiling_50_pct() -> None:
    # 2 × 30 % = 60 % → срезается потолком 50 % цены.
    assert estimate_penalty(Decimal("1000"), Decimal("30")) == Decimal("500.00")


def test_hard_cap_10000() -> None:
    # 50 % от 100 000 = 50 000 → жёсткий потолок 10 000 ₽ за единицу.
    assert estimate_penalty(Decimal("100000"), Decimal("30")) == Decimal("10000.00")


def test_floor_10_rub() -> None:
    # 2 × 5 % от 40 ₽ = 4 ₽ → пол 10 ₽.
    assert estimate_penalty(Decimal("40"), Decimal("5")) == Decimal("10.00")


def test_quantize_two_places() -> None:
    # 2 × 12.345 % от 999.99 — результат всегда в копейках (2 знака).
    result = estimate_penalty(Decimal("999.99"), Decimal("12.345"))
    assert result == result.quantize(Decimal("0.01"))
    assert result == Decimal("246.90")


def test_gte97_tier_single_commission(monkeypatch: pytest.MonkeyPatch) -> None:
    # Рейтинг ≥ 97 %: ОДИНАРНАЯ комиссия и потолок тира 3 000 ₽.
    monkeypatch.setattr(cancel_penalty, "DELIVERY_RATING_TIER", "gte97")
    assert estimate_penalty(Decimal("1000"), Decimal("10")) == Decimal("100.00")
    # 25 % от 100 000 = 25 000 → потолок тира 3 000.
    assert estimate_penalty(Decimal("100000"), Decimal("25")) == Decimal("3000.00")


def test_degenerate_inputs_give_zero() -> None:
    assert estimate_penalty(None, Decimal("10")) == Decimal(0)
    assert estimate_penalty(Decimal("0"), Decimal("10")) == Decimal(0)
    assert estimate_penalty(Decimal("-5"), Decimal("10")) == Decimal(0)
    assert estimate_penalty(Decimal("1000"), None) == Decimal(0)
    assert estimate_penalty(Decimal("1000"), Decimal("0")) == Decimal(0)


def test_order_price_prefers_sale_price_including_zero() -> None:
    # Канон coalesce(sale_price, price): 0 — валидная цена, не «пусто».
    o = WbFbsOrder(sale_price=Decimal("0"), price=Decimal("100"))
    assert order_price(o) == Decimal("0")
    o2 = WbFbsOrder(sale_price=None, price=Decimal("100"))
    assert order_price(o2) == Decimal("100")


# ─── build_cancel_stats: сводка корзины ──────────────────────────────────────

SELLER = "cancel"
CANCEL_TYPE = "Штраф МП. Невыполненный заказ (отмена продавцом)"
OTHER_TYPE = "Штраф МП. Невыполненный заказ (отправка товара отличного от заявленного)"


def _order(project_id: int, wb_order_id: int, **over) -> WbFbsOrder:
    base = dict(
        project_id=project_id,
        wb_order_id=wb_order_id,
        supplier_status=SELLER,
        created_at_wb=datetime(2026, 7, 20, 12, 0),
        price=Decimal("1000"),
        sale_price=None,
        subject="Ковёр",
        synced_at=datetime(2026, 7, 29, 10, 0),
    )
    base.update(over)
    return WbFbsOrder(**base)


def _fact_row(project_id: int, rrd_id: int, **over) -> WbFinanceRow:
    base = dict(
        project_id=project_id,
        realizationreport_id=1,
        rrd_id=rrd_id,
        date_from=date(2026, 7, 21),
        date_to=date(2026, 7, 27),
        bonus_type_name=CANCEL_TYPE,
        order_dt=date(2026, 7, 20),
        penalty=Decimal("880.00"),
    )
    base.update(over)
    return WbFinanceRow(**base)


@pytest_asyncio.fixture
async def cancel_env(db_session, project, other_project, monkeypatch):
    """Три отменённых задания + тарифы (стаб) + строки финотчёта."""
    pid = project.id
    db_session.add_all(
        [
            # Со ставкой: 2×10 % от 1000 = 200.
            _order(pid, 910001),
            # sale_price важнее price: 2×10 % от 500 = 100.
            _order(pid, 910002, sale_price=Decimal("500"), price=Decimal("2000")),
            # Предмета нет в тарифах → штраф пропущен, не выдуман.
            _order(pid, 910003, subject="Неизвестный предмет"),
            # Чужой проект — не должен попасть никуда.
            _order(other_project.id, 910004),
        ]
    )
    db_session.add_all(
        [
            _fact_row(pid, 1),
            # Тот же тип, но дата заказа вне окна теста.
            _fact_row(pid, 2, order_dt=date(2026, 6, 1), penalty=Decimal("500.00")),
            # Другой тип семейства «Невыполненный заказ» — в сумму не идёт.
            _fact_row(pid, 3, bonus_type_name=OTHER_TYPE, penalty=Decimal("300.00")),
            # Чужой проект.
            _fact_row(other_project.id, 4, penalty=Decimal("9999.00")),
        ]
    )
    await db_session.commit()

    async def fake_tariffs(db, project_id):
        return {"Ковёр": 10.0}

    monkeypatch.setattr(cancel_penalty, "get_tariff_map", fake_tariffs)
    return pid


def _seller_conditions(pid: int):
    return [WbFbsOrder.project_id == pid, WbFbsOrder.supplier_status == SELLER]


async def test_seller_bucket_full_stats(db_session, cancel_env):
    pid = cancel_env
    stats = await build_cancel_stats(
        db_session,
        pid,
        conditions=_seller_conditions(pid),
        is_seller_bucket=True,
        dt_from=date(2026, 7, 1),
        dt_to=date(2026, 7, 31),
        warehouse_scoped=False,
    )
    # Выручка = 1000 + 500 (sale_price важнее price) + 1000, чужой проект мимо.
    assert stats["revenue"] == Decimal("2500")
    assert stats["orders"] == 3
    # Оценка: 200 + 100; третий — без ставки.
    assert stats["penalty_est"] == Decimal("300.00")
    assert stats["penalty_est_count"] == 2
    assert stats["no_commission_count"] == 1
    assert stats["estimate_truncated"] is False
    # Факт: только тип «отмена продавцом» в окне дат заказа, свой проект.
    assert stats["penalty_fact"] == Decimal("880.00")
    assert stats["penalty_fact_count"] == 1
    assert stats["fact_covered_to"] == date(2026, 7, 27)
    assert stats["fact_scoped_out"] is False


async def test_client_bucket_revenue_only(db_session, cancel_env):
    pid = cancel_env
    stats = await build_cancel_stats(
        db_session,
        pid,
        conditions=_seller_conditions(pid),  # предикаты любые — важен флаг корзины
        is_seller_bucket=False,
        dt_from=None,
        dt_to=None,
        warehouse_scoped=False,
    )
    assert stats["revenue"] == Decimal("2500")
    # Клиентские отмены WB не штрафует: ни оценки, ни похода за фактом.
    assert stats["penalty_est"] == Decimal(0)
    assert stats["penalty_est_count"] == 0
    assert stats["penalty_fact"] == Decimal(0)
    assert stats["fact_covered_to"] is None


async def test_warehouse_scope_hides_fact(db_session, cancel_env):
    pid = cancel_env
    stats = await build_cancel_stats(
        db_session,
        pid,
        conditions=_seller_conditions(pid),
        is_seller_bucket=True,
        dt_from=None,
        dt_to=None,
        warehouse_scoped=True,
    )
    # Оценка живёт (она построчная), а факт скрыт: у финотчёта нет склада.
    assert stats["penalty_est"] == Decimal("300.00")
    assert stats["fact_scoped_out"] is True
    assert stats["penalty_fact"] == Decimal(0)
    # Граница отчёта отдаётся и здесь — плашке нужно объяснять свежие нули.
    assert stats["fact_covered_to"] == date(2026, 7, 27)


async def test_open_window_counts_all_fact_rows(db_session, cancel_env):
    pid = cancel_env
    stats = await build_cancel_stats(
        db_session,
        pid,
        conditions=_seller_conditions(pid),
        is_seller_bucket=True,
        dt_from=None,
        dt_to=None,
        warehouse_scoped=False,
    )
    # Без окна дат факт собирает оба удержания типа «отмена продавцом».
    assert stats["penalty_fact"] == Decimal("1380.00")
    assert stats["penalty_fact_count"] == 2
