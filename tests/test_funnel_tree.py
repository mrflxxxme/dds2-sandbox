# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests for arbitrary grouping chain (get_funnel_tree).

Цепочка измерений любой длины и порядка: «предмет → артикул → неделя» и т.п.
Проверяем: вложенность по порядку измерений, сходимость сумм родителя и детей,
недельные/месячные корзины, размножение по многозначному измерению (ярлык),
защиту от неизвестных и пустых измерений.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.funnel.grouping_tree import (
    DIMENSIONS,
    UnknownDimension,
    get_funnel_tree,
    month_bucket,
    week_bucket,
)

PROJECT_ID = 1
TAX_INFO = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}


def _row(
    nm_id: int,
    vendor_code: str,
    subject: str = "Ковры",
    brand: str = "BrandA",
    d: date = date(2026, 7, 15),
    orders_sum_rub: float = 10000.0,
    orders_count: int = 10,
    adv_sum: float = 500.0,
) -> MagicMock:
    row = MagicMock()
    row.nm_id = nm_id
    row.vendor_code = vendor_code
    row.brand = brand
    row.subject = subject
    row.date = d
    row.orders_sum_rub = Decimal(str(orders_sum_rub))
    row.orders_count = orders_count
    row.adv_sum = Decimal(str(adv_sum))
    row.adv_views = 1000
    row.adv_clicks = 50
    row.cost_price = Decimal("200.0")
    row.open_card = 500
    row.add_to_cart = 100
    row.cart_to_order_pct = None
    row.add_to_cart_pct = None
    row.avg_price = 1000.0
    return row


def _db(rows: list) -> AsyncMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _patch_maps():
    """Тарифы/выкуп не участвуют в проверках структуры — глушим их."""
    return (
        patch("backend.services.funnel.queries_grouping.get_tariff_map", AsyncMock(return_value={})),
        patch("backend.services.funnel.queries_grouping.get_avg_buyout_map", AsyncMock(return_value={})),
    )


# ─── Корзины дат ─────────────────────────────────────────────────────────────


def test_week_bucket_is_monday_to_sunday():
    # 15.07.2026 — среда; неделя должна начинаться с понедельника 13.07
    key, label = week_bucket(date(2026, 7, 15))
    assert key == "2026-07-13"
    assert label == "13.07–19.07"


def test_week_bucket_same_for_whole_week():
    keys = {week_bucket(date(2026, 7, d))[0] for d in range(13, 20)}
    assert keys == {"2026-07-13"}


def test_week_bucket_crosses_month():
    _key, label = week_bucket(date(2026, 7, 30))
    assert label == "27.07–02.08"


def test_month_bucket():
    assert month_bucket(date(2026, 7, 15)) == ("2026-07", "июль 2026")


# ─── Цепочка измерений ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tree_nests_in_dimension_order():
    rows = [
        _row(1, "COFFEE_1kg", subject="Кофе", d=date(2026, 7, 15)),
        _row(1, "COFFEE_1kg", subject="Кофе", d=date(2026, 7, 22)),
        _row(2, "COFFEE_2kg", subject="Кофе", d=date(2026, 7, 15)),
    ]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(
            _db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["subject", "nm", "week"], depth=3
        )

    assert len(tree) == 1
    cat = tree[0]
    assert cat["label"] == "Кофе"
    assert cat["dim"] == "subject"
    # второй уровень — артикулы
    assert {c["label"] for c in cat["children"]} == {"COFFEE_1kg", "COFFEE_2kg"}
    # третий уровень — недели: у первого артикула их две
    first = next(c for c in cat["children"] if c["label"] == "COFFEE_1kg")
    assert {w["dim"] for w in first["children"]} == {"week"}
    assert len(first["children"]) == 2


@pytest.mark.asyncio
async def test_parent_totals_equal_sum_of_children():
    rows = [
        _row(1, "A", subject="Кофе", orders_sum_rub=1000, orders_count=1),
        _row(2, "B", subject="Кофе", orders_sum_rub=3000, orders_count=3),
    ]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["subject", "nm"], depth=2)

    cat = tree[0]
    assert cat["orders_sum_rub"] == pytest.approx(4000)
    assert cat["orders_count"] == 4
    assert sum(c["orders_sum_rub"] for c in cat["children"]) == pytest.approx(cat["orders_sum_rub"])
    assert sum(c["orders_count"] for c in cat["children"]) == cat["orders_count"]


@pytest.mark.asyncio
async def test_single_dimension_has_no_children():
    rows = [_row(1, "A", brand="BrandA"), _row(2, "B", brand="BrandB")]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["brand"])

    assert {n["label"] for n in tree} == {"BrandA", "BrandB"}
    assert all(n.get("children") == [] for n in tree)


