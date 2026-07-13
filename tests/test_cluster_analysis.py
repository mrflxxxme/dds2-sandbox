"""Тесты кластеризатора поисковых кампаний (чистая логика классификации/стемминга/слияния)."""

from backend.services.funnel.cluster_analysis_service import (
    HEAD_SPEND_SHARE,
    TARGET_DRR,
    _classify,
    _is_relevant,
    _stem,
    _subject_stems,
)
from backend.services.funnel.wb_advertising_api import _finalize_clusters, _merge_clusters, _split_windows


# ── стемминг / релевантность ──────────────────────────────────────────────────
def test_stem_strips_russian_endings():
    assert _stem("палатки") == "палатк"
    assert _stem("палатка") == "палатк"
    assert _stem("ковры") == "ковр"
    assert _stem("диваны") == "диван"


def test_subject_stems_ignores_short_words():
    stems = _subject_stems("Диваны бескаркасные")
    assert "диван" in stems
    # короткие служебные слова (<4) не попадают
    assert all(len(s) >= 1 for s in stems)


def test_relevant_matches_by_shared_stem():
    stems = _subject_stems("Палатки")
    assert _is_relevant("палатка туристическая 4 местная", stems) is True
    assert _is_relevant("палатка", stems) is True
    # беседка/пляж не делят стем с «палатки» → мусор
    assert _is_relevant("беседка садовая", stems) is False
    assert _is_relevant("коврик для ванной", stems) is False


def test_relevant_true_when_no_stems():
    assert _is_relevant("что угодно", set()) is True


# ── классификация тиров ───────────────────────────────────────────────────────
def _row(q, views=0, clicks=0, spend=0.0, orders=0):
    return {"norm_query": q, "views": views, "clicks": clicks, "spend": spend, "orders": orders, "atbs": 0}


def test_classify_head_by_budget_share():
    stems = _subject_stems("Палатки")
    r = _classify(_row("палатка", views=100000, clicks=8000, spend=200000.0, orders=88), 14899, 400000.0, stems)
    assert r["tier"] == "HEAD"
    assert r["drr"] is not None and r["cpo"] is not None


def test_classify_efficient_longtail():
    stems = _subject_stems("Палатки")
    # маленький расход, есть заказ, ДРР низкий
    r = _classify(_row("палатка на 8 мест", views=200, clicks=22, spend=95.0, orders=5), 14899, 400000.0, stems)
    assert r["tier"] == "EFFICIENT"
    assert r["drr"] <= TARGET_DRR


def test_classify_converts_high_drr():
    stems = _subject_stems("Палатки")
    # есть заказ, но ДРР высокий и доля бюджета мала
    r = _classify(_row("палатка для отдыха", views=2000, clicks=198, spend=3000.0, orders=1), 14899, 400000.0, stems)
    assert r["tier"] == "CONVERTS"
    assert r["drr"] > TARGET_DRR


def test_classify_trash_off_subject():
    stems = _subject_stems("Палатки")
    r = _classify(_row("беседка", views=2000, clicks=28, spend=1275.0, orders=0), 14899, 400000.0, stems)
    assert r["tier"] == "TRASH"
    assert r["relevant"] is False


def test_classify_waste_relevant_zero_orders():
    stems = _subject_stems("Палатки")
    r = _classify(_row("палатка куб", views=4060, clicks=227, spend=2506.0, orders=0), 14899, 400000.0, stems)
    assert r["tier"] == "WASTE"
    assert r["relevant"] is True


def test_classify_noconv_relevant_low_spend():
    stems = _subject_stems("Палатки")
    r = _classify(_row("палатка высокая", views=100, clicks=5, spend=90.0, orders=0), 14899, 400000.0, stems)
    assert r["tier"] == "NOCONV"


# ── слияние кластеров по окнам ────────────────────────────────────────────────
def test_split_windows_respects_30_day_cap():
    wins = _split_windows("2026-04-22", "2026-07-09", max_days=30)
    assert len(wins) == 3
    assert wins[0][0] == "2026-04-22"
    assert wins[-1][1] == "2026-07-09"


