"""
Tests for get_campaign_zone_metrics (backend/services/funnel/ads_manager.py).

Посуточные РК-метрики кампании в разрезе зоны показов:
- «Всего» — из WbAdNmDaily (итог кампании по дням, с рекламными корзинами/заказами);
- «Поиск» — из WbAdSearchDaily (поисковые кластеры);
- «Рекомендации» = Всего − Поиск по каждой метрике, зажим нулём.
Разбивка по зонам только для CPM; воронка по зонам не делится.
"""

from datetime import date
from decimal import Decimal

from backend.models.integrations import WbAdCampaign, WbAdNmDaily, WbAdSearchDaily
from backend.services.funnel.ads_manager import _ad_metric_row, get_campaign_zone_metrics

CID = 999001
NM = 111


# ─── _ad_metric_row (чистая функция) ──────────────────────────────────────────


def test_ad_metric_row_derived():
    r = _ad_metric_row("2026-07-11", views=100, clicks=10, spend=50.0, atbs=5, orders=2)
    assert r["date"] == "2026-07-11"
    assert r["views"] == 100 and r["clicks"] == 10 and r["spend"] == 50.0
    assert r["ctr"] == 10.0  # 10 / 100
    assert r["cpc"] == 5.0   # 50 / 10
    assert r["cpm"] == 500.0  # 50 / 100 * 1000
    assert r["atbs"] == 5 and r["orders"] == 2
    assert r["cpo"] == 25.0  # 50 / 2


def test_ad_metric_row_zero_denominators():
    r = _ad_metric_row("x", 0, 0, 0.0, 0, 0)
    assert r["ctr"] == 0.0 and r["cpc"] == 0.0
    assert r["cpo"] is None  # нет заказов → CPO не считаем


# ─── get_campaign_zone_metrics (DB) ───────────────────────────────────────────


async def _seed(db, project_id: int, campaign_type: str = "cpm"):
    db.add(
        WbAdCampaign(
            project_id=project_id, campaign_id=CID, name="Zone test",
            campaign_type=campaign_type, status=9, nm_ids=[NM],
        )
    )
    # Всего (итог кампании по дням)
    db.add_all([
        WbAdNmDaily(project_id=project_id, campaign_id=CID, nm_id=NM, date=date(2026, 7, 11),
                    views=100, clicks=10, spend=Decimal("50"), atbs=5, orders=2),
        WbAdNmDaily(project_id=project_id, campaign_id=CID, nm_id=NM, date=date(2026, 7, 10),
                    views=200, clicks=20, spend=Decimal("80"), atbs=8, orders=4),
    ])
    # Поиск: 11-го — часть итога; 10-го — заведомо БОЛЬШЕ итога (проверить зажим «Рекомендаций»)
    db.add_all([
        WbAdSearchDaily(project_id=project_id, campaign_id=CID, nm_id=NM, date=date(2026, 7, 11),
                        views=60, clicks=6, spend=Decimal("30"), atbs=3, orders=1, shks=1),
        WbAdSearchDaily(project_id=project_id, campaign_id=CID, nm_id=NM, date=date(2026, 7, 10),
                        views=250, clicks=25, spend=Decimal("100"), atbs=10, orders=5, shks=5),
    ])
    await db.commit()


async def test_zone_total_aggregates_nm_daily(db_session, project):
    await _seed(db_session, project.id)
    res = await get_campaign_zone_metrics(db_session, project.id, CID, "2026-07-10", "2026-07-11", zone="total")
    assert res["zone"] == "total"
    assert [r["date"] for r in res["rows"]] == ["2026-07-11", "2026-07-10"]  # по убыванию
    assert res["totals"]["views"] == 300 and res["totals"]["clicks"] == 30
    assert res["totals"]["spend"] == 130.0
    assert res["totals"]["atbs"] == 13 and res["totals"]["orders"] == 6


async def test_zone_search_from_search_daily(db_session, project):
    await _seed(db_session, project.id)
    res = await get_campaign_zone_metrics(db_session, project.id, CID, "2026-07-10", "2026-07-11", zone="search")
    assert res["zone"] == "search"
    by_date = {r["date"]: r for r in res["rows"]}
    assert by_date["2026-07-11"]["views"] == 60 and by_date["2026-07-11"]["orders"] == 1
    assert by_date["2026-07-10"]["views"] == 250


async def test_zone_recommendations_is_total_minus_search_clamped(db_session, project):
    await _seed(db_session, project.id)
    res = await get_campaign_zone_metrics(db_session, project.id, CID, "2026-07-10", "2026-07-11", zone="recommendations")
    assert res["zone"] == "recommendations"
    by_date = {r["date"]: r for r in res["rows"]}
    # 11-го: 100−60=40 показов, 10−6=4 клика, 50−30=20 затрат, 5−3=2 корзины, 2−1=1 заказ
    assert by_date["2026-07-11"]["views"] == 40 and by_date["2026-07-11"]["clicks"] == 4
    assert by_date["2026-07-11"]["spend"] == 20.0
    assert by_date["2026-07-11"]["atbs"] == 2 and by_date["2026-07-11"]["orders"] == 1
    # 10-го: поиск больше итога — всё зажато в ноль, без отрицательных
    assert (by_date["2026-07-10"]["views"], by_date["2026-07-10"]["clicks"],
            by_date["2026-07-10"]["spend"], by_date["2026-07-10"]["orders"]) == (0, 0, 0.0, 0)


async def test_zone_project_isolation(db_session, project, other_project):
    await _seed(db_session, project.id)
    # Та же кампания заведена и в другом проекте, но без дневных строк —
    # данные первого проекта не должны утекать в выборку второго.
    db_session.add(
        WbAdCampaign(project_id=other_project.id, campaign_id=CID, name="Other",
                     campaign_type="cpm", status=9, nm_ids=[NM])
    )
    await db_session.commit()
    res = await get_campaign_zone_metrics(db_session, other_project.id, CID, "2026-07-10", "2026-07-11", zone="total")
    assert res["rows"] == [] and res["totals"]["views"] == 0


async def test_zone_non_cpm_falls_back_to_total(db_session, project):
    """У CPC зон нет — запрос зоны отдаёт «Всего» (zone='total')."""
    await _seed(db_session, project.id, campaign_type="cpc")
    res = await get_campaign_zone_metrics(db_session, project.id, CID, "2026-07-10", "2026-07-11", zone="search")
    assert res["zone"] == "total"
    assert res["totals"]["views"] == 300  # весь итог, не поиск