@pytest.mark.asyncio
async def test_same_dimensions_different_order_give_different_trees():
    rows = [_row(1, "A", subject="Кофе", brand="BrandA"), _row(2, "B", subject="Кофе", brand="BrandB")]
    p1, p2 = _patch_maps()
    with p1, p2:
        by_subject = await get_funnel_tree(
            _db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["subject", "brand"], depth=2
        )
    p1, p2 = _patch_maps()
    with p1, p2:
        by_brand = await get_funnel_tree(
            _db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["brand", "subject"], depth=2
        )

    assert len(by_subject) == 1 and len(by_subject[0]["children"]) == 2
    assert len(by_brand) == 2 and all(len(n["children"]) == 1 for n in by_brand)


@pytest.mark.asyncio
async def test_nm_node_carries_article_fields_for_ui():
    rows = [_row(77, "COFFEE_1kg", subject="Кофе", brand="BrandA")]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["nm"])

    node = tree[0]
    assert node["nm_id"] == 77
    assert node["vendor_code"] == "COFFEE_1kg"
    assert node["brand"] == "BrandA"
    assert node["subject"] == "Кофе"


@pytest.mark.asyncio
async def test_day_node_carries_date_field():
    rows = [_row(1, "A", d=date(2026, 7, 15))]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["day"])

    assert tree[0]["date"] == "2026-07-15"
    assert tree[0]["label"] == "2026-07-15"


# ─── Защита от неверного ввода ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_dimension_rejected():
    p1, p2 = _patch_maps()
    with p1, p2, pytest.raises(UnknownDimension):
        await get_funnel_tree(_db([]), PROJECT_ID, TAX_INFO, None, None, None, None, ["по_фазе_луны"])


@pytest.mark.asyncio
async def test_empty_chain_rejected():
    p1, p2 = _patch_maps()
    with p1, p2, pytest.raises(UnknownDimension):
        await get_funnel_tree(_db([]), PROJECT_ID, TAX_INFO, None, None, None, None, [])


@pytest.mark.asyncio
async def test_duplicate_dimension_rejected():
    """Один и тот же уровень дважды даёт вырожденное дерево — не пускаем."""
    p1, p2 = _patch_maps()
    with p1, p2, pytest.raises(UnknownDimension):
        await get_funnel_tree(_db([]), PROJECT_ID, TAX_INFO, None, None, None, None, ["brand", "brand"])


@pytest.mark.asyncio
async def test_chain_length_capped():
    """Слишком длинная цепочка — защита от комбинаторного взрыва узлов."""
    chain = ["subject", "brand", "nm", "day", "week", "month"]
    p1, p2 = _patch_maps()
    with p1, p2, pytest.raises(UnknownDimension):
        await get_funnel_tree(_db([]), PROJECT_ID, TAX_INFO, None, None, None, None, chain)


def test_all_dimensions_have_labels():
    """Каждое измерение из каталога описано для UI (иначе список группировок соврёт)."""
    for key, dim in DIMENSIONS.items():
        assert dim.label, f"измерение {key} без подписи"


# ─── Остатки в дереве ────────────────────────────────────────────────────────


def test_stock_tree_sums_unique_articles_and_skips_time_nodes():
    """Узел = сумма стока своих артикулов; временны́е узлы остаются пустыми.

    Остаток — снимок на сегодня: разрез по неделям иначе умножил бы его на число
    недель, а самой неделе приписал бы сегодняшний склад.
    """
    from backend.services.funnel.stock_costs import merge_stock_costs_tree

    def _stock(qty: int, cost: float) -> dict:
        return {"wb_qty": qty, "wb_cost_rub": cost, "own_qty": 0, "own_cost_rub": 0.0,
                "avg_daily": 0.0, "avg_daily_prev": 0.0}

    stock_map = {1: _stock(100, 1000.0), 2: _stock(40, 400.0)}
    tree = [
        {
            "dim": "subject", "label": "Кофе",
            "children": [
                {"dim": "nm", "label": "A", "nm_id": 1, "children": [
                    {"dim": "week", "label": "13.07–19.07", "children": []},
                    {"dim": "week", "label": "20.07–26.07", "children": []},
                ]},
                {"dim": "nm", "label": "B", "nm_id": 2, "children": []},
            ],
        }
    ]
    merge_stock_costs_tree(tree, stock_map)

    cat = tree[0]
    assert cat["wb_stock_qty"] == 140          # 100 + 40, а не 100×2 недели + 40
    assert cat["wb_stock_cost"] == pytest.approx(1400.0)
    nm_a = cat["children"][0]
    assert nm_a["wb_stock_qty"] == 100
    # недели остатка не получают вовсе
    assert "wb_stock_qty" not in nm_a["children"][0]