def test_merge_and_finalize_sums_and_recomputes():
    acc: dict = {}
    _merge_clusters(acc, [{"norm_query": "палатка", "views": 100, "clicks": 10, "spend": 200.0, "orders": 1, "avg_pos": 5.0}])
    _merge_clusters(acc, [{"norm_query": "палатка", "views": 100, "clicks": 10, "spend": 200.0, "orders": 1, "avg_pos": 7.0}])
    out = _finalize_clusters(acc)
    assert len(out) == 1
    c = out[0]
    assert c["views"] == 200 and c["clicks"] == 20 and c["orders"] == 2
    assert c["spend"] == 400.0
    assert c["ctr"] == 10.0  # 20/200*100
    assert c["cpc"] == 20.0  # 400/20
    assert c["avg_pos"] == 6.0  # взвешено по показам (равные показы → среднее)


def test_head_share_constant_sane():
    assert 0 < HEAD_SPEND_SHARE < 1


# ── ставки и lock (<100 показов) ──────────────────────────────────────────────
def test_bid_map_merges_max_per_query():
    from backend.services.funnel.cluster_analysis_service import _bid_map

    bids = {
        (1, 10): {"палатка": 2000.0, "шатер": 0.0},
        (1, 11): {"палатка": 2500.0},
    }
    m = _bid_map(bids)
    assert m["палатка"] == 2500.0  # max по парам
    assert "шатер" not in m         # нулевые ставки отбрасываются


def test_enrich_adds_bid_minus_and_lock():
    from backend.services.funnel.cluster_analysis_service import LOCK_MIN_VIEWS, _enrich

    assert LOCK_MIN_VIEWS == 100
    hi = _enrich({"norm_query": "палатка", "views": 500}, {"беседка"}, {"палатка": 2000.0})
    assert hi["bid"] == 2000.0 and hi["is_minused"] is False and hi["locked"] is False

    lo = _enrich({"norm_query": "беседка", "views": 40}, {"беседка"}, {})
    assert lo["locked"] is True          # <100 показов → залочен
    assert lo["is_minused"] is True      # в минус-листе
    assert lo["bid"] is None             # ставка не задана

    edge = _enrich({"norm_query": "x", "views": 100}, set(), {})
    assert edge["locked"] is False       # ровно 100 — уже не залочен


def test_enrich_lock_uses_alltime_views_not_range():
    """locked считается по показам за ВСЮ историю (all_views), а не за выбранный период.

    Юзер выбрал узкую дату → в периоде мало показов, но исторически ≥100 → НЕ «сбор данных».
    """
    from backend.services.funnel.cluster_analysis_service import _enrich

    # За период всего 5 показов, но за всю историю 500 → не залочен
    r = _enrich({"norm_query": "ковёр", "views": 5}, set(), {}, {"ковёр": 500})
    assert r["locked"] is False
    # Обратно: в периоде много, но исторически мало (напр. фраза только появилась) → залочен
    r2 = _enrich({"norm_query": "новый", "views": 300}, set(), {}, {"новый": 40})
    assert r2["locked"] is True
    # Фразы нет в all_views (не крутилась исторически) → фолбэк на показы периода
    r3 = _enrich({"norm_query": "нет", "views": 5}, set(), {}, {"иное": 500})
    assert r3["locked"] is True


def test_merge_norm_sums_across_pairs():
    from backend.services.funnel.cluster_analysis_service import _merge_norm

    stats = {
        (1, 111): [{"norm_query": "ковёр", "views": 100, "clicks": 5, "spend": 50.0}],
        (1, 222): [{"norm_query": "ковёр", "views": 40, "clicks": 2, "spend": 20.0},
                   {"norm_query": "палас", "views": 10, "clicks": 1, "spend": 5.0}],
    }
    m = _merge_norm(stats)
    assert m["ковёр"]["views"] == 140 and m["ковёр"]["clicks"] == 7 and m["ковёр"]["spend"] == 70.0
    assert m["палас"]["views"] == 10
