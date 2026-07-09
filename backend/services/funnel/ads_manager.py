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
from backend.models.integrations import WbAdCampaignDaily
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
        result.append(
            {
                "campaign_id": c.campaign_id,
                "name": c.name,
                "campaign_type": c.campaign_type,
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
                "drr": round(spend_period / orders_p * 100, 2) if orders_p else 0,
                "margin": round(profit_p / revenue_p * 100, 2) if revenue_p else 0,
                # Режим ставки CPM (единая/ручная) — из WB bid_type, заполняется синком
                "bid_mode": c.bid_mode,
                # Доля рекл. кликов = клики кампании / все переходы её товаров
                "ad_click_share": round(clicks_period / opens_p * 100, 2) if opens_p else 0,
                "cr_cart": round(carts_p / opens_p * 100, 2) if opens_p else 0,  # конверсия в корзину
                "cr_order": round(order_cnt_p / carts_p * 100, 2) if carts_p else 0,  # конверсия в заказ
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


# ─── Настройки автопополнения (project_settings, без миграций) ──────────────


def _sanitize_autopay(entry: dict) -> dict:
    """Нормализация записи настроек автопополнения одной кампании."""
    hour = int(entry.get("hour", 9))
    return {
        "enabled": bool(entry.get("enabled", False)),
        "amount": max(0.0, float(entry.get("amount", 0) or 0)),
        "hour": min(23, max(0, hour)),
        "threshold_pct": min(100, max(0, int(entry.get("threshold_pct", 50) or 50))),
    }


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
    # Выключенные с нулевой суммой не храним — не раздуваем JSON
    settings = {k: v for k, v in settings.items() if v["enabled"] or v["amount"] > 0}
    await set_setting(db, project_id, AUTOPAY_SETTINGS_KEY, json.dumps(settings, ensure_ascii=False))
    return settings


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
) -> dict:
    """Чистое решение «пополнять ли кампанию сейчас и на сколько».

    setting — {enabled, amount(X, дневной бюджет), hour, threshold_pct};
    budget — текущий бюджет кампании ₽; spend_day — открут за последние сутки ₽;
    already_topped_today — в журнале уже есть успешное пополнение за сегодня (МСК);
    pending_unknown — последняя попытка сегодня закончилась timeout/5xx (исход
    неизвестен) → не пополняем, пока бюджет не пересинкается.

    Возвращает {"action": "deposit"|"skip", "sum": int, "reason": str}.
    """
    x = float(setting.get("amount") or 0)
    if not setting.get("enabled") or x <= 0:
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

    Вызывается scheduler'ом каждые ~15 минут. Срабатывает только для кампаний,
    у которых enabled и текущий час МСК == настроенному. Идемпотентность —
    по журналу (одно успешное пополнение в день на кампанию).
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
    enabled = {int(cid): s for cid, s in settings.items() if s.get("enabled") and (s.get("amount") or 0) > 0}
    if not enabled:
        return {"deposits": 0, "checked": 0}

    now_msk = utcnow().astimezone(MSK)
    today_msk = now_msk.date()
    due_ids = [cid for cid, s in enabled.items() if now_msk.hour == int(s.get("hour", 9))]
    if not due_ids:
        return {"deposits": 0, "checked": 0}

    log = await get_autopay_log(db, project_id)
    topped_today: set[int] = set()
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
            topped_today.add(cid)
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
        decision = compute_autopay_decision(
            setting, budget, spend_day_map.get(cid, 0.0), now_msk.hour, cid in topped_today, cid in unknown_today
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