def test_stock_tree_zeroes_nodes_without_articles():
    """Ветка без артикулов (напр. «бренд → неделя») получает нули, а не чужой сток."""
    from backend.services.funnel.stock_costs import merge_stock_costs_tree

    tree = [{"dim": "brand", "label": "BrandA", "children": []}]
    merge_stock_costs_tree(tree, {1: {"wb_qty": 100, "wb_cost_rub": 1000.0, "own_qty": 0,
                                      "own_cost_rub": 0.0, "avg_daily": 0.0, "avg_daily_prev": 0.0}})
    assert tree[0].get("wb_stock_qty") in (0, None)


@pytest.mark.asyncio
async def test_nodes_carry_sortable_key():
    """У узла есть sort_key: по подписи недели («13.07–19.07») хронологию не построить."""
    rows = [_row(1, "A", d=date(2026, 7, 15)), _row(1, "A", d=date(2026, 8, 3))]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["week"])

    keys = sorted(n["sort_key"] for n in tree)
    assert keys == ["2026-07-13", "2026-08-03"]
    # подпись остаётся человеческой
    assert {n["label"] for n in tree} == {"13.07–19.07", "03.08–09.08"}


@pytest.mark.asyncio
async def test_same_label_different_keys_stay_separate():
    """Одинаковые подписи на разных ключах не должны схлопываться в один узел."""
    rows = [_row(11, "ОДИН", subject="Кофе"), _row(22, "ОДИН", subject="Кофе")]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["nm"])

    assert len(tree) == 2
    assert {n["sort_key"] for n in tree} == {"11", "22"}
    assert {n["label"] for n in tree} == {"ОДИН"}


@pytest.mark.asyncio
async def test_vendor_code_filter_reaches_query():
    """Фильтр по артикулу должен доезжать до выборки строк, а не теряться по пути."""
    from unittest.mock import patch as _patch

    p1, p2 = _patch_maps()
    with p1, p2, _patch(
        "backend.services.funnel.grouping_tree._load_funnel_rows", AsyncMock(return_value=[])
    ) as loader:
        await get_funnel_tree(
            _db([]), PROJECT_ID, TAX_INFO, None, None, None, None, ["nm"], vendor_code="COFFEE_1kg"
        )

    assert loader.await_args.kwargs["vendor_code"] == "COFFEE_1kg"


# ─── Измерение «Категория ABC» ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abc_dimension_splits_by_revenue_share():
    """A — первые 80 % выручки, B — следующие 15 %, C — хвост."""
    rows = [
        _row(1, "TOP", orders_sum_rub=80000, orders_count=80),
        _row(2, "MID", orders_sum_rub=15000, orders_count=15),
        _row(3, "TAIL", orders_sum_rub=5000, orders_count=5),
    ]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["abc", "nm"], depth=2)

    by_cat = {n["sort_key"]: n for n in tree}
    assert set(by_cat) == {"A", "B", "C"}
    assert by_cat["A"]["label"] == "Категория A (80% выручки)"
    assert [c["label"] for c in by_cat["A"]["children"]] == ["TOP"]
    assert [c["label"] for c in by_cat["B"]["children"]] == ["MID"]
    assert [c["label"] for c in by_cat["C"]["children"]] == ["TAIL"]


@pytest.mark.asyncio
async def test_abc_category_totals_equal_sum_of_children():
    """Узел категории — сумма своих товаров, как и любой другой уровень дерева."""
    rows = [
        _row(1, "TOP", orders_sum_rub=80000, orders_count=80),
        _row(2, "TOP2", orders_sum_rub=20000, orders_count=20),
    ]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["abc", "nm"], depth=2)

    for cat in tree:
        assert cat["orders_sum_rub"] == pytest.approx(sum(c["orders_sum_rub"] for c in cat["children"]))
        assert cat["orders_count"] == sum(c["orders_count"] for c in cat["children"])


@pytest.mark.asyncio
async def test_abc_is_offered_by_dimension_catalog():
    """Пресет «ABC анализ» собирается цепочкой — измерение обязано быть в каталоге."""
    assert "abc" in DIMENSIONS
    assert DIMENSIONS["abc"].label == "Категория ABC"


# ─── Ленивое дерево: уровень по требованию ───────────────────────────────────


