"""Управление рекламой — список кампаний и «нехватка бюджета».

Read-only поверх существующих таблиц: wb_ad_campaigns (кампании+бюджеты,
часовой синк), wb_ad_campaign_daily (расход по дням), wb_ad_campaign_events
(история изменений бюджета). Час «бюджет кончился» восстанавливается из
событий budget_change → 0 с точностью до интервала синка (~1 час).
"""

import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytz
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbAdCampaign, WbAdCampaignEvent, WbFunnelDaily
from backend.models.integrations import WbAdCampaignDaily, WbAdNmDaily, WbAdCampaignSnapshot
from backend.services.funnel.bdr_rates import BdrRatesLookup
from backend.utils.time import utcnow

logger = logging.getLogger("dds.funnel")

MSK = pytz.timezone("Europe/Moscow")

CAMPAIGN_STATUS_LABELS = {7: "Завершена", 9: "Активна", 11: "Пауза"}

AUTOPAY_SETTINGS_KEY = "ads_autopay"

# WB не принимает пополнение рекламной кампании меньше этой суммы.
MIN_TOPUP_RUB = 1000.0


# ─── Список кампаний ─────────────────────────────────────────────────────────


async def list_ad_campaigns(
    db: AsyncSession,
    project_id: int,
    tax_info: dict | None = None,
    bdr_rates_map: BdrRatesLookup | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Все кампании проекта + расход/клики/CTR, ДРР и маржа за выбранный период.

    Период по умолчанию — последние 7 дней (МСК). «Расход сегодня» — всегда
    за сегодня, независимо от периода. ДРР/маржа считаются по товарам кампании
    (nm_ids) из воронки (get_funnel_by_sku, та же формула, что на странице
    воронки). tax_info=None — без ДРР/маржи.
    """
    campaigns = (
        (await db.execute(select(WbAdCampaign).where(WbAdCampaign.project_id == project_id).limit(2000)))
        .scalars()
        .all()
    )
    if not campaigns:
        return []

    today_msk = utcnow().astimezone(MSK).date() if utcnow().tzinfo else utcnow().date()
    period_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today_msk
    period_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else period_to - timedelta(days=6)
    period_days = (period_to - period_from).days + 1  # дней в периоде — для «затраты в час»

    CD = WbAdCampaignDaily
    spend_rows = (
        await db.execute(
            select(
                CD.campaign_id,
                func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"),
                func.coalesce(func.sum(CD.views), 0).label("views"),
                func.coalesce(func.sum(CD.clicks), 0).label("clicks"),
            )
            .where(CD.project_id == project_id, CD.date >= period_from, CD.date <= period_to)
            .group_by(CD.campaign_id)
        )
    ).all()
    spend_map = {r.campaign_id: r for r in spend_rows}

    today_rows = (
        await db.execute(
            select(CD.campaign_id, func.coalesce(func.sum(CD.spend), Decimal("0")).label("today"))
            .where(CD.project_id == project_id, CD.date == today_msk)
            .group_by(CD.campaign_id)
        )
    ).all()
    today_map = {r.campaign_id: float(r.today) for r in today_rows}

    # Выручка ВЧЕРА по товарам (для «ДРР план»): сумма заказов из воронки (all-traffic),
    # тот же источник, что у ДРР списка. Фронт умножит её на целевой ДРР % из шапки.
    yesterday = today_msk - timedelta(days=1)
    yest_rows = (
        await db.execute(
            select(
                WbFunnelDaily.nm_id,
                func.coalesce(func.sum(WbFunnelDaily.orders_sum_rub), Decimal("0")).label("rev"),
            )
            .where(WbFunnelDaily.project_id == project_id, WbFunnelDaily.date == yesterday)
            .group_by(WbFunnelDaily.nm_id)
        )
    ).all()
    yest_rev_map = {r.nm_id: float(r.rev) for r in yest_rows}

    # Расход кампаний ВЧЕРА — для ДРР за вчерашний день (расход вчера / заказы вчера)
    yest_spend_rows = (
        await db.execute(
            select(CD.campaign_id, func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"))
            .where(CD.project_id == project_id, CD.date == yesterday)
            .group_by(CD.campaign_id)
        )
    ).all()
    yest_spend_map = {r.campaign_id: float(r.spend) for r in yest_spend_rows}

    # Недобор бюджета за сегодня (до полуночи) по кампаниям с исчерпанным бюджетом
    gap_map = await _budget_gap_today_map(db, project_id, campaigns, today_map)

    # Карта nm_id → бренд/категория (для фильтров на фронте)
    nm_meta_rows = (
        await db.execute(
            select(WbFunnelDaily.nm_id, func.max(WbFunnelDaily.brand).label("brand"), func.max(WbFunnelDaily.subject).label("subject"))
            .where(WbFunnelDaily.project_id == project_id)
            .group_by(WbFunnelDaily.nm_id)
        )
    ).all()
    nm_meta = {r.nm_id: (r.brand, r.subject) for r in nm_meta_rows}

    # Финансовые метрики товаров за период — из воронки (revenue/profit/orders_sum)
    sku_map: dict[int, dict] = {}
    if tax_info is not None:
        from backend.services.funnel.queries import get_funnel_by_sku

        sku_rows = await get_funnel_by_sku(
            db, project_id, tax_info, period_from.isoformat(), period_to.isoformat(),
            None, None, bdr_rates_map=bdr_rates_map, limit=5000,
        )
        sku_map = {r["nm_id"]: r for r in sku_rows}

    result: list[dict[str, Any]] = []
    for c in campaigns:
        s = spend_map.get(c.campaign_id)
        nm_ids = c.nm_ids or []
        spend_period = float(s.spend) if s else 0.0
        views_period = int(s.views) if s else 0
        clicks_period = int(s.clicks) if s else 0
        # Агрегат по товарам кампании (все источники трафика)
        orders_p = revenue_p = profit_p = 0.0
        opens_p = carts_p = order_cnt_p = 0
        for nm in nm_ids:
            row = sku_map.get(nm)
            if row:
                orders_p += float(row.get("orders_sum_rub") or 0)
                revenue_p += float(row.get("revenue") or 0)
                profit_p += float(row.get("profit") or 0)
                opens_p += int(row.get("open_card") or 0)
                carts_p += int(row.get("add_to_cart") or 0)
                order_cnt_p += int(row.get("orders_count") or 0)
        brands = sorted({nm_meta[nm][0] for nm in nm_ids if nm in nm_meta and nm_meta[nm][0]})
        subjects = sorted({nm_meta[nm][1] for nm in nm_ids if nm in nm_meta and nm_meta[nm][1]})
        rev_yesterday = sum(yest_rev_map.get(nm, 0.0) for nm in nm_ids)  # сумма заказов товаров вчера
        spend_yesterday = yest_spend_map.get(c.campaign_id, 0.0)  # расход кампании вчера
        result.append(
            {
                "campaign_id": c.campaign_id,
                "name": c.name,
                "campaign_type": c.campaign_type,
                "advert_type": c.advert_type,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "status": c.status,
                "status_label": CAMPAIGN_STATUS_LABELS.get(c.status, str(c.status)),
                "budget": float(c.budget or 0),
                "nm_ids": nm_ids,
                "nm_count": len(nm_ids),
                "brands": brands,
                "subjects": subjects,
                "spend_today": today_map.get(c.campaign_id, 0.0),
                "spend_period": spend_period,
                "views_period": views_period,
                "clicks_period": clicks_period,
                "ctr": round(clicks_period / views_period * 100, 2) if views_period else 0,
                "cpc": round(spend_period / clicks_period, 2) if clicks_period else 0,
                # Стоимость корзины/заказа за период: расход кампании / корзины (заказы) её товаров
                "cpl": round(spend_period / carts_p, 2) if carts_p else 0,
                "cpo": round(spend_period / order_cnt_p, 2) if order_cnt_p else 0,
                # ДРР за вчера: расход кампании вчера / сумма заказов её товаров вчера
                "drr": round(spend_yesterday / rev_yesterday * 100, 2) if rev_yesterday else 0,
                "margin": round(profit_p / revenue_p * 100, 2) if revenue_p else 0,
                # Средний расход в час за день = расход за период / (дней × 24 ч)
                "spend_per_hour": round(spend_period / (period_days * 24), 2) if period_days > 0 else 0,
                # Режим ставки CPM (единая/ручная) — из WB bid_type, заполняется синком
                "bid_mode": c.bid_mode,
                # Доля рекл. кликов = клики кампании / все переходы её товаров
                "ad_click_share": round(clicks_period / opens_p * 100, 2) if opens_p else 0,
                "cr_cart": round(carts_p / opens_p * 100, 2) if opens_p else 0,  # конверсия в корзину
                "cr_order": round(order_cnt_p / carts_p * 100, 2) if carts_p else 0,  # конверсия в заказ
                "rev_yesterday": round(rev_yesterday, 2),  # сумма заказов товаров вчера (для «ДРР план»)
                "budget_gap": gap_map.get(c.campaign_id, 0.0),  # недобор бюджета до конца дня, ₽
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
        )
    # Активные с расходом — первыми, затем по расходу за период
    result.sort(key=lambda r: (r["status"] != 9, -r["spend_period"]))
    return result


# ─── История кампании (для графика в UI) ────────────────────────────────────


async def get_campaign_history(
    db: AsyncSession,
    project_id: int,
    campaign_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    days: int = 30,
) -> list[dict]:
    """История кампании по дням за выбранный период: расход кампании + метрики товаров.

    Диапазон = [date_from, date_to] (если date_from не задан — последние `days`
    дней до date_to). Формат совпадает с точками графика артикула: date,
    price_spp (средняя цена товаров), open_card, adv_sum (расход ИМЕННО этой
    кампании), drr (расход / заказы её товаров), orders_sum_rub.
    """
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if camp is None:
        return []
    nm_ids = camp.nm_ids or []

    today_msk = utcnow().astimezone(MSK).date() if utcnow().tzinfo else utcnow().date()
    period_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today_msk
    period_from = (
        datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else period_to - timedelta(days=days - 1)
    )

    CD = WbAdCampaignDaily
    spend_rows = (
        await db.execute(
            select(CD.date, func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"))
            .where(
                CD.project_id == project_id, CD.campaign_id == campaign_id,
                CD.date >= period_from, CD.date <= period_to,
            )
            .group_by(CD.date)
        )
    ).all()
    spend_by_date = {r.date: float(r.spend) for r in spend_rows}

    funnel_by_date: dict = {}
    if nm_ids:
        F = WbFunnelDaily
        f_rows = (
            await db.execute(
                select(
                    F.date,
                    func.coalesce(func.sum(F.open_card), 0).label("open_card"),
                    func.coalesce(func.sum(F.orders_sum_rub), Decimal("0")).label("orders_sum"),
                    func.avg(func.nullif(F.avg_price, 0)).label("avg_price"),
                )
                .where(F.project_id == project_id, F.nm_id.in_(nm_ids), F.date >= period_from, F.date <= period_to)
                .group_by(F.date)
            )
        ).all()
        funnel_by_date = {r.date: r for r in f_rows}

    result = []
    d = period_from
    while d <= period_to:
        f = funnel_by_date.get(d)
        spend = spend_by_date.get(d, 0.0)
        orders_sum = float(f.orders_sum) if f else 0.0
        if f or spend > 0:
            result.append(
                {
                    "date": d.isoformat(),
                    "price_spp": round(float(f.avg_price), 2) if f and f.avg_price else 0,
                    "open_card": int(f.open_card) if f else 0,
                    "adv_sum": round(spend, 2),
                    "drr": round(spend / orders_sum * 100, 2) if orders_sum else 0,
                    "orders_sum_rub": round(orders_sum, 2),
                }
            )
        d += timedelta(days=1)
    return result


def _metric_row(
    label: str, views: int, clicks: int, spend: float,
    opens: int, carts: int, orders: int, orders_sum: float, price: float | None,
    customer_price: float | None = None, spp: float = 0.0,
) -> dict:
    """Сводит сырые агрегаты дня/периода в строку метрик (РК + воронка).

    customer_price — «Цена Клиенту»: цена товара с учётом СПП (avg_price × (1 − spp_rate)),
    т.е. сколько реально платил покупатель в тот день. Нужна, чтобы видеть, как цена влияла
    на рекламу. None → 0 (нет данных по СПП за день).
    """
    return {
        "date": label,
        # Статистика по РК
        "views": int(views),
        "clicks": int(clicks),
        "ctr": round(clicks / views * 100, 2) if views else 0.0,
        "cpc": round(spend / clicks, 2) if clicks else 0.0,
        "spend": round(spend, 2),
        # Воронка продаж
        "open_card": int(opens),
        "add_to_cart": int(carts),
        "cr1": round(carts / opens * 100, 2) if opens else 0.0,
        "orders": int(orders),
        "cr2": round(orders / carts * 100, 2) if carts else 0.0,
        "orders_sum": round(orders_sum, 2),
        "cpl": round(spend / carts, 2) if carts else None,  # стоимость 1 корзины
        "cpo": round(spend / orders, 2) if orders else None,  # стоимость 1 заказа
        "avg_price": round(price, 2) if price else 0.0,
        "customer_price": round(customer_price, 2) if customer_price else 0.0,  # цена с СПП
        "spp": round(spp, 1),  # средний СПП за день, % (для графика)
        "drr": round(spend / orders_sum * 100, 2) if orders_sum else 0.0,
    }


def _ad_metric_row(label: str, views: int, clicks: int, spend: float, atbs: int, orders: int) -> dict:
    """Строка чисто РК-метрик зоны показов (без воронки продаж).

    atbs/orders — корзины/заказы, АТРИБУТИРОВАННЫЕ рекламе (из WbAdNmDaily/WbAdSearchDaily),
    а не все продажи товара (те — в воронке по кампании). Числа не взаимозаменяемы.
    """
    return {
        "date": label,
        "views": int(views),
        "clicks": int(clicks),
        "ctr": round(clicks / views * 100, 2) if views else 0.0,
        "cpc": round(spend / clicks, 2) if clicks else 0.0,
        "cpm": round(spend / views * 1000, 2) if views else 0.0,  # цена 1000 показов зоны
        "spend": round(spend, 2),
        "atbs": int(atbs),  # корзины (реклама)
        "orders": int(orders),  # заказы (реклама)
        "cpo": round(spend / orders, 2) if orders else None,  # стоимость 1 заказа
    }


async def list_ad_article_catalog(db: AsyncSession, project_id: int) -> list[dict]:
    """Полный каталог артикулов проекта для каскадных фильтров рекламы:
    nm_id → артикул/предмет/бренд. Без фильтра активности и без топ-лимита —
    иначе в фильтре «Артикул» выпадают товары без свежих продаж/показов.
    """
    rows = (
        await db.execute(
            select(
                WbFunnelDaily.nm_id,
                func.max(WbFunnelDaily.vendor_code),
                func.max(WbFunnelDaily.subject),
                func.max(WbFunnelDaily.brand),
            )
            .where(WbFunnelDaily.project_id == project_id)
            .group_by(WbFunnelDaily.nm_id)
            .limit(50000)
        )
    ).all()
    return [
        {"nm_id": int(r[0]), "vendor_code": r[1] or str(r[0]), "subject": r[2] or "", "brand": r[3] or ""}
        for r in rows
        if r[0] is not None
    ]


async def get_campaign_zones(
    db: AsyncSession, project_id: int, campaign_id: int,
    date_from: str | None = None, date_to: str | None = None, nm_id: int | None = None,
) -> dict:
    """Зоны показов кампании: что включено, по какой ставке и как ставка устроена.

    Правила WB (подтверждены на живых кампаниях):
    - `payment_type=cpm` + `bid_type=unified` — обе зоны всегда включены и крутятся
      одновременно, ставка одна на все зоны (отключить зону нельзя);
    - `payment_type=cpc` — только «Поиск»; ставка — цена за клик, единая для всех фраз
      (в bids_kopecks.recommendations приходит 0).

    Дневного лимита в API WB нет — в кабинете он есть, наружу не отдаётся.
    """
    from backend.services.funnel.wb_advertising_api import fetch_campaign_placements

    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"error": "campaign_not_found"}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"error": "no_api_key"}

    info = await fetch_campaign_placements(api_key, campaign_id)
    payment_type = (camp.campaign_type or "").lower()

    # У CPC зона одна («Поиск»), поэтому её статистика = вся РК-статистика кампании.
    # Берём её из wb_ad_nm_daily: там рекламные заказы, а не заказы воронки со всех источников.
    zone_stats = None
    if payment_type == "cpc" and date_from and date_to:
        from backend.services.funnel.cluster_analysis_service import zone_metrics

        conds = [
            WbAdNmDaily.project_id == project_id, WbAdNmDaily.campaign_id == campaign_id,
            WbAdNmDaily.date >= datetime.strptime(date_from, "%Y-%m-%d").date(),
            WbAdNmDaily.date <= datetime.strptime(date_to, "%Y-%m-%d").date(),
        ]
        if nm_id is not None:
            conds.append(WbAdNmDaily.nm_id == nm_id)
        row = (
            await db.execute(
                select(
                    func.coalesce(func.sum(WbAdNmDaily.views), 0),
                    func.coalesce(func.sum(WbAdNmDaily.clicks), 0),
                    func.coalesce(func.sum(WbAdNmDaily.spend), 0),
                    func.coalesce(func.sum(WbAdNmDaily.orders), 0),
                ).where(*conds)
            )
        ).one()
        zone_stats = {
            "search": zone_metrics(int(row[0]), int(row[1]), float(row[2]), int(row[3])),
            "recommendations": None,
            "derived": False,  # у CPC зона одна — это прямые данные, не вычитание
        }

    locked, lock_reason = _zones_lock(payment_type, camp.bid_mode, camp.status)
    return {
        "zone_stats": zone_stats,
        "campaign_id": campaign_id,
        "payment_type": payment_type,          # cpm | cpc
        "bid_mode": camp.bid_mode,             # unified | manual
        "placements": info["placements"],      # {"search": bool, "recommendations": bool}
        "bids": info["bids"],                  # ₽ по зонам
        # Переключать зоны WB даёт только у CPM с ручной ставкой (статусы 4/9/11)
        "zones_locked": locked,
        "lock_reason": lock_reason,
        # Ставка задаётся сразу на все зоны (CPM-единая) или на все фразы (CPC)
        "single_bid": payment_type == "cpc" or (camp.bid_mode or "") == "unified",
    }


# Статусы, в которых WB разрешает менять зоны: 4 — готова, 9 — активна, 11 — пауза
ZONE_EDIT_STATUSES = (4, 9, 11)


def _zones_lock(payment_type: str, bid_mode: str | None, status: int | None) -> tuple[bool, str | None]:
    """Можно ли переключать зоны показов, и если нет — почему.

    WB (PUT /adv/v0/auction/placements) принимает только CPM с ручной ставкой
    в статусах 4/9/11. Возвращает (locked, lock_reason | None).
    """
    if payment_type == "cpc":
        return True, "CPC · показы только в поиске, зону выключить нельзя"
    if (bid_mode or "") != "manual":
        return True, "CPM · единая ставка: зоны работают одновременно, WB не даёт выключить"
    if status not in ZONE_EDIT_STATUSES:
        return True, "Зоны можно менять только у готовой, активной или приостановленной кампании"
    return False, None


async def set_campaign_zones(
    db: AsyncSession, project_id: int, campaign_id: int, placements: dict[str, bool],
) -> dict:
    """Включить/выключить зоны показов кампании (только CPM с ручной ставкой).

    `placements` — итоговое состояние обеих зон, напр. {"search": True, "recommendations": False}.
    WB заменяет набор целиком, поэтому передаём обе зоны, а не дельту.
    Возвращает {"ok": bool, "error": str | None, "placements": {...} | None}.
    """
    from backend.services.funnel.wb_advertising_api import set_campaign_placements

    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена", "placements": None}

    locked, reason = _zones_lock((camp.campaign_type or "").lower(), camp.bid_mode, camp.status)
    if locked:
        return {"ok": False, "error": reason, "placements": None}

    # Кампания без единой зоны показов нигде не крутится — WB такое молча принимает,
    # реклама встаёт. Требуем хотя бы одну включённую.
    if not any(placements.values()):
        return {"ok": False, "error": "Нельзя выключить обе зоны: реклама перестанет показываться", "placements": None}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Не настроен API-ключ «Продвижение»", "placements": None}

    res = await set_campaign_placements(api_key, campaign_id, placements)
    if not res["ok"]:
        return {"ok": False, "error": res["error"], "placements": None}
    # Зоны у нас не хранятся — их читает fetch_campaign_placements напрямую из WB.
    return {"ok": True, "error": None, "placements": placements}


async def set_campaign_zone_bid(
    db: AsyncSession, project_id: int, campaign_id: int, zone: str, bid: float,
) -> dict:
    """Сменить ставку кампании для зоны (Поиск/Рекомендации) по всем её товарам.

    Единая ставка → placement 'combined' (одна на обе зоны), ручная → placement=зона,
    CPC → 'search'. WB (PATCH /api/advert/v1/bids) принимает статусы 4/9/11. Реальные деньги.
    Возвращает {"ok", "error", "bid"} (bid — применённая ставка ₽).
    """
    from backend.services.funnel.wb_advertising_api import set_campaign_bid

    if bid is None or bid <= 0:
        return {"ok": False, "error": "Ставка должна быть больше 0", "bid": None}

    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена", "bid": None}
    if camp.status not in ZONE_EDIT_STATUSES:
        return {"ok": False, "error": "Ставку можно менять только у готовой, активной или приостановленной кампании", "bid": None}
    nm_ids = [int(n) for n in (camp.nm_ids or [])]
    if not nm_ids:
        return {"ok": False, "error": "У кампании нет товаров", "bid": None}

    ptype = (camp.campaign_type or "").lower()
    if ptype == "cpc":
        placement = "search"  # CPC крутится только в поиске
    elif (camp.bid_mode or "unified") == "manual":
        placement = zone if zone in ("search", "recommendations") else "search"
    else:
        placement = "combined"  # единая ставка — одна на обе зоны

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Не настроен API-ключ «Продвижение»", "bid": None}

    return await set_campaign_bid(api_key, campaign_id, nm_ids, bid, placement)


async def get_campaign_metrics(
    db: AsyncSession,
    project_id: int,
    campaign_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    days: int = 30,
    nm_id: int | None = None,
    zone: str | None = None,
    bdr_rates_map: BdrRatesLookup | None = None,
) -> dict:
    """Посуточные метрики кампании: статистика РК (показы/клики/CTR/CPC/затраты)
    + воронка её товаров (переходы/корзины/CR1/заказы/CR2/сумма/CPO/цена/ДРР).

    nm_id — если задан, И воронка, И РК-статистика считаются ТОЛЬКО по этому товару:
    разбивку РК по товарам даёт WB (/adv/v3/fullstats → nms), мы храним её в
    WbAdNmDaily. Флаг ответа `ad_by_nm` говорит, отфильтрована ли РК по товару —
    за даты до появления таблицы разбивки нет, и там РК-цифры будут нулевыми.

    Строки отсортированы по убыванию даты; сверху — строка-итог «За всё время».
    """
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"error": "campaign_not_found"}
    nm_ids = camp.nm_ids or []
    # Фильтр по одному товару. Не сверяем с camp.nm_ids: там текущий состав кампании,
    # а в статистике бывают товары, выведенные из неё позже (история остаётся за ними).
    if nm_id is not None:
        nm_ids = [nm_id]

    today_msk = utcnow().astimezone(MSK).date() if utcnow().tzinfo else utcnow().date()
    period_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today_msk
    period_from = (
        datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else period_to - timedelta(days=days - 1)
    )

    if nm_id is not None:
        # РК по одному товару — из разбивки кампания × товар (источник: WB fullstats → nms)
        ND = WbAdNmDaily
        ad_rows = (
            await db.execute(
                select(
                    ND.date,
                    func.coalesce(func.sum(ND.views), 0).label("views"),
                    func.coalesce(func.sum(ND.clicks), 0).label("clicks"),
                    func.coalesce(func.sum(ND.spend), Decimal("0")).label("spend"),
                )
                .where(
                    ND.project_id == project_id, ND.campaign_id == campaign_id, ND.nm_id == nm_id,
                    ND.date >= period_from, ND.date <= period_to,
                )
                .group_by(ND.date)
            )
        ).all()
    else:
        CD = WbAdCampaignDaily
        ad_rows = (
            await db.execute(
                select(
                    CD.date,
                    func.coalesce(func.sum(CD.views), 0).label("views"),
                    func.coalesce(func.sum(CD.clicks), 0).label("clicks"),
                    func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"),
                )
                .where(
                    CD.project_id == project_id, CD.campaign_id == campaign_id,
                    CD.date >= period_from, CD.date <= period_to,
                )
                .group_by(CD.date)
            )
        ).all()
    ad_by_date = {r.date: r for r in ad_rows}

    funnel_by_date: dict = {}
    if nm_ids:
        F = WbFunnelDaily
        f_rows = (
            await db.execute(
                select(
                    F.date,
                    func.coalesce(func.sum(F.open_card), 0).label("opens"),
                    func.coalesce(func.sum(F.add_to_cart), 0).label("carts"),
                    func.coalesce(func.sum(F.orders_count), 0).label("orders"),
                    func.coalesce(func.sum(F.orders_sum_rub), Decimal("0")).label("orders_sum"),
                    func.avg(func.nullif(F.avg_price, 0)).label("avg_price"),
                )
                .where(F.project_id == project_id, F.nm_id.in_(nm_ids), F.date >= period_from, F.date <= period_to)
                .group_by(F.date)
            )
        ).all()
        funnel_by_date = {r.date: r for r in f_rows}

    # Разбивка РК-части по зоне показов (только CPM). WB не даёт зоны по дням готовыми:
    # «Поиск» — из wb_ad_search_daily (сумма кластеров), «Рекомендации» — итог минус поиск.
    # Воронка по зонам НЕ разбивается — остаётся по кампании/товару (флаг zone_note).
    zone_applied = None
    if zone in ("search", "recommendations") and (camp.campaign_type or "").lower() == "cpm":
        from types import SimpleNamespace

        from backend.services.funnel.ad_search_stats import get_search_daily

        search_by_date = await get_search_daily(db, project_id, campaign_id, period_from, period_to, nm_id)
        if zone == "search":
            ad_by_date = {
                d: SimpleNamespace(views=v["views"], clicks=v["clicks"], spend=v["spend"])
                for d, v in search_by_date.items()
            }
        else:  # recommendations = итог кампании по дням минус поиск (зажим нулём)
            rec: dict = {}
            for d, a in ad_by_date.items():
                s = search_by_date.get(d, {"views": 0, "clicks": 0, "spend": 0.0})
                rec[d] = SimpleNamespace(
                    views=max(0, int(a.views) - s["views"]),
                    clicks=max(0, int(a.clicks) - s["clicks"]),
                    spend=max(0.0, float(a.spend) - s["spend"]),
                )
            ad_by_date = rec
        zone_applied = zone

    # Средний СПП кампании за день = среднее spp_rate по её товарам (из финотчёта, per (nm,дата)).
    # «Цена Клиенту» = цена дня × (1 − СПП). Нет карты СПП → 0 (customer_price = сама цена).
    def _spp_for_day(d) -> float:
        if not bdr_rates_map or not nm_ids:
            return 0.0
        rates = [
            br.spp_rate for nm in nm_ids
            if (br := bdr_rates_map.get(int(nm), d)) is not None
        ]
        return sum(rates) / len(rates) if rates else 0.0

    rows: list[dict] = []
    tot = {"views": 0, "clicks": 0, "spend": 0.0, "opens": 0, "carts": 0, "orders": 0,
           "orders_sum": 0.0, "price_sum": 0.0, "price_n": 0, "cust_sum": 0.0, "cust_n": 0,
           "spp_sum": 0.0, "spp_n": 0}
    all_dates = sorted(set(ad_by_date) | set(funnel_by_date), reverse=True)
    for d in all_dates:
        a = ad_by_date.get(d)
        f = funnel_by_date.get(d)
        views = int(a.views) if a else 0
        clicks = int(a.clicks) if a else 0
        spend = float(a.spend) if a else 0.0
        opens = int(f.opens) if f else 0
        carts = int(f.carts) if f else 0
        orders = int(f.orders) if f else 0
        orders_sum = float(f.orders_sum) if f else 0.0
        price = float(f.avg_price) if f and f.avg_price else None
        spp_frac = _spp_for_day(d)  # средний СПП кампании за день (доля)
        customer_price = round(price * (1 - spp_frac), 2) if price else None
        rows.append(_metric_row(d.isoformat(), views, clicks, spend, opens, carts, orders, orders_sum, price, customer_price, spp=spp_frac * 100))
        tot["views"] += views; tot["clicks"] += clicks; tot["spend"] += spend
        tot["opens"] += opens; tot["carts"] += carts; tot["orders"] += orders; tot["orders_sum"] += orders_sum
        if price:
            tot["price_sum"] += price; tot["price_n"] += 1
        if customer_price:
            tot["cust_sum"] += customer_price; tot["cust_n"] += 1
        if spp_frac > 0:
            tot["spp_sum"] += spp_frac * 100; tot["spp_n"] += 1

    totals = _metric_row(
        "За всё время", int(tot["views"]), int(tot["clicks"]), tot["spend"],
        int(tot["opens"]), int(tot["carts"]), int(tot["orders"]), tot["orders_sum"],
        tot["price_sum"] / tot["price_n"] if tot["price_n"] else None,
        tot["cust_sum"] / tot["cust_n"] if tot["cust_n"] else None,
        spp=tot["spp_sum"] / tot["spp_n"] if tot["spp_n"] else 0.0,
    )

    return {
        "campaign_id": campaign_id,
        "name": camp.name,
        "window": {"from": period_from.isoformat(), "to": period_to.isoformat()},
        "nm_id": nm_id,
        # РК-метрики отфильтрованы по товару (а не по всей кампании)
        "ad_by_nm": nm_id is not None,
        # Зона показов, по которой отфильтрована РК-часть (воронка остаётся по кампании)
        "zone": zone_applied,
        "totals": totals,
        "rows": rows,
    }


async def get_campaign_zone_metrics(
    db: AsyncSession,
    project_id: int,
    campaign_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    days: int = 30,
    zone: str = "total",
) -> dict:
    """Посуточные РК-метрики кампании в разрезе зоны показов: Всего / Поиск / Рекомендации.

    Только рекламная статистика (показы/клики/CTR/CPC/затраты + рекламные корзины/заказы/CPO):
    воронку продаж по зонам делить нельзя — WB не отдаёт её в разбивке по зонам.

    Источники: «Всего» — WbAdNmDaily (итог кампании по дням, из fullstats, там же рекламные
    корзины/заказы); «Поиск» — WbAdSearchDaily (сумма поисковых кластеров, /adv/v1/normquery/stats);
    «Рекомендации» = Всего − Поиск (по каждой метрике, зажим нулём; считается на чтении).
    Разбивка по зонам — только для CPM (у CPC поиск = вся кампания).

    Строки по убыванию даты; сверху — итог «За всё время».
    """
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"error": "campaign_not_found"}

    today_msk = utcnow().astimezone(MSK).date() if utcnow().tzinfo else utcnow().date()
    period_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today_msk
    period_from = (
        datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else period_to - timedelta(days=days - 1)
    )

    # «Всего» по кампании и дням — из разбивки по товарам (в ней есть рекламные корзины/заказы)
    ND = WbAdNmDaily
    tot_rows = (
        await db.execute(
            select(
                ND.date,
                func.coalesce(func.sum(ND.views), 0).label("views"),
                func.coalesce(func.sum(ND.clicks), 0).label("clicks"),
                func.coalesce(func.sum(ND.spend), Decimal("0")).label("spend"),
                func.coalesce(func.sum(ND.atbs), 0).label("atbs"),
                func.coalesce(func.sum(ND.orders), 0).label("orders"),
            )
            .where(
                ND.project_id == project_id, ND.campaign_id == campaign_id,
                ND.date >= period_from, ND.date <= period_to,
            )
            .group_by(ND.date)
        )
    ).all()
    total_by_date = {
        r.date: {
            "views": int(r.views), "clicks": int(r.clicks), "spend": float(r.spend),
            "atbs": int(r.atbs), "orders": int(r.orders),
        }
        for r in tot_rows
    }

    is_cpm = (camp.campaign_type or "").lower() == "cpm"
    zone_applied = "total"
    if zone in ("search", "recommendations") and is_cpm:
        from backend.services.funnel.ad_search_stats import get_search_daily

        search_by_date = await get_search_daily(db, project_id, campaign_id, period_from, period_to)
        if zone == "search":
            data_by_date = search_by_date  # {date: {views, clicks, spend, atbs, orders}}
        else:  # recommendations = Всего − Поиск (по каждой метрике, зажим нулём)
            data_by_date = {}
            for d, t in total_by_date.items():
                s = search_by_date.get(d, {"views": 0, "clicks": 0, "spend": 0.0, "atbs": 0, "orders": 0})
                data_by_date[d] = {
                    "views": max(0, t["views"] - s["views"]),
                    "clicks": max(0, t["clicks"] - s["clicks"]),
                    "spend": max(0.0, t["spend"] - s["spend"]),
                    "atbs": max(0, t["atbs"] - s["atbs"]),
                    "orders": max(0, t["orders"] - s["orders"]),
                }
        zone_applied = zone
    else:
        # Не CPM или зона не запрошена — отдаём «Всего» (зоны неприменимы)
        data_by_date = total_by_date

    tot = {"views": 0, "clicks": 0, "spend": 0.0, "atbs": 0, "orders": 0}
    rows: list[dict] = []
    for d in sorted(data_by_date, reverse=True):
        m = data_by_date[d]
        rows.append(_ad_metric_row(d.isoformat(), m["views"], m["clicks"], m["spend"], m["atbs"], m["orders"]))
        for k in tot:
            tot[k] += m[k]

    totals = _ad_metric_row("За всё время", tot["views"], tot["clicks"], tot["spend"], tot["atbs"], tot["orders"])

    return {
        "campaign_id": campaign_id,
        "name": camp.name,
        "zone": zone_applied,
        "window": {"from": period_from.isoformat(), "to": period_to.isoformat()},
        "totals": totals,
        "rows": rows,
    }


# ─── Настройки автопополнения (project_settings, без миграций) ──────────────


def _sanitize_autopay(entry: dict) -> dict:
    """Нормализация записи настроек автопополнения одной кампании.

    Два режима:
      to_target  — долить до дневного бюджета X в заданный час МСК при пороге по обороту (наш режим);
      low_balance — «как на ВБ»: когда остаток < low_balance_threshold, долить topup_amount (в любой час, повторяемо).
    Старые записи без mode считаются to_target (обратная совместимость).
    """
    hour = int(entry.get("hour", 9))
    mode = entry.get("mode", "to_target")
    if mode not in ("to_target", "low_balance"):
        mode = "to_target"
    return {
        "enabled": bool(entry.get("enabled", False)),
        "mode": mode,
        "amount": max(0.0, float(entry.get("amount", 0) or 0)),
        "hour": min(23, max(0, hour)),
        "threshold_pct": min(100, max(0, int(entry.get("threshold_pct", 50) or 50))),
        "low_balance_threshold": max(0.0, float(entry.get("low_balance_threshold", 1000) or 0)),
        "topup_amount": max(0.0, float(entry.get("topup_amount", 1000) or 0)),
        # low_balance: «не чаще N раз в день». 0 = без ограничения (как чекбокс WB снят).
        "daily_cap": max(0, int(entry.get("daily_cap", 1) or 0)),
    }


def _autopay_active(s: dict) -> bool:
    """Настройка реально работает: включена и у соответствующего режиму поля суммы > 0."""
    if not s.get("enabled"):
        return False
    if s.get("mode") == "low_balance":
        return float(s.get("topup_amount") or 0) > 0
    return float(s.get("amount") or 0) > 0


async def get_autopay_settings(db: AsyncSession, project_id: int) -> dict:
    """{campaign_id(str): {enabled, amount, hour, threshold_pct}} из project_settings."""
    from backend.services.settings_service import get_setting

    raw = await get_setting(db, project_id, AUTOPAY_SETTINGS_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    return {str(k): _sanitize_autopay(v) for k, v in data.items() if isinstance(v, dict)}


async def set_autopay_setting(db: AsyncSession, project_id: int, campaign_id: int, entry: dict) -> dict:
    """Обновить настройку одной кампании (merge в JSON-настройке проекта)."""
    from backend.services.settings_service import set_setting

    settings = await get_autopay_settings(db, project_id)
    settings[str(campaign_id)] = _sanitize_autopay(entry)
    # Выключенные с нулевыми суммами не храним — не раздуваем JSON (topup_amount держит конфиг low_balance)
    settings = {k: v for k, v in settings.items() if v["enabled"] or v["amount"] > 0 or v["topup_amount"] > 0}
    await set_setting(db, project_id, AUTOPAY_SETTINGS_KEY, json.dumps(settings, ensure_ascii=False))
    return settings


async def save_autopay_and_maybe_activate(
    db: AsyncSession, project_id: int, campaign_id: int, entry: dict
) -> dict:
    """Сохранить настройку автопополнения; если включили — активировать кампанию.

    На паузе WB кампанию не откручивает → автопополнение для неё бессмысленно,
    поэтому включение автопополнения запускает кампанию (best-effort: ошибка WB
    не мешает сохранению настройки, а возвращается вызывающему для показа).

    Возвращает {"settings": {...}, "activation": {ok,status,error} | None}.
    """
    settings = await set_autopay_setting(db, project_id, campaign_id, entry)
    activation = None
    saved = settings.get(str(campaign_id))
    if saved and _autopay_active(saved):
        # Активируем ТОЛЬКО кампанию на паузе. На уже активной (9) WB start
        # отбился бы ошибкой → ложный баннер + лишний write при правке суммы.
        status = (
            await db.execute(
                select(WbAdCampaign.status).where(
                    WbAdCampaign.project_id == project_id,
                    WbAdCampaign.campaign_id == campaign_id,
                )
            )
        ).scalar_one_or_none()
        if status == CAMPAIGN_STATUS_PAUSED:
            activation = await set_campaign_active(db, project_id, campaign_id, True)
    return {"settings": settings, "activation": activation}


# ─── Пауза / запуск кампании (реальный вызов WB) ─────────────────────────────

CAMPAIGN_STATUS_READY = 4       # готова к запуску (создана, не запускалась)
CAMPAIGN_STATUS_COMPLETED = 7   # завершена (необратимо)
CAMPAIGN_STATUS_ACTIVE = 9
CAMPAIGN_STATUS_PAUSED = 11

# В каких статусах WB разрешает операцию (см. openapi promotion)
MANAGE_ALLOWED_STATUSES = {
    "stop": (4, 9, 11),
    "delete": (4,),
    "bids": (4, 9, 11),
    "nms": (4, 9, 11),
    "rename": (4, 7, 9, 11, -1),  # можно в любой момент
}


def _manage_guard(action: str, status: int | None) -> str | None:
    """Причина, по которой операцией нельзя, либо None если можно.

    Отбиваем у себя ДО похода в WB: у завершённой/в-процессе-удаления кампании
    большинство операций WB вернёт 400, а удаление доступно только «готовой».
    """
    allowed = MANAGE_ALLOWED_STATUSES.get(action)
    if allowed is None:
        return f"Неизвестная операция: {action}"
    if status in allowed:
        return None
    if action == "delete":
        return "Удалить можно только кампанию в статусе «Готова» (не запускавшуюся)"
    if action == "stop":
        return "Завершить можно только готовую, активную или приостановленную кампанию"
    return "Операция недоступна в текущем статусе кампании"


async def _get_advert_api_key(db: AsyncSession, project_id: int) -> str | None:
    """Ключ для рекламных вызовов — каскад wb_advert → wb_analytics → wb."""
    from backend.services.funnel.wb_api_client import get_wb_key

    return (
        await get_wb_key(db, project_id, "wb_advert")
        or await get_wb_key(db, project_id, "wb_analytics")
        or await get_wb_key(db, project_id, "wb")
    )


async def set_campaign_active(db: AsyncSession, project_id: int, campaign_id: int, active: bool) -> dict:
    """Запустить (active=True) или поставить на паузу (False) кампанию в WB.

    Дёргает WB start/pause; при успехе синхронно обновляет наш WbAdCampaign.status
    (чтобы UI не ждал часового синка). Возвращает {"ok", "status", "error"}.
    """
    from backend.services.funnel.wb_advertising_api import set_campaign_state

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "status": None, "error": "Нет WB-ключа с доступом к рекламе."}

    result = await set_campaign_state(api_key, campaign_id, "start" if active else "pause")
    if not result["ok"]:
        return {"ok": False, "status": None, "error": result.get("error")}

    new_status = CAMPAIGN_STATUS_ACTIVE if active else CAMPAIGN_STATUS_PAUSED
    row = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id,
                WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        row.status = new_status
        await db.commit()
    return {"ok": True, "status": new_status, "error": None}


async def _load_campaign(db: AsyncSession, project_id: int, campaign_id: int) -> WbAdCampaign | None:
    return (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()


async def stop_campaign(db: AsyncSession, project_id: int, campaign_id: int) -> dict:
    """Завершить кампанию в WB (НЕОБРАТИМО: статус → 7). Только из 4/9/11."""
    from backend.services.funnel.wb_advertising_api import set_campaign_state

    camp = await _load_campaign(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена"}
    reason = _manage_guard("stop", camp.status)
    if reason:
        return {"ok": False, "error": reason}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}
    res = await set_campaign_state(api_key, campaign_id, "stop")
    if not res["ok"]:
        return {"ok": False, "error": res.get("error")}
    camp.status = CAMPAIGN_STATUS_COMPLETED
    await db.commit()
    return {"ok": True, "status": CAMPAIGN_STATUS_COMPLETED, "error": None}


async def rename_campaign(db: AsyncSession, project_id: int, campaign_id: int, name: str) -> dict:
    """Переименовать кампанию в WB (в любом статусе). При успехе обновляем наше имя."""
    from backend.services.funnel.wb_advertising_api import rename_campaign as wb_rename

    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "Название не может быть пустым"}
    if len(name) > 50:
        return {"ok": False, "error": "Название длиннее 50 символов"}
    camp = await _load_campaign(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена"}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}
    res = await wb_rename(api_key, campaign_id, name)
    if not res["ok"]:
        return {"ok": False, "error": res.get("error")}
    camp.name = name
    await db.commit()
    return {"ok": True, "name": name, "error": None}


async def delete_campaign(db: AsyncSession, project_id: int, campaign_id: int) -> dict:
    """Удалить кампанию в WB (только статус 4). После удаления WB держит её в -1."""
    from backend.services.funnel.wb_advertising_api import delete_campaign as wb_delete

    camp = await _load_campaign(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена"}
    reason = _manage_guard("delete", camp.status)
    if reason:
        return {"ok": False, "error": reason}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}
    res = await wb_delete(api_key, campaign_id)
    if not res["ok"]:
        return {"ok": False, "error": res.get("error")}
    # WB переводит в -1 (в процессе удаления); полное удаление — позже. Пометим у себя.
    camp.status = -1
    await db.commit()
    return {"ok": True, "error": None}


async def change_campaign_nms(
    db: AsyncSession, project_id: int, campaign_id: int, add: list[int], delete: list[int]
) -> dict:
    """Добавить/убрать товары кампании в WB (статусы 4/9/11)."""
    from backend.services.funnel.wb_advertising_api import change_campaign_nms as wb_change

    add = [int(x) for x in (add or [])]
    delete = [int(x) for x in (delete or [])]
    if not add and not delete:
        return {"ok": False, "error": "Не указаны товары для добавления или удаления"}
    camp = await _load_campaign(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена"}
    reason = _manage_guard("nms", camp.status)
    if reason:
        return {"ok": False, "error": reason}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}
    res = await wb_change(api_key, campaign_id, add, delete)
    if not res["ok"]:
        return {"ok": False, "error": res.get("error")}
    return {"ok": True, "error": None}


async def set_card_bids(
    db: AsyncSession, project_id: int, campaign_id: int, bids: list[dict]
) -> dict:
    """Ставки карточек товаров (статусы 4/9/11).

    bids — [{"nm_id": int, "bid_rub": float, "placement": "search"|"recommendations"}].
    Ставку в рублях переводим в копейки для WB.
    """
    from backend.services.funnel.wb_advertising_api import set_card_bids as wb_set_bids

    camp = await _load_campaign(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена"}
    reason = _manage_guard("bids", camp.status)
    if reason:
        return {"ok": False, "error": reason}

    wb_bids = []
    for b in bids or []:
        placement = b.get("placement", "search")
        if placement not in ("search", "recommendations"):
            return {"ok": False, "error": f"Недопустимая зона: {placement}"}
        bid_rub = float(b.get("bid_rub") or 0)
        if bid_rub <= 0:
            return {"ok": False, "error": "Ставка должна быть больше нуля"}
        wb_bids.append({
            "nm_id": int(b["nm_id"]),
            "bid_kopecks": round(bid_rub * 100),
            "placement": placement,
        })
    if not wb_bids:
        return {"ok": False, "error": "Не переданы ставки"}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}
    res = await wb_set_bids(api_key, campaign_id, wb_bids)
    if not res["ok"]:
        return {"ok": False, "error": res.get("error")}
    return {"ok": True, "error": None}


async def get_ad_subjects(db: AsyncSession, project_id: int) -> dict:
    """Предметы, по которым можно создать рекламную кампанию."""
    from backend.services.funnel.wb_advertising_api import fetch_ad_subjects

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"error": "no_api_key", "subjects": []}
    subjects = await fetch_ad_subjects(api_key)
    return {"subjects": subjects}


async def get_ad_nms(db: AsyncSession, project_id: int, subject_ids: list[int]) -> dict:
    """Карточки товаров для кампании по выбранным предметам."""
    from backend.services.funnel.wb_advertising_api import fetch_ad_nms

    if not subject_ids:
        return {"nms": []}
    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"error": "no_api_key", "nms": []}
    nms = await fetch_ad_nms(api_key, subject_ids)
    return {"nms": nms}


# Ограничения WB на создание кампании
CREATE_MAX_NMS = 50
CREATE_BID_TYPES = ("manual", "unified")
CREATE_PAYMENT_TYPES = ("cpm", "cpc")
CREATE_PLACEMENTS = ("search", "recommendations")


def validate_create_campaign(
    name: str, nms: list[int], bid_type: str, payment_type: str, placement_types: list[str] | None,
) -> str | None:
    """Проверка параметров создания ДО похода в WB. None — ок, иначе текст ошибки.

    Правила WB: ≤50 товаров; placement_types только для ручной ставки; CPC — показы
    только в поиске (зона одна). Единая ставка — зоны не выбираются, крутятся обе.
    """
    name = (name or "").strip()
    if not name:
        return "Укажите название кампании"
    if len(name) > 50:
        return "Название длиннее 50 символов"
    if not nms:
        return "Выберите хотя бы один товар"
    if len(nms) > CREATE_MAX_NMS:
        return f"Не больше {CREATE_MAX_NMS} товаров в кампании"
    if bid_type not in CREATE_BID_TYPES:
        return "Недопустимый тип ставки"
    if payment_type not in CREATE_PAYMENT_TYPES:
        return "Недопустимый тип оплаты"
    if placement_types:
        bad = [p for p in placement_types if p not in CREATE_PLACEMENTS]
        if bad:
            return f"Недопустимая зона показа: {', '.join(bad)}"
    # Ручной CPM крутится по выбранным зонам — без единой зоны кампания нигде не покажется
    if bid_type == "manual" and payment_type == "cpm" and not placement_types:
        return "Для ручной ставки выберите хотя бы одну зону показа"
    return None


async def create_campaign(
    db: AsyncSession, project_id: int, name: str, nms: list[int],
    bid_type: str = "manual", payment_type: str = "cpm", placement_types: list[str] | None = None,
) -> dict:
    """Создать рекламную кампанию в WB. Возвращает {"ok", "campaign_id", "error"}.

    После создания синкаем список кампаний, чтобы новая сразу появилась у нас.
    """
    from backend.services.funnel.wb_advertising_api import create_campaign as wb_create

    name = (name or "").strip()
    nms = [int(x) for x in (nms or [])]
    # Для единой ставки и CPC зоны не задаются вручную (WB решает сам)
    if bid_type != "manual":
        placement_types = None
    elif payment_type == "cpc":
        placement_types = ["search"]

    reason = validate_create_campaign(name, nms, bid_type, payment_type, placement_types)
    if reason:
        return {"ok": False, "campaign_id": None, "error": reason}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "campaign_id": None, "error": "Нет WB-ключа с доступом к рекламе."}

    res = await wb_create(api_key, name, nms, bid_type, payment_type, placement_types)
    if not res["ok"]:
        return res
    # Подтянем новую кампанию в наш список (не критично, если не успеет)
    try:
        from backend.services.funnel.ad_campaigns_service import sync_ad_campaigns

        await sync_ad_campaigns(db, project_id)
    except Exception as e:
        logger.warning("create_campaign: sync после создания не удался: %s", e)
    return res


async def deposit_campaign_budget_manual(
    db: AsyncSession, project_id: int, campaign_id: int, amount: int, source: int
) -> dict:
    """Ручное пополнение бюджета кампании (реальные деньги). Инициируется пользователем.

    Пишет запись в тот же журнал, что и автопополнение; при успехе синхронно
    обновляет наш WbAdCampaign.budget новым значением из WB.
    """
    from backend.services.funnel.wb_advertising_api import deposit_campaign_budget

    if amount < 1000:
        return {"ok": False, "error": "Минимальная сумма пополнения — 1000 ₽ (ограничение WB)."}
    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}

    row = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    budget_before = float(row.budget) if row and row.budget is not None else None

    res = await deposit_campaign_budget(api_key, campaign_id, amount, source)
    budget_after = res.get("total")
    if res.get("ok") and row is not None and budget_after is not None:
        row.budget = Decimal(str(budget_after))
        await db.commit()

    await append_autopay_log(db, project_id, {
        "campaign_id": campaign_id,
        "ts": utcnow().isoformat(),
        "amount": amount if res.get("ok") else 0,
        "requested": amount,
        "source": "вручную",
        "status": res.get("status", "unknown"),
        "budget_before": budget_before,
        "budget_after": budget_after,
        "reason": res.get("error"),
    })
    return {"ok": bool(res.get("ok")), "status": res.get("status"), "budget_after": budget_after, "error": res.get("error")}


# ─── Автопополнение: журнал и исполнение (реальные деньги) ───────────────────

AUTOPAY_LOG_KEY = "ads_autopay_log"
AUTOPAY_LOG_CAP = 500  # храним последние N записей, чтобы не раздувать JSON


async def get_autopay_log(db: AsyncSession, project_id: int) -> list[dict]:
    """Журнал пополнений проекта (новые записи первыми)."""
    from backend.services.settings_service import get_setting

    raw = await get_setting(db, project_id, AUTOPAY_LOG_KEY)
    try:
        data = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        data = []
    return [e for e in data if isinstance(e, dict)]


async def append_autopay_log(db: AsyncSession, project_id: int, entry: dict) -> None:
    """Дописать запись в журнал (в начало) с обрезкой до AUTOPAY_LOG_CAP."""
    from backend.services.settings_service import set_setting

    log = await get_autopay_log(db, project_id)
    log.insert(0, entry)
    await set_setting(db, project_id, AUTOPAY_LOG_KEY, json.dumps(log[:AUTOPAY_LOG_CAP], ensure_ascii=False))


def _round_up_50(value: float) -> int:
    """Округление суммы пополнения вверх до шага 50₽ (шаг кабинета WB)."""
    import math

    return int(math.ceil(value / 50.0) * 50)


def compute_autopay_decision(
    setting: dict,
    budget: float,
    spend_day: float,
    now_hour_msk: int,
    already_topped_today: bool,
    pending_unknown: bool,
    topped_today_count: int = 0,
) -> dict:
    """Чистое решение «пополнять ли кампанию сейчас и на сколько».

    setting — {enabled, mode, amount(X), hour, threshold_pct, low_balance_threshold, topup_amount, daily_cap};
    budget — текущий бюджет кампании ₽; spend_day — открут за последние сутки ₽;
    already_topped_today — в журнале уже есть успешное пополнение за сегодня (МСК, для to_target);
    topped_today_count — сколько успешных пополнений уже было сегодня (для low_balance daily_cap);
    pending_unknown — последняя попытка сегодня закончилась timeout/5xx (исход
    неизвестен) → не пополняем, пока бюджет не пересинкается.

    Возвращает {"action": "deposit"|"skip", "sum": int, "reason": str}.
    """
    if not setting.get("enabled"):
        return {"action": "skip", "sum": 0, "reason": "disabled"}

    # Режим «как на ВБ»: остаток упал ниже порога → долить фиксированную сумму (любой час, повторяемо).
    if setting.get("mode") == "low_balance":
        topup = float(setting.get("topup_amount") or 0)
        if topup <= 0:
            return {"action": "skip", "sum": 0, "reason": "disabled"}
        if pending_unknown:  # исход прошлой попытки неизвестен — не рискуем двойным списанием
            return {"action": "skip", "sum": 0, "reason": "pending_unknown"}
        cap = int(setting.get("daily_cap", 1) or 0)  # «не чаще N раз в день»; 0 = без ограничения
        if cap > 0 and topped_today_count >= cap:
            return {"action": "skip", "sum": 0, "reason": "cap_reached"}
        thr = float(setting.get("low_balance_threshold") or 0)
        if float(budget or 0) >= thr:
            return {"action": "skip", "sum": 0, "reason": "above_threshold"}
        return {"action": "deposit", "sum": _round_up_50(max(MIN_TOPUP_RUB, topup)), "reason": "ok"}

    # Режим to_target (наш): долить до X в заданный час при пороге по обороту.
    x = float(setting.get("amount") or 0)
    if x <= 0:
        return {"action": "skip", "sum": 0, "reason": "disabled"}
    if now_hour_msk != int(setting.get("hour", 9)):
        return {"action": "skip", "sum": 0, "reason": "not_time"}
    if already_topped_today:
        return {"action": "skip", "sum": 0, "reason": "already_today"}
    if pending_unknown:
        return {"action": "skip", "sum": 0, "reason": "pending_unknown"}
    threshold_pct = int(setting.get("threshold_pct", 50) or 0)
    if threshold_pct > 0 and spend_day < x * threshold_pct / 100.0:
        return {"action": "skip", "sum": 0, "reason": "below_threshold"}
    gap = x - float(budget or 0)
    if gap <= 0:
        return {"action": "skip", "sum": 0, "reason": "budget_full"}
    return {"action": "deposit", "sum": _round_up_50(max(MIN_TOPUP_RUB, gap)), "reason": "ok"}


async def run_autopay_tick(db: AsyncSession, project_id: int, api_key: str) -> dict:
    """Один проход автопополнения проекта: решения + реальные списания + журнал.

    Вызывается scheduler'ом каждые ~15 минут.
      to_target — срабатывает в настроенный час МСК, идемпотентность по журналу
        (одно успешное пополнение в день на кампанию);
      low_balance — проверяется каждый проход, доливает как только остаток < порога
        (несколько раз в день); повтор в тот же тик исключён свежим чтением бюджета из WB.
    """
    from backend.services.funnel.wb_advertising_api import (
        DEPOSIT_SOURCE_ACCOUNT,
        DEPOSIT_SOURCE_BALANCE,
        DEPOSIT_SOURCE_LABELS,
        deposit_campaign_budget,
        fetch_adv_balance,
        fetch_campaign_budgets_batch,
    )

    settings = await get_autopay_settings(db, project_id)
    enabled = {int(cid): s for cid, s in settings.items() if _autopay_active(s)}
    if not enabled:
        return {"deposits": 0, "checked": 0}

    now_msk = utcnow().astimezone(MSK)
    today_msk = now_msk.date()
    # to_target срабатывает только в свой час МСК; low_balance проверяется каждый проход (по остатку)
    due_ids = [
        cid for cid, s in enabled.items()
        if s.get("mode") == "low_balance" or now_msk.hour == int(s.get("hour", 9))
    ]
    if not due_ids:
        return {"deposits": 0, "checked": 0}

    log = await get_autopay_log(db, project_id)
    topped_count_today: dict[int, int] = {}  # успешных пополнений сегодня на кампанию (для daily_cap)
    unknown_today: set[int] = set()
    for e in log:
        try:
            ts = datetime.fromisoformat(e.get("ts", ""))
            e_date = ts.astimezone(MSK).date() if ts.tzinfo else ts.date()
        except (TypeError, ValueError):
            continue
        if e_date != today_msk:
            continue
        cid = int(e.get("campaign_id") or 0)
        if e.get("status") == "ok":
            topped_count_today[cid] = topped_count_today.get(cid, 0) + 1
        elif e.get("status") == "unknown":
            unknown_today.add(cid)

    # Открут за последние сутки (вчера МСК — полные сутки)
    CD = WbAdCampaignDaily
    yesterday = today_msk - timedelta(days=1)
    spend_rows = (
        await db.execute(
            select(CD.campaign_id, func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"))
            .where(CD.project_id == project_id, CD.date == yesterday, CD.campaign_id.in_(due_ids))
            .group_by(CD.campaign_id)
        )
    ).all()
    spend_day_map = {r.campaign_id: float(r.spend) for r in spend_rows}

    # Свежие бюджеты — прямо из WB (не из нашей таблицы: синк мог отстать на час)
    budgets = await fetch_campaign_budgets_batch(api_key, due_ids)

    deposits = 0
    for cid in due_ids:
        setting = enabled[cid]
        budget = float(budgets.get(cid, -1))
        if budget < 0:
            logger.warning(f"Autopay: project {project_id} campaign {cid} — бюджет из WB не получен, скип")
            continue
        topped_n = topped_count_today.get(cid, 0)
        decision = compute_autopay_decision(
            setting, budget, spend_day_map.get(cid, 0.0), now_msk.hour,
            topped_n > 0, cid in unknown_today, topped_today_count=topped_n,
        )
        if decision["action"] != "deposit":
            continue

        # Источник: как в кабинете — сначала счёт, при отказе баланс. Бонусы не трогаем.
        balance_info = await fetch_adv_balance(api_key) or {}
        result = await deposit_campaign_budget(api_key, cid, decision["sum"], DEPOSIT_SOURCE_ACCOUNT)
        source = DEPOSIT_SOURCE_ACCOUNT
        if result["status"] == "error":
            result = await deposit_campaign_budget(api_key, cid, decision["sum"], DEPOSIT_SOURCE_BALANCE)
            source = DEPOSIT_SOURCE_BALANCE

        await append_autopay_log(
            db,
            project_id,
            {
                "campaign_id": cid,
                "ts": utcnow().isoformat(),
                "amount": decision["sum"] if result["ok"] else 0,
                "requested": decision["sum"],
                "source": DEPOSIT_SOURCE_LABELS.get(source, str(source)),
                "status": result["status"],
                "budget_before": budget,
                "budget_after": result.get("total"),
                "reason": result.get("error"),
                "balance_snapshot": {k: balance_info.get(k) for k in ("balance", "net", "bonus")},
            },
        )
        if result["ok"]:
            deposits += 1

    return {"deposits": deposits, "checked": len(due_ids)}


# ─── Нехватка бюджета ────────────────────────────────────────────────────────


def compute_budget_gap(spend_today: float, ran_out_hour: float | None, now_hour: float) -> dict:
    """Чистый расчёт для строки «нехватка бюджета».

    spend_today — потрачено сегодня ₽; ran_out_hour — час МСК (дробный), когда
    бюджет кончился (None = неизвестно, кончился до первого синка);
    now_hour — текущий час МСК (для скорости, если ран-аут неизвестен).

    burn_rate: ₽/час за время работы (с 00:00 до остановки);
    needed_till_midnight: сколько долить, чтобы при той же скорости хватило
    до 00:00. WB не принимает пополнение меньше MIN_TOPUP_RUB (1000 ₽): если
    расчётная нужда >0, но меньше минимума — показываем минимум (иначе долить
    физически нельзя). raw_needed — расчётная нужда без учёта минимума.
    """
    effective_stop = ran_out_hour if ran_out_hour is not None else min(now_hour, 24.0)
    hours_active = max(effective_stop, 0.25)  # защита от деления на ноль
    burn_rate = spend_today / hours_active if spend_today > 0 else 0.0
    remaining_hours = max(24.0 - effective_stop, 0.0)
    raw_needed = burn_rate * remaining_hours
    needed = max(MIN_TOPUP_RUB, round(raw_needed)) if raw_needed > 0 else 0.0
    return {
        "burn_rate": round(burn_rate, 2),
        "needed_till_midnight": needed,
        "raw_needed": round(raw_needed, 2),
        "min_topup": MIN_TOPUP_RUB,
        "hours_active": round(hours_active, 2),
        "remaining_hours": round(remaining_hours, 2),
    }


def _parse_num(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def get_budget_gaps(db: AsyncSession, project_id: int) -> list[dict]:
    """Кампании, у которых сегодня (МСК) кончился бюджет до конца дня.

    Критерий: активная кампания (status=9), текущий бюджет 0, сегодня был
    расход. Час остановки — последнее событие budget_change → 0 за сегодня;
    если события нет (кончился между синками/до первого) — None.
    """
    now_utc = utcnow()
    now_msk = now_utc.astimezone(MSK) if now_utc.tzinfo else MSK.localize(now_utc.replace(tzinfo=None), is_dst=None).astimezone(MSK)
    today_msk = now_msk.date()
    # Начало суток МСК в naive-UTC (события хранятся в UTC)
    day_start_utc = MSK.localize(datetime.combine(today_msk, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)

    campaigns = (
        (
            await db.execute(
                select(WbAdCampaign).where(
                    WbAdCampaign.project_id == project_id,
                    WbAdCampaign.status == 9,
                    WbAdCampaign.budget <= 0,
                ).limit(2000)
            )
        )
        .scalars()
        .all()
    )
    if not campaigns:
        return []
    ids = [c.campaign_id for c in campaigns]

    CD = WbAdCampaignDaily
    spend_rows = (
        await db.execute(
            select(CD.campaign_id, func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"))
            .where(CD.project_id == project_id, CD.campaign_id.in_(ids), CD.date == today_msk)
            .group_by(CD.campaign_id)
        )
    ).all()
    spend_map = {r.campaign_id: float(r.spend) for r in spend_rows}

    # Последнее событие «бюджет → 0» за сегодня по каждой кампании
    ev_rows = (
        (
            await db.execute(
                select(WbAdCampaignEvent)
                .where(
                    WbAdCampaignEvent.project_id == project_id,
                    WbAdCampaignEvent.campaign_id.in_(ids),
                    WbAdCampaignEvent.event_type == "budget_change",
                    WbAdCampaignEvent.created_at >= day_start_utc,
                )
                .order_by(WbAdCampaignEvent.created_at)
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    ran_out_at: dict[int, datetime] = {}
    for ev in ev_rows:
        new_v = _parse_num(ev.new_value)
        if new_v is not None and new_v <= 0:
            ran_out_at[ev.campaign_id] = ev.created_at  # последний по времени выигрывает
        elif new_v is not None and new_v > 0:
            ran_out_at.pop(ev.campaign_id, None)  # после пополнения не считается остановленной

    # Карта nm_id → бренд/категория (для фильтров бренд/категория на фронте)
    all_nm_ids = {nm for c in campaigns for nm in (c.nm_ids or [])}
    nm_meta: dict[int, tuple] = {}
    if all_nm_ids:
        meta_rows = (
            await db.execute(
                select(
                    WbFunnelDaily.nm_id,
                    func.max(WbFunnelDaily.brand).label("brand"),
                    func.max(WbFunnelDaily.subject).label("subject"),
                )
                .where(WbFunnelDaily.project_id == project_id, WbFunnelDaily.nm_id.in_(all_nm_ids))
                .group_by(WbFunnelDaily.nm_id)
            )
        ).all()
        nm_meta = {r.nm_id: (r.brand, r.subject) for r in meta_rows}

    now_hour = now_msk.hour + now_msk.minute / 60
    result = []
    for c in campaigns:
        spend_today = spend_map.get(c.campaign_id, 0.0)
        if spend_today <= 0:
            continue  # сегодня не крутилась — это не «нехватка бюджета», а «не работает реклама»
        stopped_utc = ran_out_at.get(c.campaign_id)
        if stopped_utc is not None:
            stopped_msk = pytz.UTC.localize(stopped_utc).astimezone(MSK)
            ran_out_hour: float | None = stopped_msk.hour + stopped_msk.minute / 60
            ran_out_iso = stopped_msk.isoformat()
        else:
            ran_out_hour = None
            ran_out_iso = None
        gap = compute_budget_gap(spend_today, ran_out_hour, now_hour)
        result.append(
            {
                "campaign_id": c.campaign_id,
                "name": c.name,
                "campaign_type": c.campaign_type,
                "nm_ids": c.nm_ids or [],
                "nm_count": len(c.nm_ids or []),
                "brands": sorted({nm_meta[nm][0] for nm in (c.nm_ids or []) if nm in nm_meta and nm_meta[nm][0]}),
                "subjects": sorted({nm_meta[nm][1] for nm in (c.nm_ids or []) if nm in nm_meta and nm_meta[nm][1]}),
                "spend_today": round(spend_today, 2),
                "ran_out_at": ran_out_iso,  # None = кончился до первого синка (час неизвестен)
                **gap,
            }
        )
    result.sort(key=lambda r: -r["spend_today"])
    return result


async def _budget_gap_today_map(
    db: AsyncSession, project_id: int, campaigns: list, today_map: dict[int, float],
) -> dict[int, float]:
    """{campaign_id: ₽ недобора до полуночи} для кампаний, у которых сегодня (МСК)
    исчерпан бюджет (status=9, budget<=0, был расход). Момент остановки — последнее
    событие budget_change→0 за сегодня (как в get_budget_gaps); если события нет —
    считаем от текущего часа. Остальным кампаниям недобора нет (в карте отсутствуют).
    """
    ids = [
        c.campaign_id for c in campaigns
        if c.status == 9 and float(c.budget or 0) <= 0 and today_map.get(c.campaign_id, 0.0) > 0
    ]
    if not ids:
        return {}
    now_utc = utcnow()
    now_msk = now_utc.astimezone(MSK) if now_utc.tzinfo else MSK.localize(now_utc.replace(tzinfo=None), is_dst=None).astimezone(MSK)
    today_msk = now_msk.date()
    day_start_utc = MSK.localize(datetime.combine(today_msk, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)
    ev_rows = (
        (
            await db.execute(
                select(WbAdCampaignEvent)
                .where(
                    WbAdCampaignEvent.project_id == project_id,
                    WbAdCampaignEvent.campaign_id.in_(ids),
                    WbAdCampaignEvent.event_type == "budget_change",
                    WbAdCampaignEvent.created_at >= day_start_utc,
                )
                .order_by(WbAdCampaignEvent.created_at)
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    ran_out_at: dict[int, datetime] = {}
    for ev in ev_rows:
        new_v = _parse_num(ev.new_value)
        if new_v is not None and new_v <= 0:
            ran_out_at[ev.campaign_id] = ev.created_at  # последний по времени выигрывает
        elif new_v is not None and new_v > 0:
            ran_out_at.pop(ev.campaign_id, None)  # после пополнения не считается остановленной
    now_hour = now_msk.hour + now_msk.minute / 60
    out: dict[int, float] = {}
    for cid in ids:
        stopped = ran_out_at.get(cid)
        if stopped is not None:
            st = pytz.UTC.localize(stopped).astimezone(MSK)
            ran_out_hour: float | None = st.hour + st.minute / 60
        else:
            ran_out_hour = None
        out[cid] = compute_budget_gap(today_map[cid], ran_out_hour, now_hour)["needed_till_midnight"]
    return out


async def get_hourly_spend(
    db: AsyncSession, project_id: int, campaign_id: int, date: str | None = None,
) -> dict:
    """Почасовой расход кампании за день, восстановленный из снимков остатка бюджета.

    WB НЕ отдаёт показы/клики/заказы по часам — только суточно. Но остаток бюджета
    синкается каждые ~10 мин (WbAdCampaignEvent budget_change), и убывание остатка между
    снимками = потраченные деньги. Суммируем убывания по часу МСК (рост = пополнение —
    пропускаем). Точность — до интервала синка (~10 мин); события есть только пока кампания
    активна (status=9). Возвращает 24 часа (0..23) с расходом ₽ + итог.
    """
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"error": "campaign_not_found"}

    now_utc = utcnow()
    now_msk = now_utc.astimezone(MSK) if now_utc.tzinfo else MSK.localize(now_utc.replace(tzinfo=None), is_dst=None).astimezone(MSK)
    day = datetime.strptime(date, "%Y-%m-%d").date() if date else now_msk.date()
    day_start_utc = MSK.localize(datetime.combine(day, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)
    day_end_utc = MSK.localize(datetime.combine(day, datetime.min.time()) + timedelta(days=1)).astimezone(pytz.UTC).replace(tzinfo=None)

    events = (
        (
            await db.execute(
                select(WbAdCampaignEvent)
                .where(
                    WbAdCampaignEvent.project_id == project_id,
                    WbAdCampaignEvent.campaign_id == campaign_id,
                    WbAdCampaignEvent.event_type == "budget_change",
                    WbAdCampaignEvent.created_at >= day_start_utc,
                    WbAdCampaignEvent.created_at < day_end_utc,
                )
                .order_by(WbAdCampaignEvent.created_at)
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    hourly = [0.0] * 24
    for ev in events:
        old_v = _parse_num(ev.old_value)
        new_v = _parse_num(ev.new_value)
        if old_v is None or new_v is None:
            continue
        delta = old_v - new_v  # >0 = потрачено; <0 = пополнение (пропускаем)
        if delta > 0:
            hour = pytz.UTC.localize(ev.created_at).astimezone(MSK).hour
            hourly[hour] += delta

    rows = [{"hour": h, "spend": round(hourly[h], 2)} for h in range(24)]
    return {
        "campaign_id": campaign_id,
        "name": camp.name,
        "date": day.isoformat(),
        "total": round(sum(hourly), 2),
        "hours": rows,
    }


async def get_intraday_metrics(
    db: AsyncSession, project_id: int, campaign_id: int, date: str | None = None,
) -> dict:
    """Внутридневные показы/клики/расход по кампании из снимков накопительного счётчика.

    WB нативно почасовку показов/кликов НЕ отдаёт (мин. ось — сутки). Мы копим снимки
    кабинетного campaigns-stats каждые ~30 мин (WbAdCampaignSnapshot / snapshot_ad_intraday).
    Дельта между соседними снимками одного дня = метрики за интервал (стиль mkeeper,
    «место принятия решения»). Первый снимок дня = накопление от полуночи МСК.

    CTR и порог «мин показов» считает ФРОНТ (интерактивный контрол), поэтому отдаём сырые
    дельты. points[] упорядочены; time = ЧЧ:ММ МСК момента снимка (конец интервала).
    totals = последний накопительный счётчик (авторитетное число WB за день).
    """
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"error": "campaign_not_found"}

    now_msk = pytz.UTC.localize(utcnow()).astimezone(MSK)
    day = datetime.strptime(date, "%Y-%m-%d").date() if date else now_msk.date()

    snaps = (
        (
            await db.execute(
                select(WbAdCampaignSnapshot)
                .where(
                    WbAdCampaignSnapshot.project_id == project_id,
                    WbAdCampaignSnapshot.campaign_id == campaign_id,
                    WbAdCampaignSnapshot.stat_date == day,
                )
                .order_by(WbAdCampaignSnapshot.captured_at)
                .limit(500)
            )
        )
        .scalars()
        .all()
    )

    points = []
    prev_v = prev_c = 0
    prev_s = 0.0
    for snap in snaps:
        cum_v, cum_c = snap.views_cum or 0, snap.clicks_cum or 0
        cum_s = float(snap.spend_cum or 0)
        points.append(
            {
                "time": pytz.UTC.localize(snap.captured_at).astimezone(MSK).strftime("%H:%M"),
                "views": max(0, cum_v - prev_v),  # clamp: WB может скорректировать счётчик вниз
                "clicks": max(0, cum_c - prev_c),
                "spend": round(max(0.0, cum_s - prev_s), 2),
            }
        )
        prev_v, prev_c, prev_s = cum_v, cum_c, cum_s

    return {
        "campaign_id": campaign_id,
        "name": camp.name,
        "date": day.isoformat(),
        "points": points,
        "totals": {"views": prev_v, "clicks": prev_c, "spend": round(prev_s, 2)},
        "snapshots": len(snaps),
    }