@pytest.mark.asyncio
async def test_only_requested_level_is_returned():
    """По умолчанию приезжает один уровень: полное дерево — это десятки мегабайт."""
    rows = [
        _row(1, "A", subject="Кофе", d=date(2026, 7, 15)),
        _row(2, "B", subject="Чай", d=date(2026, 7, 16)),
    ]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["subject", "day", "nm"])

    assert {n["label"] for n in tree} == {"Кофе", "Чай"}
    assert all(n["children"] == [] for n in tree)
    assert all(n["has_children"] is True for n in tree)


@pytest.mark.asyncio
async def test_last_level_reports_no_children():
    rows = [_row(1, "A", subject="Кофе")]
    p1, p2 = _patch_maps()
    with p1, p2:
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["subject"])

    assert tree[0]["has_children"] is False


@pytest.mark.asyncio
async def test_path_returns_children_of_that_node_only():
    """Раскрытие ветки — узкий запрос: только дети выбранного узла."""
    rows = [
        _row(1, "A", subject="Кофе", d=date(2026, 7, 15)),
        _row(2, "B", subject="Кофе", d=date(2026, 7, 16)),
        _row(3, "C", subject="Чай", d=date(2026, 7, 15)),
    ]
    p1, p2 = _patch_maps()
    with p1, p2:
        kids = await get_funnel_tree(
            _db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["subject", "day", "nm"], path=["Кофе"]
        )

    assert {n["label"] for n in kids} == {"2026-07-15", "2026-07-16"}
    assert all(n["dim"] == "day" for n in kids)
    assert all(n["has_children"] is True for n in kids)


@pytest.mark.asyncio
async def test_path_two_levels_deep_reaches_articles():
    rows = [
        _row(1, "COFFEE_1kg", subject="Кофе", d=date(2026, 7, 15)),
        _row(2, "COFFEE_2kg", subject="Кофе", d=date(2026, 7, 15)),
        _row(3, "COFFEE_3kg", subject="Кофе", d=date(2026, 7, 16)),
    ]
    p1, p2 = _patch_maps()
    with p1, p2:
        kids = await get_funnel_tree(
            _db(rows), PROJECT_ID, TAX_INFO, None, None, None, None,
            ["subject", "day", "nm"], path=["Кофе", "2026-07-15"],
        )

    assert {n["label"] for n in kids} == {"COFFEE_1kg", "COFFEE_2kg"}
    assert all(n["has_children"] is False for n in kids)


@pytest.mark.asyncio
async def test_branch_totals_equal_parent_row():
    """Цифры ветки, загруженной отдельно, сходятся с её родительской строкой."""
    rows = [
        _row(1, "A", subject="Кофе", orders_sum_rub=1000, orders_count=1),
        _row(2, "B", subject="Кофе", orders_sum_rub=3000, orders_count=3),
    ]
    p1, p2 = _patch_maps()
    with p1, p2:
        top = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["subject", "nm"])
        kids = await get_funnel_tree(
            _db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["subject", "nm"], path=["Кофе"]
        )

    assert top[0]["orders_sum_rub"] == pytest.approx(sum(k["orders_sum_rub"] for k in kids))
    assert top[0]["orders_count"] == sum(k["orders_count"] for k in kids)


@pytest.mark.asyncio
async def test_path_longer_than_chain_rejected():
    p1, p2 = _patch_maps()
    with p1, p2, pytest.raises(UnknownDimension):
        await get_funnel_tree(
            _db([]), PROJECT_ID, TAX_INFO, None, None, None, None, ["subject"], path=["Кофе"]
        )


@pytest.mark.asyncio
async def test_glue_node_carries_products_for_thumbnails():
    """Миниатюры склейки рисуются до раскрытия — значит, товары едут в самой строке."""
    rows = [
        _row(1, "A", d=date(2026, 7, 15), orders_sum_rub=5000),
        _row(2, "B", d=date(2026, 7, 15), orders_sum_rub=9000),
        _row(2, "B", d=date(2026, 7, 16), orders_sum_rub=1000),
    ]
    p1, p2 = _patch_maps()
    with p1, p2, patch(
        "backend.services.funnel.grouping_tree._build_context",
        AsyncMock(return_value={"nm_tags": {}, "nm_imt": {1: "#100", 2: "#100"}, "nm_abc": {}}),
    ):
        tree = await get_funnel_tree(_db(rows), PROJECT_ID, TAX_INFO, None, None, None, None, ["imt", "day", "nm"])

    node = tree[0]
    assert node["label"] == "#100"
    assert node["nm_total"] == 2
    assert node["nm_ids"][0] == 2      # первым — товар с большей суммой заказов
    assert node["children"] == [] and node["has_children"] is True
